"""
generate_all_detector_samples.py
=================================
Generates sample data for all 6 Sentinel detectors and runs the REAL detector
code against it. Outputs results to demo/sample_data/{detector}_{trigger|normal}.json.

Integrity contract
------------------
- Every JSON file contains BOTH the input sample data AND the real detector output.
- The detector output (score, severity, explanation) was produced by importing and
  calling the actual function in app/detection/*.py — not re-implemented here.
- Sample data is synthetic / illustrative. It is labelled "sample_data": true
  in every JSON file and is NOT derived from any real market session or account.
- If a detector returns None on the "trigger" scenario, this is reported honestly
  (see RESULTS SUMMARY at the end of script output). The sample data is NOT
  silently adjusted until it "works".

Run from the repo root:
    python demo/generate_all_detector_samples.py
"""

import json
import pathlib
import sys
import uuid
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta

# ── ensure repo root is importable ──────────────────────────────────────────
ROOT = pathlib.Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

OUTPUT_DIR = ROOT / "demo" / "sample_data"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


def _uid() -> str:
    return str(uuid.uuid4())


def _save(name: str, scenario: str, payload: dict) -> pathlib.Path:
    """Write payload to demo/sample_data/{name}_{scenario}.json"""
    path = OUTPUT_DIR / f"{name}_{scenario}.json"
    path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    return path


RESULTS: dict[str, dict] = {}  # detector → {trigger: ..., normal: ...}


# ═══════════════════════════════════════════════════════════════════════════════
# 1. SPOOFING / LAYERING
# ═══════════════════════════════════════════════════════════════════════════════

def run_spoofing() -> None:
    from app.detection.spoofing import detect_spoofing_for_account, MIN_CANCEL_RATIO, MIN_SIZE_MULTIPLE, MIN_PRICE_IMPACT_PCT
    from app.db.models import Order, OrderSide, OrderStatus, Instrument, InstrumentType

    print("\n[1/6] spoofing.py — detect_spoofing_for_account()")

    instr = Instrument(
        id=_uid(), symbol="MAURIUDYOG", exchange="NSE",
        instrument_type=InstrumentType.PENNY_STOCK,
        avg_daily_volume_30d=120_000,
        avg_order_size_30d=500,
        avg_daily_turnover_30d=250_000,
    )

    BASE_T = datetime(2018, 10, 12, 9, 17, 0)

    def make_order(eid, side, qty, price, status, dt):
        return Order(
            id=_uid(), exchange_order_id=eid, account_id="ACC-7741",
            instrument_id=instr.id, side=side, status=status,
            price=price, quantity=qty, filled_quantity=qty if status == OrderStatus.EXECUTED else 0,
            timestamp=dt, exchange="NSE",
        )

    # TRIGGER: 4 large BUY orders ALL cancelled, then a SELL executed.
    # Resolved orders after dedup by exchange_order_id:
    #   EO-1 → CANCELLED (2200 @ 8.50)
    #   EO-2 → CANCELLED (2200 @ 8.50)
    #   EO-3 → CANCELLED (2200 @ 8.60)
    #   EO-4 → CANCELLED (2200 @ 8.55)
    #   EO-5 → EXECUTED  (450 @ 8.65)
    # placed_value = 4*2200*avg + 450*8.65 ≈ 75,667
    # cancelled_value = 4*2200*avg ≈ 75,245
    # cancel_ratio ≈ 0.995 >> 0.85 threshold
    t_orders = [
        make_order("EO-1", OrderSide.BUY,  2200, 8.50, OrderStatus.PLACED,    BASE_T),
        make_order("EO-2", OrderSide.BUY,  2200, 8.50, OrderStatus.PLACED,    BASE_T + timedelta(minutes=1)),
        make_order("EO-3", OrderSide.BUY,  2200, 8.60, OrderStatus.PLACED,    BASE_T + timedelta(minutes=2)),
        make_order("EO-4", OrderSide.BUY,  2200, 8.55, OrderStatus.PLACED,    BASE_T + timedelta(minutes=3)),
        # All four large BUY orders cancelled
        make_order("EO-1", OrderSide.BUY,  2200, 8.50, OrderStatus.CANCELLED, BASE_T + timedelta(minutes=4)),
        make_order("EO-2", OrderSide.BUY,  2200, 8.50, OrderStatus.CANCELLED, BASE_T + timedelta(minutes=4, seconds=10)),
        make_order("EO-3", OrderSide.BUY,  2200, 8.60, OrderStatus.CANCELLED, BASE_T + timedelta(minutes=4, seconds=20)),
        make_order("EO-4", OrderSide.BUY,  2200, 8.55, OrderStatus.CANCELLED, BASE_T + timedelta(minutes=4, seconds=30)),
        # Small SELL executed on the opposite side (profit-trade)
        make_order("EO-5", OrderSide.SELL,  450, 8.65, OrderStatus.EXECUTED,  BASE_T + timedelta(minutes=5)),
    ]

    win_start = BASE_T
    win_end = BASE_T + timedelta(minutes=15)
    trigger_sig = detect_spoofing_for_account(t_orders, instr, win_start, win_end)

    trigger_payload = {
        "meta": {"sample_data": True, "detector": "spoofing", "scenario": "trigger",
                  "import": "from app.detection.spoofing import detect_spoofing_for_account",
                  "call": "detect_spoofing_for_account(orders, instrument, window_start, window_end)"},
        "input": {
            "instrument": {"symbol": instr.symbol, "exchange": instr.exchange,
                           "avg_daily_volume_30d": instr.avg_daily_volume_30d,
                           "avg_order_size_30d": instr.avg_order_size_30d},
            "orders": [{"exchange_order_id": o.exchange_order_id, "side": str(o.side),
                         "quantity": o.quantity, "price": o.price, "status": str(o.status),
                         "timestamp": o.timestamp.isoformat()} for o in t_orders],
            "window_start": win_start.isoformat(),
            "window_end": win_end.isoformat(),
        },
        "output": None if trigger_sig is None else {
            "fired": True,
            "score": trigger_sig.score,
            "severity": trigger_sig.severity,
            "cancel_ratio": trigger_sig.cancel_ratio,
            "size_multiple": trigger_sig.size_multiple,
            "price_impact_pct": trigger_sig.price_impact_pct,
            "opposite_side_executed": trigger_sig.opposite_side_executed,
            "explanation": trigger_sig.explanation,
        },
    }
    if trigger_sig is None:
        trigger_payload["output"] = {"fired": False, "reason": "detector returned None — thresholds not met"}

    _save("spoofing", "trigger", trigger_payload)
    print(f"    trigger → fired={trigger_sig is not None}  "
          f"score={trigger_sig.score:.3f}  sev={trigger_sig.severity}" if trigger_sig else "    trigger → NOT fired")

    # NORMAL: 2 buy orders executed normally, no cancellations
    n_orders = [
        make_order("EN-1", OrderSide.BUY,  400, 8.45, OrderStatus.EXECUTED, BASE_T),
        make_order("EN-2", OrderSide.SELL, 400, 8.55, OrderStatus.EXECUTED, BASE_T + timedelta(minutes=30)),
    ]
    normal_sig = detect_spoofing_for_account(n_orders, instr, win_start, win_end)
    normal_payload = {
        "meta": {"sample_data": True, "detector": "spoofing", "scenario": "normal",
                 "import": "from app.detection.spoofing import detect_spoofing_for_account",
                 "call": "detect_spoofing_for_account(orders, instrument, window_start, window_end)"},
        "input": {
            "instrument": {"symbol": instr.symbol, "exchange": instr.exchange},
            "orders": [{"exchange_order_id": o.exchange_order_id, "side": str(o.side),
                         "quantity": o.quantity, "price": o.price, "status": str(o.status),
                         "timestamp": o.timestamp.isoformat()} for o in n_orders],
        },
        "output": {"fired": normal_sig is not None,
                   "score": getattr(normal_sig, "score", None),
                   "reason": "no excessive cancellations — below cancel_ratio threshold"},
    }
    _save("spoofing", "normal", normal_payload)
    print(f"    normal  → fired={normal_sig is not None}  (expected: False)")

    RESULTS["spoofing"] = {
        "trigger_fired": trigger_sig is not None,
        "trigger_score": getattr(trigger_sig, "score", None),
        "trigger_severity": getattr(trigger_sig, "severity", None),
        "normal_fired": normal_sig is not None,
    }


# ═══════════════════════════════════════════════════════════════════════════════
# 2. CIRCULAR TRADING
# ═══════════════════════════════════════════════════════════════════════════════

def run_circular_trading() -> None:
    from app.detection.circular_trading import detect_circular_trading, NET_POSITION_THRESHOLD
    from app.db.models import Trade, Instrument, InstrumentType, Order, OrderSide, OrderStatus

    print("\n[2/6] circular_trading.py — detect_circular_trading()")

    # Circular trading needs Trade objects with buy_order / sell_order having account_id set.
    # We build lightweight Trade stubs with synthetic order objects attached.

    instr = Instrument(
        id=_uid(), symbol="KAVITIND", exchange="NSE",
        instrument_type=InstrumentType.PENNY_STOCK,
        avg_daily_volume_30d=45_000,
        avg_order_size_30d=300,
    )

    BASE_T = datetime(2018, 3, 15, 10, 0, 0)

    # TRIGGER: 5-account ring: A→B→C→D→E→A
    RING = ["ACC-A", "ACC-B", "ACC-C", "ACC-D", "ACC-E"]

    def make_trade_with_accounts(seller_id, buyer_id, qty, price, t):
        """Create a Trade with buy_order.account_id and sell_order.account_id set."""
        sell_ord = Order(
            id=_uid(), exchange_order_id=_uid(), account_id=seller_id,
            instrument_id=instr.id, side=OrderSide.SELL,
            status=OrderStatus.EXECUTED, price=price, quantity=qty,
            filled_quantity=qty, timestamp=t, exchange="NSE",
        )
        buy_ord = Order(
            id=_uid(), exchange_order_id=_uid(), account_id=buyer_id,
            instrument_id=instr.id, side=OrderSide.BUY,
            status=OrderStatus.EXECUTED, price=price, quantity=qty,
            filled_quantity=qty, timestamp=t, exchange="NSE",
        )
        tr = Trade(
            id=_uid(), buy_order_id=buy_ord.id, sell_order_id=sell_ord.id,
            instrument_id=instr.id, price=price, quantity=qty,
            timestamp=t, exchange="NSE",
        )
        tr.buy_order = buy_ord
        tr.sell_order = sell_ord
        return tr

    ring_qty = 3_000  # each leg of the ring: same quantity → net position = 0
    trigger_trades = [
        make_trade_with_accounts(RING[0], RING[1], ring_qty, 12.50, BASE_T),
        make_trade_with_accounts(RING[1], RING[2], ring_qty, 12.55, BASE_T + timedelta(minutes=5)),
        make_trade_with_accounts(RING[2], RING[3], ring_qty, 12.50, BASE_T + timedelta(minutes=10)),
        make_trade_with_accounts(RING[3], RING[4], ring_qty, 12.55, BASE_T + timedelta(minutes=15)),
        make_trade_with_accounts(RING[4], RING[0], ring_qty, 12.50, BASE_T + timedelta(minutes=20)),
    ]

    win_start = BASE_T
    win_end = BASE_T + timedelta(minutes=60)
    trigger_sigs = detect_circular_trading(trigger_trades, instr, win_start, win_end)
    trigger_sig = trigger_sigs[0] if trigger_sigs else None

    def ring_trade_dict(tr):
        return {"seller": tr.sell_order.account_id, "buyer": tr.buy_order.account_id,
                "quantity": tr.quantity, "price": tr.price, "timestamp": tr.timestamp.isoformat()}

    trigger_payload = {
        "meta": {"sample_data": True, "detector": "circular_trading", "scenario": "trigger",
                 "import": "from app.detection.circular_trading import detect_circular_trading",
                 "call": "detect_circular_trading(trades, instrument, window_start, window_end)"},
        "input": {
            "instrument": {"symbol": instr.symbol, "exchange": instr.exchange,
                           "avg_daily_volume_30d": instr.avg_daily_volume_30d},
            "trades": [ring_trade_dict(t) for t in trigger_trades],
            "window_start": win_start.isoformat(), "window_end": win_end.isoformat(),
        },
        "output": None if trigger_sig is None else {
            "fired": True,
            "score": trigger_sig.score,
            "severity": trigger_sig.severity,
            "cycle_accounts": trigger_sig.cycle_accounts,
            "cycle_length": trigger_sig.cycle_length,
            "gross_volume": trigger_sig.gross_volume,
            "max_net_position_pct": trigger_sig.max_net_position_pct,
            "volume_multiple": trigger_sig.volume_multiple,
            "counterparty_known": trigger_sig.counterparty_known,
            "is_illiquid": trigger_sig.is_illiquid,
            "explanation": trigger_sig.explanation,
            "false_positive_warning": trigger_sig.false_positive_warning,
        },
    }
    if trigger_sig is None:
        trigger_payload["output"] = {"fired": False,
            "reason": "detector returned no signals — NET_POSITION_THRESHOLD or cycle detection not met"}
    _save("circular_trading", "trigger", trigger_payload)
    print(f"    trigger → fired={trigger_sig is not None}  "
          f"score={trigger_sig.score:.3f}  sev={trigger_sig.severity}" if trigger_sig else
          "    trigger → NOT fired (see honest_results in JSON)")

    # NORMAL: 5 independent accounts trading in opposite directions (no ring)
    normal_trades = [
        make_trade_with_accounts("IND-A", "IND-B", 200,  12.50, BASE_T),
        make_trade_with_accounts("IND-C", "IND-D", 150,  12.55, BASE_T + timedelta(minutes=10)),
        make_trade_with_accounts("IND-E", "IND-F", 300,  12.48, BASE_T + timedelta(minutes=25)),
        make_trade_with_accounts("IND-B", "IND-G", 180,  12.60, BASE_T + timedelta(minutes=40)),
    ]
    normal_sigs = detect_circular_trading(normal_trades, instr, win_start, win_end)
    normal_payload = {
        "meta": {"sample_data": True, "detector": "circular_trading", "scenario": "normal",
                 "import": "from app.detection.circular_trading import detect_circular_trading",
                 "call": "detect_circular_trading(trades, instrument, window_start, window_end)"},
        "input": {"trades": [ring_trade_dict(t) for t in normal_trades]},
        "output": {"fired": len(normal_sigs) > 0, "num_signals": len(normal_sigs),
                   "reason": "independent accounts, no closed cycle possible"},
    }
    _save("circular_trading", "normal", normal_payload)
    print(f"    normal  → fired={len(normal_sigs) > 0}  (expected: False)")

    RESULTS["circular_trading"] = {
        "trigger_fired": trigger_sig is not None,
        "trigger_score": getattr(trigger_sig, "score", None),
        "trigger_severity": getattr(trigger_sig, "severity", None),
        "normal_fired": len(normal_sigs) > 0,
    }


# ═══════════════════════════════════════════════════════════════════════════════
# 3. COORDINATED PUMP
# ═══════════════════════════════════════════════════════════════════════════════

def run_coordinated_pump() -> None:
    from app.detection.coordinated_pump import detect_coordinated_pump, MIN_COORDINATING_ACCOUNTS, VOLUME_SPIKE_MULTIPLE
    from app.db.models import Order, OrderSide, OrderStatus, Instrument, InstrumentType

    print("\n[3/6] coordinated_pump.py — detect_coordinated_pump()")

    instr = Instrument(
        id=_uid(), symbol="TINYLTD", exchange="NSE",
        instrument_type=InstrumentType.PENNY_STOCK,
        avg_daily_volume_30d=30_000,
        avg_order_size_30d=200,
        avg_daily_turnover_30d=90_000,
    )

    BASE_T = datetime(2019, 6, 10, 10, 30, 0)

    # TRIGGER: 7 accounts, all dormant, buying in a 30-min window with 6x normal volume
    # normal_window_volume for 30-min window = 30000 * (0.5/6.5) ≈ 2307 shares
    # 7 accounts × 2000 shares = 14000 → 14000 / 2307 ≈ 6.07x (> VOLUME_SPIKE_MULTIPLE=5)
    pump_accts = [f"PUMP-{i:02d}" for i in range(1, 8)]
    trigger_orders = []
    for i, acct in enumerate(pump_accts):
        o = Order(
            id=_uid(), exchange_order_id=_uid(), account_id=acct,
            instrument_id=instr.id, side=OrderSide.BUY, status=OrderStatus.EXECUTED,
            price=3.20 + i * 0.01, quantity=2000, filled_quantity=2000,
            timestamp=BASE_T + timedelta(minutes=i * 3), exchange="NSE",
        )
        trigger_orders.append(o)

    win_start = BASE_T
    win_end = BASE_T + timedelta(minutes=30)

    # Prior trade dates: all dormant (no activity in > 30 days)
    prior_dates = {acct: BASE_T - timedelta(days=60) for acct in pump_accts}
    # First seen dates: all "new" (within 7 days)
    first_seen = {acct: BASE_T - timedelta(days=3) for acct in pump_accts}

    trigger_sig = detect_coordinated_pump(
        trigger_orders, instr, win_start, win_end,
        prior_trade_dates=prior_dates,
        first_seen_dates=first_seen,
    )

    def order_dict(o):
        return {"account_id": o.account_id, "side": str(o.side), "quantity": o.quantity,
                "price": o.price, "status": str(o.status), "timestamp": o.timestamp.isoformat()}

    trigger_payload = {
        "meta": {"sample_data": True, "detector": "coordinated_pump", "scenario": "trigger",
                 "import": "from app.detection.coordinated_pump import detect_coordinated_pump",
                 "call": "detect_coordinated_pump(buy_orders, instrument, window_start, window_end, prior_trade_dates, first_seen_dates)"},
        "input": {
            "instrument": {"symbol": instr.symbol, "exchange": instr.exchange,
                           "avg_daily_volume_30d": instr.avg_daily_volume_30d},
            "orders": [order_dict(o) for o in trigger_orders],
            "window_start": win_start.isoformat(), "window_end": win_end.isoformat(),
            "prior_trade_dates_summary": f"all {len(pump_accts)} accounts dormant >60 days",
            "first_seen_dates_summary": f"all {len(pump_accts)} accounts new (<7 days)",
        },
        "output": None if trigger_sig is None else {
            "fired": True,
            "score": trigger_sig.score,
            "severity": trigger_sig.severity,
            "num_accounts": trigger_sig.num_accounts,
            "dormant_accounts": trigger_sig.dormant_accounts,
            "new_accounts": trigger_sig.new_accounts,
            "combined_buy_volume": trigger_sig.combined_buy_volume,
            "volume_multiple": trigger_sig.volume_multiple,
            "is_illiquid": trigger_sig.is_illiquid,
            "explanation": trigger_sig.explanation,
            "false_positive_warning": trigger_sig.false_positive_warning,
        },
    }
    if trigger_sig is None:
        trigger_payload["output"] = {"fired": False,
            "reason": "detector returned None — VOLUME_SPIKE_MULTIPLE or MIN_COORDINATING_ACCOUNTS not met. "
                      "This may indicate the threshold needs tuning for this instrument size."}
    _save("coordinated_pump", "trigger", trigger_payload)
    print(f"    trigger → fired={trigger_sig is not None}  "
          f"score={trigger_sig.score:.3f}  sev={trigger_sig.severity}" if trigger_sig else
          "    trigger → NOT fired (see honest_results in JSON)")

    # NORMAL: organic staggered buying from active accounts, modest volume
    normal_accts = [f"ORG-{i}" for i in range(1, 5)]
    normal_orders = []
    for i, acct in enumerate(normal_accts):
        o = Order(
            id=_uid(), exchange_order_id=_uid(), account_id=acct,
            instrument_id=instr.id, side=OrderSide.BUY, status=OrderStatus.EXECUTED,
            price=3.15 + i * 0.02, quantity=200, filled_quantity=200,
            timestamp=BASE_T + timedelta(minutes=i * 8), exchange="NSE",
        )
        normal_orders.append(o)

    n_prior = {acct: BASE_T - timedelta(days=5) for acct in normal_accts}
    normal_sig = detect_coordinated_pump(
        normal_orders, instr, win_start, win_end,
        prior_trade_dates=n_prior, first_seen_dates=None,
    )
    normal_payload = {
        "meta": {"sample_data": True, "detector": "coordinated_pump", "scenario": "normal",
                 "import": "from app.detection.coordinated_pump import detect_coordinated_pump",
                 "call": "detect_coordinated_pump(...)"},
        "input": {"orders": [order_dict(o) for o in normal_orders],
                  "note": "4 active accounts, small volume, staggered by 8 minutes"},
        "output": {"fired": normal_sig is not None,
                   "score": getattr(normal_sig, "score", None),
                   "reason": "volume < VOLUME_SPIKE_MULTIPLE × normal OR < MIN_COORDINATING_ACCOUNTS"},
    }
    _save("coordinated_pump", "normal", normal_payload)
    print(f"    normal  → fired={normal_sig is not None}  (expected: False)")

    RESULTS["coordinated_pump"] = {
        "trigger_fired": trigger_sig is not None,
        "trigger_score": getattr(trigger_sig, "score", None),
        "trigger_severity": getattr(trigger_sig, "severity", None),
        "normal_fired": normal_sig is not None,
    }


# ═══════════════════════════════════════════════════════════════════════════════
# 4. OI MANIPULATION
# ═══════════════════════════════════════════════════════════════════════════════

def run_oi_manipulation() -> None:
    import pandas as pd
    from app.detection.oi_manipulation import detect_oi_concentration, OI_CONCENTRATION_THRESHOLD

    print("\n[4/6] oi_manipulation.py — detect_oi_concentration()")

    NOW = datetime(2024, 11, 28, 12, 0, 0)
    SPOT = 24_500.0
    EXPIRY = "2024-12-26"

    # TRIGGER: single PE strike at 24000 holds 42% of total PE chain OI (> 35% threshold)
    # total PE OI = 1_200_000, flagged strike OI = 504_000 = 42%
    strike_rows_trigger = [
        # CE chain (normal distribution)
        {"strike": 24000, "option_type": "CE", "oi": 80_000,  "expiry": EXPIRY, "underlying_value": SPOT},
        {"strike": 24200, "option_type": "CE", "oi": 120_000, "expiry": EXPIRY, "underlying_value": SPOT},
        {"strike": 24400, "option_type": "CE", "oi": 200_000, "expiry": EXPIRY, "underlying_value": SPOT},
        {"strike": 24600, "option_type": "CE", "oi": 180_000, "expiry": EXPIRY, "underlying_value": SPOT},
        {"strike": 24800, "option_type": "CE", "oi": 90_000,  "expiry": EXPIRY, "underlying_value": SPOT},
        # PE chain: 24000 PE holds 42% of total PE OI — abnormal concentration
        {"strike": 24000, "option_type": "PE", "oi": 504_000, "expiry": EXPIRY, "underlying_value": SPOT},
        {"strike": 24200, "option_type": "PE", "oi": 220_000, "expiry": EXPIRY, "underlying_value": SPOT},
        {"strike": 24400, "option_type": "PE", "oi": 180_000, "expiry": EXPIRY, "underlying_value": SPOT},
        {"strike": 24600, "option_type": "PE", "oi": 150_000, "expiry": EXPIRY, "underlying_value": SPOT},
        {"strike": 24800, "option_type": "PE", "oi": 146_000, "expiry": EXPIRY, "underlying_value": SPOT},
    ]
    chain_trigger = pd.DataFrame(strike_rows_trigger)

    trigger_sigs = detect_oi_concentration(chain_trigger, "NIFTY", "NSE", NOW)
    trigger_sig = next((s for s in trigger_sigs if s.option_type == "PE"), None) or (trigger_sigs[0] if trigger_sigs else None)

    def chain_row(r):
        return {"strike": r["strike"], "option_type": r["option_type"], "oi": r["oi"]}

    trigger_payload = {
        "meta": {"sample_data": True, "detector": "oi_manipulation", "scenario": "trigger",
                 "import": "from app.detection.oi_manipulation import detect_oi_concentration",
                 "call": "detect_oi_concentration(chain_df, symbol, exchange, snapshot_time)"},
        "input": {
            "symbol": "NIFTY", "exchange": "NSE",
            "snapshot_time": NOW.isoformat(),
            "underlying_spot": SPOT,
            "chain_rows": [chain_row(r) for r in strike_rows_trigger],
        },
        "output": None if trigger_sig is None else {
            "fired": True,
            "score": trigger_sig.score,
            "severity": trigger_sig.severity,
            "strike": trigger_sig.strike,
            "option_type": trigger_sig.option_type,
            "concentration_ratio": trigger_sig.concentration_ratio,
            "strike_oi": trigger_sig.strike_oi,
            "total_chain_oi": trigger_sig.total_chain_oi,
            "moneyness_pct": trigger_sig.moneyness_pct,
            "explanation": trigger_sig.explanation,
        },
    }
    if trigger_sig is None:
        trigger_payload["output"] = {"fired": False, "reason": "concentration below OI_CONCENTRATION_THRESHOLD"}
    _save("oi_manipulation", "trigger", trigger_payload)
    print(f"    trigger → fired={trigger_sig is not None}  "
          f"score={trigger_sig.score:.3f}  sev={trigger_sig.severity}" if trigger_sig else
          "    trigger → NOT fired")

    # NORMAL: balanced OI distribution across strikes, no single strike > 25%
    strike_rows_normal = [
        {"strike": 24200, "option_type": "PE", "oi": 300_000, "expiry": EXPIRY, "underlying_value": SPOT},
        {"strike": 24400, "option_type": "PE", "oi": 280_000, "expiry": EXPIRY, "underlying_value": SPOT},
        {"strike": 24600, "option_type": "PE", "oi": 260_000, "expiry": EXPIRY, "underlying_value": SPOT},
        {"strike": 24800, "option_type": "PE", "oi": 240_000, "expiry": EXPIRY, "underlying_value": SPOT},
        {"strike": 25000, "option_type": "PE", "oi": 220_000, "expiry": EXPIRY, "underlying_value": SPOT},
        {"strike": 24200, "option_type": "CE", "oi": 290_000, "expiry": EXPIRY, "underlying_value": SPOT},
        {"strike": 24400, "option_type": "CE", "oi": 310_000, "expiry": EXPIRY, "underlying_value": SPOT},
        {"strike": 24600, "option_type": "CE", "oi": 270_000, "expiry": EXPIRY, "underlying_value": SPOT},
        {"strike": 24800, "option_type": "CE", "oi": 230_000, "expiry": EXPIRY, "underlying_value": SPOT},
        {"strike": 25000, "option_type": "CE", "oi": 200_000, "expiry": EXPIRY, "underlying_value": SPOT},
    ]
    chain_normal = pd.DataFrame(strike_rows_normal)
    normal_sigs = detect_oi_concentration(chain_normal, "NIFTY", "NSE", NOW)
    normal_payload = {
        "meta": {"sample_data": True, "detector": "oi_manipulation", "scenario": "normal",
                 "import": "from app.detection.oi_manipulation import detect_oi_concentration",
                 "call": "detect_oi_concentration(chain_df, symbol, exchange, snapshot_time)"},
        "input": {"chain_rows": [chain_row(r) for r in strike_rows_normal],
                  "note": "balanced OI — max single strike share ~22%, below 35% threshold"},
        "output": {"fired": len(normal_sigs) > 0, "num_signals": len(normal_sigs),
                   "reason": "OI well-distributed; no strike exceeds OI_CONCENTRATION_THRESHOLD"},
    }
    _save("oi_manipulation", "normal", normal_payload)
    print(f"    normal  → fired={len(normal_sigs) > 0}  (expected: False)")

    RESULTS["oi_manipulation"] = {
        "trigger_fired": trigger_sig is not None,
        "trigger_score": getattr(trigger_sig, "score", None),
        "trigger_severity": getattr(trigger_sig, "severity", None),
        "normal_fired": len(normal_sigs) > 0,
    }


# ═══════════════════════════════════════════════════════════════════════════════
# 5. BASIS DISTORTION
# ═══════════════════════════════════════════════════════════════════════════════

def run_basis_distortion() -> None:
    from app.detection.basis_distortion import detect_basis_distortion, BASIS_DEVIATION_THRESHOLD, RISK_FREE_RATE

    print("\n[5/6] basis_distortion.py — detect_basis_distortion()")

    NOW = datetime(2024, 11, 28, 14, 30, 0)
    EXPIRY = date(2024, 11, 28) + timedelta(days=28)  # 28 DTE

    # TRIGGER: RELIANCE spot=3020, fair-value basis=3020*0.065*(28/365)=15.08
    # We set futures=3065 → actual_basis=45, deviation=45-15.08=29.92, dev_pct=0.0099 (>0.5%)
    trigger_sig = detect_basis_distortion(
        symbol="RELIANCE", exchange="NSE",
        spot_price=3020.00,
        futures_price=3065.00,
        expiry_date=EXPIRY,
        snapshot_time=NOW,
        risk_free_rate=RISK_FREE_RATE,
        basis_deviation_threshold=BASIS_DEVIATION_THRESHOLD,
    )

    trigger_payload = {
        "meta": {"sample_data": True, "detector": "basis_distortion", "scenario": "trigger",
                 "import": "from app.detection.basis_distortion import detect_basis_distortion",
                 "call": "detect_basis_distortion(symbol, exchange, spot_price, futures_price, expiry_date, snapshot_time)"},
        "input": {"symbol": "RELIANCE", "exchange": "NSE", "spot_price": 3020.00,
                  "futures_price": 3065.00, "expiry_date": EXPIRY.isoformat(),
                  "snapshot_time": NOW.isoformat(), "risk_free_rate": RISK_FREE_RATE},
        "output": None if trigger_sig is None else {
            "fired": True,
            "score": trigger_sig.score,
            "severity": trigger_sig.severity,
            "actual_basis": trigger_sig.actual_basis,
            "fair_value_basis": trigger_sig.fair_value_basis,
            "basis_deviation": trigger_sig.basis_deviation,
            "deviation_pct": trigger_sig.deviation_pct,
            "direction": trigger_sig.direction,
            "days_to_expiry": trigger_sig.days_to_expiry,
            "explanation": trigger_sig.explanation,
        },
    }
    if trigger_sig is None:
        trigger_payload["output"] = {"fired": False,
            "reason": "deviation below BASIS_DEVIATION_THRESHOLD — adjust spot/futures spread if needed"}
    _save("basis_distortion", "trigger", trigger_payload)
    print(f"    trigger → fired={trigger_sig is not None}  "
          f"score={trigger_sig.score:.3f}  sev={trigger_sig.severity}" if trigger_sig else
          "    trigger → NOT fired")

    # NORMAL: spot=3020, futures=3035.10 — very close to theoretical FV (3020*0.065*28/365=15.08)
    # actual_basis=15.10 ≈ FV → deviation ≈ 0.02 → dev_pct ≈ 0.00001 (well below 0.5%)
    normal_sig = detect_basis_distortion(
        symbol="RELIANCE", exchange="NSE",
        spot_price=3020.00, futures_price=3035.10,
        expiry_date=EXPIRY, snapshot_time=NOW,
        risk_free_rate=RISK_FREE_RATE,
        basis_deviation_threshold=BASIS_DEVIATION_THRESHOLD,
    )
    normal_payload = {
        "meta": {"sample_data": True, "detector": "basis_distortion", "scenario": "normal",
                 "import": "from app.detection.basis_distortion import detect_basis_distortion",
                 "call": "detect_basis_distortion(...)"},
        "input": {"spot_price": 3020.00, "futures_price": 3035.10,
                  "note": "futures near theoretical fair-value — normal basis"},
        "output": {"fired": normal_sig is not None,
                   "score": getattr(normal_sig, "score", None),
                   "reason": "basis within fair-value range; deviation < BASIS_DEVIATION_THRESHOLD"},
    }
    _save("basis_distortion", "normal", normal_payload)
    print(f"    normal  → fired={normal_sig is not None}  (expected: False)")

    RESULTS["basis_distortion"] = {
        "trigger_fired": trigger_sig is not None,
        "trigger_score": getattr(trigger_sig, "score", None),
        "trigger_severity": getattr(trigger_sig, "severity", None),
        "normal_fired": normal_sig is not None,
    }


# ═══════════════════════════════════════════════════════════════════════════════
# 6. OPTION PINNING
# ═══════════════════════════════════════════════════════════════════════════════

def run_option_pinning() -> None:
    import pandas as pd
    from app.detection.option_pinning import detect_option_pinning, PIN_DISTANCE_THRESHOLD, PIN_OI_DOMINANCE_THRESHOLD

    print("\n[6/6] option_pinning.py — detect_option_pinning()")

    # Pinning: expiry today (0 DTE), spot very close to the dominant OI strike
    EXPIRY_DT = date(2024, 11, 28)
    NOW = datetime(2024, 11, 28, 14, 45, 0)  # expiry day, afternoon
    SPOT = 24_497.0  # spot within 0.012% of 24500 strike

    # TRIGGER: 24500 CE+PE combined OI = 520,000 >> adjacent strikes ~80,000
    rows_trigger = [
        # ATM strike with massive OI (the pin candidate)
        {"strike": 24500, "option_type": "CE", "oi": 260_000},
        {"strike": 24500, "option_type": "PE", "oi": 260_000},
        # Adjacent strikes with much lower OI
        {"strike": 24400, "option_type": "CE", "oi": 60_000},
        {"strike": 24400, "option_type": "PE", "oi": 60_000},
        {"strike": 24600, "option_type": "CE", "oi": 55_000},
        {"strike": 24600, "option_type": "PE", "oi": 55_000},
        # Further OTM
        {"strike": 24300, "option_type": "CE", "oi": 30_000},
        {"strike": 24300, "option_type": "PE", "oi": 30_000},
        {"strike": 24700, "option_type": "CE", "oi": 25_000},
        {"strike": 24700, "option_type": "PE", "oi": 25_000},
    ]
    chain_trigger = pd.DataFrame(rows_trigger)

    trigger_sig = detect_option_pinning(
        chain_df=chain_trigger,
        symbol="NIFTY", exchange="NSE",
        spot_price=SPOT,
        expiry_date=EXPIRY_DT,
        snapshot_time=NOW,
    )

    def chain_row(r):
        return {"strike": r["strike"], "option_type": r["option_type"], "oi": r["oi"]}

    trigger_payload = {
        "meta": {"sample_data": True, "detector": "option_pinning", "scenario": "trigger",
                 "import": "from app.detection.option_pinning import detect_option_pinning",
                 "call": "detect_option_pinning(chain_df, symbol, exchange, spot_price, expiry_date, snapshot_time)"},
        "input": {"symbol": "NIFTY", "exchange": "NSE", "spot_price": SPOT,
                  "expiry_date": EXPIRY_DT.isoformat(), "snapshot_time": NOW.isoformat(),
                  "chain_rows": [chain_row(r) for r in rows_trigger]},
        "output": None if trigger_sig is None else {
            "fired": True,
            "score": trigger_sig.score,
            "severity": trigger_sig.severity,
            "pin_strike": trigger_sig.pin_strike,
            "distance_pct": trigger_sig.distance_pct,
            "pin_strike_total_oi": trigger_sig.pin_strike_total_oi,
            "adjacent_avg_oi": trigger_sig.adjacent_avg_oi,
            "oi_dominance_ratio": trigger_sig.oi_dominance_ratio,
            "days_to_expiry": trigger_sig.days_to_expiry,
            "max_pain_strike": trigger_sig.max_pain_strike,
            "explanation": trigger_sig.explanation,
        },
    }
    if trigger_sig is None:
        trigger_payload["output"] = {"fired": False,
            "reason": "PIN_EXPIRY_DAYS_THRESHOLD, PIN_DISTANCE_THRESHOLD, or PIN_OI_DOMINANCE_THRESHOLD not met"}
    _save("option_pinning", "trigger", trigger_payload)
    print(f"    trigger → fired={trigger_sig is not None}  "
          f"score={trigger_sig.score:.3f}  sev={trigger_sig.severity}" if trigger_sig else
          "    trigger → NOT fired")

    # NORMAL: 5 DTE (beyond threshold of 2), so detector returns None immediately
    NORMAL_DT = date(2024, 12, 3)  # 5 days out
    NOW_NORMAL = datetime(2024, 11, 28, 14, 45, 0)
    normal_sig = detect_option_pinning(
        chain_df=chain_trigger,  # same chain — detector gates on DTE first
        symbol="NIFTY", exchange="NSE",
        spot_price=SPOT,
        expiry_date=NORMAL_DT,
        snapshot_time=NOW_NORMAL,
    )
    normal_payload = {
        "meta": {"sample_data": True, "detector": "option_pinning", "scenario": "normal",
                 "import": "from app.detection.option_pinning import detect_option_pinning",
                 "call": "detect_option_pinning(...)"},
        "input": {"spot_price": SPOT, "expiry_date": NORMAL_DT.isoformat(),
                  "note": "5 DTE — beyond PIN_EXPIRY_DAYS_THRESHOLD=2"},
        "output": {"fired": normal_sig is not None,
                   "reason": "days_to_expiry > PIN_EXPIRY_DAYS_THRESHOLD → returns None immediately"},
    }
    _save("option_pinning", "normal", normal_payload)
    print(f"    normal  → fired={normal_sig is not None}  (expected: False)")

    RESULTS["option_pinning"] = {
        "trigger_fired": trigger_sig is not None,
        "trigger_score": getattr(trigger_sig, "score", None),
        "trigger_severity": getattr(trigger_sig, "severity", None),
        "normal_fired": normal_sig is not None,
    }


# ═══════════════════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    print("=" * 70)
    print("Sentinel — generate_all_detector_samples.py")
    print("Running REAL detector functions against synthetic sample data.")
    print("=" * 70)

    errors = {}

    for name, fn in [
        ("spoofing",          run_spoofing),
        ("circular_trading",  run_circular_trading),
        ("coordinated_pump",  run_coordinated_pump),
        ("oi_manipulation",   run_oi_manipulation),
        ("basis_distortion",  run_basis_distortion),
        ("option_pinning",    run_option_pinning),
    ]:
        try:
            fn()
        except Exception as exc:
            import traceback
            errors[name] = traceback.format_exc()
            print(f"    ERROR: {exc}")

    print("\n" + "=" * 70)
    print("RESULTS SUMMARY")
    print("=" * 70)
    print(f"{'Detector':<22} {'Trigger fired':<16} {'Score':<8} {'Severity':<10} {'Normal fired'}")
    print("-" * 70)
    for det, res in RESULTS.items():
        score_str = f"{res['trigger_score']:.3f}" if res['trigger_score'] is not None else "N/A"
        sev = res['trigger_severity'] or "N/A"
        print(f"{det:<22} {str(res['trigger_fired']):<16} {score_str:<8} {sev:<10} {res['normal_fired']}")

    if errors:
        print("\nERRORS (reported honestly — not hidden):")
        for det, tb in errors.items():
            print(f"\n  [{det}]:\n{tb}")

    print(f"\nFiles written to: {OUTPUT_DIR}")

    # Write master summary JSON for the demo to reference
    summary_path = OUTPUT_DIR / "_summary.json"
    summary_path.write_text(
        json.dumps({"generated_at": datetime.utcnow().isoformat() + "Z",
                    "sample_data": True,
                    "results": RESULTS,
                    "errors": {k: v[:200] for k, v in errors.items()}},
                   indent=2),
        encoding="utf-8",
    )
    print(f"Summary: {summary_path}")
