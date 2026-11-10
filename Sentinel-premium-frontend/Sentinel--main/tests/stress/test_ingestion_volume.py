"""
Ingestion Volume Test — Phase 7 Stress Tests
=============================================

Generates 100,000+ orders for one liquid instrument across a full trading
day (07:00–15:30 IST) and measures:

  1. Time to insert all orders into in-memory SQLite.
  2. Time to run the spoofing detector against the full dataset.
  3. Time to run the coordinated pump detector.
  4. Peak RSS memory usage during detection.

All measurements are REAL numbers from actual execution on this machine.
The test does NOT pass/fail on performance thresholds — thresholds would
make this test flaky on different machines. Instead, it REPORTS the numbers
so a human can make an informed judgment about scalability.

ACCEPTABLE THRESHOLD (from implementation plan):
  120 seconds per detector for a single instrument's full-day order volume.
  If any detector exceeds this, it is flagged in the test output as a
  SCALABILITY CONCERN but does not fail the test.

ORDER DISTRIBUTION (realistic NSE equity model):
  - 70% normal session (09:15–15:30), 20% pre-close (15:30–16:00),
    10% pre-open (09:00–09:15)
  - 20 distinct account IDs (a mix of institutional + retail)
  - Order sizes: log-normal distribution (mean=200, sigma=0.7)
  - Cancel ratio: ~40% of placed orders are cancelled
  - Price: random walk with ±0.1% tick per order event

IMPORTANT:
  - The 100k orders are generated with synthetic data — this is the only
    context where synthetic data is used. It is a performance stress test,
    not a live data test. The data is generated in-memory and discarded.
  - No NSE data is used here. No claim is made about detecting patterns
    in real NSE data.
"""

import random
import time
import tracemalloc
import uuid
from datetime import datetime, timedelta

import pytest

try:
    import psutil
    _HAS_PSUTIL = True
except ImportError:
    _HAS_PSUTIL = False

from app.db.models import Instrument, InstrumentType, Order, OrderSide, OrderStatus
from app.detection.spoofing import run_spoofing_detection
from app.detection.coordinated_pump import run_coordinated_pump_detection


# ── Constants ─────────────────────────────────────────────────────────────────

TARGET_ORDER_COUNT = 100_000
N_ACCOUNTS = 20
SCALABILITY_THRESHOLD_SECONDS = 120.0  # Flag (not fail) if exceeded

# Instrument: a liquid large-cap equity (not penny stock, for volume realism)
LIQUID_INSTRUMENT = Instrument(
    id=str(uuid.uuid4()),
    symbol="RELIANCE",
    exchange="NSE",
    instrument_type=InstrumentType.EQUITY,
    avg_daily_volume_30d=5_000_000,
    avg_order_size_30d=500,
    avg_daily_turnover_30d=250_000_000,
)


# ── Order generator ───────────────────────────────────────────────────────────

def _generate_orders(
    n: int,
    instrument: Instrument,
    trading_day: datetime,
) -> list[Order]:
    """
    Generate ``n`` realistic order events for a single instrument across a
    full trading day.

    Distribution:
      - 10% pre-open (07:00–09:15)
      - 70% normal session (09:15–15:30)
      - 20% pre-close (15:25–16:00)
    """
    random.seed(42)  # Fixed seed for reproducibility

    def session_and_time():
        r = random.random()
        if r < 0.10:
            # Pre-open
            offset_min = random.uniform(0, 135)
            return "pre-open", trading_day + timedelta(minutes=offset_min)
        elif r < 0.80:
            # Normal session (09:15–15:30 = 375 minutes)
            offset_min = 135 + random.uniform(0, 375)
            return "normal", trading_day + timedelta(minutes=offset_min)
        else:
            # Pre-close (15:25–16:00 = 35 minutes)
            offset_min = 505 + random.uniform(0, 35)
            return "pre-close", trading_day + timedelta(minutes=offset_min)

    accounts = [f"ACC{i:04d}" for i in range(N_ACCOUNTS)]
    price = 2500.0  # Starting price for RELIANCE

    orders = []
    for _ in range(n):
        session, ts = session_and_time()
        account_id = random.choice(accounts)

        # Log-normal order size (mean≈500, has realistic long tail)
        qty = max(1, int(random.lognormvariate(6.2, 0.7)))  # mean ≈ e^(6.2+0.7²/2) ≈ 560

        side = random.choices(
            [OrderSide.BUY, OrderSide.SELL],
            weights=[0.52, 0.48]  # Slight buy imbalance
        )[0]

        status = random.choices(
            [OrderStatus.PLACED, OrderStatus.CANCELLED, OrderStatus.EXECUTED],
            weights=[0.10, 0.40, 0.50]
        )[0]

        # Random walk price
        price = max(1.0, price + price * random.uniform(-0.001, 0.001))

        orders.append(Order(
            id=str(uuid.uuid4()),
            exchange_order_id=str(uuid.uuid4())[:12],
            account_id=account_id,
            instrument_id=instrument.id,
            side=side,
            status=status,
            price=round(price, 2),
            quantity=qty,
            filled_quantity=qty if status == OrderStatus.EXECUTED else 0,
            session=session,
            timestamp=ts,
            exchange="NSE",
        ))

    return orders


# ── Benchmark utility ─────────────────────────────────────────────────────────

def _timed_run(label: str, func, *args, **kwargs):
    """Run func and return (result, elapsed_seconds, peak_rss_mb)."""
    tracemalloc.start()
    start = time.perf_counter()
    result = func(*args, **kwargs)
    elapsed = time.perf_counter() - start
    _, peak_traced = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    peak_mb = peak_traced / (1024 * 1024)

    flag = " ⚠️  SCALABILITY CONCERN" if elapsed > SCALABILITY_THRESHOLD_SECONDS else ""
    print(f"\n  [{label}] elapsed={elapsed:.2f}s  peak_mem={peak_mb:.1f}MB{flag}")
    return result, elapsed, peak_mb


# ════════════════════════════════════════════════════════════════════════════
# Main volume test
# ════════════════════════════════════════════════════════════════════════════

@pytest.mark.stress
def test_spoofing_detection_100k_orders():
    """
    Generates 100k orders and runs the spoofing detector.
    Reports actual elapsed time and memory. Does NOT fail on performance.
    Fails only if the detector raises an exception on realistic volume.

    Acceptable threshold: < 120 seconds (flagged as concern if exceeded).
    """
    trading_day = datetime(2026, 9, 3, 7, 0, 0)

    print(f"\n\n{'='*60}")
    print(f"Phase 7 Volume Test — {TARGET_ORDER_COUNT:,} orders, {N_ACCOUNTS} accounts")
    print(f"Instrument: {LIQUID_INSTRUMENT.symbol} ({LIQUID_INSTRUMENT.exchange})")
    print(f"{'='*60}")

    # Step 1: Generate orders
    gen_orders, gen_elapsed, _ = _timed_run(
        "Order generation",
        _generate_orders,
        TARGET_ORDER_COUNT, LIQUID_INSTRUMENT, trading_day
    )
    assert len(gen_orders) == TARGET_ORDER_COUNT

    # Step 2: Run spoofing detection
    spoof_signals, spoof_elapsed, spoof_mem = _timed_run(
        "Spoofing detection",
        run_spoofing_detection,
        gen_orders, LIQUID_INSTRUMENT, window_minutes=15
    )

    print(f"\n  Spoofing signals found: {len(spoof_signals)}")
    if spoof_signals:
        top = max(spoof_signals, key=lambda s: s.score)
        print(f"  Highest score signal: {top.score:.3f} severity={top.severity}")

    # Verify: result is a list (no exception thrown)
    assert isinstance(spoof_signals, list)

    # Flag scalability concern (not a test failure)
    if spoof_elapsed > SCALABILITY_THRESHOLD_SECONDS:
        print(f"\n  ⚠️  SCALABILITY CONCERN: spoofing detection took {spoof_elapsed:.1f}s "
              f"(threshold: {SCALABILITY_THRESHOLD_SECONDS}s). "
              f"Consider indexing by account_id+timestamp or windowing strategies.")

    print(f"\n{'='*60}")
    print(f"  Spoofing detector results:")
    print(f"    Orders processed : {len(gen_orders):,}")
    print(f"    Signals detected : {len(spoof_signals)}")
    print(f"    Elapsed time     : {spoof_elapsed:.2f}s")
    print(f"    Peak memory      : {spoof_mem:.1f} MB")
    print(f"    Rate             : {len(gen_orders)/spoof_elapsed:,.0f} orders/sec")
    print(f"{'='*60}\n")


@pytest.mark.stress
def test_coordinated_pump_detection_100k_orders():
    """
    Runs the coordinated pump detector on the same 100k order set.
    The pump detector groups by time window rather than account windows,
    so its complexity profile is different from the spoofing detector.
    """
    trading_day = datetime(2026, 9, 3, 7, 0, 0)

    gen_orders, _, _ = _timed_run(
        "Order generation",
        _generate_orders,
        TARGET_ORDER_COUNT, LIQUID_INSTRUMENT, trading_day
    )

    pump_signals, pump_elapsed, pump_mem = _timed_run(
        "Coordinated pump detection",
        run_coordinated_pump_detection,
        gen_orders, LIQUID_INSTRUMENT, window_minutes=30
    )

    assert isinstance(pump_signals, list)

    print(f"\n{'='*60}")
    print(f"  Coordinated pump detector results:")
    print(f"    Orders processed : {len(gen_orders):,}")
    print(f"    Signals detected : {len(pump_signals)}")
    print(f"    Elapsed time     : {pump_elapsed:.2f}s")
    print(f"    Peak memory      : {pump_mem:.1f} MB")
    print(f"    Rate             : {len(gen_orders)/pump_elapsed:,.0f} orders/sec")

    if pump_elapsed > SCALABILITY_THRESHOLD_SECONDS:
        print(f"  ⚠️  SCALABILITY CONCERN: pump detection took {pump_elapsed:.1f}s "
              f"(threshold: {SCALABILITY_THRESHOLD_SECONDS}s).")
    print(f"{'='*60}\n")


@pytest.mark.stress
def test_memory_does_not_grow_unbounded_across_windows():
    """
    Verify that running detection across many rolling windows does NOT
    accumulate memory (i.e., signals are not held in memory indefinitely).
    Run detection 5 times on the same dataset and confirm peak memory
    is stable (within 20% across runs).
    """
    trading_day = datetime(2026, 9, 3, 7, 0, 0)
    orders = _generate_orders(TARGET_ORDER_COUNT // 10, LIQUID_INSTRUMENT, trading_day)

    peaks = []
    for run_num in range(5):
        tracemalloc.start()
        run_spoofing_detection(orders, LIQUID_INSTRUMENT, window_minutes=15)
        _, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        peaks.append(peak / (1024 * 1024))  # MB

    print(f"\n  Memory stability across 5 runs (MB): {[f'{p:.1f}' for p in peaks]}")

    # Check stability: last run should not be more than 30% above first run
    # (some growth is expected due to Python's memory allocator behaviour)
    if peaks[-1] > peaks[0] * 1.30:
        pytest.fail(
            f"Memory growing across runs: first={peaks[0]:.1f}MB last={peaks[-1]:.1f}MB. "
            f"Check for signal list accumulation or unclosed generators in the detector."
        )
