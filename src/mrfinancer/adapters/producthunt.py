"""Product Hunt OAuth and documented topic-based launch discovery."""
from __future__ import annotations

from datetime import date, timedelta

from ..models import Idea, ProductMatch
from ..net import FetchError, Fetcher
from ..provenance import AccessMethod, Gap, Provenance, ReasonCode, Trust
from ..ranking import query_variants, rank_matches
from ..textutil import extract_domain

ENDPOINT = "https://api.producthunt.com/v2/api/graphql"
TOKEN_ENDPOINT = "https://api.producthunt.com/v2/oauth/token"
TOPICS = "query DiscoverTopics($q: String!) { topics(query: $q, first: 3) { nodes { slug } } }"
POSTS = """
query DiscoverLaunches($topic: String!, $first: Int!, $after: DateTime!, $order: PostsOrder!) {
  posts(topic: $topic, first: $first, postedAfter: $after, order: $order) {
    nodes {
      id name tagline description url website productLinks { url } votesCount commentsCount createdAt
    }
  }
}
"""
TOPIC_HINTS = {
    "invoic": "invoicing", "billing": "accounting", "payment": "payments",
    "schedule": "productivity", "booking": "productivity", "freelanc": "freelancing",
    "education": "education", "fintech": "fintech", "accounting": "accounting",
    "design": "design-tools", "marketing": "marketing", "analytics": "analytics",
}


class ProductHuntAdapter:
    id = "producthunt"
    name = "Product Hunt"
    is_scraper = False

    def __init__(self, token: str | None, client_id: str | None = None,
                 client_secret: str | None = None, enabled: bool = True) -> None:
        self.token = token or None
        self.client_id = client_id
        self.client_secret = client_secret
        self.enabled = enabled

    def available(self) -> Gap | None:
        if not self.enabled:
            return Gap(field="discovery", reason=ReasonCode.ADAPTER_DISABLED, source=self.id)
        if not self.token and not (self.client_id and self.client_secret):
            return Gap(field="discovery", reason=ReasonCode.CREDENTIALS_MISSING, source=self.id,
                       detail="Configure a Product Hunt token or both OAuth client credentials.")
        return None

    async def access_token(self, fetcher: Fetcher) -> str:
        if self.token:
            return self.token
        payload = await fetcher.post_json(TOKEN_ENDPOINT, {
            "grant_type": "client_credentials",
            "client_id": self.client_id,
            "client_secret": self.client_secret,
        }, use_cache=False)
        token = payload.get("access_token")
        if not isinstance(token, str) or not token:
            raise FetchError(ReasonCode.PARSE_FAILED, "Product Hunt OAuth response had no access token")
        self.token = token
        return token

    def skip_reason(self, idea: Idea) -> str | None:
        if idea.delivery_mode == "local":
            return "Skipped for a physical service idea"
        return None

    async def discover(self, idea: Idea, fetcher: Fetcher, limit: int) -> list[ProductMatch]:
        if idea.delivery_mode == "local":
            return []
        token = await self.access_token(fetcher)
        hints = [topic for term, topic in TOPIC_HINTS.items() if term in idea.raw.casefold()]
        seeds = list(dict.fromkeys([*hints, *query_variants(idea), *idea.keywords]))[:4]
        slugs: list[str] = []
        for seed in seeds:
            data = await fetcher.post_graphql(ENDPOINT, TOPICS, {"q": seed},
                                             token=token, estimated_cost=25, cache_public=True)
            for node in data["data"]["topics"]["nodes"]:
                slug = node.get("slug")
                if slug and slug not in slugs:
                    slugs.append(slug)
            if len(slugs) >= 3:
                break
        out: dict[str, ProductMatch] = {}
        today = date.today()
        for slug in slugs[:3]:
            for days, order in ((540, "VOTES"), (120, "NEWEST")):
                cutoff = today - timedelta(days=days + today.day - 1)
                size = min(30, max(12, limit))
                data = await fetcher.post_graphql(ENDPOINT, POSTS, {
                    "topic": slug, "first": size,
                    "after": cutoff.isoformat() + "T00:00:00Z", "order": order,
                }, token=token, estimated_cost=10 + size * 4, cache_public=True)
                for node in data["data"]["posts"]["nodes"]:
                    if not node.get("name") or not node.get("url"):
                        continue
                    record_id = str(node.get("id") or node["url"])
                    if record_id in out:
                        continue
                    website = next((link["url"] for link in (node.get("productLinks") or [])
                                    if link.get("url") and extract_domain(link["url"])), None)
                    website = website or node.get("website")
                    if extract_domain(website) is None:
                        website = None
                    out[record_id] = ProductMatch(
                        name=node["name"], tagline=node.get("tagline"),
                        description=node.get("description"), url=node["url"],
                        homepage=website, domain=extract_domain(website),
                        source_record_id=record_id, topics=[slug.replace("-", " ")],
                        signals={key: node[field] for key, field in (
                            ("producthunt_votes", "votesCount"), ("producthunt_comments", "commentsCount"),
                            ("launched_at", "createdAt"),
                        ) if node.get(field) is not None},
                        provenance=Provenance(
                            source=self.id, source_name=self.name, method=AccessMethod.OFFICIAL_API,
                            trust=Trust.OFFICIAL, url=node["url"], note="Maker-submitted launch listing.",
                        ),
                    )
        return rank_matches(idea, list(out.values()))[:limit]

    async def health(self, fetcher: Fetcher) -> str:
        token = await self.access_token(fetcher)
        await fetcher.post_graphql(ENDPOINT, "query { posts(first: 1) { nodes { id } } }",
                                   {}, token=token, estimated_cost=12)
        return f"ok, approximately {fetcher.ph_budget.remaining} complexity points left"
