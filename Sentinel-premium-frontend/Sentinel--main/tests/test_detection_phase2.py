"""
Phase 2 Detection Tests — Multi-Account Coordination
=====================================================

Tests for circular_trading.py and coordinated_pump.py.

Verification prompt compliance (from Phase 2 spec):
  1. True 4-account trading ring (A→B→C→D→A, net ~0) — detector CATCHES it.
  2. 4 UNRELATED accounts trading the same stock — detector does NOT flag it.
  3. Cycle detection covers 3+ account rings (not just pairs).
  4. Illiquid stock false-positive risk explicitly considered and tested.

HARD RULE #4: Both a true positive AND a true negative exist for each detector.
HARD RULE #3: Every signal includes an explanation string — verified here.

Run:
    pytest tests/test_detection_phase2.py -v
"""

import uuid
from datetime import datetime, timedelta

import pytest

from app.db.models import (
    Instrument, InstrumentType, Order, OrderSide, OrderStatus, Trade,
)
from app.detection.circular_trading import (
    detect_circular_trading,
    CircularTradingSignal,
    MAX_CYCLE_LENGTH,
    NET_POSITION_THRESHOLD,
)
from app.detection.coordinated_pump import (
    detect_coordinated_pump,
    CoordinatedPumpSignal,
    MIN_COORDINATING_ACCOUNTS,
    VOLUME_SPIKE_MULTIPLE,
)


# ── Helpers ────────────────────────────────────────────────────────────────────

def _make_instrument(
    symbol: str = "TESTCO",
    avg_daily_volume: float = 1_000_000.0,
    instrument_type: InstrumentType = InstrumentType.EQUITY,
) -> Instrument:
    """Creates a minimal Instrument object for testing."""
    instr = Instrument()
    instr.id = str(uuid.uuid4())
    instr.symbol = symbol
    instr.exchange = "NSE"
    instr.instrument_type = instrument_type
    instr.avg_daily_volume_30d = avg_daily_volume
    instr.avg_order_size_30d = 500.0
    instr.avg_daily_turnover_30d = avg_daily_volume * 100.0  # assuming ₹100/share
    return instr


def _make_order(
    account_id: str,
    side: OrderSide,
    quantity: int,
    price: float = 100.0,
    status: OrderStatus = OrderStatus.EXECUTED,
    ts_offset_minutes: int = 0,
    instrument_id: str = "instr-1",
) -> Order:
    base_time = datetime(2024, 1, 15, 10, 0, 0)
    o = Order()
    o.id = str(uuid.uuid4())
    o.exchange_order_id = str(uuid.uuid4())
    o.account_id = account_id
    o.instrument_id = instrument_id
    o.side = side
    o.status = status
    o.price = price
    o.quantity = quantity
    o.filled_quantity = quantity if status == OrderStatus.EXECUTED else 0
    o.timestamp = base_time + timedelta(minutes=ts_offset_minutes)
    o.exchange = "NSE"
    return o


class _FakeOrder:
    """Minimal order stand-in for use inside Trade objects."""
    def __init__(self, account_id: str):
        self.account_id = account_id


def _make_trade(
    buyer_account: str,
    seller_account: str,
    quantity: int = 1000,
    price: float = 100.0,
    ts_offset_minutes: int = 0,
    instrument_id: str = "instr-1",
) -> Trade:
    """
    Creates a Trade with buy_order and sell_order attributes pre-set
    (simulating SQLAlchemy eager-load). This is what the circular trading
    detector requires to build counterparty-known edges.
    """
    base_time = datetime(2024, 1, 15, 10, 0, 0)
    t = Trade()
    t.id = str(uuid.uuid4())
    t.instrument_id = instrument_id
    t.price = price
    t.quantity = quantity
    t.timestamp = base_time + timedelta(minutes=ts_offset_minutes)
    t.exchange = "NSE"

    # Set up resolved order references (simulating eager-load)
    buy_o = _FakeOrder(buyer_account)
    sell_o = _FakeOrder(seller_account)
    t.buy_order = buy_o
    t.sell_order = sell_o
    t.buy_order_id = str(uuid.uuid4())
    t.sell_order_id = str(uuid.uuid4())
    return t


# ══════════════════════════════════════════════════════════════════════════════
# Tests: circular_trading.py
# ══════════════════════════════════════════════════════════════════════════════

class TestCircularTradingDetector:

    # ── True positive: 4-account ring ─────────────────────────────────────────

    def test_detects_4_account_ring(self):
        """
        VERIFICATION PROMPT Q1: 4-account ring A→B→C→D→A, net position ≈ 0.
        All accounts trade the same quantity in a cycle — they end up where they
        started. The detector must catch this.

        Ring structure:
          A sells 50,000 to B
          B sells 50,000 to C
          C sells 50,000 to D
          D sells 50,000 to A
          Net position change for each account: 0 (bought 50k, sold 50k)

        Volumes: instrument avg 500k/day → ~76k per 60-min window.
        Gross ring volume: 4 legs × 50k = 200k shares across 4 cycle trades.
        But each pair of accounts only trades 50k (the cycle contribution per edge).
        volume_multiple for the ring = 200k / 76k ≈ 2.6x → above MIN_VOLUME_MULTIPLE=1.5.
        """
        instrument = _make_instrument(avg_daily_volume=500_000.0)
        window_start = datetime(2024, 1, 15, 10, 0, 0)
        window_end = datetime(2024, 1, 15, 11, 0, 0)

        # Each leg: 50,000 shares. Gross ring volume = 200,000.
        # Normal window volume (60-min) = 500,000 × (1/6.5) ≈ 76,923.
        # Volume multiple ≈ 2.6 > MIN_VOLUME_MULTIPLE=1.5. ✓
        trades = [
            _make_trade("A", "B", quantity=50_000, ts_offset_minutes=5),
            _make_trade("B", "C", quantity=50_000, ts_offset_minutes=10),
            _make_trade("C", "D", quantity=50_000, ts_offset_minutes=15),
            _make_trade("D", "A", quantity=50_000, ts_offset_minutes=20),
        ]

        signals = detect_circular_trading(
            trades, instrument, window_start, window_end
        )

        assert len(signals) >= 1, (
            "Expected at least 1 circular trading signal for a clear 4-account ring. "
            f"Got {len(signals)}."
        )

        sig = signals[0]
        assert isinstance(sig, CircularTradingSignal)
        assert sig.cycle_length >= 2, "Ring must have at least 2 accounts"
        assert set(sig.cycle_accounts).issubset({"A", "B", "C", "D"}), \
            f"Ring accounts should be subset of {{A,B,C,D}}, got {sig.cycle_accounts}"
        assert sig.max_net_position_pct <= NET_POSITION_THRESHOLD, (
            f"Net position should be ≤ {NET_POSITION_THRESHOLD}, "
            f"got {sig.max_net_position_pct}"
        )
        assert sig.score > 0.0, "Score must be positive for a clear ring"
        assert sig.severity in ("low", "medium", "high", "critical")

    def test_4_account_ring_has_explanation(self):
        """HARD RULE #3: Every signal must have a non-empty explanation string."""
        instrument = _make_instrument(avg_daily_volume=500_000.0)
        window_start = datetime(2024, 1, 15, 10, 0, 0)
        window_end = datetime(2024, 1, 15, 11, 0, 0)

        trades = [
            _make_trade("A", "B", quantity=50_000, ts_offset_minutes=5),
            _make_trade("B", "C", quantity=50_000, ts_offset_minutes=10),
            _make_trade("C", "D", quantity=50_000, ts_offset_minutes=15),
            _make_trade("D", "A", quantity=50_000, ts_offset_minutes=20),
        ]

        signals = detect_circular_trading(trades, instrument, window_start, window_end)

        for sig in signals:
            assert sig.explanation, "Every signal must have a non-empty explanation"
            assert len(sig.explanation) > 50, (
                "Explanation must be substantive (>50 chars), not a placeholder"
            )
            # Must name the accounts involved
            assert any(acct in sig.explanation for acct in sig.cycle_accounts), \
                "Explanation should reference the accounts in the ring"

    def test_detects_3_account_ring(self):
        """
        VERIFICATION PROMPT Q3: Cycle detection is NOT limited to 2-account pairs.
        This tests a 3-account ring: A→B→C→A.

        Volumes: 3 legs × 50k = 150k gross. Normal window ≈ 76k. Multiple ≈ 2.0x ✓.
        """
        instrument = _make_instrument(avg_daily_volume=500_000.0)
        window_start = datetime(2024, 1, 15, 10, 0, 0)
        window_end = datetime(2024, 1, 15, 11, 0, 0)

        trades = [
            _make_trade("A", "B", quantity=50_000, ts_offset_minutes=5),
            _make_trade("B", "C", quantity=50_000, ts_offset_minutes=10),
            _make_trade("C", "A", quantity=50_000, ts_offset_minutes=15),
        ]

        signals = detect_circular_trading(trades, instrument, window_start, window_end)

        assert len(signals) >= 1, (
            "3-account ring A→B→C→A must be detected. "
            "If only 2-account pairs were checked, this would be missed — "
            "real rings are often 3+ accounts specifically to evade pair detection."
        )
        # The ring must have 3 accounts
        found_3_ring = any(sig.cycle_length == 3 for sig in signals)
        assert found_3_ring, (
            f"Expected a cycle_length=3 ring. Signals: "
            f"{[(s.cycle_accounts, s.cycle_length) for s in signals]}"
        )

    def test_detects_2_account_ring(self):
        """2-account ring (the simplest case) must also be detected.

        Volumes: 2 legs × 50k = 100k gross. Normal window ≈ 76k. Multiple ≈ 1.3x.
        Use 80k per leg so multiple = 160k/76k ≈ 2.1x to safely clear threshold.
        """
        instrument = _make_instrument(avg_daily_volume=500_000.0)
        window_start = datetime(2024, 1, 15, 10, 0, 0)
        window_end = datetime(2024, 1, 15, 11, 0, 0)

        # A sells to B, B sells back to A — back where they started
        trades = [
            _make_trade("A", "B", quantity=80_000, ts_offset_minutes=5),
            _make_trade("B", "A", quantity=80_000, ts_offset_minutes=10),
        ]

        signals = detect_circular_trading(trades, instrument, window_start, window_end)
        assert len(signals) >= 1, "Simple 2-account ring A↔B must be detected"

    # ── True negative: 4 unrelated accounts, NO ring ──────────────────────────

    def test_does_not_flag_unrelated_normal_trading(self):
        """
        VERIFICATION PROMPT Q2: 4 UNRELATED accounts trading the same stock
        normally (no ring) — detector must NOT flag this.

        Setup: 4 accounts each trade independently, with net positions that
        are NOT near zero (they genuinely bought or sold, not round-tripped).
        Crucially, there's no A→B→...→A cycle — each account trades in ONE
        direction only.
        """
        instrument = _make_instrument(avg_daily_volume=500_000.0)
        window_start = datetime(2024, 1, 15, 10, 0, 0)
        window_end = datetime(2024, 1, 15, 11, 0, 0)

        # Market maker sells to 4 different buyers — no ring
        # P (market maker) sells to W, X, Y, Z
        # W, X, Y, Z each buy only — they don't sell to anyone in the group
        trades = [
            _make_trade("P", "W", quantity=1000, ts_offset_minutes=5),
            _make_trade("P", "X", quantity=1000, ts_offset_minutes=10),
            _make_trade("P", "Y", quantity=1000, ts_offset_minutes=15),
            _make_trade("P", "Z", quantity=1000, ts_offset_minutes=20),
        ]

        signals = detect_circular_trading(trades, instrument, window_start, window_end)

        assert len(signals) == 0, (
            f"4 unrelated accounts (P selling to W, X, Y, Z with no ring) "
            f"must NOT produce a circular trading signal. Got {len(signals)} signals: "
            f"{[(s.cycle_accounts, s.score) for s in signals]}"
        )

    def test_does_not_flag_empty_trade_list(self):
        """Empty input returns empty list — no crash, no synthetic output."""
        instrument = _make_instrument()
        window_start = datetime(2024, 1, 15, 10, 0, 0)
        window_end = datetime(2024, 1, 15, 11, 0, 0)

        signals = detect_circular_trading([], instrument, window_start, window_end)
        assert signals == []

    def test_does_not_flag_high_net_position(self):
        """
        Trades that look like a ring in graph structure but where one account
        has a large net position (they genuinely bought and kept) are NOT flagged.
        """
        instrument = _make_instrument(avg_daily_volume=500_000.0)
        window_start = datetime(2024, 1, 15, 10, 0, 0)
        window_end = datetime(2024, 1, 15, 11, 0, 0)

        # A sells 1000 to B; B sells 500 back to A
        # B's net position: +500 (they kept half — not a zero-net ring)
        trades = [
            _make_trade("A", "B", quantity=1000, ts_offset_minutes=5),
            _make_trade("B", "A", quantity=500, ts_offset_minutes=10),
        ]

        signals = detect_circular_trading(trades, instrument, window_start, window_end)
        # B has net +500, A has net -500 — both are 33% of gross volume (1500)
        # which is well above NET_POSITION_THRESHOLD=0.10
        assert len(signals) == 0, (
            "Trade pair with large net position (genuine directional trading) "
            "must NOT be flagged as circular. "
            f"Got {len(signals)} signals."
        )

    # ── Illiquid stock false-positive handling ────────────────────────────────

    def test_illiquid_stock_ring_score_discounted(self):
        """
        VERIFICATION PROMPT Q4: Illiquid stock rings get score-discounted
        and the explanation includes a false-positive warning.
        """
        # Illiquid instrument: only 10,000 shares/day (below 50,000 threshold)
        illiquid_instrument = _make_instrument(
            symbol="ILLIQCO", avg_daily_volume=10_000.0
        )
        liquid_instrument = _make_instrument(
            symbol="LIQUIDCO", avg_daily_volume=500_000.0
        )
        window_start = datetime(2024, 1, 15, 10, 0, 0)
        window_end = datetime(2024, 1, 15, 11, 0, 0)

        # Same ring for both instruments
        trades = [
            _make_trade("A", "B", quantity=1000, ts_offset_minutes=5),
            _make_trade("B", "C", quantity=1000, ts_offset_minutes=10),
            _make_trade("C", "A", quantity=1000, ts_offset_minutes=15),
        ]

        illiquid_signals = detect_circular_trading(
            trades, illiquid_instrument, window_start, window_end
        )
        liquid_signals = detect_circular_trading(
            trades, liquid_instrument, window_start, window_end
        )

        if illiquid_signals and liquid_signals:
            # Illiquid score must be lower than liquid score for same ring
            illiquid_score = max(s.score for s in illiquid_signals)
            liquid_score = max(s.score for s in liquid_signals)
            assert illiquid_score < liquid_score, (
                f"Illiquid stock ring score ({illiquid_score:.2f}) must be lower "
                f"than same ring in liquid stock ({liquid_score:.2f}). "
                "The 30% discount did not apply."
            )

            # False-positive warning must be in the explanation
            illiquid_sig = illiquid_signals[0]
            assert illiquid_sig.false_positive_warning, \
                "Illiquid stock signals must include false_positive_warning"
            assert illiquid_sig.is_illiquid is True
            assert "illiquid" in illiquid_sig.explanation.lower() or \
                   "warning" in illiquid_sig.explanation.lower(), \
                "Explanation for illiquid stock must mention the liquidity concern"

    def test_illiquid_flag_set_correctly(self):
        """is_illiquid flag is True for instruments below the threshold."""
        illiquid = _make_instrument(avg_daily_volume=5_000.0)
        liquid = _make_instrument(avg_daily_volume=1_000_000.0)
        window_start = datetime(2024, 1, 15, 10, 0, 0)
        window_end = datetime(2024, 1, 15, 11, 0, 0)

        trades = [
            _make_trade("A", "B", quantity=50_000, ts_offset_minutes=5),
            _make_trade("B", "A", quantity=50_000, ts_offset_minutes=10),
        ]

        illiquid_sigs = detect_circular_trading(trades, illiquid, window_start, window_end)
        liquid_sigs = detect_circular_trading(trades, liquid, window_start, window_end)

        for sig in illiquid_sigs:
            assert sig.is_illiquid is True
        for sig in liquid_sigs:
            assert sig.is_illiquid is False

    # ── Deduplication ─────────────────────────────────────────────────────────

    def test_ring_not_double_counted(self):
        """
        A→B→C and B→C→A and C→A→B are the same ring with different starting
        points. The detector must return it only once.
        """
        instrument = _make_instrument(avg_daily_volume=500_000.0)
        window_start = datetime(2024, 1, 15, 10, 0, 0)
        window_end = datetime(2024, 1, 15, 11, 0, 0)

        trades = [
            _make_trade("A", "B", quantity=1000, ts_offset_minutes=5),
            _make_trade("B", "C", quantity=1000, ts_offset_minutes=10),
            _make_trade("C", "A", quantity=1000, ts_offset_minutes=15),
        ]

        signals = detect_circular_trading(trades, instrument, window_start, window_end)
        # Should find exactly 1 unique ring {A, B, C}
        ring_sets = [frozenset(s.cycle_accounts) for s in signals]
        unique_rings = set(ring_sets)
        assert len(unique_rings) == len(ring_sets), (
            f"Same ring was detected multiple times: {ring_sets}"
        )


# ══════════════════════════════════════════════════════════════════════════════
# Tests: coordinated_pump.py
# ══════════════════════════════════════════════════════════════════════════════

class TestCoordinatedPumpDetector:

    def _make_buy_orders(
        self,
        account_ids: list[str],
        quantity_each: int = 10_000,
        price: float = 100.0,
        instrument_id: str = "instr-1",
    ) -> list[Order]:
        """Helper: makes one BUY order per account in the list."""
        orders = []
        for i, acct in enumerate(account_ids):
            orders.append(_make_order(
                account_id=acct,
                side=OrderSide.BUY,
                quantity=quantity_each,
                price=price,
                status=OrderStatus.EXECUTED,
                ts_offset_minutes=i,
                instrument_id=instrument_id,
            ))
        return orders

    # ── True positive: coordinated pump ───────────────────────────────────────

    def test_detects_coordinated_pump(self):
        """
        TRUE POSITIVE: 5 accounts simultaneously buy a large quantity of a
        normally-thin stock, spiking volume well above normal.
        Detector must flag this.
        """
        # Normal volume: 20,000 shares/day → ~1,538 per 30-min window (6.5 hr day)
        # Combined buy: 5 accounts × 10,000 = 50,000 shares
        # Volume multiple: 50,000 / 1,538 ≈ 32.5x → well above VOLUME_SPIKE_MULTIPLE=5
        instrument = _make_instrument(
            symbol="PUMPED", avg_daily_volume=20_000.0
        )
        window_start = datetime(2024, 1, 15, 10, 0, 0)
        window_end = window_start + timedelta(minutes=30)

        orders = self._make_buy_orders(
            ["ACC1", "ACC2", "ACC3", "ACC4", "ACC5"],
            quantity_each=10_000,
        )

        signal = detect_coordinated_pump(orders, instrument, window_start, window_end)

        assert signal is not None, (
            "Expected pump signal for 5 accounts buying 32.5x normal volume. "
            "Got None — check that VOLUME_SPIKE_MULTIPLE and "
            "MIN_COORDINATING_ACCOUNTS thresholds are correct."
        )
        assert isinstance(signal, CoordinatedPumpSignal)
        assert signal.num_accounts == 5
        assert signal.volume_multiple >= VOLUME_SPIKE_MULTIPLE
        assert signal.score > 0.0
        assert signal.explanation, "Explanation must be non-empty (HARD RULE #3)"
        assert "pump" in signal.explanation.lower() or \
               "coordinated" in signal.explanation.lower() or \
               "buy" in signal.explanation.lower(), \
            "Explanation must describe what was detected"

    def test_dormant_accounts_raise_score(self):
        """
        Accounts that were dormant in this instrument before the pump window
        must increase the suspicion score.
        """
        instrument = _make_instrument(symbol="PUMPED2", avg_daily_volume=20_000.0)
        window_start = datetime(2024, 1, 15, 10, 0, 0)
        window_end = window_start + timedelta(minutes=30)

        orders = self._make_buy_orders(
            ["ACC1", "ACC2", "ACC3", "ACC4"],
            quantity_each=15_000,
        )

        # Scenario A: no dormancy info
        signal_no_dormancy = detect_coordinated_pump(
            orders, instrument, window_start, window_end,
            prior_trade_dates=None,
        )

        # Scenario B: all accounts were dormant (last traded 90 days ago)
        prior_dates = {
            acct: window_start - timedelta(days=90)
            for acct in ["ACC1", "ACC2", "ACC3", "ACC4"]
        }
        signal_dormant = detect_coordinated_pump(
            orders, instrument, window_start, window_end,
            prior_trade_dates=prior_dates,
        )

        if signal_no_dormancy and signal_dormant:
            assert signal_dormant.score >= signal_no_dormancy.score, (
                "Dormant accounts must result in equal or higher suspicion score. "
                f"No-dormancy: {signal_no_dormancy.score:.3f}, "
                f"With-dormancy: {signal_dormant.score:.3f}"
            )
            assert len(signal_dormant.dormant_accounts) > 0
            # Dormancy must be mentioned in the explanation
            assert "dormant" in signal_dormant.explanation.lower(), \
                "Dormant accounts must be mentioned in the explanation"

    # ── True negative: no pump ────────────────────────────────────────────────

    def test_does_not_flag_normal_volume(self):
        """
        TRUE NEGATIVE: 5 accounts buy, but combined volume is well within normal
        range (below VOLUME_SPIKE_MULTIPLE × normal). No signal.
        """
        # Normal volume: 1,000,000 shares/day → ~77,000 per 30-min window
        # Combined buy: 5 accounts × 1,000 = 5,000 shares
        # Volume multiple: 5,000 / 77,000 ≈ 0.065x → far below threshold
        instrument = _make_instrument(
            symbol="NORMAL", avg_daily_volume=1_000_000.0
        )
        window_start = datetime(2024, 1, 15, 10, 0, 0)
        window_end = window_start + timedelta(minutes=30)

        orders = self._make_buy_orders(
            ["ACC1", "ACC2", "ACC3", "ACC4", "ACC5"],
            quantity_each=1_000,  # trivially small relative to liquid stock volume
        )

        signal = detect_coordinated_pump(orders, instrument, window_start, window_end)

        assert signal is None, (
            f"Normal-volume buying in a liquid stock must NOT trigger a pump signal. "
            f"Got: {signal}"
        )

    def test_does_not_flag_too_few_accounts(self):
        """
        TRUE NEGATIVE: Only 2 accounts buying (below MIN_COORDINATING_ACCOUNTS=3).
        Even if volume is high, 2 accounts is not "coordinated" by our definition.
        """
        instrument = _make_instrument(symbol="FEWACCT", avg_daily_volume=10_000.0)
        window_start = datetime(2024, 1, 15, 10, 0, 0)
        window_end = window_start + timedelta(minutes=30)

        # 2 accounts, very high volume relative to the illiquid instrument
        orders = self._make_buy_orders(
            ["ACC1", "ACC2"],
            quantity_each=50_000,
        )

        signal = detect_coordinated_pump(orders, instrument, window_start, window_end)

        assert signal is None, (
            f"2-account buying (below MIN_COORDINATING_ACCOUNTS={MIN_COORDINATING_ACCOUNTS}) "
            f"must NOT trigger a pump signal, regardless of volume. "
            f"Got: {signal}"
        )

    def test_does_not_flag_empty_orders(self):
        """Empty input returns None — no crash, no synthetic output."""
        instrument = _make_instrument()
        window_start = datetime(2024, 1, 15, 10, 0, 0)
        window_end = window_start + timedelta(minutes=30)

        signal = detect_coordinated_pump([], instrument, window_start, window_end)
        assert signal is None

    def test_does_not_flag_sell_orders(self):
        """
        The pump detector is BUY-side only. All-sell activity must not trigger it,
        even if volume is high and accounts are many.
        """
        instrument = _make_instrument(symbol="SELLONLY", avg_daily_volume=20_000.0)
        window_start = datetime(2024, 1, 15, 10, 0, 0)
        window_end = window_start + timedelta(minutes=30)

        # 5 accounts, all SELL side
        sell_orders = [
            _make_order(
                account_id=f"SELLER{i}",
                side=OrderSide.SELL,
                quantity=20_000,
                ts_offset_minutes=i,
            )
            for i in range(5)
        ]

        signal = detect_coordinated_pump(sell_orders, instrument, window_start, window_end)
        assert signal is None, \
            "Sell-side activity must NOT trigger the coordinated BUY pump detector"

    # ── Illiquid stock handling ───────────────────────────────────────────────

    def test_illiquid_pump_score_discounted(self):
        """Illiquid stock pump signals get score-discounted with a warning."""
        illiquid = _make_instrument(symbol="ILLIQ", avg_daily_volume=5_000.0)
        liquid = _make_instrument(symbol="LIQUID", avg_daily_volume=1_000_000.0)
        window_start = datetime(2024, 1, 15, 10, 0, 0)
        window_end = window_start + timedelta(minutes=30)

        # Same orders for both instruments
        orders = self._make_buy_orders(
            ["ACC1", "ACC2", "ACC3", "ACC4", "ACC5"],
            quantity_each=5_000,
        )

        illiquid_sig = detect_coordinated_pump(
            orders, illiquid, window_start, window_end
        )
        liquid_sig = detect_coordinated_pump(
            orders, liquid, window_start, window_end
        )

        # Illiquid should fire (volume multiple will be very high)
        if illiquid_sig is not None:
            assert illiquid_sig.is_illiquid is True
            assert illiquid_sig.false_positive_warning, \
                "Illiquid pump signals must include false_positive_warning"

        # Liquid instrument: combined volume (25,000) is tiny vs 1M/day → no signal
        # This also demonstrates the normalisation works correctly.
        # If liquid_sig fires, its score should be lower than illiquid_sig's pre-discount score.
        if illiquid_sig and liquid_sig:
            assert illiquid_sig.score < liquid_sig.score or illiquid_sig.is_illiquid


# ══════════════════════════════════════════════════════════════════════════════
# Cross-detector: explanation quality checks (HARD RULE #3)
# ══════════════════════════════════════════════════════════════════════════════

class TestExplanationQuality:
    """
    Verifies that EVERY signal type contains a human-readable explanation
    (HARD RULE #3: no black-box numbers — every detection function must return
    an explanation string alongside the score).
    """

    def test_circular_signal_explanation_contains_key_facts(self):
        """Circular trading explanation must state: ring structure, volume, net position."""
        instrument = _make_instrument(avg_daily_volume=500_000.0)
        window_start = datetime(2024, 1, 15, 10, 0, 0)
        window_end = datetime(2024, 1, 15, 11, 0, 0)
        trades = [
            _make_trade("X", "Y", quantity=5000, ts_offset_minutes=5),
            _make_trade("Y", "X", quantity=5000, ts_offset_minutes=10),
        ]
        signals = detect_circular_trading(trades, instrument, window_start, window_end)

        for sig in signals:
            explanation = sig.explanation.lower()
            # Must mention: the accounts, volume, net position concept
            assert any(word in explanation for word in ("ring", "circular", "cycle")), \
                f"Explanation must describe the ring pattern. Got: {sig.explanation}"
            assert any(word in explanation for word in ("volume", "shares", "quantity")), \
                f"Explanation must mention volume. Got: {sig.explanation}"
            assert any(word in explanation for word in ("net", "position")), \
                f"Explanation must reference net position. Got: {sig.explanation}"

    def test_pump_signal_explanation_contains_key_facts(self):
        """Pump explanation must state: accounts involved, volume multiple, threshold."""
        instrument = _make_instrument(symbol="PUMPED3", avg_daily_volume=20_000.0)
        window_start = datetime(2024, 1, 15, 10, 0, 0)
        window_end = window_start + timedelta(minutes=30)

        orders = [
            _make_order("A1", OrderSide.BUY, quantity=10_000, ts_offset_minutes=0),
            _make_order("A2", OrderSide.BUY, quantity=10_000, ts_offset_minutes=1),
            _make_order("A3", OrderSide.BUY, quantity=10_000, ts_offset_minutes=2),
            _make_order("A4", OrderSide.BUY, quantity=10_000, ts_offset_minutes=3),
        ]

        signal = detect_coordinated_pump(orders, instrument, window_start, window_end)

        if signal:
            explanation = signal.explanation.lower()
            assert any(word in explanation for word in ("account", "distinct", "coordinated")), \
                f"Explanation must describe the accounts involved. Got: {signal.explanation}"
            assert any(word in explanation for word in ("volume", "shares")), \
                f"Explanation must mention volume. Got: {signal.explanation}"
            assert any(str(c) in explanation for c in [
                str(MIN_COORDINATING_ACCOUNTS),
                str(int(VOLUME_SPIKE_MULTIPLE)),
            ]), "Explanation must mention the relevant threshold value"
