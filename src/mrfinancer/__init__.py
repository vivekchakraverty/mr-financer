"""Mr. Financer -- module 1: product idea research.

Takes a founder's product or service idea, finds similar products, resolves
them to real companies and attaches whatever financial data is genuinely
available for free, with provenance on every figure.
"""

from .idea import normalize_idea
from .models import IdeaReport
from .pipeline import analyze_idea
from .report import render_markdown
from .validation import PROFILES, Thresholds, thresholds_for, validate_facts

__version__ = "0.1.2"

__all__ = [
    "analyze_idea",
    "normalize_idea",
    "render_markdown",
    "validate_facts",
    "thresholds_for",
    "Thresholds",
    "PROFILES",
    "IdeaReport",
]
