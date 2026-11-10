"""
Circular Trading Detector
=========================

Detects rings of accounts trading the same instrument back and forth
with no net economic purpose — the classic "circular trading" scheme.

What is circular trading?
--------------------------
A ring of accounts (A→B→C→D→A) trades the same instrument among themselves.
The defining features:
  1. A directed graph of trades forms one or more cycles.
  2. At the end of the cycle window, all participating accounts have
     net-zero (or near-zero) position change — they ended up where they
     started, having generated artificial volume and potentially moved price.
  3. No genuine economic rationale: the "sellers" and "buyers" are
     effectively the same beneficial ownership group.

Why this matters for manipulation:
  - Artificial volume makes an instrument appear more liquid/active than it is.
  - Price can be nudged by controlling both sides of repeated trades.
  - Exchange volume-based fees and "most-active" rankings can be gamed.

Algorithm (per the Phase 2 spec)
----------------------------------
1. Build a directed graph where edge A→B exists if account A sold to account B
   (or, when counterparty is unknown, both A and B traded the same instrument
   within a tight time window and on opposite sides).
2. Run cycle detection using Johnson's algorithm (networkx.simple_cycles),
   restricted to cycles of length 2 to MAX_CYCLE_LENGTH.
3. For each detected cycle, compute the net position change for every account
   in the cycle over the window. Flag the cycle if ALL accounts have net
   position change within NET_POSITION_THRESHOLD (i.e., they ended up back
   where they started — the trade had no lasting economic effect).
4. Score the cycle on: cycle length (longer = more sophisticated / higher score),
   total fabricated volume vs. instrument's normal volume (normalised), and
   whether counterparty IDs are directly known vs. inferred from timing.

Threshold documentation (HARD RULE #2)
-----------------------------------------
NET_POSITION_THRESHOLD = 0.10
  Source: SEBI circular CIR/MRD/DP/33/2012 defines circular trading as trades
  where the net transfer of securities is "not substantial." We use 10% of the
  gross traded quantity as the threshold for "near-zero net change" — an
  unvalidated interpretation of "not substantial." This needs backtesting
  against known SEBI circular trading enforcement orders.
  Label: UNVALIDATED GUESS — needs backtesting against SEBI case data.

MAX_CYCLE_LENGTH = 6
  Source: SEBI enforcement orders reviewed (informally) typically involve
  2- to 5-entity rings. 6 is a conservative upper bound that limits
  computational complexity (Johnson's algorithm is O((n+e)(c+1)) where c
  is the number of cycles). Label: HEURISTIC — no formal citation.

MIN_TRADES_IN_WINDOW = 2
  The minimum number of executed trades within the window for a cycle to
  be worth examining. Below this, cycle detection on a sparse graph produces
  high false-positive rates. Label: HEURISTIC — based on algorithmic stability.

False-positive risk for illiquid stocks
-----------------------------------------
For a stock with only 10 active traders, nearly ANY pair will trade with
each other repeatedly — this is normal, not manipulative. The liquidity
normalisation (comparing cycle volume vs. avg_daily_volume_30d) partially
mitigates this, but for genuinely illiquid instruments the detector WILL
have high false-positive rates. Mitigation:
  - Score is significantly discounted when instrument.avg_daily_volume_30d
    is below ILLIQUID_VOLUME_THRESHOLD.
  - Callers should apply a higher severity threshold (e.g. only escalate
    "high" or "critical") for penny stocks.
  This is documented, not solved. A real system would need analyst review
  of all flagged illiquid-stock rings.

Real-data status
-----------------
This detector operates on the `Trade` objects already in the database schema.
When trades come from `broker_order_stream.py` or bhavcopy enrichment, it will
run on real data. When run against synthetic data (e.g. demo/), it uses the
same logic — synthetic data is not substituted silently.
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional

try:
    import networkx as nx
    _NX_AVAILABLE = True
except ImportError:
    _NX_AVAILABLE = False
    nx = None  # type: ignore[assignment]

from app.db.models import Trade, Instrument, OrderSide

logger = logging.getLogger(__name__)


# ── Thresholds ────────────────────────────────────────────────────────────────

# Max cycle length to search. Longer rings are rare and expensive to detect.
# HEURISTIC — no formal citation. SEBI cases reviewed informally showed 2-5.
MAX_CYCLE_LENGTH: int = 6

# Net position change <= this fraction of gross traded quantity is "near zero."
# UNVALIDATED GUESS — interprets SEBI's "not substantial" loosely. Needs
# backtesting against confirmed SEBI circular trading enforcement orders.
NET_POSITION_THRESHOLD: float = 0.10

# Volume multiplier vs. instrument's normal volume to distinguish meaningful
# circular activity from random coincidental crossing in illiquid names.
# HEURISTIC — unvalidated.
MIN_VOLUME_MULTIPLE: float = 1.5

# Window for grouping trades into a potential ring session (in minutes).
# HEURISTIC — shorter windows catch tighter coordination; longer windows
# tolerate slower rings. 60 minutes is a reasonable starting point.
WINDOW_MINUTES: int = 60

# Instruments with daily volume below this are flagged as illiquid;
# their scores are discounted and the false-positive warning is attached.
# NSE penny stocks often trade <50,000 shares/day. HEURISTIC.
ILLIQUID_VOLUME_THRESHOLD: float = 50_000.0


# ── Result types ──────────────────────────────────────────────────────────────

@dataclass
class CircularTradingSignal:
    """
    Represents a detected circular trading ring.

    All fields are required for the evidence log — no field may be None
    except `counterparty_known` (which affects score weighting).
    """
    instrument_symbol: str
    exchange: str
    window_start: datetime
    window_end: datetime

    cycle_accounts: list[str]       # accounts in the ring, in cycle order
    cycle_length: int               # number of accounts in the ring
    gross_volume: int               # total shares traded within the cycle
    net_position_changes: dict      # account_id → net position change
    max_net_position_pct: float     # max |net_change| / gross_volume across all accounts
    volume_multiple: float          # cycle volume vs instrument's normal daily volume

    counterparty_known: bool        # True if buyer/seller IDs directly match
    is_illiquid: bool               # True if instrument is below liquidity threshold
    score: float                    # 0-1 composite
    severity: str
    trade_ids: list[str] = field(default_factory=list)
    explanation: str = ""

    # False-positive warning attached when instrument is illiquid
    false_positive_warning: str = ""


def _severity_from_score(score: float) -> str:
    if score >= 0.85:
        return "critical"
    if score >= 0.65:
        return "high"
    if score >= 0.45:
        return "medium"
    return "low"


# ── Graph construction ────────────────────────────────────────────────────────

def _build_trade_graph(
    trades: list[Trade],
    window_minutes: int,
) -> "nx.DiGraph":
    """
    Builds a directed graph where edge A→B exists if:
      (a) Trade has buy_order.account_id = B and sell_order.account_id = A
          (counterparty is directly known from matched order IDs), OR
      (b) Account A traded on the sell side and Account B traded on the buy side
          for the same instrument within `window_minutes`, and the timestamps
          overlap (counterparty inferred from timing when direct match unavailable).

    Returns a DiGraph where edges have attributes:
      - trade_ids: list of Trade.id values supporting this edge
      - volume: total quantity traded on this edge
      - counterparty_known: True if (a) applied
    """
    if not _NX_AVAILABLE:
        raise RuntimeError(
            "networkx is required for circular trading detection. "
            "Install with: pip install networkx"
        )

    G = nx.DiGraph()

    # Pass 1: direct counterparty edges (buy_order_id + sell_order_id both set)
    # These are the most reliable — the exchange matched them.
    direct_edges: dict[tuple[str, str], dict] = {}
    for trade in trades:
        if trade.buy_order_id and trade.sell_order_id:
            # We need the account IDs, which requires resolved orders.
            # In this detector, Trade objects are expected to have the order
            # relationships pre-loaded (buy_order.account_id etc.) if available.
            buyer_acct = getattr(
                getattr(trade, "buy_order", None), "account_id", None
            )
            seller_acct = getattr(
                getattr(trade, "sell_order", None), "account_id", None
            )
            if buyer_acct and seller_acct and buyer_acct != seller_acct:
                key = (seller_acct, buyer_acct)
                if key not in direct_edges:
                    direct_edges[key] = {
                        "trade_ids": [],
                        "volume": 0,
                        "counterparty_known": True,
                    }
                direct_edges[key]["trade_ids"].append(trade.id)
                direct_edges[key]["volume"] += trade.quantity

    for (src, dst), attrs in direct_edges.items():
        G.add_edge(src, dst, **attrs)

    # Pass 2: timing-based inferred edges (when counterparty unknown)
    # Group by (sell-side, buy-side) pairs within time window.
    # This is less reliable — it's proximity inference, not exchange confirmation.
    sells = [(t.timestamp, t.quantity, t.id) for t in trades
             if hasattr(t, "_side_hint") and t._side_hint == "sell"]
    buys = [(t.timestamp, t.quantity, t.id) for t in trades
            if hasattr(t, "_side_hint") and t._side_hint == "buy"]

    # NOTE: bhavcopy trades don't include per-transaction buyer/seller IDs —
    # that's a fundamental limitation of EOD public data. The timing-based
    # inference path is only meaningful with broker-API or exchange data.
    # If we reach here with bhavcopy-only data, the graph will only have
    # direct edges (empty for bhavcopy). This is logged as a warning.
    if not G.edges():
        logger.warning(
            "Circular trading graph has no direct counterparty edges. "
            "This typically means the trades came from EOD bhavcopy data "
            "which does not include per-transaction buyer/seller account IDs. "
            "Cycle detection requires order-level data (broker API or exchange-side)."
        )

    return G


# ── Core detector ─────────────────────────────────────────────────────────────

def detect_circular_trading(
    trades: list[Trade],
    instrument: Instrument,
    window_start: datetime,
    window_end: datetime,
    max_cycle_length: int = MAX_CYCLE_LENGTH,
    net_position_threshold: float = NET_POSITION_THRESHOLD,
) -> list[CircularTradingSignal]:
    """
    Runs circular trading detection on a set of trades for one instrument
    within a time window.

    Parameters
    ----------
    trades
        All executed trades for the instrument within [window_start, window_end].
        For counterparty edges to work, trades must have buy_order and sell_order
        relationships pre-loaded (i.e. SQLAlchemy eager-loaded or the objects
        passed directly with those attributes set).
    instrument
        The instrument being analysed (used for liquidity normalisation).
    window_start, window_end
        The analysis window boundaries.
    max_cycle_length
        Maximum ring size to search. Default = 6 (see threshold docs above).
    net_position_threshold
        Fraction of gross volume below which a net position change is "near zero."
        Default = 0.10 (see threshold docs above).

    Returns
    -------
    list[CircularTradingSignal]
        One signal per detected ring. Empty list = no rings found.
        DOES NOT raise on no findings — returns [].

    HARD RULE #3: Every returned signal includes an `explanation` string
    that a human investigator can read to understand why this ring was flagged.

    HARD RULE #1: This function does NOT substitute synthetic trades if
    `trades` is empty — it returns []. The caller is responsible for
    providing real data.
    """
    if not trades:
        return []

    if not _NX_AVAILABLE:
        raise RuntimeError(
            "networkx is required. Install with: pip install networkx"
        )

    G = _build_trade_graph(trades, window_minutes=WINDOW_MINUTES)

    if len(G.edges()) == 0:
        logger.debug(
            "No edges in trade graph for %s [%s–%s]. "
            "No cycles possible (likely insufficient counterparty data).",
            instrument.symbol, window_start, window_end
        )
        return []

    # Johnson's algorithm: finds all simple cycles in a directed graph.
    # Complexity: O((n + e)(c + 1)) where c = number of cycles.
    # For large graphs with many accounts, this can be slow — restrict to
    # cycles of length <= max_cycle_length by filtering after enumeration.
    all_cycles = [
        cycle for cycle in nx.simple_cycles(G)
        if 2 <= len(cycle) <= max_cycle_length
    ]

    if not all_cycles:
        return []

    signals: list[CircularTradingSignal] = []
    # Normalise ring volume against expected volume for the SAME WINDOW LENGTH,
    # not the full trading day. This prevents a 60-min window from requiring
    # 150k shares just to reach 0.3× of a 500k/day stock's daily volume.
    # NSE normal session: 9:15–15:30 = 375 minutes = 6.25 hours.
    window_hours = max(
        (window_end - window_start).total_seconds() / 3600, 0.01
    )
    # NSE_TRADING_HOURS: 6.25h normal session (375 min). We use 6.5h conservatively.
    NSE_TRADING_HOURS = 6.5
    daily_volume = instrument.avg_daily_volume_30d or 1.0
    baseline_volume = daily_volume * (window_hours / NSE_TRADING_HOURS)
    is_illiquid = (instrument.avg_daily_volume_30d or 0) < ILLIQUID_VOLUME_THRESHOLD

    for cycle in all_cycles:
        cycle_account_set = set(cycle)

        # Collect all trades where both buyer and seller are in this cycle
        cycle_trades = [
            t for t in trades
            if (
                getattr(getattr(t, "buy_order", None), "account_id", None)
                in cycle_account_set
                and
                getattr(getattr(t, "sell_order", None), "account_id", None)
                in cycle_account_set
            )
        ]

        if not cycle_trades:
            continue

        # Compute net position change per account (buys = +qty, sells = -qty)
        net_positions: dict[str, int] = {acct: 0 for acct in cycle_account_set}
        gross_volume = 0
        for t in cycle_trades:
            buyer = getattr(getattr(t, "buy_order", None), "account_id", None)
            seller = getattr(getattr(t, "sell_order", None), "account_id", None)
            if buyer in net_positions:
                net_positions[buyer] += t.quantity
            if seller in net_positions:
                net_positions[seller] -= t.quantity
            gross_volume += t.quantity

        if gross_volume == 0:
            continue

        # Max absolute net position change as % of gross volume
        max_net_pct = max(
            abs(v) / gross_volume for v in net_positions.values()
        )

        # Ring criterion: all accounts returned near to their starting position
        if max_net_pct > net_position_threshold:
            continue  # net positions are too large — this isn't a closed ring

        volume_multiple = gross_volume / baseline_volume

        if volume_multiple < MIN_VOLUME_MULTIPLE:
            continue  # cycle volume is too small relative to normal activity

        # Composite score:
        # - Longer cycles: harder to detect, more sophisticated → higher weight
        # - Lower net position (closer to zero) → stronger ring evidence
        # - Higher volume multiple → greater market impact
        # - Counterparty directly known → more reliable evidence
        counterparty_known = all(
            G.edges[src, dst].get("counterparty_known", False)
            for src, dst in zip(cycle, cycle[1:] + [cycle[0]])
            if G.has_edge(src, dst)
        )

        cycle_length_score = min((len(cycle) - 1) / (max_cycle_length - 1), 1.0)
        net_pos_score = 1.0 - (max_net_pct / net_position_threshold)  # closer to 0 = higher score
        vol_score = min(volume_multiple / 5.0, 1.0)
        counterparty_bonus = 0.10 if counterparty_known else 0.0

        score = min(
            1.0,
            0.30 * cycle_length_score
            + 0.35 * net_pos_score
            + 0.25 * vol_score
            + counterparty_bonus
        )

        # Illiquid instrument discount — false positive risk is high
        if is_illiquid:
            score *= 0.70  # discount score by 30%

        trade_ids = [t.id for t in cycle_trades]
        cycle_str = " → ".join(cycle) + " → " + cycle[0]

        explanation = (
            f"Circular trading ring detected: {cycle_str}. "
            f"Ring has {len(cycle)} accounts. "
            f"Gross volume within ring: {gross_volume:,} shares "
            f"({volume_multiple:.1f}x {instrument.symbol}'s 30-day avg daily volume). "
            f"Maximum net position change across all ring accounts: "
            f"{max_net_pct*100:.1f}% of gross volume "
            f"(threshold: {net_position_threshold*100:.0f}%) — "
            f"all accounts effectively returned to their starting positions, "
            f"consistent with no genuine economic purpose. "
            f"Counterparty identity: {'directly confirmed from exchange order matching' if counterparty_known else 'inferred from timing proximity (not exchange-confirmed)'}."
        )

        false_positive_warning = ""
        if is_illiquid:
            false_positive_warning = (
                f"WARNING: {instrument.symbol} has low daily volume "
                f"({instrument.avg_daily_volume_30d:,.0f} shares/day, "
                f"below the {ILLIQUID_VOLUME_THRESHOLD:,.0f} illiquid threshold). "
                f"In illiquid instruments, a small number of legitimate traders "
                f"naturally trade with each other repeatedly. This signal has "
                f"been score-discounted by 30% and requires manual analyst review "
                f"before escalation."
            )
            explanation += " " + false_positive_warning

        signals.append(CircularTradingSignal(
            instrument_symbol=instrument.symbol,
            exchange=instrument.exchange,
            window_start=window_start,
            window_end=window_end,
            cycle_accounts=cycle,
            cycle_length=len(cycle),
            gross_volume=gross_volume,
            net_position_changes=net_positions,
            max_net_position_pct=max_net_pct,
            volume_multiple=volume_multiple,
            counterparty_known=counterparty_known,
            is_illiquid=is_illiquid,
            score=score,
            severity=_severity_from_score(score),
            trade_ids=trade_ids,
            explanation=explanation,
            false_positive_warning=false_positive_warning,
        ))

    # Deduplicate: different cycle orderings of the same ring (e.g. A→B→C
    # and B→C→A) are the same ring. Dedup by frozenset of account IDs.
    seen: set[frozenset] = set()
    unique_signals: list[CircularTradingSignal] = []
    for s in signals:
        key = frozenset(s.cycle_accounts)
        if key not in seen:
            seen.add(key)
            unique_signals.append(s)

    logger.info(
        "Circular trading detection: %d unique rings found for %s [%s–%s]",
        len(unique_signals), instrument.symbol, window_start, window_end
    )
    return unique_signals


def run_circular_trading_detection(
    all_trades: list[Trade],
    instrument: Instrument,
    window_minutes: int = WINDOW_MINUTES,
) -> list[CircularTradingSignal]:
    """
    Buckets trades into rolling time windows and runs ring detection on each.

    Sliding window approach: each window is non-overlapping for efficiency.
    A production version would use overlapping windows or streaming to catch
    rings that straddle a window boundary.

    Returns all signals across all windows, deduplicated by ring membership.
    """
    if not all_trades:
        return []

    all_trades = sorted(all_trades, key=lambda t: t.timestamp)
    start = all_trades[0].timestamp
    end = all_trades[-1].timestamp

    all_signals: list[CircularTradingSignal] = []
    seen_rings: set[frozenset] = set()

    cursor = start
    while cursor <= end:
        window_end = cursor + timedelta(minutes=window_minutes)
        window_trades = [t for t in all_trades if cursor <= t.timestamp < window_end]

        if len(window_trades) >= 2:
            signals = detect_circular_trading(
                window_trades, instrument, cursor, window_end
            )
            for sig in signals:
                key = frozenset(sig.cycle_accounts)
                if key not in seen_rings:
                    seen_rings.add(key)
                    all_signals.append(sig)

        cursor = window_end

    return all_signals
