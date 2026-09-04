"""
Coordinated Pump Detector
=========================

Detects multiple accounts buying the same instrument in a synchronized
time window — the "pump" mechanic of a pump-and-dump scheme.

What is a coordinated pump?
-----------------------------
Multiple accounts (often new/dormant) place buy orders for the same
illiquid instrument within a short window. Combined, their buying pressure
pushes price up, creating the appearance of genuine demand. A separate
group (or the same accounts) then sells at the inflated price (the "dump").

This detector covers the BUY side (coordinated entry) — the "dump" side
typically shows up as abnormal sell volume after a price spike, which is
captured by volume anomaly detection (a future detector) or by looking for
sell-side clustering shortly after the pump window.

Algorithm (per the Phase 2 spec)
----------------------------------
For each instrument, bucket buy orders into rolling windows. Flag when:
  1. >= MIN_COORDINATING_ACCOUNTS distinct accounts placed buy orders
     within the window, AND
  2. Combined buy volume in that window is >= VOLUME_SPIKE_MULTIPLE times
     the instrument's normal volume for that window length, AND
  3. The accounts have no (or very recent) prior trading history in this
     instrument (new/dormant accounts suddenly active = higher suspicion).

Threshold documentation (HARD RULE #2)
-----------------------------------------
MIN_COORDINATING_ACCOUNTS = 3
  Source: SEBI enforcement orders on pump-and-dump typically name 3+ accounts
  acting in concert. A 2-account "pump" is often indistinguishable from
  normal institutional block-and-cross activity. 3 accounts is the minimum
  that suggests coordination rather than coincidence.
  Label: HEURISTIC — informed by SEBI case review, not formally validated.

VOLUME_SPIKE_MULTIPLE = 5.0
  The combined buy volume in the window must exceed 5x the instrument's
  normal volume for that same window length.
  Label: UNVALIDATED GUESS — needs backtesting against confirmed SEBI
  pump-and-dump enforcement orders.

DORMANT_DAYS_THRESHOLD = 30
  Accounts with no prior activity in this instrument for >= 30 days before
  the window are considered "dormant" for this instrument — sudden
  re-activation is a suspicion multiplier.
  Label: HEURISTIC — no formal citation.

NEW_ACCOUNT_WINDOW_DAYS = 7
  Accounts first seen within 7 days of the pump window are considered "new."
  Label: HEURISTIC.

Window length
  WINDOW_MINUTES = 30 for buy-side pump detection. Tighter than the 60-min
  circular trading window because pump coordination typically happens faster.
  Label: HEURISTIC.

False-positive risk
--------------------
Illiquid stocks with normally low volume will easily trip the volume spike
threshold on any unusual (but legitimate) day — e.g. a news catalyst causing
genuine retail buying. The detector explicitly:
  1. Discounts scores for illiquid instruments.
  2. Requires >= MIN_COORDINATING_ACCOUNTS distinct accounts (not just high volume).
  3. Weights dormant/new accounts more heavily (normal news buying tends to come
     from accounts with recent activity, not dormant ones).

This is documented, not solved. Analyst review is required for illiquid stocks.

Real-data status
-----------------
Operates on Order objects (the primary surveillance unit). Requires orders
with account_id and side (BUY/SELL) fields — available from both the broker
order stream and from synthetic order data. Prior trading history requires
a query window wider than the detection window (implemented via
`prior_trade_dates` parameter).
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional

from app.db.models import Order, OrderSide, OrderStatus, Instrument

logger = logging.getLogger(__name__)


# ── Thresholds ────────────────────────────────────────────────────────────────

# Minimum distinct accounts buying in same window for coordination signal.
# HEURISTIC — informed by SEBI case review (3+ accounts in typical orders).
MIN_COORDINATING_ACCOUNTS: int = 3

# Combined buy volume must exceed this multiple of instrument's normal volume.
# UNVALIDATED GUESS — needs backtesting vs SEBI pump-and-dump cases.
VOLUME_SPIKE_MULTIPLE: float = 5.0

# Accounts with no prior activity in the instrument for this many days
# are considered "dormant" — reactivation is a suspicion multiplier.
# HEURISTIC.
DORMANT_DAYS_THRESHOLD: int = 30

# Accounts first seen within this many days of the window are "new."
# HEURISTIC.
NEW_ACCOUNT_WINDOW_DAYS: int = 7

# Rolling window length for buy-side pump detection.
# HEURISTIC — tighter than circular trading window (pumps are faster).
WINDOW_MINUTES: int = 30

# Illiquid volume threshold (shares/day) — same as circular_trading.py.
ILLIQUID_VOLUME_THRESHOLD: float = 50_000.0

# Dormancy score weight: how much extra score weight new/dormant accounts add.
# 0.0 = dormancy ignored; 1.0 = max weight. HEURISTIC.
DORMANCY_WEIGHT: float = 0.20


# ── Result type ───────────────────────────────────────────────────────────────

@dataclass
class CoordinatedPumpSignal:
    """
    Represents a detected coordinated buy-side pump.

    `accounts_involved` lists every account that placed buy orders in the window.
    `dormant_accounts` is the subset with no prior activity in this instrument.
    `new_accounts` is the subset first seen within NEW_ACCOUNT_WINDOW_DAYS.
    """
    instrument_symbol: str
    exchange: str
    window_start: datetime
    window_end: datetime

    accounts_involved: list[str]
    dormant_accounts: list[str]    # subset with no recent prior activity
    new_accounts: list[str]        # subset first seen recently

    combined_buy_volume: int       # total buy quantity in this window
    volume_multiple: float         # combined_buy_volume / normal window volume
    num_accounts: int

    is_illiquid: bool
    score: float
    severity: str
    order_ids: list[str] = field(default_factory=list)
    explanation: str = ""
    false_positive_warning: str = ""


def _severity_from_score(score: float) -> str:
    if score >= 0.85:
        return "critical"
    if score >= 0.65:
        return "high"
    if score >= 0.45:
        return "medium"
    return "low"


# ── Core detector ─────────────────────────────────────────────────────────────

def detect_coordinated_pump(
    buy_orders: list[Order],
    instrument: Instrument,
    window_start: datetime,
    window_end: datetime,
    prior_trade_dates: dict[str, datetime] | None = None,
    first_seen_dates: dict[str, datetime] | None = None,
) -> Optional["CoordinatedPumpSignal"]:
    """
    Detects a coordinated pump within a single time window.

    Parameters
    ----------
    buy_orders
        All BUY-side orders (any status: placed, executed, even cancelled)
        within the window for one instrument. Includes both placed and
        executed orders — placed-and-cancelled buys are also a signal of
        attempted coordination even if the trade didn't complete.
    instrument
        The instrument being analysed (for liquidity normalisation).
    window_start, window_end
        Boundaries of the detection window.
    prior_trade_dates
        Optional dict mapping account_id → datetime of their last prior
        trade in this instrument (BEFORE window_start). Used to identify
        dormant accounts. If None, dormancy analysis is skipped.
    first_seen_dates
        Optional dict mapping account_id → datetime when the account was
        first seen in ANY instrument. Used to identify new accounts.
        If None, new-account analysis is skipped.

    Returns
    -------
    CoordinatedPumpSignal | None
        None if no pump detected. Signal if thresholds are met.

    HARD RULE #3: Returned signal includes `explanation` describing exactly
    what was detected and why each threshold fired.
    HARD RULE #1: Returns None (not synthetic data) if buy_orders is empty.
    """
    if not buy_orders:
        return None

    # De-duplicate to latest event per exchange_order_id (same as spoofing.py)
    latest: dict[str, Order] = {}
    for o in buy_orders:
        prev = latest.get(o.exchange_order_id)
        if prev is None or o.timestamp >= prev.timestamp:
            latest[o.exchange_order_id] = o
    resolved = list(latest.values())

    # Only count BUY-side orders
    buy_resolved = [
        o for o in resolved
        if (o.side == OrderSide.BUY or
            (hasattr(o.side, "value") and o.side.value == "buy") or
            str(o.side).lower() in ("buy", "ordersidebuy"))
    ]
    if not buy_resolved:
        return None

    distinct_accounts = list({o.account_id for o in buy_resolved})
    num_accounts = len(distinct_accounts)

    if num_accounts < MIN_COORDINATING_ACCOUNTS:
        return None  # not enough distinct accounts for coordination

    combined_buy_volume = sum(o.quantity for o in buy_resolved)

    # Normalise: compare combined window volume to instrument's normal volume
    # for a window of the same length (not the full day).
    window_hours = (window_end - window_start).total_seconds() / 3600
    normal_window_volume = (
        (instrument.avg_daily_volume_30d or 1.0) * (window_hours / 6.5)
    )  # 6.5 = NSE trading hours per day
    volume_multiple = combined_buy_volume / max(normal_window_volume, 1.0)

    if volume_multiple < VOLUME_SPIKE_MULTIPLE:
        return None  # volume spike is insufficient

    # Dormancy analysis
    dormant_accounts: list[str] = []
    new_accounts: list[str] = []
    now = window_start

    if prior_trade_dates is not None:
        dormant_threshold = now - timedelta(days=DORMANT_DAYS_THRESHOLD)
        for acct in distinct_accounts:
            last_trade = prior_trade_dates.get(acct)
            if last_trade is None or last_trade < dormant_threshold:
                dormant_accounts.append(acct)

    if first_seen_dates is not None:
        new_threshold = now - timedelta(days=NEW_ACCOUNT_WINDOW_DAYS)
        for acct in distinct_accounts:
            first_seen = first_seen_dates.get(acct)
            if first_seen is not None and first_seen >= new_threshold:
                new_accounts.append(acct)

    dormancy_fraction = len(dormant_accounts) / num_accounts if num_accounts > 0 else 0.0

    is_illiquid = (instrument.avg_daily_volume_30d or 0) < ILLIQUID_VOLUME_THRESHOLD

    # Composite score:
    # - Account count component: more accounts = stronger coordination signal
    # - Volume spike component: higher multiple = greater market impact
    # - Dormancy component: more dormant/new accounts = more suspicious
    #   (legit news-driven buying usually comes from active accounts)
    acct_score = min((num_accounts - MIN_COORDINATING_ACCOUNTS) / 7.0 + 0.3, 1.0)
    vol_score = min(volume_multiple / (VOLUME_SPIKE_MULTIPLE * 2), 1.0)
    dormancy_score = dormancy_fraction * DORMANCY_WEIGHT

    score = min(
        1.0,
        0.35 * acct_score
        + 0.45 * vol_score
        + dormancy_score
    )

    if is_illiquid:
        score *= 0.70  # 30% score discount for illiquid instruments

    order_ids = [o.id for o in buy_resolved]
    window_str = f"{window_start.strftime('%H:%M')}–{window_end.strftime('%H:%M')}"

    explanation = (
        f"Coordinated buy-side pump detected for {instrument.symbol} "
        f"({instrument.exchange}) in window {window_str}. "
        f"{num_accounts} distinct accounts placed buy orders with combined "
        f"volume of {combined_buy_volume:,} shares "
        f"({volume_multiple:.1f}x the instrument's normal volume for this window). "
        f"Threshold: >= {MIN_COORDINATING_ACCOUNTS} accounts and "
        f">= {VOLUME_SPIKE_MULTIPLE:.0f}x normal volume."
    )

    if dormant_accounts:
        explanation += (
            f" {len(dormant_accounts)} of {num_accounts} accounts were dormant "
            f"in this instrument for >= {DORMANT_DAYS_THRESHOLD} days before this "
            f"window ({', '.join(dormant_accounts[:3])}{'...' if len(dormant_accounts) > 3 else ''}) — "
            f"sudden reactivation of dormant accounts raises suspicion of coordinated entry."
        )

    if new_accounts:
        explanation += (
            f" {len(new_accounts)} accounts are newly active (first seen within "
            f"{NEW_ACCOUNT_WINDOW_DAYS} days)."
        )

    false_positive_warning = ""
    if is_illiquid:
        # avg_daily_volume_30d may be None for newly listed instruments;
        # guard against NoneType format error.
        vol_display = (
            f"{instrument.avg_daily_volume_30d:,.0f}"
            if instrument.avg_daily_volume_30d is not None
            else "<unknown>"
        )
        false_positive_warning = (
            f"WARNING: {instrument.symbol} is illiquid "
            f"({vol_display} shares/day avg). "
            f"Any unusual buy interest can trip volume thresholds in illiquid names "
            f"without implying manipulation. Score discounted 30%. Requires analyst review."
        )
        explanation += " " + false_positive_warning

    return CoordinatedPumpSignal(
        instrument_symbol=instrument.symbol,
        exchange=instrument.exchange,
        window_start=window_start,
        window_end=window_end,
        accounts_involved=distinct_accounts,
        dormant_accounts=dormant_accounts,
        new_accounts=new_accounts,
        combined_buy_volume=combined_buy_volume,
        volume_multiple=volume_multiple,
        num_accounts=num_accounts,
        is_illiquid=is_illiquid,
        score=score,
        severity=_severity_from_score(score),
        order_ids=order_ids,
        explanation=explanation,
        false_positive_warning=false_positive_warning,
    )


def run_coordinated_pump_detection(
    all_orders: list[Order],
    instrument: Instrument,
    window_minutes: int = WINDOW_MINUTES,
    prior_trade_dates: dict[str, datetime] | None = None,
    first_seen_dates: dict[str, datetime] | None = None,
) -> list[CoordinatedPumpSignal]:
    """
    Buckets orders into rolling windows and runs pump detection on each.

    `prior_trade_dates` and `first_seen_dates` should be pre-computed from
    historical order/trade data BEFORE the detection run to avoid the detector
    being seeded with in-window data (which would corrupt dormancy analysis).

    Returns all signals found across all windows.
    """
    if not all_orders:
        return []

    # Filter to buy-side only for this detector
    buy_orders = [
        o for o in all_orders
        if (o.side == OrderSide.BUY or
            (hasattr(o.side, "value") and o.side.value == "buy") or
            str(o.side).lower() in ("buy", "ordersidebuy"))
    ]

    if not buy_orders:
        return []

    buy_orders = sorted(buy_orders, key=lambda o: o.timestamp)
    start = buy_orders[0].timestamp
    end = buy_orders[-1].timestamp

    signals: list[CoordinatedPumpSignal] = []
    cursor = start

    while cursor <= end:
        window_end = cursor + timedelta(minutes=window_minutes)
        window_orders = [o for o in buy_orders if cursor <= o.timestamp < window_end]

        signal = detect_coordinated_pump(
            window_orders, instrument, cursor, window_end,
            prior_trade_dates=prior_trade_dates,
            first_seen_dates=first_seen_dates,
        )
        if signal:
            signals.append(signal)

        cursor = window_end

    logger.info(
        "Coordinated pump detection: %d signals found for %s",
        len(signals), instrument.symbol
    )
    return signals
