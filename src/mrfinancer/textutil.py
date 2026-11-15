"""Name and domain normalization used by discovery and entity resolution."""

from __future__ import annotations

import re
import unicodedata
from urllib.parse import urlparse

from rapidfuzz import fuzz

# Legal suffixes stripped before comparing company names. Keeping this list
# explicit (rather than regexing anything after a comma) avoids mangling names
# that genuinely contain words like "Group" or "Labs".
LEGAL_SUFFIXES = [
    "private limited", "pvt limited", "pvt ltd", "pvt. ltd.", "p ltd",
    "public limited company", "limited liability partnership", "llp",
    "limited", "ltd", "ltd.", "plc", "inc", "inc.", "incorporated",
    "corporation", "corp", "corp.", "company", "co", "co.",
    "llc", "l.l.c.", "gmbh", "ag", "bv", "b.v.", "nv", "n.v.", "sa", "s.a.",
    "spol s r o", "s r o", "kft", "sas", "sasu", "sarl", "co ltd",
    "pte ltd", "pte", "sdn bhd", "oy", "ab", "as", "aps", "srl", "spa",
]

# Hosts that never identify a company, so a product listed under one gives us
# no entity-resolution signal.
GENERIC_HOSTS = {
    "github.com", "github.io", "gitlab.com", "notion.so", "notion.site",
    "google.com", "docs.google.com", "sites.google.com", "forms.gle",
    "apps.apple.com", "play.google.com", "chromewebstore.google.com",
    "chrome.google.com", "producthunt.com", "medium.com", "substack.com",
    "wordpress.com", "wixsite.com", "webflow.io", "vercel.app", "netlify.app",
    "herokuapp.com", "firebaseapp.com", "pages.dev", "replit.app",
    "linkedin.com", "twitter.com", "x.com", "facebook.com", "youtube.com",
    "gumroad.com", "carrd.co", "framer.website", "softr.app", "bubbleapps.io",
}

_NON_ALNUM = re.compile(r"[^\w ]|_", re.UNICODE)
_SPACES = re.compile(r"\s+")


def extract_domain(url: str | None) -> str | None:
    """Registrable-ish domain, or None if the host tells us nothing.

    Not a full public-suffix implementation; it handles the common two-label
    country suffixes (co.uk, co.in, com.au) which is what matters here.
    """
    if not url:
        return None
    if "//" not in url:
        url = f"https://{url}"
    host = (urlparse(url).hostname or "").lower().strip(".")
    if not host or "." not in host:
        return None
    if host.startswith("www."):
        host = host[4:]

    labels = host.split(".")
    second_level = {"co", "com", "net", "org", "gov", "ac", "edu"}
    if len(labels) >= 3 and labels[-2] in second_level and len(labels[-1]) == 2:
        registrable = ".".join(labels[-3:])
    else:
        registrable = ".".join(labels[-2:])

    if registrable in GENERIC_HOSTS or host in GENERIC_HOSTS:
        return None
    return registrable


def domain_root(domain: str | None) -> str | None:
    """'acmepay.co.uk' -> 'acmepay'. Used to compare a domain with a name."""
    if not domain:
        return None
    return domain.split(".")[0]


def normalize_company_name(name: str) -> str:
    """Lowercase, strip legal suffixes and punctuation, collapse spaces."""
    s = "".join(c for c in unicodedata.normalize("NFKD", name.casefold())
                if not unicodedata.combining(c)).strip()
    s = s.replace("&", " and ")
    s = _NON_ALNUM.sub(" ", s)
    s = _SPACES.sub(" ", s).strip()
    # Strip suffixes repeatedly: "Acme Pay Private Limited" -> "acme pay"
    changed = True
    while changed:
        changed = False
        for suffix in LEGAL_SUFFIXES:
            suf = _SPACES.sub(" ", _NON_ALNUM.sub(" ", suffix)).strip()
            if suf and s.endswith(" " + suf):
                s = s[: -(len(suf) + 1)].strip()
                changed = True
            elif s == suf:
                return ""
    return s


def name_similarity(a: str, b: str) -> float:
    """0-1 similarity between two company or product names.

    Deliberately NOT token_set_ratio. That scorer treats a subset as a perfect
    match, so "Acme" vs "Acme Pay Ltd" scores 1.00 -- which would clear the
    entity-resolution confidence floor and attach Acme Pay's filed accounts to
    an unrelated product called Acme. Wrong-company financials are the worst
    output this module can produce, so the scorer must punish missing tokens.

    token_sort_ratio still handles word order ("Acme Pay" vs "Pay Acme"), which
    is the legitimate reason to want a token-based scorer at all.
    """
    na, nb = normalize_company_name(a), normalize_company_name(b)
    if not na or not nb:
        return 0.0
    if na == nb:
        return 1.0
    return max(fuzz.ratio(na, nb), fuzz.token_sort_ratio(na, nb)) / 100.0


def text_overlap(idea_terms: list[str], *texts: str | None) -> float:
    """Share of idea keywords present in the product's own text. Crude but
    transparent -- a founder can see exactly why something scored."""
    if not idea_terms:
        return 0.0
    blob = " ".join(t.lower() for t in texts if t)
    if not blob:
        return 0.0
    hits = sum(1 for term in idea_terms if term.lower() in blob)
    return hits / len(idea_terms)
