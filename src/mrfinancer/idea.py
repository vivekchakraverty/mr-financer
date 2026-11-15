"""Turn a founder's free-text idea into search terms.

This is deliberately deterministic: no LLM call, so it runs offline, costs
nothing and behaves identically in tests. `normalize_idea` is the seam where an
LLM pass can later be added (see `extract_with_llm` placeholder in README) --
but the rule-based version must stay as the fallback, because a founder typing
an idea should never hit a paid API before they see any value.
"""

from __future__ import annotations

import re

from .models import Idea, Jurisdiction

STOPWORDS = {
    "a", "about", "after", "all", "also", "an", "and", "any", "app", "application",
    "are", "as", "at", "be", "been", "being", "best", "both", "build", "building",
    "but", "by", "can", "could", "create", "day", "do", "does", "each", "easy",
    "for", "from", "get", "give", "gives", "had", "has", "have", "help", "helps",
    "her", "here", "his", "how", "i", "idea", "if", "in", "into", "is", "it",
    "its", "just", "let", "lets", "like", "make", "makes", "many", "me", "more",
    "most", "my", "need", "needs", "new", "no", "not", "of", "on", "one", "only",
    "or", "other", "our", "out", "over", "own", "people", "platform", "product",
    "really", "service", "should", "simple", "so", "software", "solution", "some",
    "such", "than", "that", "the", "their", "them", "then", "there", "these",
    "they", "thing", "this", "those", "to", "tool", "up", "us", "use", "used",
    "user", "users", "using", "very", "want", "wants", "was", "way", "we", "well",
    "what", "when", "where", "which", "while", "who", "why", "will", "with",
    "would", "you", "your",
}

# Words that signal a market category worth searching on its own.
CATEGORY_HINTS = {
    "accounting", "analytics", "automation", "banking", "billing", "booking",
    "chat", "crm", "credit", "dashboard", "delivery", "design", "ecommerce",
    "education", "email", "erp", "fintech", "fitness", "fraud", "funding",
    "healthcare", "hiring", "hr", "insurance", "inventory", "invoice",
    "lending", "logistics", "marketing", "marketplace", "payments", "payroll",
    "pitch", "pos", "procurement", "recruitment", "reporting", "retail",
    "scheduling", "seo", "subscription", "support", "tax", "ticketing",
    "travel", "valuation", "video", "wallet", "warehouse",
}

JURISDICTION_WORDS = {
    Jurisdiction.FR: {"france", "french"},
    Jurisdiction.KR: {"south korea", "korea", "korean"},
    Jurisdiction.SK: {"slovakia", "slovak"},
    Jurisdiction.NO: {"norway", "norwegian"},
    Jurisdiction.IN: {"india", "indian", "bharat", "inr", "rupee", "gst", "mca", "sebi"},
    Jurisdiction.GB: {"uk", "britain", "british", "england", "gbp", "hmrc", "companies house"},
    Jurisdiction.US: {"usa", "us", "america", "american", "usd", "sec", "delaware"},
}

_WORD = re.compile(r"[^\W\d_][\w+#.-]*")
_LOCATION = re.compile(r"\b(?:near|based in|located in|in)\s+([A-Z][\w.-]*(?:[ ,]+[A-Z][\w.-]*){0,3})(?=$|[,.;!?]|\s)")


def _tokens(text: str) -> list[str]:
    return [word.strip('.-') for word in _WORD.findall(text.casefold())]


def _detect_jurisdiction(text: str) -> Jurisdiction:
    lowered = text.lower()
    for juris, words in JURISDICTION_WORDS.items():
        if any(re.search(rf"\b{re.escape(w)}\b", lowered) for w in words):
            return juris
    return Jurisdiction.UNKNOWN


def _keywords(tokens: list[str], limit: int = 10) -> list[str]:
    """Frequency-ranked content words, order of first appearance preserved for
    ties so the founder's own emphasis survives."""
    counts: dict[str, int] = {}
    first_seen: dict[str, int] = {}
    for i, tok in enumerate(tokens):
        if tok in STOPWORDS or len(tok) < 3:
            continue
        counts[tok] = counts.get(tok, 0) + 1
        first_seen.setdefault(tok, i)
    ranked = sorted(counts, key=lambda t: (-counts[t], first_seen[t]))
    return ranked[:limit]


def _bigrams(tokens: list[str], keywords: set[str], limit: int = 4) -> list[str]:
    """Adjacent content-word pairs: 'expense tracking' beats 'expense' alone."""
    out: list[str] = []
    for a, b in zip(tokens, tokens[1:]):
        if a in keywords and b in keywords and f"{a} {b}" not in out:
            out.append(f"{a} {b}")
        if len(out) >= limit:
            break
    return out


def normalize_idea(raw: str, location: str | None = None) -> Idea:
    if not raw or not raw.strip():
        raise ValueError("idea text is empty")

    from .ranking import delivery_mode

    detected = _LOCATION.search(raw)
    location = (location or (detected.group(1) if detected else "")).strip(" .") or None
    search_text = raw
    if location:
        search_text = re.sub(rf"(?<!\w){re.escape(location)}(?!\w)", " ", raw, flags=re.I)
    tokens = _tokens(search_text)
    keywords = _keywords(tokens)
    kw_set = set(keywords)
    phrases = _bigrams(tokens, kw_set)
    categories = [k for k in keywords if k in CATEGORY_HINTS]

    # Query ladder: most specific first. Adapters stop early when they have
    # enough, so ordering is what controls result quality.
    queries: list[str] = []
    queries.extend(phrases[:2])
    if categories and keywords:
        lead = next((k for k in keywords if k not in categories), None)
        for cat in categories[:2]:
            queries.append(f"{lead} {cat}".strip() if lead else cat)
    if keywords:
        queries.append(" ".join(keywords[:3]))
    if categories:
        queries.append(f"{categories[0]} software")

    seen: set[str] = set()
    deduped = [q for q in queries if q and not (q in seen or seen.add(q))]

    return Idea(
        raw=raw.strip(),
        keywords=keywords,
        category_terms=categories,
        search_queries=deduped[:5],
        jurisdiction_hint=_detect_jurisdiction(f"{raw} {location or ''}"),
        location=location,
        delivery_mode=delivery_mode(search_text, location),
    )
