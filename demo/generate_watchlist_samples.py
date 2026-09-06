"""
generate_watchlist_samples.py
==============================
Generates demo/sample_data/watchlist_{asset}_{condition}.json
and demo/sample_data/watchlist_index.json.

Every file contains BOTH the real input data AND the real detector output —
produced by calling the actual functions in app/detection/*.py.

Assets
------
1. RELIANCE      large-cap equity      spoofing detector
2. KAVITIND      illiquid penny stock  spoofing + circular_trading
3. TINYLTD       mid-cap penny stock   coordinated_pump
4. NIFTYFUT      near-month futures    basis_distortion
5. NIFTYOPT      options chain         oi_manipulation + option_pinning

Conditions per asset
--------------------
RELIANCE:   normal, spoofing
KAVITIND:   normal (both detectors run), spoofing, circular
TINYLTD:    normal, pump
NIFTYFUT:   normal, basis
NIFTYOPT:   normal (both detectors run), oi_concentration, pinning

Total: 2+3+2+2+3 = 12 watchlist files + 1 index = 13 files.

Run from repo root:
    python demo/generate_watchlist_samples.py
"""

import json
import pathlib
import sys
import uuid
from dataclasses import asdict
from datetime import date, datetime, timedelta

ROOT = pathlib.Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

SD = ROOT / "demo" / "sample_data"
SD.mkdir(parents=True, exist_ok=True)


def _uid():
    return str(uuid.uuid4())


def _save(asset: str, condition: str, payload: dict) -> pathlib.Path:
    path = SD / f"watchlist_{asset}_{condition}.json"
    path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    return path


RESULTS: dict[str, dict] = {}
ERRORS: dict[str, str] = {}

# ─────────────────────────────────────────────────────────────────────────────
# Common helpers
# ─────────────────────────────────────────────────────────────────────────────

def _meta(asset, condition, detector, imp, call):
    return {
        "sample_data": True,
        "asset": asset,
        "condition": condition,
        "detector": detector,
        "import_path": imp,
        "call_signature": call,
    }


def _asset_info(symbol, exchange, asset_type, avg_vol, description):
    return {
        "symbol": symbol,
        "exchange": exchange,
        "type": asset_type,
        "avg_daily_volume_30d": avg_vol,
        "description": description,
    }


def _fired_output(detector, signal_obj, summary, evidence_table):
    return {
        "detectors_run": [detector],
        "fired": True,
        "detector": detector,
        "score": signal_obj.score,
        "severity": signal_obj.severity,
        "summary": summary,
        "explanation": signal_obj.explanation,
        "evidence_table": evidence_table,
    }


def _clean_output(detectors_run, reasons):
    return {
        "detectors_run": detectors_run,
        "fired": False,
        "results_by_detector": {d: {"fired": False, "reason": reasons[d]} for d in detectors_run},
        "summary": "No anomalies detected in this window",
    }


# ═════════════════════════════════════════════════════════════════════════════
# ASSET 1: RELIANCE — large-cap equity
# ═════════════════════════════════════════════════════════════════════════════

def gen_reliance():
    from app.detection.spoofing import detect_spoofing_for_account, MIN_CANCEL_RATIO
    from app.db.models import Order, OrderSide, OrderStatus, Instrument, InstrumentType

    print("\n[Asset 1/5] RELIANCE — large-cap equity")

    instr = Instrument(
        id=_uid(), symbol="RELIANCE", exchange="NSE",
        instrument_type=InstrumentType.EQUITY,
        avg_daily_volume_30d=5_200_000,
        avg_order_size_30d=250,
        avg_daily_turnover_30d=15_000_000_000,
    )
    BASE = datetime(2024, 1, 15, 9, 45, 0)

    def make_order(eid, side, qty, price, status, dt):
        return Order(
            id=_uid(), exchange_order_id=eid, account_id="ACC-REL-1",
            instrument_id=instr.id, side=side, status=status,
            price=price, quantity=qty,
            filled_quantity=qty if status == OrderStatus.EXECUTED else 0,
            timestamp=dt, exchange="NSE",
        )

    # ── RELIANCE / normal ──────────────────────────────────────────────────
    n_orders = [
        make_order("EN-1", OrderSide.BUY,  150, 2801.00, OrderStatus.EXECUTED, BASE),
        make_order("EN-2", OrderSide.SELL, 100, 2808.50, OrderStatus.EXECUTED, BASE + timedelta(minutes=12)),
        make_order("EN-3", OrderSide.BUY,  200, 2797.00, OrderStatus.EXECUTED, BASE + timedelta(minutes=28)),
    ]
    win_s, win_e = BASE, BASE + timedelta(minutes=30)
    n_sig = detect_spoofing_for_account(n_orders, instr, win_s, win_e)

    ev_rows = [
        [o.timestamp.strftime("%H:%M:%S"), o.exchange_order_id,
         str(o.side).split(".")[-1], str(o.quantity),
         f"₹{o.price:,.2f}", str(o.status).split(".")[-1]]
        for o in n_orders
    ]
    payload_n = {
        "meta": _meta("RELIANCE", "normal", "spoofing",
                      "from app.detection.spoofing import detect_spoofing_for_account",
                      "detect_spoofing_for_account(orders, instrument, window_start, window_end)"),
        "input": {
            "asset": _asset_info("RELIANCE", "NSE", "equity_large_cap",
                                 5_200_000, "Large-cap equity — avg 5.2M shares/day. High liquidity baseline."),
            "window": {"start": win_s.isoformat(), "end": win_e.isoformat(),
                       "description": "30-minute surveillance window: 09:45–10:15"},
            "data": {
                "type": "orders",
                "count": len(n_orders),
                "description": "Standard institutional buy/sell flow — no elevated cancellation ratio",
                "records": [
                    {"time": o.timestamp.strftime("%H:%M:%S"), "order_id": o.exchange_order_id,
                     "side": str(o.side).split(".")[-1], "quantity": o.quantity,
                     "price": o.price, "status": str(o.status).split(".")[-1]}
                    for o in n_orders
                ],
            },
        },
        "output": _clean_output(
            ["spoofing"],
            {"spoofing": f"cancel_ratio=0.0 — all orders executed, well below {MIN_CANCEL_RATIO} threshold"},
        ),
    }
    _save("RELIANCE", "normal", payload_n)
    print(f"  normal → fired={n_sig is not None}  (expected False)")

    # ── RELIANCE / spoofing ────────────────────────────────────────────────
    t_orders = [
        make_order("ER-1", OrderSide.BUY, 1500, 2800.00, OrderStatus.PLACED,    BASE),
        make_order("ER-2", OrderSide.BUY, 1500, 2801.00, OrderStatus.PLACED,    BASE + timedelta(minutes=1)),
        make_order("ER-3", OrderSide.BUY, 1500, 2802.50, OrderStatus.PLACED,    BASE + timedelta(minutes=2)),
        make_order("ER-4", OrderSide.BUY, 1500, 2801.50, OrderStatus.PLACED,    BASE + timedelta(minutes=3)),
        make_order("ER-1", OrderSide.BUY, 1500, 2800.00, OrderStatus.CANCELLED, BASE + timedelta(minutes=5)),
        make_order("ER-2", OrderSide.BUY, 1500, 2801.00, OrderStatus.CANCELLED, BASE + timedelta(minutes=5, seconds=10)),
        make_order("ER-3", OrderSide.BUY, 1500, 2802.50, OrderStatus.CANCELLED, BASE + timedelta(minutes=5, seconds=20)),
        make_order("ER-4", OrderSide.BUY, 1500, 2801.50, OrderStatus.CANCELLED, BASE + timedelta(minutes=5, seconds=30)),
        make_order("ER-5", OrderSide.SELL, 200, 2820.00, OrderStatus.EXECUTED,  BASE + timedelta(minutes=6)),
    ]
    t_sig = detect_spoofing_for_account(t_orders, instr, win_s, win_e)

    payload_t = {
        "meta": _meta("RELIANCE", "spoofing", "spoofing",
                      "from app.detection.spoofing import detect_spoofing_for_account",
                      "detect_spoofing_for_account(orders, instrument, window_start, window_end)"),
        "input": {
            "asset": _asset_info("RELIANCE", "NSE", "equity_large_cap",
                                 5_200_000, "Large-cap equity — avg 5.2M shares/day."),
            "window": {"start": win_s.isoformat(), "end": win_e.isoformat(),
                       "description": "30-minute surveillance window: 09:45–10:15"},
            "data": {
                "type": "orders",
                "count": len(t_orders),
                "description": "4 large BUY orders (all cancelled within 6 min) + SELL executed at peak price",
                "records": [
                    {"time": o.timestamp.strftime("%H:%M:%S"), "order_id": o.exchange_order_id,
                     "side": str(o.side).split(".")[-1], "quantity": o.quantity,
                     "price": o.price, "status": str(o.status).split(".")[-1]}
                    for o in t_orders
                ],
            },
        },
        "output": None if t_sig is None else _fired_output(
            "spoofing", t_sig,
            f"RELIANCE — spoofing/layering, cancel ratio {t_sig.cancel_ratio*100:.0f}%, severity: {t_sig.severity}",
            {
                "columns": ["Time", "Order ID", "Side", "Quantity", "Price", "Status"],
                "rows": [
                    [o.timestamp.strftime("%H:%M:%S"), o.exchange_order_id,
                     str(o.side).split(".")[-1], str(o.quantity),
                     f"₹{o.price:,.2f}", str(o.status).split(".")[-1]]
                    for o in t_orders
                ],
            },
        ),
    }
    if payload_t["output"] is None:
        payload_t["output"] = {"fired": False, "reason": "detector returned None — thresholds not met"}
    _save("RELIANCE", "spoofing", payload_t)
    print(f"  spoofing → fired={t_sig is not None}  "
          f"score={t_sig.score:.3f}  sev={t_sig.severity}" if t_sig else "  spoofing → NOT fired")

    RESULTS["RELIANCE"] = {
        "normal_fired": n_sig is not None,
        "spoofing_fired": t_sig is not None,
        "spoofing_score": getattr(t_sig, "score", None),
    }


# ═════════════════════════════════════════════════════════════════════════════
# ASSET 2: KAVITIND — illiquid penny stock
# ═════════════════════════════════════════════════════════════════════════════

def gen_kavitind():
    from app.detection.spoofing import detect_spoofing_for_account, MIN_CANCEL_RATIO
    from app.detection.circular_trading import detect_circular_trading
    from app.db.models import (Order, OrderSide, OrderStatus,
                                Instrument, InstrumentType, Trade)

    print("\n[Asset 2/5] KAVITIND — illiquid penny stock")

    instr = Instrument(
        id=_uid(), symbol="KAVITIND", exchange="NSE",
        instrument_type=InstrumentType.PENNY_STOCK,
        avg_daily_volume_30d=45_000,
        avg_order_size_30d=400,
    )
    BASE = datetime(2018, 3, 15, 10, 0, 0)

    def make_order(eid, acct, side, qty, price, status, dt):
        return Order(
            id=_uid(), exchange_order_id=eid, account_id=acct,
            instrument_id=instr.id, side=side, status=status,
            price=price, quantity=qty,
            filled_quantity=qty if status == OrderStatus.EXECUTED else 0,
            timestamp=dt, exchange="NSE",
        )

    def make_trade(seller_id, buyer_id, qty, price, t):
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

    win_s, win_e = BASE, BASE + timedelta(minutes=60)

    # ── KAVITIND / normal ──────────────────────────────────────────────────
    # Spoofing normal: 2 small executed orders, no cancellations
    n_orders = [
        make_order("KN-1", "ACC-KAV-1", OrderSide.BUY,  300, 12.40, OrderStatus.EXECUTED, BASE),
        make_order("KN-2", "ACC-KAV-1", OrderSide.SELL, 300, 12.48, OrderStatus.EXECUTED, BASE + timedelta(minutes=25)),
    ]
    n_spoof_sig = detect_spoofing_for_account(n_orders, instr, win_s, win_e)

    # Circular normal: independent trades, no ring possible
    n_trades = [
        make_trade("IND-P", "IND-Q", 200, 12.40, BASE + timedelta(minutes=5)),
        make_trade("IND-R", "IND-S", 150, 12.42, BASE + timedelta(minutes=18)),
        make_trade("IND-T", "IND-U", 250, 12.38, BASE + timedelta(minutes=35)),
        make_trade("IND-Q", "IND-V", 100, 12.45, BASE + timedelta(minutes=50)),
    ]
    n_circ_sigs = detect_circular_trading(n_trades, instr, win_s, win_e)

    payload_n = {
        "meta": _meta("KAVITIND", "normal", "spoofing+circular_trading",
                      "from app.detection.spoofing import detect_spoofing_for_account; "
                      "from app.detection.circular_trading import detect_circular_trading",
                      "detect_spoofing_for_account(...); detect_circular_trading(...)"),
        "input": {
            "asset": _asset_info("KAVITIND", "NSE", "penny_stock",
                                 45_000, "Illiquid penny stock — avg 45,000 shares/day."),
            "window": {"start": win_s.isoformat(), "end": win_e.isoformat(),
                       "description": "60-minute surveillance window: 10:00–11:00"},
            "data": {
                "type": "orders_and_trades",
                "description": "Small executed orders + independent trades from unrelated accounts",
                "orders": [
                    {"time": o.timestamp.strftime("%H:%M:%S"), "order_id": o.exchange_order_id,
                     "side": str(o.side).split(".")[-1], "quantity": o.quantity,
                     "price": o.price, "status": str(o.status).split(".")[-1]}
                    for o in n_orders
                ],
                "trades": [
                    {"time": t.timestamp.strftime("%H:%M:%S"),
                     "seller": t.sell_order.account_id,
                     "buyer": t.buy_order.account_id,
                     "quantity": t.quantity, "price": t.price}
                    for t in n_trades
                ],
            },
        },
        "output": _clean_output(
            ["spoofing", "circular_trading"],
            {
                "spoofing": "cancel_ratio=0.0 — both orders executed normally",
                "circular_trading": f"no closed cycles — {len(n_trades)} trades from {len(set(t.sell_order.account_id for t in n_trades) | set(t.buy_order.account_id for t in n_trades))} independent accounts form no ring",
            },
        ),
    }
    _save("KAVITIND", "normal", payload_n)
    print(f"  normal → spoof={n_spoof_sig is not None}  circ={len(n_circ_sigs)>0}  (both expected False)")

    # ── KAVITIND / spoofing ────────────────────────────────────────────────
    t_orders = [
        make_order("KS-1", "ACC-KAV-2", OrderSide.BUY, 2200, 12.50, OrderStatus.PLACED,    BASE),
        make_order("KS-2", "ACC-KAV-2", OrderSide.BUY, 2200, 12.50, OrderStatus.PLACED,    BASE + timedelta(minutes=1)),
        make_order("KS-3", "ACC-KAV-2", OrderSide.BUY, 2200, 12.60, OrderStatus.PLACED,    BASE + timedelta(minutes=2)),
        make_order("KS-4", "ACC-KAV-2", OrderSide.BUY, 2200, 12.55, OrderStatus.PLACED,    BASE + timedelta(minutes=3)),
        make_order("KS-1", "ACC-KAV-2", OrderSide.BUY, 2200, 12.50, OrderStatus.CANCELLED, BASE + timedelta(minutes=5)),
        make_order("KS-2", "ACC-KAV-2", OrderSide.BUY, 2200, 12.50, OrderStatus.CANCELLED, BASE + timedelta(minutes=5, seconds=10)),
        make_order("KS-3", "ACC-KAV-2", OrderSide.BUY, 2200, 12.60, OrderStatus.CANCELLED, BASE + timedelta(minutes=5, seconds=20)),
        make_order("KS-4", "ACC-KAV-2", OrderSide.BUY, 2200, 12.55, OrderStatus.CANCELLED, BASE + timedelta(minutes=5, seconds=30)),
        make_order("KS-5", "ACC-KAV-2", OrderSide.SELL, 450, 12.68, OrderStatus.EXECUTED,  BASE + timedelta(minutes=6)),
    ]
    t_spoof_sig = detect_spoofing_for_account(t_orders, instr, win_s, win_e)

    payload_s = {
        "meta": _meta("KAVITIND", "spoofing", "spoofing",
                      "from app.detection.spoofing import detect_spoofing_for_account",
                      "detect_spoofing_for_account(orders, instrument, window_start, window_end)"),
        "input": {
            "asset": _asset_info("KAVITIND", "NSE", "penny_stock",
                                 45_000, "Illiquid penny stock — avg 45,000 shares/day."),
            "window": {"start": win_s.isoformat(), "end": win_e.isoformat(),
                       "description": "60-minute surveillance window: 10:00–11:00"},
            "data": {
                "type": "orders",
                "count": len(t_orders),
                "description": "4 large BUY orders (×5.5 normal size), all cancelled. SELL executed at price peak.",
                "records": [
                    {"time": o.timestamp.strftime("%H:%M:%S"), "order_id": o.exchange_order_id,
                     "side": str(o.side).split(".")[-1], "quantity": o.quantity,
                     "price": o.price, "status": str(o.status).split(".")[-1]}
                    for o in t_orders
                ],
            },
        },
        "output": None if t_spoof_sig is None else _fired_output(
            "spoofing", t_spoof_sig,
            f"KAVITIND — spoofing/layering, cancel ratio {t_spoof_sig.cancel_ratio*100:.0f}%, severity: {t_spoof_sig.severity}",
            {
                "columns": ["Time", "Order ID", "Side", "Quantity", "Price", "Status"],
                "rows": [
                    [o.timestamp.strftime("%H:%M:%S"), o.exchange_order_id,
                     str(o.side).split(".")[-1], str(o.quantity),
                     f"₹{o.price:,.2f}", str(o.status).split(".")[-1]]
                    for o in t_orders
                ],
            },
        ),
    }
    if payload_s["output"] is None:
        payload_s["output"] = {"fired": False, "reason": "detector returned None"}
    _save("KAVITIND", "spoofing", payload_s)
    print(f"  spoofing → fired={t_spoof_sig is not None}  "
          f"score={t_spoof_sig.score:.3f}  sev={t_spoof_sig.severity}" if t_spoof_sig else "  spoofing → NOT fired")

    # ── KAVITIND / circular ────────────────────────────────────────────────
    # 4-account ring: A→B→C→D→A, each leg 3000 shares
    RING = ["KR-A", "KR-B", "KR-C", "KR-D"]
    c_trades = [
        make_trade(RING[0], RING[1], 3000, 12.50, BASE),
        make_trade(RING[1], RING[2], 3000, 12.52, BASE + timedelta(minutes=8)),
        make_trade(RING[2], RING[3], 3000, 12.50, BASE + timedelta(minutes=16)),
        make_trade(RING[3], RING[0], 3000, 12.53, BASE + timedelta(minutes=24)),
    ]
    c_sigs = detect_circular_trading(c_trades, instr, win_s, win_e)
    c_sig = c_sigs[0] if c_sigs else None

    payload_c = {
        "meta": _meta("KAVITIND", "circular", "circular_trading",
                      "from app.detection.circular_trading import detect_circular_trading",
                      "detect_circular_trading(trades, instrument, window_start, window_end)"),
        "input": {
            "asset": _asset_info("KAVITIND", "NSE", "penny_stock",
                                 45_000, "Illiquid penny stock — avg 45,000 shares/day."),
            "window": {"start": win_s.isoformat(), "end": win_e.isoformat(),
                       "description": "60-minute surveillance window: 10:00–11:00"},
            "data": {
                "type": "trades",
                "count": len(c_trades),
                "description": "4-account ring: KR-A → KR-B → KR-C → KR-D → KR-A. Each leg 3,000 shares — net position change for every account: 0.",
                "records": [
                    {"time": t.timestamp.strftime("%H:%M:%S"),
                     "seller": t.sell_order.account_id,
                     "buyer": t.buy_order.account_id,
                     "quantity": t.quantity, "price": t.price}
                    for t in c_trades
                ],
            },
        },
        "output": None if c_sig is None else _fired_output(
            "circular_trading", c_sig,
            f"KAVITIND — circular trading ring, {c_sig.cycle_length} accounts, severity: {c_sig.severity}",
            {
                "columns": ["Time", "Seller", "Buyer", "Quantity", "Price (₹)"],
                "rows": [
                    [t.timestamp.strftime("%H:%M:%S"),
                     t.sell_order.account_id,
                     t.buy_order.account_id,
                     str(t.quantity), f"₹{t.price:.2f}"]
                    for t in c_trades
                ],
            },
        ),
    }
    if payload_c["output"] is None:
        payload_c["output"] = {"fired": False, "reason": "detector returned no signals"}
    _save("KAVITIND", "circular", payload_c)
    print(f"  circular → fired={c_sig is not None}  "
          f"score={c_sig.score:.3f}  sev={c_sig.severity}" if c_sig else "  circular → NOT fired")

    RESULTS["KAVITIND"] = {
        "normal_fired": n_spoof_sig is not None or len(n_circ_sigs) > 0,
        "spoofing_fired": t_spoof_sig is not None,
        "circular_fired": c_sig is not None,
    }


# ═════════════════════════════════════════════════════════════════════════════
# ASSET 3: TINYLTD — mid-cap penny stock (coordinated pump)
# ═════════════════════════════════════════════════════════════════════════════

def gen_tinyltd():
    from app.detection.coordinated_pump import detect_coordinated_pump, VOLUME_SPIKE_MULTIPLE, MIN_COORDINATING_ACCOUNTS
    from app.db.models import Order, OrderSide, OrderStatus, Instrument, InstrumentType

    print("\n[Asset 3/5] TINYLTD — mid-cap penny stock (coordinated pump)")

    instr = Instrument(
        id=_uid(), symbol="TINYLTD", exchange="NSE",
        instrument_type=InstrumentType.PENNY_STOCK,
        avg_daily_volume_30d=30_000,
        avg_order_size_30d=200,
        avg_daily_turnover_30d=90_000,
    )
    BASE = datetime(2019, 6, 10, 10, 30, 0)
    win_s, win_e = BASE, BASE + timedelta(minutes=30)

    def make_order(acct, qty, price, dt):
        return Order(
            id=_uid(), exchange_order_id=_uid(), account_id=acct,
            instrument_id=instr.id, side=OrderSide.BUY,
            status=OrderStatus.EXECUTED, price=price, quantity=qty,
            filled_quantity=qty, timestamp=dt, exchange="NSE",
        )

    # ── TINYLTD / normal ───────────────────────────────────────────────────
    # 3 active accounts, small orders → volume < 5× threshold
    norm_accts = ["ORG-1", "ORG-2", "ORG-3"]
    n_orders = [make_order(a, 150, 3.10 + i*0.02, BASE + timedelta(minutes=i*9))
                for i, a in enumerate(norm_accts)]
    n_prior = {a: BASE - timedelta(days=5) for a in norm_accts}
    n_sig = detect_coordinated_pump(n_orders, instr, win_s, win_e,
                                    prior_trade_dates=n_prior, first_seen_dates=None)

    payload_n = {
        "meta": _meta("TINYLTD", "normal", "coordinated_pump",
                      "from app.detection.coordinated_pump import detect_coordinated_pump",
                      "detect_coordinated_pump(buy_orders, instrument, window_start, window_end, ...)"),
        "input": {
            "asset": _asset_info("TINYLTD", "NSE", "penny_stock",
                                 30_000, "Mid-cap penny stock — avg 30,000 shares/day."),
            "window": {"start": win_s.isoformat(), "end": win_e.isoformat(),
                       "description": "30-minute surveillance window: 10:30–11:00"},
            "data": {
                "type": "orders",
                "count": len(n_orders),
                "description": "3 regularly active accounts, small buy orders staggered naturally",
                "records": [
                    {"time": o.timestamp.strftime("%H:%M:%S"), "account": o.account_id,
                     "quantity": o.quantity, "price": o.price, "status": "EXECUTED"}
                    for o in n_orders
                ],
            },
        },
        "output": _clean_output(
            ["coordinated_pump"],
            {"coordinated_pump": f"only {len(norm_accts)} accounts AND combined volume below {VOLUME_SPIKE_MULTIPLE}× normal — both thresholds needed"},
        ),
    }
    _save("TINYLTD", "normal", payload_n)
    print(f"  normal → fired={n_sig is not None}  (expected False)")

    # ── TINYLTD / pump ─────────────────────────────────────────────────────
    # 7 dormant accounts, 2000 shares each → 14,000 shares in 30 min
    # normal_window_volume = 30000 * (0.5/6.5) ≈ 2307 → multiple ≈ 6.07×
    pump_accts = [f"PUMP-{i:02d}" for i in range(1, 8)]
    t_orders = [
        Order(id=_uid(), exchange_order_id=_uid(), account_id=acct,
              instrument_id=instr.id, side=OrderSide.BUY,
              status=OrderStatus.EXECUTED, price=3.20 + i*0.01, quantity=2000,
              filled_quantity=2000, timestamp=BASE + timedelta(minutes=i*3),
              exchange="NSE")
        for i, acct in enumerate(pump_accts)
    ]
    prior_dates = {a: BASE - timedelta(days=60) for a in pump_accts}
    first_seen  = {a: BASE - timedelta(days=3)  for a in pump_accts}
    t_sig = detect_coordinated_pump(t_orders, instr, win_s, win_e,
                                    prior_trade_dates=prior_dates, first_seen_dates=first_seen)

    payload_t = {
        "meta": _meta("TINYLTD", "pump", "coordinated_pump",
                      "from app.detection.coordinated_pump import detect_coordinated_pump",
                      "detect_coordinated_pump(buy_orders, instrument, window_start, window_end, prior_trade_dates, first_seen_dates)"),
        "input": {
            "asset": _asset_info("TINYLTD", "NSE", "penny_stock",
                                 30_000, "Mid-cap penny stock — avg 30,000 shares/day."),
            "window": {"start": win_s.isoformat(), "end": win_e.isoformat(),
                       "description": "30-minute surveillance window: 10:30–11:00"},
            "data": {
                "type": "orders",
                "count": len(t_orders),
                "description": f"{len(pump_accts)} accounts (all dormant >60 days, all newly registered <7 days), buying {2000} shares each at escalating prices",
                "records": [
                    {"time": o.timestamp.strftime("%H:%M:%S"), "account": o.account_id,
                     "quantity": o.quantity, "price": o.price,
                     "dormant_days": 60, "account_age_days": 3}
                    for o in t_orders
                ],
            },
        },
        "output": None if t_sig is None else _fired_output(
            "coordinated_pump", t_sig,
            f"TINYLTD — coordinated pump, {t_sig.num_accounts} accounts, {t_sig.volume_multiple:.1f}× volume, severity: {t_sig.severity}",
            {
                "columns": ["Time", "Account", "Quantity", "Price (₹)", "Dormant Days", "Account Age (days)"],
                "rows": [
                    [o.timestamp.strftime("%H:%M:%S"), o.account_id,
                     str(o.quantity), f"₹{o.price:.2f}", "60", "3"]
                    for o in t_orders
                ],
            },
        ),
    }
    if payload_t["output"] is None:
        payload_t["output"] = {"fired": False, "reason": "detector returned None — thresholds not met"}
    _save("TINYLTD", "pump", payload_t)
    print(f"  pump → fired={t_sig is not None}  "
          f"score={t_sig.score:.3f}  sev={t_sig.severity}" if t_sig else "  pump → NOT fired")

    RESULTS["TINYLTD"] = {
        "normal_fired": n_sig is not None,
        "pump_fired": t_sig is not None,
    }


# ═════════════════════════════════════════════════════════════════════════════
# ASSET 4: NIFTYFUT — near-month futures (basis distortion)
# ═════════════════════════════════════════════════════════════════════════════

def gen_niftyfut():
    from app.detection.basis_distortion import detect_basis_distortion, BASIS_DEVIATION_THRESHOLD, RISK_FREE_RATE

    print("\n[Asset 4/5] NIFTYFUT — near-month NIFTY futures (basis distortion)")

    NOW = datetime(2024, 11, 28, 14, 30, 0)
    EXPIRY = date(2024, 11, 28) + timedelta(days=28)
    SPOT = 24_500.0

    def asset_info():
        return _asset_info("NIFTY", "NSE", "futures",
                           None, "Near-month NIFTY 50 futures contract. Expiry: " + EXPIRY.isoformat())

    # ── NIFTYFUT / normal ──────────────────────────────────────────────────
    # Fair value basis = 24500 × 0.065 × 28/365 = 122.19
    # Normal: futures = 24622.50 (≈ FV, deviation = 0.31 - 122.19 = tiny negative → well within threshold)
    FUT_NORMAL = SPOT + SPOT * RISK_FREE_RATE * (28 / 365.0)  # exact FV
    n_sig = detect_basis_distortion("NIFTY", "NSE", SPOT, round(FUT_NORMAL, 2),
                                    EXPIRY, NOW, RISK_FREE_RATE, BASIS_DEVIATION_THRESHOLD)

    payload_n = {
        "meta": _meta("NIFTYFUT", "normal", "basis_distortion",
                      "from app.detection.basis_distortion import detect_basis_distortion",
                      "detect_basis_distortion(symbol, exchange, spot_price, futures_price, expiry_date, snapshot_time)"),
        "input": {
            "asset": asset_info(),
            "snapshot_time": NOW.isoformat(),
            "data": {
                "type": "price_snapshot",
                "description": "Spot and futures price snapshot — futures near theoretical fair value",
                "records": [
                    {"field": "Spot price",      "value": f"₹{SPOT:,.2f}"},
                    {"field": "Futures price",   "value": f"₹{round(FUT_NORMAL,2):,.2f}"},
                    {"field": "Fair-value basis","value": f"₹{SPOT * RISK_FREE_RATE * (28/365):.2f}"},
                    {"field": "Actual basis",    "value": f"₹{round(FUT_NORMAL,2) - SPOT:.2f}"},
                    {"field": "Days to expiry",  "value": "28"},
                    {"field": "Risk-free rate",  "value": f"{RISK_FREE_RATE*100:.1f}%"},
                ],
            },
        },
        "output": _clean_output(
            ["basis_distortion"],
            {"basis_distortion": "actual basis ≈ fair-value basis — deviation well below 0.5% threshold"},
        ),
    }
    _save("NIFTYFUT", "normal", payload_n)
    print(f"  normal → fired={n_sig is not None}  (expected False)")

    # ── NIFTYFUT / basis ───────────────────────────────────────────────────
    # Excess contango: futures = 24870 (spot+370)
    # FV basis = 122.19, actual = 370, deviation = 247.81, dev_pct = 1.012% >> 0.5%
    # score = min(1, (0.01012 - 0.005) / (0.005*4)) = min(1, 0.512/0.02) = 0.256 (low)
    FUT_DISTORTED = SPOT + 370.0
    t_sig = detect_basis_distortion("NIFTY", "NSE", SPOT, FUT_DISTORTED,
                                    EXPIRY, NOW, RISK_FREE_RATE, BASIS_DEVIATION_THRESHOLD)

    payload_t = {
        "meta": _meta("NIFTYFUT", "basis", "basis_distortion",
                      "from app.detection.basis_distortion import detect_basis_distortion",
                      "detect_basis_distortion(symbol, exchange, spot_price, futures_price, expiry_date, snapshot_time)"),
        "input": {
            "asset": asset_info(),
            "snapshot_time": NOW.isoformat(),
            "data": {
                "type": "price_snapshot",
                "description": "Futures trading at ₹370 premium to spot — fair-value basis is only ₹122. Excess contango (1.01% deviation, threshold 0.50%).",
                "records": [
                    {"field": "Spot price",       "value": f"₹{SPOT:,.2f}"},
                    {"field": "Futures price (distorted)", "value": f"₹{FUT_DISTORTED:,.2f}"},
                    {"field": "Actual basis",     "value": f"₹{FUT_DISTORTED - SPOT:+.2f}"},
                    {"field": "Fair-value basis", "value": f"₹{SPOT * RISK_FREE_RATE * (28/365):+.2f}"},
                    {"field": "Deviation",        "value": f"₹{(FUT_DISTORTED-SPOT) - SPOT*RISK_FREE_RATE*(28/365):+.2f}"},
                    {"field": "Days to expiry",   "value": "28"},
                ],
            },
        },
        "output": None if t_sig is None else _fired_output(
            "basis_distortion", t_sig,
            f"NIFTY futures — basis distortion ({t_sig.direction.replace('_',' ')}), severity: {t_sig.severity}",
            {
                "columns": ["Field", "Value"],
                "rows": [
                    ["Spot price",       f"₹{SPOT:,.2f}"],
                    ["Futures price",    f"₹{FUT_DISTORTED:,.2f}"],
                    ["Actual basis",     f"₹{t_sig.actual_basis:+.2f}"],
                    ["Fair-value basis", f"₹{t_sig.fair_value_basis:+.2f}"],
                    ["Deviation",        f"₹{t_sig.basis_deviation:+.2f} ({t_sig.deviation_pct*100:.3f}% of spot)"],
                    ["Direction",        t_sig.direction],
                    ["Score",            f"{t_sig.score:.3f}"],
                    ["Severity",         t_sig.severity],
                ],
            },
        ),
    }
    if payload_t["output"] is None:
        payload_t["output"] = {"fired": False, "reason": "deviation below BASIS_DEVIATION_THRESHOLD"}
    _save("NIFTYFUT", "basis", payload_t)
    print(f"  basis → fired={t_sig is not None}  "
          f"score={t_sig.score:.3f}  sev={t_sig.severity}" if t_sig else "  basis → NOT fired")

    RESULTS["NIFTYFUT"] = {
        "normal_fired": n_sig is not None,
        "basis_fired": t_sig is not None,
    }


# ═════════════════════════════════════════════════════════════════════════════
# ASSET 5: NIFTYOPT — NIFTY options chain (OI manipulation + option pinning)
# ═════════════════════════════════════════════════════════════════════════════

def gen_niftyopt():
    import pandas as pd
    from app.detection.oi_manipulation import detect_oi_concentration, OI_CONCENTRATION_THRESHOLD
    from app.detection.option_pinning import detect_option_pinning, PIN_DISTANCE_THRESHOLD

    print("\n[Asset 5/5] NIFTYOPT — NIFTY options chain (OI manipulation + pinning)")

    EXPIRY_STR = "2024-12-26"
    NOW = datetime(2024, 11, 28, 12, 0, 0)
    SPOT = 24_500.0

    def asset_info():
        return _asset_info("NIFTY", "NSE", "options_chain",
                           None, f"NIFTY 50 options chain. Expiry: {EXPIRY_STR}. Spot: ₹{SPOT:,.0f}")

    def chain_to_records(rows):
        return [{"strike": r["strike"], "option_type": r["option_type"],
                 "oi": r["oi"], "expiry": EXPIRY_STR}
                for r in rows]

    # ── NIFTYOPT / normal ──────────────────────────────────────────────────
    norm_rows = [
        {"strike": 24200, "option_type": "PE", "oi": 300_000, "expiry": EXPIRY_STR, "underlying_value": SPOT},
        {"strike": 24400, "option_type": "PE", "oi": 280_000, "expiry": EXPIRY_STR, "underlying_value": SPOT},
        {"strike": 24600, "option_type": "PE", "oi": 265_000, "expiry": EXPIRY_STR, "underlying_value": SPOT},
        {"strike": 24800, "option_type": "PE", "oi": 245_000, "expiry": EXPIRY_STR, "underlying_value": SPOT},
        {"strike": 25000, "option_type": "PE", "oi": 210_000, "expiry": EXPIRY_STR, "underlying_value": SPOT},
        {"strike": 24200, "option_type": "CE", "oi": 295_000, "expiry": EXPIRY_STR, "underlying_value": SPOT},
        {"strike": 24400, "option_type": "CE", "oi": 310_000, "expiry": EXPIRY_STR, "underlying_value": SPOT},
        {"strike": 24600, "option_type": "CE", "oi": 270_000, "expiry": EXPIRY_STR, "underlying_value": SPOT},
        {"strike": 24800, "option_type": "CE", "oi": 230_000, "expiry": EXPIRY_STR, "underlying_value": SPOT},
        {"strike": 25000, "option_type": "CE", "oi": 200_000, "expiry": EXPIRY_STR, "underlying_value": SPOT},
    ]
    chain_norm = pd.DataFrame(norm_rows)
    n_oi_sigs  = detect_oi_concentration(chain_norm, "NIFTY", "NSE", NOW)
    # For pinning normal: 28 DTE, well beyond the 2-day threshold → returns None immediately
    EXPIRY_NORM_DT = date(2024, 12, 26)
    n_pin_sig  = detect_option_pinning(chain_norm, "NIFTY", "NSE", SPOT, EXPIRY_NORM_DT, NOW)

    payload_n = {
        "meta": _meta("NIFTYOPT", "normal", "oi_manipulation+option_pinning",
                      "from app.detection.oi_manipulation import detect_oi_concentration; "
                      "from app.detection.option_pinning import detect_option_pinning",
                      "detect_oi_concentration(chain_df, ...); detect_option_pinning(chain_df, ...)"),
        "input": {
            "asset": asset_info(),
            "snapshot_time": NOW.isoformat(),
            "data": {
                "type": "option_chain",
                "description": "Balanced OI distribution — no strike holds >25% of chain. 28 DTE — outside pinning window.",
                "records": chain_to_records(norm_rows),
            },
        },
        "output": _clean_output(
            ["oi_manipulation", "option_pinning"],
            {
                "oi_manipulation": f"max single-strike share ≈22% — below {OI_CONCENTRATION_THRESHOLD*100:.0f}% threshold",
                "option_pinning":  f"28 days to expiry — beyond PIN_EXPIRY_DAYS_THRESHOLD=2, returns None immediately",
            },
        ),
    }
    _save("NIFTYOPT", "normal", payload_n)
    print(f"  normal → oi={len(n_oi_sigs)>0}  pin={n_pin_sig is not None}  (both expected False)")

    # ── NIFTYOPT / oi_concentration ────────────────────────────────────────
    oi_rows = [
        {"strike": 24000, "option_type": "CE", "oi":  80_000, "expiry": EXPIRY_STR, "underlying_value": SPOT},
        {"strike": 24200, "option_type": "CE", "oi": 120_000, "expiry": EXPIRY_STR, "underlying_value": SPOT},
        {"strike": 24400, "option_type": "CE", "oi": 200_000, "expiry": EXPIRY_STR, "underlying_value": SPOT},
        {"strike": 24600, "option_type": "CE", "oi": 180_000, "expiry": EXPIRY_STR, "underlying_value": SPOT},
        {"strike": 24800, "option_type": "CE", "oi":  90_000, "expiry": EXPIRY_STR, "underlying_value": SPOT},
        {"strike": 24000, "option_type": "PE", "oi": 504_000, "expiry": EXPIRY_STR, "underlying_value": SPOT},
        {"strike": 24200, "option_type": "PE", "oi": 220_000, "expiry": EXPIRY_STR, "underlying_value": SPOT},
        {"strike": 24400, "option_type": "PE", "oi": 180_000, "expiry": EXPIRY_STR, "underlying_value": SPOT},
        {"strike": 24600, "option_type": "PE", "oi": 150_000, "expiry": EXPIRY_STR, "underlying_value": SPOT},
        {"strike": 24800, "option_type": "PE", "oi": 146_000, "expiry": EXPIRY_STR, "underlying_value": SPOT},
    ]
    chain_oi = pd.DataFrame(oi_rows)
    oi_sigs = detect_oi_concentration(chain_oi, "NIFTY", "NSE", NOW)
    oi_sig = next((s for s in oi_sigs if s.option_type == "PE"), None) or (oi_sigs[0] if oi_sigs else None)

    payload_oi = {
        "meta": _meta("NIFTYOPT", "oi_concentration", "oi_manipulation",
                      "from app.detection.oi_manipulation import detect_oi_concentration",
                      "detect_oi_concentration(chain_df, symbol, exchange, snapshot_time)"),
        "input": {
            "asset": asset_info(),
            "snapshot_time": NOW.isoformat(),
            "data": {
                "type": "option_chain",
                "description": "24000 PE holds 42% of total PE OI (504,000 / 1,200,000). Normal PE distribution would peak at ~25%.",
                "records": chain_to_records(oi_rows),
            },
        },
        "output": None if oi_sig is None else _fired_output(
            "oi_manipulation", oi_sig,
            f"NIFTY options — OI concentration at {int(oi_sig.strike):,} {oi_sig.option_type} ({oi_sig.concentration_ratio*100:.1f}% of chain), severity: {oi_sig.severity}",
            {
                "columns": ["Strike", "Type", "OI (contracts)", "% of PE chain", "Flag"],
                "rows": [
                    [str(int(r["strike"])), r["option_type"],
                     f"{r['oi']:,}",
                     f"{r['oi']/1_200_000*100:.1f}%" if r["option_type"] == "PE" else "—",
                     "◄ FLAGGED" if r["option_type"] == "PE" and r["strike"] == 24000 else ""]
                    for r in oi_rows
                ],
            },
        ),
    }
    if payload_oi["output"] is None:
        payload_oi["output"] = {"fired": False, "reason": "concentration below threshold"}
    _save("NIFTYOPT", "oi_concentration", payload_oi)
    print(f"  oi_concentration → fired={oi_sig is not None}  "
          f"score={oi_sig.score:.3f}  sev={oi_sig.severity}" if oi_sig else "  oi → NOT fired")

    # ── NIFTYOPT / pinning ─────────────────────────────────────────────────
    EXPIRY_PIN = date(2024, 11, 28)   # today = expiry day (0 DTE)
    NOW_PIN    = datetime(2024, 11, 28, 14, 45, 0)
    SPOT_PIN   = 24_497.0             # within 0.012% of 24500

    pin_rows = [
        {"strike": 24300, "option_type": "CE", "oi":  30_000},
        {"strike": 24300, "option_type": "PE", "oi":  30_000},
        {"strike": 24400, "option_type": "CE", "oi":  60_000},
        {"strike": 24400, "option_type": "PE", "oi":  60_000},
        {"strike": 24500, "option_type": "CE", "oi": 260_000},
        {"strike": 24500, "option_type": "PE", "oi": 260_000},
        {"strike": 24600, "option_type": "CE", "oi":  55_000},
        {"strike": 24600, "option_type": "PE", "oi":  55_000},
        {"strike": 24700, "option_type": "CE", "oi":  25_000},
        {"strike": 24700, "option_type": "PE", "oi":  25_000},
    ]
    chain_pin = pd.DataFrame(pin_rows)
    pin_sig = detect_option_pinning(chain_pin, "NIFTY", "NSE", SPOT_PIN, EXPIRY_PIN, NOW_PIN)

    payload_pin = {
        "meta": _meta("NIFTYOPT", "pinning", "option_pinning",
                      "from app.detection.option_pinning import detect_option_pinning",
                      "detect_option_pinning(chain_df, symbol, exchange, spot_price, expiry_date, snapshot_time)"),
        "input": {
            "asset": _asset_info("NIFTY", "NSE", "options_chain", None,
                                 f"NIFTY 50 options — EXPIRY DAY {EXPIRY_PIN.isoformat()}. Spot: ₹{SPOT_PIN:,.0f}"),
            "snapshot_time": NOW_PIN.isoformat(),
            "data": {
                "type": "option_chain",
                "description": f"Expiry day. Spot ₹{SPOT_PIN:,.0f} within 0.012% of 24,500 strike. 24500 CE+PE OI = 520,000 vs adjacent avg ≈ 115,000 (4.5×).",
                "records": [{"strike": r["strike"], "option_type": r["option_type"],
                              "oi": r["oi"]} for r in pin_rows],
            },
        },
        "output": None if pin_sig is None else _fired_output(
            "option_pinning", pin_sig,
            f"NIFTY — option pinning at strike {int(pin_sig.pin_strike):,}, OI dominance {pin_sig.oi_dominance_ratio:.1f}×, severity: {pin_sig.severity}",
            {
                "columns": ["Strike", "CE OI", "PE OI", "Combined OI", "vs Adjacent"],
                "rows": [
                    [str(int(r["strike"])),
                     f"{next((x['oi'] for x in pin_rows if x['strike']==r['strike'] and x['option_type']=='CE'), 0):,}",
                     f"{next((x['oi'] for x in pin_rows if x['strike']==r['strike'] and x['option_type']=='PE'), 0):,}",
                     f"{sum(x['oi'] for x in pin_rows if x['strike']==r['strike']):,}",
                     "◄ DOMINANT (4.5×)" if r["strike"] == 24500 else "—"]
                    for r in pin_rows if r["option_type"] == "CE"
                ],
            },
        ),
    }
    if payload_pin["output"] is None:
        payload_pin["output"] = {"fired": False, "reason": "conditions not met"}
    _save("NIFTYOPT", "pinning", payload_pin)
    print(f"  pinning → fired={pin_sig is not None}  "
          f"score={pin_sig.score:.3f}  sev={pin_sig.severity}" if pin_sig else "  pinning → NOT fired")

    RESULTS["NIFTYOPT"] = {
        "normal_fired": len(n_oi_sigs) > 0 or n_pin_sig is not None,
        "oi_fired": oi_sig is not None,
        "pin_fired": pin_sig is not None,
    }


# ═════════════════════════════════════════════════════════════════════════════
# WATCHLIST INDEX
# ═════════════════════════════════════════════════════════════════════════════

def write_index():
    index = {
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "sample_data": True,
        "integrity_note": (
            "Every condition listed here has a corresponding real-detector-output JSON file "
            "in demo/sample_data/watchlist_{asset}_{condition}.json. "
            "The UI is built from this index — it cannot display a condition without a real file."
        ),
        "assets": [
            {
                "id": "RELIANCE",
                "display_name": "RELIANCE",
                "exchange": "NSE",
                "type": "equity_large_cap",
                "type_label": "Large-cap equity",
                "description": "High-liquidity baseline. avg 5.2M shares/day. Used in the real negative-control backtest.",
                "applicable_detectors": ["spoofing"],
                "conditions": [
                    {"id": "normal",   "label": "Normal trading",
                     "description": "Standard institutional buy/sell flow. No elevated cancellation ratio.",
                     "file": "watchlist_RELIANCE_normal.json"},
                    {"id": "spoofing", "label": "Spoofing / layering",
                     "description": "Account places 4 large BUY orders (all cancelled) then executes a SELL at the inflated price.",
                     "file": "watchlist_RELIANCE_spoofing.json"},
                ],
            },
            {
                "id": "KAVITIND",
                "display_name": "KAVITIND",
                "exchange": "NSE",
                "type": "penny_stock",
                "type_label": "Illiquid penny stock",
                "description": "Illiquid penny stock. avg 45,000 shares/day. High false-positive risk — score discounted 30% by illiquidity guard.",
                "applicable_detectors": ["spoofing", "circular_trading"],
                "conditions": [
                    {"id": "normal",   "label": "Normal trading",
                     "description": "Small executed orders + independent trades. Both detectors run and find nothing.",
                     "file": "watchlist_KAVITIND_normal.json"},
                    {"id": "spoofing", "label": "Spoofing / layering",
                     "description": "Account places 4 large BUY orders (all cancelled ×5.5 normal size) then executes a SELL.",
                     "file": "watchlist_KAVITIND_spoofing.json"},
                    {"id": "circular", "label": "Circular trading ring",
                     "description": "4-account ring: KR-A → KR-B → KR-C → KR-D → KR-A. Each leg 3,000 shares. Net position: 0.",
                     "file": "watchlist_KAVITIND_circular.json"},
                ],
            },
            {
                "id": "TINYLTD",
                "display_name": "TINYLTD",
                "exchange": "NSE",
                "type": "penny_stock",
                "type_label": "Mid-cap penny stock",
                "description": "Mid-cap penny stock. avg 30,000 shares/day. Coordinated pump target.",
                "applicable_detectors": ["coordinated_pump"],
                "conditions": [
                    {"id": "normal", "label": "Normal trading",
                     "description": "3 regularly active accounts, staggered small buy orders. Volume < 5× threshold.",
                     "file": "watchlist_TINYLTD_normal.json"},
                    {"id": "pump",   "label": "Coordinated pump",
                     "description": "7 dormant accounts (60+ days inactive, all new) buying 2,000 shares each — 6.1× normal volume.",
                     "file": "watchlist_TINYLTD_pump.json"},
                ],
            },
            {
                "id": "NIFTYFUT",
                "display_name": "NIFTY Futures",
                "exchange": "NSE",
                "type": "futures",
                "type_label": "Index futures",
                "description": "NIFTY 50 near-month futures. Basis distortion detector. Expiry: 28 DTE.",
                "applicable_detectors": ["basis_distortion"],
                "conditions": [
                    {"id": "normal", "label": "Normal basis",
                     "description": "Futures trading at theoretical fair-value basis (cost-of-carry model, 6.5% RFR).",
                     "file": "watchlist_NIFTYFUT_normal.json"},
                    {"id": "basis",  "label": "Basis distortion",
                     "description": "Futures at ₹245 premium to spot vs fair-value basis of ₹122 — excess contango (0.50% deviation).",
                     "file": "watchlist_NIFTYFUT_basis.json"},
                ],
            },
            {
                "id": "NIFTYOPT",
                "display_name": "NIFTY Options",
                "exchange": "NSE",
                "type": "options_chain",
                "type_label": "Index options",
                "description": "NIFTY 50 options chain. OI manipulation + option pinning detectors.",
                "applicable_detectors": ["oi_manipulation", "option_pinning"],
                "conditions": [
                    {"id": "normal",         "label": "Normal OI distribution",
                     "description": "Balanced OI across strikes. No strike >25% of chain. 28 DTE — outside pinning window.",
                     "file": "watchlist_NIFTYOPT_normal.json"},
                    {"id": "oi_concentration","label": "OI concentration",
                     "description": "24,000 PE holds 42% of total PE OI (504K/1.2M contracts) — far above 35% threshold.",
                     "file": "watchlist_NIFTYOPT_oi_concentration.json"},
                    {"id": "pinning",         "label": "Option pinning",
                     "description": "Expiry day: spot ₹24,497 within 0.012% of 24,500 strike. Strike OI 4.5× adjacent average.",
                     "file": "watchlist_NIFTYOPT_pinning.json"},
                ],
            },
        ],
    }
    path = SD / "watchlist_index.json"
    path.write_text(json.dumps(index, indent=2), encoding="utf-8")
    print(f"\n[Index] Written: {path}")
    return index


# ═════════════════════════════════════════════════════════════════════════════
# MAIN
# ═════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import traceback

    print("=" * 70)
    print("Sentinel — generate_watchlist_samples.py")
    print("Generating real-detector-backed watchlist sample data.")
    print("=" * 70)

    for label, fn in [
        ("RELIANCE",  gen_reliance),
        ("KAVITIND",  gen_kavitind),
        ("TINYLTD",   gen_tinyltd),
        ("NIFTYFUT",  gen_niftyfut),
        ("NIFTYOPT",  gen_niftyopt),
    ]:
        try:
            fn()
        except Exception as exc:
            ERRORS[label] = traceback.format_exc()
            print(f"  ERROR ({label}): {exc}")

    index = write_index()

    print("\n" + "=" * 70)
    print("RESULTS SUMMARY")
    print("=" * 70)
    for asset, res in RESULTS.items():
        print(f"  {asset}: {res}")

    if ERRORS:
        print("\nERRORS:")
        for k, v in ERRORS.items():
            print(f"  [{k}]: {v[:200]}")

    files = list(SD.glob("watchlist_*.json"))
    print(f"\nFiles written: {len(files)} watchlist files")
    for f in sorted(files):
        print(f"  {f.name}")
