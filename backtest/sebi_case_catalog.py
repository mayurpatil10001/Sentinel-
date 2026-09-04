"""
SEBI Case Catalog — Phase 8 Backtest
=====================================

Static catalog of real SEBI enforcement cases used for backtesting.
Each entry is manually researched from SEBI's public enforcement orders.

HOW TO VERIFY: Every case below has a SEBI order reference. To verify:
  1. Go to https://www.sebi.gov.in/enforcement/orders.html
  2. Search by entity name or order reference in the search box
  3. The order PDF contains the investigation period, scrips, and
     alleged modus operandi.

DO NOT ADD CASES without a verifiable SEBI order reference.
"""

from dataclasses import dataclass, field
from datetime import date
from typing import Optional


@dataclass
class SEBICase:
    """
    One real, citable SEBI enforcement case.

    Fields
    ------
    case_id         Internal identifier for this backtest run.
    order_reference Exact SEBI order number or title as published.
    order_date      Date the SEBI order was passed.
    order_url       Direct URL to the SEBI order page (HTML wrapper;
                    the actual order is a PDF embedded in it). Verify this
                    yourself — SEBI sometimes reorganizes their URLs.
    entity_names    Name(s) of entity/entities named in the order.
    scrips          List of scrip/company names mentioned in the order.
    exchange        Primary exchange where manipulation alleged (NSE/BSE).
    investigation_start  Start of the investigation/manipulation period.
    investigation_end    End of the investigation/manipulation period.
    alleged_pattern      SEBI's characterization of the manipulation type.
    data_available_on_nse Whether NSE bhavcopy covers this scrip/period.
    summary         Paraphrase (not quote) of what SEBI alleged. No
                    reproduction of SEBI's exact text at length.
    testability_verdict  One of: TESTABLE, PARTIALLY_TESTABLE, UNTESTABLE.
    untestable_reason    If UNTESTABLE or PARTIALLY_TESTABLE, explain why.
    """
    case_id: str
    order_reference: str
    order_date: date
    order_url: str
    entity_names: list[str]
    scrips: list[str]
    exchange: str
    investigation_start: date
    investigation_end: date
    alleged_pattern: str
    data_available_on_nse: bool
    summary: str
    testability_verdict: str  # TESTABLE | PARTIALLY_TESTABLE | UNTESTABLE
    untestable_reason: str = ""
    # NSE symbol (upper-case), if known and verified on NSE
    nse_symbol: Optional[str] = None
    # BSE scrip code, if the scrip traded on BSE
    bse_code: Optional[str] = None
    # Which Sentinel detectors are even applicable
    applicable_detectors: list[str] = field(default_factory=list)
    # Any detectors that cannot be applied and why
    inapplicable_detectors: dict[str, str] = field(default_factory=dict)


# ─────────────────────────────────────────────────────────────────────────────
# Case Catalog
# ─────────────────────────────────────────────────────────────────────────────

CASES: list[SEBICase] = [

    # ── Case 1: Kavit Industries Limited ────────────────────────────────────
    SEBICase(
        case_id="KIL-2019",
        order_reference=(
            "Adjudication Order in the matter of Kavit Industries Limited, "
            "passed February 28, 2025 (SEBI Adjudicating Officer Order)"
        ),
        order_date=date(2025, 2, 28),
        order_url=(
            "https://www.sebi.gov.in/enforcement/orders.html"
            " — search 'Kavit Industries' under Adjudicating Officer Orders"
        ),
        entity_names=["Vijay Pujara", "Ajay Pujara", "Natvarbhai Vegda",
                      "20 trading accounts managed by above"],
        scrips=["Kavit Industries Limited"],
        exchange="BSE",
        investigation_start=date(2019, 8, 1),
        investigation_end=date(2019, 12, 23),
        alleged_pattern=(
            "Synchronized trades, circular trades, and reversal trades to "
            "create artificial volume and a misleading appearance of trading "
            "activity in the KIL scrip on BSE."
        ),
        data_available_on_nse=False,  # KIL traded on BSE, not NSE
        summary=(
            "SEBI found that a network of 20 trading accounts, managed by "
            "three individuals, engaged in a pattern of buying and selling "
            "KIL shares among themselves on BSE. The trades were "
            "synchronized in price and time, creating the appearance of "
            "market activity without genuine change in beneficial ownership. "
            "The scrip's price rose approximately 113% during the period "
            "(Rs. 44 → Rs. 93.80)."
        ),
        testability_verdict="PARTIALLY_TESTABLE",
        untestable_reason=(
            "Kavit Industries traded on BSE, not NSE. The Sentinel "
            "ingestion pipeline (nse_bhavcopy.py) fetches NSE bhavcopy only. "
            "A BSE bhavcopy fetch would be needed to retrieve OHLCV for this "
            "scrip. ADDITIONALLY: the detector (circular_trading.py, "
            "coordinated_pump.py) requires account-level trade data. Bhavcopy "
            "provides only daily OHLCV — account-level data for KIL is not "
            "publicly available. Only price/volume anomaly signals can be "
            "tested, via a daily OHLCV adapter."
        ),
        nse_symbol=None,   # not listed on NSE
        bse_code="KAVIT",  # approximate — verify on BSE website
        applicable_detectors=["price_volume_anomaly_adapter"],
        inapplicable_detectors={
            "coordinated_pump.py": (
                "Requires account-level buy orders. BSE does not publish "
                "historical order-level or account-level trade data publicly."
            ),
            "circular_trading.py": (
                "Requires account-level trade pairs. Not publicly available "
                "for BSE historical data."
            ),
            "spoofing.py": "Requires order lifecycle data. Not publicly available.",
            "basis_distortion.py": "KIL is not an F&O scrip.",
            "oi_manipulation.py": "KIL is not an F&O scrip.",
            "option_pinning.py": "KIL is not an F&O scrip.",
        },
    ),

    # ── Case 2: Mauria Udyog / 7NR Retail cluster ──────────────────────────
    SEBICase(
        case_id="PUMP-DUMP-2017-2020",
        order_reference=(
            "Ex Parte Ad Interim Order-cum-Show Cause Notice, June 19, 2023 "
            "(SEBI Whole Time Member Order); Final Order June 2026 — "
            "In the Matter of Manipulation in Scrips including Mauria Udyog "
            "Ltd., 7NR Retail Ltd., GBL Industries Ltd., Darjeeling Ropeway "
            "Co. Ltd., Vishal Fabrics Ltd."
        ),
        order_date=date(2023, 6, 19),   # interim order; final: June 2026
        order_url=(
            "https://www.sebi.gov.in/enforcement/orders.html "
            "— search 'Mauria Udyog' or 'Hanif Shekh' under Orders of "
            "Chairperson/Members"
        ),
        entity_names=[
            "Hanif Shekh (alleged mastermind)",
            "222 entities barred in final order",
        ],
        scrips=[
            "Mauria Udyog Ltd.",
            "7NR Retail Ltd.",
            "GBL Industries Ltd.",
            "Darjeeling Ropeway Company Ltd.",
            "Vishal Fabrics Ltd.",
        ],
        exchange="NSE/BSE",
        investigation_start=date(2017, 1, 1),
        investigation_end=date(2020, 12, 31),
        alleged_pattern=(
            "Multi-phase pump-and-dump: (1) a network of 200+ connected "
            "entities created artificial price and volume through synchronized "
            "and circular trades in illiquid small-cap scrips; (2) bulk SMS "
            "campaigns recommended these scrips as 'buy' to retail investors; "
            "(3) the coordinated network offloaded their holdings at inflated "
            "prices to retail buyers."
        ),
        data_available_on_nse=True,   # some scrips traded on NSE; verify per scrip
        summary=(
            "SEBI alleged a large-scale coordinated price manipulation scheme "
            "across multiple illiquid scrips. Connected entities inflated "
            "prices through coordinated buying, then used mass SMS campaigns "
            "to create genuine retail demand, then sold at the top. Hanif "
            "Shekh was identified as the coordinator. Final order barred 222 "
            "entities and ordered disgorgement of approximately Rs. 143.79 "
            "crore plus interest."
        ),
        testability_verdict="PARTIALLY_TESTABLE",
        untestable_reason=(
            "The scrips in this case are illiquid small-caps. Some may be "
            "delisted (Darjeeling Ropeway, GBL Industries). NSE bhavcopy "
            "will be attempted for each. The manipulation involved "
            "account-level circular trades that were BELOW the bulk deal "
            "disclosure threshold (by design) — so no account-level data "
            "exists in public archives. Only price/volume anomaly signals "
            "are testable via daily OHLCV adapter. The 3-year investigation "
            "period (2017-2020) means bhavcopy should be available but "
            "data quality for illiquid scrips on non-active days may be poor."
        ),
        nse_symbol=None,   # multiple scrips; see per-scrip attempt in runner
        applicable_detectors=["price_volume_anomaly_adapter"],
        inapplicable_detectors={
            "coordinated_pump.py": (
                "Requires account-level buy orders. Account-level data for "
                "small-cap scrip manipulation is not in public archives — "
                "specifically, the manipulation was designed to stay below "
                "bulk deal disclosure thresholds."
            ),
            "circular_trading.py": (
                "Same reason: account-level trade pairs not available."
            ),
            "spoofing.py": "Requires order lifecycle data. Not publicly available.",
            "basis_distortion.py": "None of these scrips are F&O instruments.",
            "oi_manipulation.py": "None of these scrips are F&O instruments.",
            "option_pinning.py": "None of these scrips are F&O instruments.",
        },
    ),

    # ── Case 3: Gravity India Limited ───────────────────────────────────────
    SEBICase(
        case_id="GIL-2003-2004",
        order_reference=(
            "Adjudication Order in the matter of Gravity India Limited, "
            "ORDER/SBM/KL/2021-22/15788, March 31, 2022 "
            "(SEBI Adjudicating Officer Kiran Lohia)"
        ),
        order_date=date(2022, 3, 31),
        order_url=(
            "https://www.sebi.gov.in/enforcement/orders.html "
            "— search 'Gravity India' under Adjudicating Officer Orders, "
            "order reference ORDER/SBM/KL/2021-22/15788"
        ),
        entity_names=["Sunil Kumar Purohit"],
        scrips=["Gravity India Limited"],
        exchange="BSE",
        investigation_start=date(2003, 12, 23),
        investigation_end=date(2004, 3, 3),
        alleged_pattern=(
            "Circular trading and reversal trades in the Gravity India scrip "
            "on BSE, accounting for approximately 71% of gross market volumes "
            "in the scrip during the period. Artificial volume creation without "
            "genuine change in beneficial ownership."
        ),
        data_available_on_nse=False,
        summary=(
            "SEBI investigated trading in Gravity India Limited on BSE between "
            "December 2003 and March 2004, following a report from BSE. A group "
            "of connected clients, trading through various members, generated "
            "trades among themselves that represented 71% of total gross volume "
            "in the scrip, artificially inflating apparent market activity."
        ),
        testability_verdict="UNTESTABLE",
        untestable_reason=(
            "Manipulation period is December 2003 – March 2004. NSE bhavcopy "
            "archives go back to approximately 2000-2001 but data quality for "
            "pre-2007 dates is unreliable — many dates return 404 or corrupt "
            "files. BSE bhavcopy for 2003-2004 is not in our pipeline at all. "
            "Additionally, this is a BSE case (not NSE), and even if bhavcopy "
            "were available, account-level circular trade data is not. "
            "VERDICT: UNTESTABLE by any method available in this pipeline."
        ),
        nse_symbol=None,
        applicable_detectors=[],
        inapplicable_detectors={
            "all_detectors": (
                "Manipulation period (Dec 2003 – Mar 2004) predates reliable "
                "public archive coverage. BSE bhavcopy for this period is not "
                "accessible via Sentinel's ingestion pipeline."
            ),
        },
    ),
]


# ─────────────────────────────────────────────────────────────────────────────
# Negative control configuration
# ─────────────────────────────────────────────────────────────────────────────

# Large, liquid, uncontroversial NSE-listed stocks for false-positive testing.
# Selection criteria: top-10 Nifty constituents by market cap; no enforcement
# actions known; continuous trading throughout the test period.
NEGATIVE_CONTROL_STOCKS = [
    "RELIANCE",    # Reliance Industries Ltd.
    "TCS",         # Tata Consultancy Services Ltd.
    "HDFCBANK",    # HDFC Bank Ltd.
    "INFY",        # Infosys Ltd.
    "ICICIBANK",   # ICICI Bank Ltd.
]

# 20 trading days spread across different months (2019-2021)
# to avoid seasonal bias. Chosen to avoid major market events
# (COVID crash, Nifty all-time highs) that could cause legitimate
# volume spikes that look like manipulation.
NEGATIVE_CONTROL_DATES = [
    # 2019: normal trading days
    date(2019, 3, 4),
    date(2019, 5, 14),
    date(2019, 7, 8),
    date(2019, 9, 3),
    date(2019, 11, 12),
    # 2020: post-March (after COVID shock normalised)
    date(2020, 6, 15),
    date(2020, 8, 10),
    date(2020, 10, 5),
    date(2020, 12, 7),
    date(2020, 12, 14),
    # 2021: normal trading days
    date(2021, 2, 15),
    date(2021, 4, 6),
    date(2021, 6, 7),
    date(2021, 8, 9),
    date(2021, 10, 4),
    date(2021, 11, 8),
    # 2022: post-pandemic normalcy
    date(2022, 1, 3),
    date(2022, 3, 14),
    date(2022, 6, 6),
    date(2022, 9, 5),
]
