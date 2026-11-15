"""Rank retrieved evidence by the job, audience, and delivery mode in the idea."""
from __future__ import annotations

import math
import re
import unicodedata
from collections import Counter

from .idea import STOPWORDS
from .models import Idea, ProductMatch

# Each group describes one interchangeable concept, not a whole category.
ALIASES = {
    "invoice follow-up": ("invoice chasing", "chase invoice", "chasing invoice", "invoice reminder", "payment reminder", "late payment", "overdue invoice", "accounts receivable", "debt collection"),
    "freelancer": ("freelance", "independent contractor", "solopreneur", "self employed"),
    "small business": ("small enterprise", "small company", "sme", "smb", "msme"),
    "booking": ("appointment scheduling", "appointment booking", "online booking", "reservation"),
    "laundry": ("dry cleaning", "laundromat", "launderette", "wash and fold"),
    "food delivery": ("meal delivery", "meal subscription", "tiffin", "lunch delivery"),
    "coworking": ("shared office", "serviced office", "co working"),
    "pet grooming": ("dog grooming", "pet salon", "dog salon"),
    "childcare": ("day care", "daycare", "nursery school"),
    "ev charging": ("electric vehicle charging", "charging station"),
    "solar": ("photovoltaic",),
    "beauty salon": ("hair salon", "beauty parlour", "hairdresser"),
    "tutoring": ("tuition", "private tutor", "test prep"),
    "inventory": ("stock management", "stock control"),
    "payment": ("checkout", "payment processing"),
    "accounting": ("bookkeeping",),
    "crm": ("customer relationship management",),
}
AUDIENCES = {
    "freelancer", "small business", "enterprise", "restaurant", "student",
    "teacher", "creator", "developer", "parent", "clinic", "retailer",
    "landlord", "nonprofit", "school", "salon", "shop", "agency", "founder",
}
COUNTRY_WORDS = {
    "india": "in", "united kingdom": "gb", "uk": "gb", "united states": "us",
    "usa": "us", "australia": "au", "canada": "ca", "germany": "de",
    "france": "fr", "japan": "jp", "new zealand": "nz", "singapore": "sg",
    "south africa": "za", "brazil": "br", "mexico": "mx", "ireland": "ie",
}
GENERIC = {"online", "business", "customer", "company", "management", "system", "professional", "local", "digital", "global"}
DIGITAL = re.compile(r"\b(?:app|software|saas|api|plugin|extension|website|marketplace|automation|dashboard)\b", re.I)
PHYSICAL = re.compile(r"\b(?:laundry|dry.cleaning|tiffin|restaurant|caf[eé]|grooming|salon|clinic|coworking|repair|tutoring|daycare|installation|catering|gym|venue|delivery|pickup)\b", re.I)


def words(text: str) -> list[str]:
    text = unicodedata.normalize("NFKC", text).casefold()
    return re.findall(r"[^\W_]+", text, flags=re.UNICODE)


def singular(word: str) -> str:
    if len(word) > 4 and word.endswith("ies"):
        return word[:-3] + "y"
    if len(word) > 3 and word.endswith("s") and not word.endswith(("ss", "us", "is", "ics")):
        return word[:-1]
    return word


def tokens(text: str) -> list[str]:
    return [singular(w) for w in words(text)]


def spans(text: str) -> set[str]:
    terms = tokens(text)
    return {" ".join(terms[i:i + n]) for n in (1, 2, 3, 4) for i in range(len(terms) - n + 1)}


ALIASES_BY_FORM = {
    " ".join(tokens(form)): canonical
    for canonical, variants in ALIASES.items()
    for form in (canonical, *variants)
}


def concepts(text: str) -> set[str]:
    return {ALIASES_BY_FORM[form] for form in spans(text) if form in ALIASES_BY_FORM}


def concept_components(concept: str) -> set[str]:
    return {word for phrase, canonical in ALIASES_BY_FORM.items()
            if canonical == concept for word in phrase.split()}


def market_terms(text: str) -> set[str]:
    parts = (part.strip().casefold() for part in text.split(","))
    return {term for part in parts for term in tokens(COUNTRY_WORDS.get(part, part))}


def delivery_mode(text: str, location: str | None) -> str:
    digital, physical = bool(DIGITAL.search(text)), bool(PHYSICAL.search(text))
    if digital:
        # A booking app for salons remains software. A salon with an app can
        # appear as an adjacent analogue in the physical market.
        return "digital"
    if physical or (location and re.search(r"\b(?:shop|store|service|studio|centre|center|venue|business)\b", text, re.I)):
        return "local"
    return "general"


def query_variants(idea: Idea) -> list[str]:
    expanded = sorted(concepts(idea.raw) - AUDIENCES - {"payment"})
    return list(dict.fromkeys([*expanded[:2], *idea.search_queries]))[:6]


def rank_matches(idea: Idea, matches: list[ProductMatch]) -> list[ProductMatch]:
    """Combine exact terms, curated equivalents, phrase, audience and context."""
    if not matches:
        return []
    idea_terms = {
        singular(term) for word in idea.keywords
        for term in words(word)
        if term not in STOPWORDS
    }
    location_words = market_terms(idea.location or "")
    idea_terms -= set(tokens(idea.location or "")) | location_words
    idea_concepts = concepts(idea.raw)
    audiences = (idea_terms | idea_concepts) & AUDIENCES
    job_concepts = idea_concepts - AUDIENCES
    equivalent_words = set().union(*(concept_components(c) for c in job_concepts))
    job_terms = (idea_terms - equivalent_words - audiences) | job_concepts
    # Some audience terms also name the actual service, as with a restaurant.
    if not (job_terms - GENERIC):
        job_terms |= audiences
        audiences = set()
    phrase_terms = (idea_terms - audiences) | job_concepts
    phrases = {
        phrase for phrase in spans(idea.raw)
        if 2 <= len(phrase.split()) <= 3 and set(phrase.split()) <= (phrase_terms - GENERIC)
    }
    head_texts = [" ".join(filter(None, [m.name, m.tagline, *m.topics])) for m in matches]
    body_texts = [" ".join(filter(None, [head, m.description])) for head, m in zip(head_texts, matches)]
    heads = [set(tokens(text)) | concepts(text) for text in head_texts]
    bodies = [set(tokens(text)) | concepts(text) for text in body_texts]
    frequencies = Counter(term for body in bodies for term in body & job_terms)

    weights = {
        term: (1.0 + math.log((len(matches) + 2) / (frequencies[term] + 2)))
        * (2.4 if term in idea_concepts else 0.3 if term in GENERIC else 1.0)
        for term in job_terms
    }
    total = sum(weights.values()) or 1.0
    requires_automatic = bool(re.search(r"\bautomat(?:ic(?:ally)?|ed|ion)\b", idea.raw, re.I))
    for match, head, body, body_text in zip(matches, heads, bodies, body_texts):
        hit_terms = job_terms & body
        core_hits = hit_terms - GENERIC
        nearby_phrases = (spans(body_text) & phrases) | ((idea_concepts - AUDIENCES) & body)
        audience_hits = audiences & body
        coverage = sum(weights[t] * (1 if t in head else 0.72) for t in hit_terms) / total
        job = max(coverage, 0.65 if nearby_phrases & idea_concepts else 0.0)
        audience = len(audience_hits) / len(audiences) if audiences else 1.0
        match.score_reasons = []
        if not core_hits:
            match.similarity = 0
            match.match_type = "related"
            continue
        if nearby_phrases:
            match.score_reasons.append("Matching offering: " + ", ".join(sorted(nearby_phrases)[:3]))
        other_hits = sorted(core_hits - nearby_phrases)
        if other_hits:
            match.score_reasons.append("Shared terms: " + ", ".join(other_hits[:5]))
        if audience_hits:
            match.score_reasons.append("Matching audience: " + ", ".join(sorted(audience_hits)))
        elif audiences:
            match.score_reasons.append("Target audience not stated in the listing")
        score = 0.63 * job + 0.19 * bool(nearby_phrases) + 0.12 * audience
        competing = match.kind in ("product", "local_business", "supplier")
        mode_matches = (
            (idea.delivery_mode != "digital" or match.kind == "product")
            and (idea.delivery_mode != "local" or match.kind in ("local_business", "supplier"))
        )
        market_words = market_terms(match.market or "")
        location_matches = not location_words or bool(location_words <= market_words)
        if idea.delivery_mode == "local" and location_words:
            match.score_reasons.append(
                "Matches the requested market" if location_matches else "Location differs or is unstated"
            )
        offering_terms = job_terms - GENERIC - idea_concepts
        enough_detail = coverage >= 0.53 or len(offering_terms) <= 1
        disclaims_automatic = bool(re.search(
            r"\b(?:no|without)\s+(?:any\s+)?automat(?:ic|ed)\s+(?:sending|emails?|reminders?|follow[- ]?ups?)\b"
            r"|\b(?:does not|doesn['â€™]t|never)\s+(?:automatically|send automatically)\b",
            body_text, re.I,
        ))
        lacks_required_automation = requires_automatic and disclaims_automatic
        if lacks_required_automation:
            match.score_reasons.append("Listing explicitly excludes automatic sending")
        high_confidence = bool((nearby_phrases and enough_detail)
                               or (len(core_hits) >= 2 and coverage >= 0.58))
        if competing and mode_matches and location_matches and high_confidence and audience >= 0.5 and not lacks_required_automation:
            match.match_type = "direct"
            score += 0.06
        elif competing and mode_matches:
            # A directory category can identify a local analogue, yet give no
            # evidence for a service detail such as pickup or subscriptions.
            match.match_type = "similar"
            score = min(score, 0.82 if location_matches else 0.64)
        else:
            match.match_type = "related"
            score = min(score, 0.58 if match.kind != "demand_signal" else 0.48)
        match.similarity = min(1.0, round(score, 4))
    return sorted(matches, key=lambda m: (-m.similarity, m.name.casefold(), m.url or ""))
