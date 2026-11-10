"""
Spoofing / Layering detector.

Definition being detected:
  An account (or coordinated group of accounts) places large order(s)
  on one side of the book to move the price or create a false
  impression of demand/supply, then cancels before execution —
  optionally executing a smaller genuine order on the OPPOSITE side
  to profit from the price move it caused.

Why this is "early stage": it fires on the CANCELLED ORDER PATTERN,
before any (or before most) of the manipulative volume ever becomes a
trade. This is the core reason Sentinel is order-level, not trade-level.

Liquidity normalization: an account placing 5x its usual order size in
a penny stock is a much stronger signal than the same absolute
quantity in a liquid large-cap. We normalize against the instrument's
own avg_order_size_30d baseline rather than a fixed absolute threshold.
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional

from app.db.models import Order, OrderStatus, OrderSide, Instrument


@dataclass
class SpoofingSignal:
    account_id: str
    instrument_symbol: str
    exchange: str
    window_start: datetime
    window_end: datetime
    cancel_ratio: float          # fraction of order value cancelled vs placed
    size_multiple: float         # order size vs instrument's normal avg order size
    price_impact_pct: float      # price move while spoof orders were resting
    opposite_side_executed: bool # did they profit-trade the other side
    score: float                 # 0-1 composite
    severity: str
    order_ids: list = field(default_factory=list)
    explanation: str = ""


# Thresholds — deliberately conservative to keep precision high in v1.
# These should be tuned against real/backtested data (see README roadmap).
MIN_CANCEL_RATIO = 0.85          # >=85% of placed order value cancelled
MIN_SIZE_MULTIPLE = 3.0          # order size >= 3x instrument's normal average
MIN_PRICE_IMPACT_PCT = 0.5       # price moved >=0.5% while spoof order was resting
WINDOW_MINUTES = 15               # look at rolling windows of this length


def _severity_from_score(score: float) -> str:
    if score >= 0.85:
        return "critical"
    if score >= 0.65:
        return "high"
    if score >= 0.45:
        return "medium"
    return "low"


def detect_spoofing_for_account(
    orders: list[Order],
    instrument: Instrument,
    window_start: datetime,
    window_end: datetime,
) -> Optional[SpoofingSignal]:
    """
    orders: all order EVENTS for ONE account in ONE instrument within the
    window, already sorted by timestamp ascending. Each order lifecycle
    (placed -> modified -> cancelled/executed) may appear as multiple event
    rows sharing the same exchange_order_id — we resolve each order to its
    FINAL state before computing ratios, so a placed+cancelled pair isn't
    double-counted as two distinct orders.
    """
    if not orders:
        return None

    account_id = orders[0].account_id

    # Resolve each distinct order (by exchange_order_id) to its latest event.
    latest_by_order: dict[str, Order] = {}
    for o in orders:
        prev = latest_by_order.get(o.exchange_order_id)
        if prev is None or o.timestamp >= prev.timestamp:
            latest_by_order[o.exchange_order_id] = o
    resolved_orders = list(latest_by_order.values())

    placed_value = sum(o.price * o.quantity for o in resolved_orders)
    cancelled_value = sum(
        o.price * o.quantity
        for o in resolved_orders
        if o.status == OrderStatus.CANCELLED
    )
    if placed_value == 0:
        return None

    cancel_ratio = cancelled_value / placed_value

    baseline_size = instrument.avg_order_size_30d or 1.0
    max_order_qty = max(o.quantity for o in resolved_orders)
    size_multiple = max_order_qty / baseline_size if baseline_size > 0 else 0.0

    prices = [o.price for o in resolved_orders]
    price_impact_pct = (
        (max(prices) - min(prices)) / min(prices) * 100 if min(prices) > 0 else 0.0
    )

    # Did they execute meaningfully on the opposite side of the cancelled bulk?
    cancelled_sides = {
        o.side for o in resolved_orders if o.status == OrderStatus.CANCELLED
    }
    executed_orders = [
        o for o in resolved_orders if o.status == OrderStatus.EXECUTED
    ]
    opposite_side_executed = any(
        e.side != s for e in executed_orders for s in cancelled_sides
    )

    passes_threshold = (
        cancel_ratio >= MIN_CANCEL_RATIO
        and size_multiple >= MIN_SIZE_MULTIPLE
        and price_impact_pct >= MIN_PRICE_IMPACT_PCT
    )
    if not passes_threshold:
        return None

    # Composite score: weight cancel behavior heaviest, since that's the
    # defining feature; size and price impact confirm intent/effect;
    # profiting on the opposite side is an aggravating factor, not required.
    score = min(
        1.0,
        0.45 * min(cancel_ratio, 1.0)
        + 0.25 * min(size_multiple / 10.0, 1.0)
        + 0.20 * min(price_impact_pct / 5.0, 1.0)
        + (0.10 if opposite_side_executed else 0.0),
    )

    explanation = (
        f"Account {account_id} placed orders totaling {placed_value:,.0f} in value "
        f"for {instrument.symbol} ({instrument.exchange}), then cancelled "
        f"{cancel_ratio*100:.1f}% of that value. Peak order size was "
        f"{size_multiple:.1f}x the instrument's 30-day average order size. "
        f"Price moved {price_impact_pct:.2f}% while the order(s) were resting."
    )
    if opposite_side_executed:
        explanation += (
            " The account also executed trades on the opposite side of the "
            "cancelled orders, consistent with profiting from the price move "
            "it caused."
        )

    return SpoofingSignal(
        account_id=account_id,
        instrument_symbol=instrument.symbol,
        exchange=instrument.exchange,
        window_start=window_start,
        window_end=window_end,
        cancel_ratio=cancel_ratio,
        size_multiple=size_multiple,
        price_impact_pct=price_impact_pct,
        opposite_side_executed=opposite_side_executed,
        score=score,
        severity=_severity_from_score(score),
        order_ids=[o.id for o in orders],
        explanation=explanation,
    )


def run_spoofing_detection(
    all_orders: list[Order],
    instrument: Instrument,
    window_minutes: int = WINDOW_MINUTES,
) -> list[SpoofingSignal]:
    """
    Buckets orders into rolling windows per account and runs the
    detector on each bucket. Simple sliding window for v1 — a
    production version would use overlapping windows or a streaming
    approach (see roadmap).
    """
    if not all_orders:
        return []

    all_orders = sorted(all_orders, key=lambda o: o.timestamp)
    by_account: dict[str, list[Order]] = {}
    for o in all_orders:
        by_account.setdefault(o.account_id, []).append(o)

    signals = []
    for account_id, orders in by_account.items():
        start = orders[0].timestamp
        end = orders[-1].timestamp
        cursor = start
        while cursor <= end:
            window_end = cursor + timedelta(minutes=window_minutes)
            window_orders = [
                o for o in orders if cursor <= o.timestamp < window_end
            ]
            signal = detect_spoofing_for_account(
                window_orders, instrument, cursor, window_end
            )
            if signal:
                signals.append(signal)
            cursor = window_end

    return signals
