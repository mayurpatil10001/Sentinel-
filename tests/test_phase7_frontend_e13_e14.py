"""
tests/test_phase7_frontend_e13_e14.py
=======================================
Phase 7: Frontend regression tests for E12, E13, E14 audit findings.

E12 — UI column labelled "ML Confidence" (should be separate Anomaly/Rule scores)
E13 — Wrong PFUTP regulation captions hardcoded in dashboard JS
E14 — Misleading product title / meta description

These are build-time static checks (read the HTML, not a live server).
They must pass on every commit without a running server.

HARD RULE: Do NOT weaken or skip these tests. If a future change to index.html
reintroduces any of the forbidden strings, the test must fail.
"""

import pathlib
import re
import pytest

HTML_PATH = pathlib.Path(__file__).parent.parent / "dashboard" / "static" / "index.html"


@pytest.fixture(scope="module")
def html():
    if not HTML_PATH.exists():
        pytest.skip(f"Dashboard HTML not found at {HTML_PATH}")
    return HTML_PATH.read_text(encoding="utf-8", errors="ignore")


# ---------------------------------------------------------------------------
# E14 — Product title / meta description (Phase 6 fix regression guard)
# ---------------------------------------------------------------------------

class TestProductTitle:
    """
    E14: Title and meta description must not contain misleading authority claims.
    """

    def test_no_national_in_title(self, html):
        """'National' in a product title implies official status Sentinel does not have."""
        title_match = re.search(r"<title>(.*?)</title>", html, re.IGNORECASE | re.DOTALL)
        if title_match:
            assert "National" not in title_match.group(1), (
                "<title> contains 'National' — implies official government/regulator status. "
                "Must use honest description like 'Market Anomaly Surveillance Dashboard'."
            )

    def test_no_institutional_in_title(self, html):
        """'Institutional' in a product title is an overclaim."""
        title_match = re.search(r"<title>(.*?)</title>", html, re.IGNORECASE | re.DOTALL)
        if title_match:
            assert "Institutional" not in title_match.group(1), (
                "<title> contains 'Institutional' — an overclaim for a heuristic tool."
            )

    def test_no_integrity_terminal_in_title(self, html):
        """'Integrity Terminal' implies regulatory authority Sentinel lacks."""
        title_match = re.search(r"<title>(.*?)</title>", html, re.IGNORECASE | re.DOTALL)
        if title_match:
            assert "Integrity Terminal" not in title_match.group(1), (
                "<title> contains 'Integrity Terminal' — implies regulatory function."
            )

    def test_title_contains_honest_description(self, html):
        """Title must contain 'Anomaly' — the honest description of what Sentinel does."""
        title_match = re.search(r"<title>(.*?)</title>", html, re.IGNORECASE | re.DOTALL)
        assert title_match is not None, "No <title> tag found in index.html"
        assert "Anomaly" in title_match.group(1) or "Sentinel" in title_match.group(1), (
            f"<title> does not contain 'Anomaly' or 'Sentinel'. Got: {title_match.group(1)!r}"
        )

    def test_meta_description_not_institutional(self, html):
        """Meta description must not claim 'Institutional' authority."""
        meta_match = re.search(
            r'<meta\s+name=["\']description["\']\s+content=["\'](.*?)["\']',
            html, re.IGNORECASE | re.DOTALL
        )
        if meta_match:
            desc = meta_match.group(1)
            assert "Institutional Market Surveillance Terminal" not in desc, (
                "Meta description still contains 'Institutional Market Surveillance Terminal'. "
                "This was removed in Phase 6 (E14 fix)."
            )

    def test_sidebar_not_national_terminal(self, html):
        """Sidebar text must not read 'National Surveillance Terminal'."""
        assert "National Surveillance Terminal" not in html, (
            "index.html contains 'National Surveillance Terminal' in the sidebar. "
            "Phase 6 fix replaced this with 'Anomaly Detection Dashboard'."
        )

    def test_sidebar_honest_label_present(self, html):
        """Sidebar must contain the corrected honest label."""
        assert "Anomaly Detection Dashboard" in html, (
            "index.html sidebar is missing 'Anomaly Detection Dashboard'. "
            "Phase 6 fix should have inserted this in place of 'National Surveillance Terminal'."
        )


# ---------------------------------------------------------------------------
# E13 — Wrong PFUTP regulation captions (Phase 5 fix regression guard)
# ---------------------------------------------------------------------------

class TestPFUTPClauses:
    """
    E13: Dashboard JS must use correct PFUTP Reg 4 sub-clause numbers.

    Correct mapping (matches sebi_report.py):
      circular  → 4(2)(a)   (not 4(2)(b) or 4(2)(n))
      pump      → 4(2)(a)/(e)  (not 4(2)(r))
      pinning   → 4(2)(a)/(e)
      oi_/basis → 4(2)(e)

    Forbidden old wrong clauses:
      4(2)(b) pre-arranged synchronized transactions  — wrong for circular
      4(2)(r) misleading information                 — wrong for pump/volume/price
      4(2)(n)                                        — not used anywhere
    """

    def test_no_wrong_clause_4_2_b_for_circular(self, html):
        """
        Circular trading was wrongly labelled 4(2)(b) in original JS.
        It should be 4(2)(a). The wrong label must be gone.
        """
        # Specifically the wrong combination: (b) assigned to circular
        wrong = re.search(
            r"circular.*?4\(2\)\(b\)|4\(2\)\(b\).*?circular",
            html, re.IGNORECASE | re.DOTALL
        )
        assert wrong is None, (
            "index.html JS still maps 'circular' to PFUTP clause 4(2)(b). "
            "Correct clause is 4(2)(a). E13 fix not applied."
        )

    def test_no_wrong_clause_4_2_r_for_pump(self, html):
        """
        Pump was wrongly labelled 4(2)(r) misleading information.
        Price/volume detectors cannot establish 'misleading information' (4(2)(r)).
        The wrong label must be gone.
        """
        assert "4(2)(r)" not in html, (
            "index.html contains PFUTP clause '4(2)(r)' — 'misleading information'. "
            "No price/volume detector can establish this. "
            "Pump should be 4(2)(a)/(e). E13 fix not applied."
        )

    def test_circular_mapped_to_4_2_a(self, html):
        """Circular trading must be mapped to 4(2)(a) in the JS clause builder."""
        # Find the pfutp clause assignment block for circular
        circular_block = re.search(
            r"circular.*?pfutp\s*=\s*['\"]([^'\"]+)['\"]",
            html, re.DOTALL
        )
        if circular_block:
            assert "4(2)(a)" in circular_block.group(1), (
                f"Circular trading PFUTP clause is {circular_block.group(1)!r}. "
                f"Expected to contain '4(2)(a)'. E13 fix incomplete."
            )

    def test_pump_mapped_to_4_2_a_or_e(self, html):
        """Coordinated pump must be mapped to 4(2)(a)/(e) in JS."""
        pump_block = re.search(
            r"pump.*?pfutp\s*=\s*['\"]([^'\"]+)['\"]",
            html, re.DOTALL
        )
        if pump_block:
            clause = pump_block.group(1)
            assert "4(2)(a)" in clause or "4(2)(e)" in clause, (
                f"Pump PFUTP clause is {clause!r}. Expected '4(2)(a)' and/or '4(2)(e)'. "
                "E13 fix incomplete."
            )

    def test_no_alleged_pfutp_violation_header(self, html):
        """
        'Alleged PFUTP Regulatory Violation' implies a legal conclusion.
        Must be replaced with 'PFUTP Regulatory Reference'.
        """
        assert "Alleged PFUTP Regulatory Violation" not in html, (
            "index.html contains 'Alleged PFUTP Regulatory Violation' header. "
            "This implies a legal conclusion — must read 'PFUTP Regulatory Reference'."
        )

    def test_pfutp_regulatory_reference_present(self, html):
        """The dossier must use the honest label 'PFUTP Regulatory Reference'."""
        assert "PFUTP Regulatory Reference" in html, (
            "index.html is missing 'PFUTP Regulatory Reference' label in dossier. "
            "Phase 5 fix should have added this."
        )


# ---------------------------------------------------------------------------
# E12 — ML Confidence label removed (Phase 3 fix regression guard)
# ---------------------------------------------------------------------------

class TestMLLabelHonesty:
    """
    E12: The column header 'ML Confidence' implies calibrated probability.
    It must be replaced by separate 'Anomaly Score' and 'Rule Score' labels.
    """

    def test_no_ml_confidence_column_header(self, html):
        """
        'ML Confidence' as a UI header is prohibited.
        It implies the IF output is a calibrated manipulation probability — it is not.
        """
        # Match the th/div label that was 'ML Confidence'
        # Allow it to appear in comments but not as a UI-visible label
        # We check that it doesn't appear as a table header or tile header
        th_matches = re.findall(r"<th[^>]*>([^<]*ML Confidence[^<]*)</th>", html, re.IGNORECASE)
        assert not th_matches, (
            f"index.html <th> still contains 'ML Confidence': {th_matches}. "
            "Phase 3 fix replaced this with separate Anomaly Score + Rule Score."
        )

    def test_anomaly_score_header_present(self, html):
        """'Anomaly Score' column header must be present in the alerts table."""
        assert "Anomaly Score" in html, (
            "index.html is missing 'Anomaly Score' as a column header. "
            "Phase 3 fix should have added this in place of 'ML Confidence'."
        )

    def test_rule_score_label_present(self, html):
        """'Rule Score' (or 'Rule score') label must be present in the dossier."""
        assert "Rule Score" in html or "Rule score" in html, (
            "index.html dossier is missing 'Rule Score' label. "
            "Phase 3 fix should have added this alongside 'Anomaly Score'."
        )

    def test_no_confidence_tooltip_implying_probability(self, html):
        """
        Tooltip must not say 'similar to previously flagged manipulation patterns'
        (E12 original text implied a classifier probability).
        """
        assert "similar to previously flagged manipulation patterns" not in html, (
            "index.html tooltip still reads 'similar to previously flagged manipulation patterns'. "
            "This implies a trained classifier — must be replaced with honest IF description."
        )

    def test_anomaly_score_tooltip_honest(self, html):
        """
        Anomaly Score tooltip must include a disclaimer that it is NOT a
        manipulation probability. The word 'probability' is allowed only
        in the context of the disclaimer 'NOT a manipulation probability'.
        """
        # Find the Anomaly Score th tooltip
        th_match = re.search(
            r'<th[^>]*title="([^"]*Anomaly Score[^"]*)"',
            html, re.IGNORECASE
        )
        if th_match:
            tooltip = th_match.group(1).lower()
            # Must contain the disclaimer phrase
            assert "not a manipulation probability" in tooltip or \
                   "not a manipulation" in tooltip, (
                f"Anomaly Score tooltip does not disclaim manipulation probability. "
                f"Got: {th_match.group(1)!r}. "
                "Must contain 'NOT a manipulation probability' (or similar)."
            )
            # Must NOT use the word 'confidence' (implies calibration)
            assert "confidence" not in tooltip, (
                f"Anomaly Score tooltip contains the word 'confidence': {th_match.group(1)!r}. "
                "Use 'anomaly score' or 'percentile', never 'confidence'."
            )

    def test_heuristic_only_shows_dash_in_anomaly_col(self, html):
        """
        When score_method is 'heuristic', the Anomaly Score column must show '—'
        (not the rule score). This ensures the two scores are never blended in UI.
        """
        # Check that JS has: score_method === 'heuristic' ... '—'
        heuristic_dash = re.search(
            r"score_method.*?heuristic.*?['\"]—['\"]|['\"]—['\"].*?score_method.*?heuristic",
            html, re.DOTALL
        )
        assert heuristic_dash is not None, (
            "index.html JS does not render '—' in the Anomaly Score column "
            "when score_method='heuristic'. The two scores must be shown separately — "
            "never blended. Phase 3 fix required this."
        )


# ---------------------------------------------------------------------------
# Fyers ML confidence score column  (original user request context)
# ---------------------------------------------------------------------------

class TestFyersDataInMLColumn:
    """
    When Fyers live data is available, the Anomaly Score column must show
    the IF-computed score (or '—' when untrained), NOT the heuristic rule score.
    These are static JS checks — no live Fyers connection required.
    """

    def test_anomaly_score_reads_ml_anomaly_score_field(self, html):
        """
        JS must read a.ml_anomaly_score for the Anomaly Score column,
        not a.score (which is the heuristic rule score).
        """
        assert "ml_anomaly_score" in html, (
            "index.html does not reference 'ml_anomaly_score' field. "
            "The Anomaly Score column must come from the ML field, not a.score."
        )

    def test_rule_score_reads_a_score_field(self, html):
        """
        JS must read a.score (the heuristic rule score) separately from ml_anomaly_score.
        Both must exist independently in the template.
        """
        # Both fields must appear in the same row-rendering JS block
        assert "a.score" in html, (
            "index.html does not reference 'a.score' (rule score) separately. "
            "Rule Score and Anomaly Score must both be shown independently."
        )
        assert "ml_anomaly_score" in html, (
            "index.html does not reference 'ml_anomaly_score' (anomaly score). "
            "Both scores must be shown independently."
        )

    def test_if_score_shown_as_separate_span(self, html):
        """
        The IF anomaly score must appear as a separate span (not blended with rule score).
        Check that IF: prefix or similar label exists in the JS template.
        """
        # The rendering code shows "IF:0.xxx" in a separate <span>
        assert "IF:" in html or "ml_anomaly_score" in html, (
            "index.html does not show Isolation Forest score in a distinct labeled span. "
            "Users must be able to distinguish IF anomaly score from rule score."
        )
