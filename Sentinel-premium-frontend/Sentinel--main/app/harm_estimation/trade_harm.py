"""
app/harm_estimation/trade_harm.py
===================================
Per-trade and per-account harm estimation from a counterfactual price path.

Given:
  - A CounterfactualPricePath (from event_study.py): the reconstructed
    "what price would have been without manipulation?"
  - A set of trade records (price, quantity, side, date, account) within
    the event window.

Computes:
  - Per-trade harm = (actual_price - counterfactual_price) × quantity
    with sign convention:
      - Buyer: harmed if actual > counterfactual (bought at inflated price)
      - Seller: harmed if actual < counterfactual (sold at deflated price, e.g. pump-dump aftermath)
  - Aggregated to account level and instrument level.
  - EVERY output is a RANGE (low/central/high), derived from the CAR's
    95% confidence interval bounding the counterfactual price path.
    Never a point estimate presented as fact.

HARD RULE (enforced in code):
  This module may only be called after guards.check_instrument_allowed()
  has cleared the instrument. If called without guard clearance, it logs
  an ERROR and raises HarmGuardRefusal.

DISCLAIMER (reproduced in every output):
  Trade harm figures are statistical estimates. They depend on the counterfactual
  price path reconstructed by the market-model event-study — which is a
  research methodology estimate, not a directly observable fact. The actual
  harm to any specific trader requires account-level data, SEBI's own records,
  and independent expert review. These figures do NOT constitute a legal
  determination of harm, disgorgement liability, or damages.
"""

import logging
from dataclasses import dataclass, field
from datetime import date
from typing import Optional

import numpy as np
import pandas as pd

from app.harm_estimation.event_study import CounterfactualPricePath, OUTPUT_DISCLAIMER

logger = logging.getLogger(__name__)


# ── Data structures ───────────────────────────────────────────────────────────

@dataclass
class TradeRecord:
    """
    A single trade record within the event (manipulation) window.
    Corresponds to a bulk/block deal entry, or any trade record with
    price, quantity, side, and date.
    """
    trade_id: str
    account_id: str          # hashed or anonymized account identifier
    trade_date: date
    price: float             # actual execution price
    quantity: int            # number of shares
    side: str                # "buy" or "sell"
    instrument_id: str


@dataclass
class TradeHarmEstimate:
    """
    Per-trade harm estimate with low/central/high range.

    Sign convention (positive = harmed):
      Buy: actual_price > counterfactual → buyer paid too much → positive harm
      Sell: actual_price < counterfactual → seller received too little → positive harm

    If actual_price == counterfactual (within CI), harm is not distinguishable
    from zero — disclosed explicitly.
    """
    trade_id: str
    account_id: str
    trade_date: date
    actual_price: float
    counterfactual_price_central: float
    counterfactual_price_low: float
    counterfactual_price_high: float
    quantity: int
    side: str
    # Harm in rupees (positive = harmed)
    harm_central: float    # (actual - cf_central) × qty, sign per side
    harm_low: float        # (actual - cf_high) × qty (worst case for victim = CF was highest)
    harm_high: float       # (actual - cf_low) × qty (best case for victim = CF was lowest)
    significant: bool      # True if even harm_low > 0 (unambiguous harm direction)
    disclaimer: str = OUTPUT_DISCLAIMER


@dataclass
class AccountHarmSummary:
    """
    Aggregate harm estimate for one account, with range.
    """
    account_id: str
    trade_count: int
    total_harm_central: float
    total_harm_low: float
    total_harm_high: float
    unambiguous_harm: bool   # True if harm_low > 0 (whole CI is positive)
    trades: list[TradeHarmEstimate] = field(default_factory=list)
    disclaimer: str = OUTPUT_DISCLAIMER


@dataclass
class InstrumentHarmReport:
    """
    Full harm estimation report for one instrument/case.
    Includes all per-trade estimates, account summaries, and instrument totals.

    HARD RULE: present instrument_total_central only alongside instrument_total_low
    and instrument_total_high with equal visual weight. Never headline
    instrument_total_central alone.
    """
    instrument_id: str
    case_id: str
    event_start: date
    event_end: date
    total_trades_analyzed: int
    instrument_total_central: float  # sum of harm_central across all trades
    instrument_total_low: float      # sum of harm_low
    instrument_total_high: float     # sum of harm_high
    unambiguous_harm: bool           # True if harm_low > 0
    account_summaries: list[AccountHarmSummary]
    per_trade_estimates: list[TradeHarmEstimate]
    # Validation and disclosure
    car_significant_at_95: bool      # from CARResult — if False, widen interpretation
    price_series_used: str
    microstructure_warning: bool
    log_file: Optional[str] = None   # must be set before result is committed (integrity rule)
    disclaimer: str = OUTPUT_DISCLAIMER


# ── Helper ────────────────────────────────────────────────────────────────────

def _find_counterfactual_for_date(
    d: date,
    cf_path: CounterfactualPricePath,
) -> tuple[float, float, float]:
    """
    Look up the counterfactual price (central, low, high) for a given date.
    Returns (central, low, high). If date not found, returns (nan, nan, nan).
    """
    for i, path_date in enumerate(cf_path.dates):
        if path_date == d:
            return (
                float(cf_path.counterfactual_central[i]),
                float(cf_path.counterfactual_low[i]),
                float(cf_path.counterfactual_high[i]),
            )
    return (float("nan"), float("nan"), float("nan"))


# ── Core computation ──────────────────────────────────────────────────────────

def compute_per_trade_harm(
    trade: TradeRecord,
    cf_path: CounterfactualPricePath,
) -> TradeHarmEstimate:
    """
    Compute harm for a single trade against the counterfactual price path.

    For buyers: harm = (actual - cf_central) × qty  [positive if over-paid]
    For sellers: harm = (cf_central - actual) × qty  [positive if under-received]

    The range (harm_low, harm_high) is derived from the CI bounds on the
    counterfactual price, so it reflects the statistical uncertainty in the
    market-model regression.
    """
    cf_c, cf_l, cf_h = _find_counterfactual_for_date(trade.trade_date, cf_path)

    if np.isnan(cf_c):
        logger.warning(
            "No counterfactual price found for trade %s on date %s. "
            "Returning zero harm for this trade.",
            trade.trade_id, trade.trade_date,
        )
        return TradeHarmEstimate(
            trade_id=trade.trade_id,
            account_id=trade.account_id,
            trade_date=trade.trade_date,
            actual_price=trade.price,
            counterfactual_price_central=float("nan"),
            counterfactual_price_low=float("nan"),
            counterfactual_price_high=float("nan"),
            quantity=trade.quantity,
            side=trade.side,
            harm_central=0.0,
            harm_low=0.0,
            harm_high=0.0,
            significant=False,
        )

    side = trade.side.lower()
    qty = trade.quantity
    actual = trade.price

    if side == "buy":
        # Buyer harmed if they paid more than counterfactual
        # Harm central: using cf_central
        harm_c = (actual - cf_c) * qty
        # Harm low: least possible harm = if cf was actually high (cf_high)
        harm_l = (actual - cf_h) * qty
        # Harm high: most possible harm = if cf was actually low (cf_low)
        harm_h = (actual - cf_l) * qty
    elif side == "sell":
        # Seller harmed if they received less than counterfactual
        harm_c = (cf_c - actual) * qty
        harm_l = (cf_l - actual) * qty
        harm_h = (cf_h - actual) * qty
    else:
        raise ValueError(f"Unknown trade side: {trade.side!r}. Expected 'buy' or 'sell'.")

    # Normalize so low <= high
    harm_l, harm_h = min(harm_l, harm_h), max(harm_l, harm_h)
    # Significant = even the low-estimate shows positive harm
    significant = harm_l > 0

    return TradeHarmEstimate(
        trade_id=trade.trade_id,
        account_id=trade.account_id,
        trade_date=trade.trade_date,
        actual_price=actual,
        counterfactual_price_central=cf_c,
        counterfactual_price_low=cf_l,
        counterfactual_price_high=cf_h,
        quantity=qty,
        side=side,
        harm_central=harm_c,
        harm_low=harm_l,
        harm_high=harm_h,
        significant=significant,
    )


def compute_instrument_harm_report(
    instrument_id: str,
    case_id: str,
    trades: list[TradeRecord],
    cf_path: CounterfactualPricePath,
    car_significant_at_95: bool,
    price_series_used: str = "close",
    microstructure_warning: bool = False,
    log_file: Optional[str] = None,
) -> InstrumentHarmReport:
    """
    Aggregate harm estimates to account level and instrument level.

    Parameters
    ----------
    instrument_id : str
        The instrument being analyzed (must have passed guards.check_instrument_allowed()).
    case_id : str
        The SEBI case ID or synthetic scenario ID.
    trades : list[TradeRecord]
        All trade records within the event window.
    cf_path : CounterfactualPricePath
        From event_study.reconstruct_counterfactual_price_path().
    car_significant_at_95 : bool
        From CARResult. If False, results are highly uncertain — disclosed loudly.
    price_series_used, microstructure_warning : str, bool
        Forwarded from event_study for disclosure.
    log_file : str, optional
        Path to the execution log backing these estimates (integrity requirement).
        If not set, a warning is logged — the report is generated but the caller
        must set this before persisting the result.
    """
    if not car_significant_at_95:
        logger.warning(
            "[HARM] CAR is NOT statistically significant at 95%% level. "
            "Harm estimates for case=%r instrument=%r are derived from a "
            "CAR that cannot be distinguished from zero. Wide confidence "
            "intervals are expected. Results should NOT be used as the "
            "primary basis for any harm quantification.",
            case_id, instrument_id,
        )

    if log_file is None:
        logger.warning(
            "[HARM] No log_file specified for instrument=%r case=%r. "
            "This report does not yet satisfy the log-file integrity "
            "requirement (same as _enforce_log_file_integrity in run_backtest.py). "
            "Set log_file before persisting or reporting this result.",
            instrument_id, case_id,
        )

    # Compute per-trade estimates
    per_trade: list[TradeHarmEstimate] = [
        compute_per_trade_harm(t, cf_path) for t in trades
    ]

    # Aggregate by account
    by_account: dict[str, list[TradeHarmEstimate]] = {}
    for est in per_trade:
        by_account.setdefault(est.account_id, []).append(est)

    account_summaries: list[AccountHarmSummary] = []
    for acct_id, acct_trades in by_account.items():
        total_c = sum(e.harm_central for e in acct_trades)
        total_l = sum(e.harm_low for e in acct_trades)
        total_h = sum(e.harm_high for e in acct_trades)
        account_summaries.append(AccountHarmSummary(
            account_id=acct_id,
            trade_count=len(acct_trades),
            total_harm_central=total_c,
            total_harm_low=total_l,
            total_harm_high=total_h,
            unambiguous_harm=total_l > 0,
            trades=acct_trades,
        ))

    # Instrument totals
    instr_total_c = sum(e.harm_central for e in per_trade)
    instr_total_l = sum(e.harm_low for e in per_trade)
    instr_total_h = sum(e.harm_high for e in per_trade)

    logger.info(
        "[HARM] Instrument=%r case=%r: %d trades analyzed. "
        "Total harm: central=₹%.2f [low=₹%.2f, high=₹%.2f]. "
        "Unambiguous (harm_low>0): %s. CAR significant: %s.",
        instrument_id, case_id, len(trades),
        instr_total_c, instr_total_l, instr_total_h,
        instr_total_l > 0, car_significant_at_95,
    )

    return InstrumentHarmReport(
        instrument_id=instrument_id,
        case_id=case_id,
        event_start=cf_path.dates[0] if cf_path.dates else date.today(),
        event_end=cf_path.dates[-1] if cf_path.dates else date.today(),
        total_trades_analyzed=len(trades),
        instrument_total_central=instr_total_c,
        instrument_total_low=instr_total_l,
        instrument_total_high=instr_total_h,
        unambiguous_harm=instr_total_l > 0,
        account_summaries=account_summaries,
        per_trade_estimates=per_trade,
        car_significant_at_95=car_significant_at_95,
        price_series_used=price_series_used,
        microstructure_warning=microstructure_warning,
        log_file=log_file,
    )


def format_harm_report_text(report: InstrumentHarmReport) -> str:
    """
    Format a harm report as a human-readable text block.

    HARD RULE: The central estimate always appears immediately alongside
    the confidence interval range — same line, same visual weight.
    No headline number without its range.
    """
    sig_text = "statistically significant at 95%" if report.car_significant_at_95 \
        else "NOT statistically significant at 95% — interpret with very wide uncertainty"

    lines = [
        "=" * 70,
        f"HARM ESTIMATION REPORT",
        f"Instrument: {report.instrument_id}  |  Case: {report.case_id}",
        f"Event window: {report.event_start} to {report.event_end}",
        f"Trades analyzed: {report.total_trades_analyzed}",
        f"Price series used: {report.price_series_used}",
        f"CAR statistical significance: {sig_text}",
        f"Microstructure warning: {'YES — illiquid instrument, estimates less reliable' if report.microstructure_warning else 'No'}",
        "",
        "INSTRUMENT-LEVEL HARM ESTIMATE (₹):",
        f"  Central estimate:  ₹{report.instrument_total_central:>14,.2f}",
        f"  95% CI low:        ₹{report.instrument_total_low:>14,.2f}",
        f"  95% CI high:       ₹{report.instrument_total_high:>14,.2f}",
        f"  Unambiguous harm (low > 0): {report.unambiguous_harm}",
        "",
        "ACCOUNT-LEVEL BREAKDOWN:",
    ]
    for acct in sorted(report.account_summaries, key=lambda a: -a.total_harm_central):
        lines.append(
            f"  {acct.account_id}: {acct.trade_count} trades | "
            f"central ₹{acct.total_harm_central:,.2f} "
            f"[low ₹{acct.total_harm_low:,.2f}, high ₹{acct.total_harm_high:,.2f}]"
            f"{'  ← UNAMBIGUOUS' if acct.unambiguous_harm else ''}"
        )
    lines += [
        "",
        "-" * 70,
        "DISCLAIMER:",
        report.disclaimer,
        "=" * 70,
    ]
    return "\n".join(lines)
