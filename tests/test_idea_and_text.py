from __future__ import annotations

import pytest

from mrfinancer.idea import normalize_idea
from mrfinancer.models import Jurisdiction
from mrfinancer.textutil import (
    extract_domain,
    name_similarity,
    normalize_company_name,
    text_overlap,
)


class TestIdeaNormalization:
    def test_pulls_content_words_and_drops_filler(self):
        idea = normalize_idea(
            "An app that helps freelancers track invoices and chase late payments"
        )
        assert "invoices" in idea.keywords
        assert "freelancers" in idea.keywords
        # "app", "helps", "that" are filler and must not become search terms.
        assert "app" not in idea.keywords
        assert "helps" not in idea.keywords

    def test_recognises_category_terms(self):
        idea = normalize_idea("a payments and invoice reconciliation tool for retail")
        assert "payments" in idea.category_terms
        assert "retail" in idea.category_terms

    def test_builds_a_query_ladder_without_duplicates(self):
        idea = normalize_idea("subscription billing analytics for SaaS companies")
        assert idea.search_queries
        assert len(idea.search_queries) == len(set(idea.search_queries))
        assert all(q.strip() for q in idea.search_queries)

    @pytest.mark.parametrize(
        "text,expected",
        [
            ("GST invoicing for Indian small businesses", Jurisdiction.IN),
            ("a tool that files with Companies House in the UK", Jurisdiction.GB),
            ("Delaware incorporation helper", Jurisdiction.US),
            ("a generic scheduling tool", Jurisdiction.UNKNOWN),
        ],
    )
    def test_detects_jurisdiction_hints(self, text, expected):
        assert normalize_idea(text).jurisdiction_hint is expected

    def test_empty_idea_is_rejected(self):
        with pytest.raises(ValueError):
            normalize_idea("   ")


class TestDomainExtraction:
    @pytest.mark.parametrize(
        "url,expected",
        [
            ("https://www.acmepay.com/pricing", "acmepay.com"),
            ("https://app.acmepay.co.uk", "acmepay.co.uk"),
            ("acmepay.in", "acmepay.in"),
            ("https://sub.deep.acmepay.com", "acmepay.com"),
        ],
    )
    def test_registrable_domain(self, url, expected):
        assert extract_domain(url) == expected

    @pytest.mark.parametrize(
        "url",
        [
            "https://github.com/someone/project",
            "https://myapp.vercel.app",
            "https://apps.apple.com/app/id123",
            "https://www.producthunt.com/posts/thing",
            None,
            "not a url",
        ],
    )
    def test_hosts_that_identify_no_company_return_none(self, url):
        # A GitHub or App Store link tells us nothing about which company is
        # behind a product, so it must not be used for entity resolution.
        assert extract_domain(url) is None


class TestCompanyNames:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("Acme Pay Private Limited", "acme pay"),
            ("ACME PAY LTD", "acme pay"),
            ("Acme Pay, Inc.", "acme pay"),
            ("Acme Pay Technologies LLP", "acme pay technologies"),
            ("Acme & Sons Ltd", "acme and sons"),
        ],
    )
    def test_strips_legal_suffixes(self, raw, expected):
        assert normalize_company_name(raw) == expected

    def test_similarity_ignores_legal_form(self):
        assert name_similarity("Acme Pay", "ACME PAY LIMITED") == 1.0

    def test_similarity_is_low_for_different_companies(self):
        assert name_similarity("Acme Pay", "Zenith Logistics") < 0.5

    def test_partial_names_score_between(self):
        score = name_similarity("Acme", "Acme Pay Ltd")
        assert 0.5 < score < 1.0

    def test_subset_name_must_not_score_as_a_perfect_match(self):
        """Regression: rapidfuzz's token_set_ratio scores a subset at 100, so
        "Acme" matched "Acme Pay Ltd" perfectly and cleared the 0.80 entity
        resolution floor -- attaching one company's filed accounts to another
        company's product. Any scorer used here must stay below the floor."""
        assert name_similarity("Acme", "Acme Pay Ltd") < 0.80
        assert name_similarity("Pay", "Global Payments Holdings Limited") < 0.80

    def test_word_order_still_matches(self):
        # The legitimate reason to use a token-aware scorer at all.
        assert name_similarity("Acme Pay", "Pay Acme Limited") > 0.95


class TestTextOverlap:
    def test_counts_matching_keywords(self):
        score = text_overlap(
            ["invoice", "freelancer", "payment"],
            "Invoice tool for freelancers",
            None,
        )
        assert score == pytest.approx(2 / 3)

    def test_no_terms_or_no_text_is_zero(self):
        assert text_overlap([], "anything") == 0.0
        assert text_overlap(["invoice"], None, None) == 0.0
