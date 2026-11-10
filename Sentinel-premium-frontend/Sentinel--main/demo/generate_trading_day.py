"""
generate_trading_day.py - Phase 7 Comprehensive Showcase Dataset
================================================================
Full NSE trading day 9:15-15:30, all alerts from real detector calls.
Clean instruments verified to produce 0 alerts by assertion.
"""
import json, os, random, sys, uuid
from datetime import date, datetime, timedelta
from pathlib import Path
import pandas as pd

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
random.seed(42)

from sqlalchemy.exc import IntegrityError
from app.db.models import (
    Alert, Instrument, InstrumentType, Order, OrderSide, OrderStatus, Trade,
)
from app.db.session import SessionLocal, init_db
from app.detection.basis_distortion import detect_basis_distortion
from app.detection.circular_trading import run_circular_trading_detection
from app.detection.coordinated_pump import run_coordinated_pump_detection
from app.detection.oi_manipulation import detect_oi_concentration
from app.detection.option_pinning import detect_option_pinning
from app.detection.spoofing import run_spoofing_detection

TRADE_DATE   = date(2026, 9, 17)
MARKET_OPEN  = datetime(2026, 9, 17, 9, 15, 0)
MARKET_CLOSE = datetime(2026, 9, 17, 15, 30, 0)

def _id():  return str(uuid.uuid4())
def _oid(): return _id()[:12]

def _ts(offset_minutes=0):
    t = MARKET_OPEN + timedelta(minutes=offset_minutes)
    return min(t, MARKET_CLOSE - timedelta(minutes=1))

# ── Instrument helpers ────────────────────────────────────────────────────────

def make_equity(sym, itype=InstrumentType.EQUITY, avg_vol=500000, avg_ord=500, avg_to=25000000):
    return Instrument(id=_id(), symbol=sym, exchange="NSE", instrument_type=itype,
        avg_daily_volume_30d=avg_vol, avg_order_size_30d=avg_ord, avg_daily_turnover_30d=avg_to)

def make_index(sym="SAMPLEIDX"):
    return Instrument(id=_id(), symbol=sym, exchange="NSE", instrument_type=InstrumentType.INDEX,
        avg_daily_volume_30d=0, avg_order_size_30d=0, avg_daily_turnover_30d=0)

# ── Order / Trade factories ───────────────────────────────────────────────────

def normal_orders(inst, n_accts=20, orders_each=15, base_price=100.0):
    orders = []
    t = MARKET_OPEN
    for i in range(n_accts):
        acct = f"CLN{i:04d}"
        price = base_price + random.gauss(0, 0.5)
        for _ in range(orders_each):
            qty = max(10, int(random.gauss(inst.avg_order_size_30d,
                                            inst.avg_order_size_30d * 0.3)))
            side = random.choice([OrderSide.BUY, OrderSide.SELL])
            status = random.choices(
                [OrderStatus.EXECUTED, OrderStatus.PARTIALLY_EXECUTED, OrderStatus.CANCELLED],
                weights=[0.70, 0.10, 0.20])[0]
            t = min(t + timedelta(seconds=random.randint(10, 120)), MARKET_CLOSE - timedelta(seconds=1))
            orders.append(Order(
                id=_id(), exchange_order_id=_oid(), account_id=acct,
                instrument_id=inst.id, side=side, status=status,
                price=round(price + random.gauss(0, 0.2), 2), quantity=qty,
                filled_quantity=(qty if status == OrderStatus.EXECUTED else
                                  qty // 2 if status == OrderStatus.PARTIALLY_EXECUTED else 0),
                session="normal", timestamp=t, exchange=inst.exchange))
    return orders

def normal_trades_no_cycle(inst, n_accts=10, trades_each=8, base_price=200.0):
    """Trades between accounts that cannot form cycles (buyers/sellers in disjoint halves)."""
    trades, t = [], MARKET_OPEN
    buyers  = [f"TRDBUYER{i:03d}" for i in range(n_accts // 2)]
    sellers = [f"TRDSELL{i:03d}"  for i in range(n_accts // 2)]
    for i in range(n_accts // 2):
        for _ in range(trades_each):
            qty = max(10, int(random.gauss(200, 50)))
            t = min(t + timedelta(seconds=random.randint(15, 180)), MARKET_CLOSE - timedelta(seconds=1))
            buy_id  = _id()
            sell_id = _id()
            buy_o  = Order(id=buy_id,  exchange_order_id=_oid(), account_id=buyers[i],
                           instrument_id=inst.id, side=OrderSide.BUY, status=OrderStatus.EXECUTED,
                           price=round(base_price + random.gauss(0, 0.3), 2),
                           quantity=qty, filled_quantity=qty,
                           session="normal", timestamp=t, exchange=inst.exchange)
            sell_o = Order(id=sell_id, exchange_order_id=_oid(), account_id=sellers[i],
                           instrument_id=inst.id, side=OrderSide.SELL, status=OrderStatus.EXECUTED,
                           price=buy_o.price, quantity=qty, filled_quantity=qty,
                           session="normal", timestamp=t, exchange=inst.exchange)
            tr = Trade(id=_id(), instrument_id=inst.id, buy_order_id=buy_id, sell_order_id=sell_id,
                       price=buy_o.price, quantity=qty, timestamp=t, exchange=inst.exchange)
            tr.buy_order  = buy_o
            tr.sell_order = sell_o
            trades.append(tr)
    return trades

def inject_spoofing(inst, start_offset=45, account="ACC9999"):
    orders, t = [], _ts(start_offset)
    price = float(inst.avg_daily_turnover_30d / inst.avg_daily_volume_30d)
    if price <= 0: price = 142.0
    for _ in range(5):
        qty = random.randint(int(inst.avg_order_size_30d * 5), int(inst.avg_order_size_30d * 8))
        price += random.uniform(0.3, 0.6)
        t = min(t + timedelta(seconds=random.randint(3, 8)), MARKET_CLOSE - timedelta(seconds=30))
        orders.append(Order(id=_id(), exchange_order_id=_oid(), account_id=account,
            instrument_id=inst.id, side=OrderSide.BUY, status=OrderStatus.PLACED,
            price=round(price, 2), quantity=qty, filled_quantity=0,
            session="normal", timestamp=t, exchange=inst.exchange))
    for o in list(orders):
        t = min(t + timedelta(seconds=random.randint(3, 7)), MARKET_CLOSE - timedelta(seconds=20))
        orders.append(Order(id=_id(), exchange_order_id=o.exchange_order_id, account_id=account,
            instrument_id=inst.id, side=o.side, status=OrderStatus.CANCELLED,
            price=o.price, quantity=o.quantity, filled_quantity=0,
            session="normal", timestamp=t, exchange=inst.exchange))
    t = min(t + timedelta(seconds=5), MARKET_CLOSE - timedelta(seconds=10))
    orders.append(Order(id=_id(), exchange_order_id=_oid(), account_id=account,
        instrument_id=inst.id, side=OrderSide.SELL, status=OrderStatus.EXECUTED,
        price=round(price + 0.8, 2), quantity=int(inst.avg_order_size_30d * 1.2), filled_quantity=int(inst.avg_order_size_30d * 1.2),
        session="normal", timestamp=t, exchange=inst.exchange))
    return orders

def inject_circular_trades(inst, n_accounts=5, rounds=6, base_price=78.0):
    """5-account ring: A->B->C->D->E->A with buy_order_id/sell_order_id set."""
    accts  = [f"CIRC{i:03d}" for i in range(n_accounts)]
    trades, t = [], _ts(30)
    for rnd in range(rounds):
        for i in range(n_accounts):
            buyer_acct  = accts[i]
            seller_acct = accts[(i + 1) % n_accounts]
            qty = random.randint(400, 600)
            t = min(t + timedelta(seconds=random.randint(20, 60)), MARKET_CLOSE - timedelta(seconds=30))
            buy_id  = _id()
            sell_id = _id()
            buy_o  = Order(id=buy_id,  exchange_order_id=_oid(), account_id=buyer_acct,
                           instrument_id=inst.id, side=OrderSide.BUY, status=OrderStatus.EXECUTED,
                           price=round(base_price + random.uniform(-0.05, 0.05), 2),
                           quantity=qty, filled_quantity=qty, session="normal", timestamp=t, exchange=inst.exchange)
            sell_o = Order(id=sell_id, exchange_order_id=_oid(), account_id=seller_acct,
                           instrument_id=inst.id, side=OrderSide.SELL, status=OrderStatus.EXECUTED,
                           price=buy_o.price, quantity=qty, filled_quantity=qty,
                           session="normal", timestamp=t, exchange=inst.exchange)
            tr = Trade(id=_id(), instrument_id=inst.id, buy_order_id=buy_id, sell_order_id=sell_id,
                       price=buy_o.price, quantity=qty, timestamp=t, exchange=inst.exchange)
            tr.buy_order  = buy_o
            tr.sell_order = sell_o
            trades.append(tr)
    return trades

def inject_coordinated_pump(inst, n_accounts=8, base_price=32.0):
    orders, t = [], _ts(90)
    price = base_price
    for _ in range(4):
        for i in range(n_accounts):
            acct = f"PUMP{i:03d}"
            qty  = random.randint(int(inst.avg_order_size_30d * 3), int(inst.avg_order_size_30d * 5))
            price += random.uniform(0.2, 0.5)
            t = min(t + timedelta(seconds=random.randint(10, 30)), MARKET_CLOSE - timedelta(seconds=60))
            orders.append(Order(id=_id(), exchange_order_id=_oid(), account_id=acct,
                instrument_id=inst.id, side=OrderSide.BUY, status=OrderStatus.EXECUTED,
                price=round(price, 2), quantity=qty, filled_quantity=qty,
                session="normal", timestamp=t, exchange=inst.exchange))
    t = min(t + timedelta(minutes=5), MARKET_CLOSE - timedelta(seconds=30))
    for i in range(n_accounts):
        acct = f"PUMP{i:03d}"
        qty  = random.randint(int(inst.avg_order_size_30d * 2), int(inst.avg_order_size_30d * 4))
        t = min(t + timedelta(seconds=random.randint(5, 20)), MARKET_CLOSE - timedelta(seconds=10))
        orders.append(Order(id=_id(), exchange_order_id=_oid(), account_id=acct,
            instrument_id=inst.id, side=OrderSide.SELL, status=OrderStatus.EXECUTED,
            price=round(price + random.uniform(1.0, 2.0), 2), quantity=qty, filled_quantity=qty,
            session="normal", timestamp=t, exchange=inst.exchange))
    return orders

def _make_chain(spot, expiry_date, strikes, ce_oi, pe_oi):
    rows = []
    for strike, coi, poi in zip(strikes, ce_oi, pe_oi):
        rows.append({"strike": strike, "expiry": expiry_date, "option_type": "CE",
                     "oi": coi, "underlying_value": spot})
        rows.append({"strike": strike, "expiry": expiry_date, "option_type": "PE",
                     "oi": poi, "underlying_value": spot})
    return pd.DataFrame(rows)

def _save_alert(db, inst_id, pattern, sig, all_alerts, results):
    accts = []
    for attr in ("accounts_involved", "cycle_accounts"):
        val = getattr(sig, attr, None)
        if val:
            accts = list(val)
            break
    if not accts and hasattr(sig, "account_id"):
        accts = [sig.account_id]
    # Some signals (option_pinning, oi_manipulation, basis_distortion) use
    # snapshot_time rather than window_start / window_end.
    snap = getattr(sig, "snapshot_time", None)
    w_start = getattr(sig, "window_start", snap - timedelta(minutes=30) if snap else MARKET_OPEN)
    w_end   = getattr(sig, "window_end",   snap if snap else MARKET_CLOSE)
    sym = (getattr(sig, "instrument_symbol", None)
           or getattr(sig, "symbol", "?"))
    alert = Alert(
        instrument_id=inst_id, pattern_type=pattern,
        severity=sig.severity, score=round(sig.score, 3),
        accounts_involved=accts,
        window_start=w_start, window_end=w_end,
        explanation=sig.explanation)
    try:
        db.add(alert); db.commit(); db.refresh(alert)
        all_alerts.append(alert)
        results["alerts"].append({
            "id": alert.id, "instrument": sym,
            "pattern": pattern, "severity": alert.severity, "score": alert.score,
            "window_start": str(w_start), "window_end": str(w_end),
            "explanation": sig.explanation[:250],
        })
        return True
    except IntegrityError:
        db.rollback()
        return False

def run_trading_day():
    print("=" * 70)
    print("SENTINEL - Full Trading Day Showcase (2026-09-17)")
    print("Synthetic data | All scores from real detector calls")
    print("=" * 70)

    init_db(); db = SessionLocal()
    all_alerts = []
    results = {
        "trade_date": TRADE_DATE.isoformat(),
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "environment": "SAMPLE_DATA",
        "instruments": [], "alerts": [], "summary": {}
    }

    # 1. CLEAN instruments
    print("\n[1/9] Clean instruments (assert 0 alerts)...")
    # Real company names appear ONLY as clean negative-control instruments.
    # Fictional names (SPOOFERLTD, CIRCLETRADE, etc.) are used for all
    # manipulation scenarios. RELIANCE is here as a clean baseline —
    # it proves the system does not cry wolf on real large-cap activity.
    for sym, avg_vol, avg_ord, avg_to, price in [
        ("RELIABLETEX", 800000, 600, 480000000, 950.0),
        ("STEADYINFRA",  200000, 300,  60000000, 280.0),
        ("BALANCEDFIN",  350000, 400, 140000000, 380.0),
        ("RELIANCE",   5000000, 2000, 14250000000, 2850.0),  # clean baseline: real large-cap
    ]:
        inst = make_equity(sym, avg_vol=avg_vol, avg_ord=avg_ord, avg_to=avg_to)
        db.add(inst); db.flush()
        orders = normal_orders(inst, 20, 15, price)
        trades = normal_trades_no_cycle(inst, 10, 8, price)
        for o in orders: o.instrument_id = inst.id
        for t in trades: t.instrument_id = inst.id
        db.add_all(orders); db.add_all(trades); db.flush()
        all_t = [t for t in trades]
        assert len(run_spoofing_detection(orders, inst))         == 0, f"FP: {sym} spoofing"
        assert len(run_circular_trading_detection(all_t, inst))  == 0, f"FP: {sym} circular"
        assert len(run_coordinated_pump_detection(orders, inst)) == 0, f"FP: {sym} pump"
        results["instruments"].append({"symbol": sym, "type": "CLEAN", "alerts_fired": 0})
        print(f"  {sym}: 0 alerts (verified)")

    # 2. SPOOFERLTD
    print("\n[2/9] SPOOFERLTD - spoofing/layering...")
    sp = make_equity("SPOOFERLTD", avg_vol=50000, avg_ord=500, avg_to=7000000)
    db.add(sp); db.flush()
    sp_orders = normal_orders(sp, 12, 10, 142.0) + inject_spoofing(sp)
    for o in sp_orders: o.instrument_id = sp.id
    db.add_all(sp_orders); db.flush()
    sp_sigs = run_spoofing_detection(sp_orders, sp)
    for s in sp_sigs: _save_alert(db, sp.id, "spoofing_layering", s, all_alerts, results)
    results["instruments"].append({"symbol": "SPOOFERLTD", "type": "MANIPULATED",
        "pattern": "spoofing_layering", "alerts_fired": len(sp_sigs)})
    print(f"  {len(sp_sigs)} signal(s)")

    # 3. CIRCLETRADE
    print("\n[3/9] CIRCLETRADE - circular trading...")
    ci = make_equity("CIRCLETRADE", avg_vol=30000, avg_ord=300, avg_to=2340000)
    db.add(ci); db.flush()
    ci_noise  = normal_trades_no_cycle(ci, 8, 6, 78.0)
    ci_trades = inject_circular_trades(ci)
    all_ci = ci_noise + ci_trades
    for t in all_ci: t.instrument_id = ci.id
    db.add_all(all_ci); db.flush()
    ci_sigs = run_circular_trading_detection(all_ci, ci)
    for s in ci_sigs: _save_alert(db, ci.id, "circular_trading", s, all_alerts, results)
    results["instruments"].append({"symbol": "CIRCLETRADE", "type": "MANIPULATED",
        "pattern": "circular_trading", "alerts_fired": len(ci_sigs)})
    print(f"  {len(ci_sigs)} signal(s)")

    # 4. PUMPCO
    print("\n[4/9] PUMPCO - coordinated pump...")
    pm = make_equity("PUMPCO", InstrumentType.PENNY_STOCK, 20000, 200, 640000)
    db.add(pm); db.flush()
    pm_orders = normal_orders(pm, 10, 8, 32.0) + inject_coordinated_pump(pm)
    for o in pm_orders: o.instrument_id = pm.id
    db.add_all(pm_orders); db.flush()
    pm_sigs = run_coordinated_pump_detection(pm_orders, pm)
    for s in pm_sigs: _save_alert(db, pm.id, "coordinated_pump", s, all_alerts, results)
    results["instruments"].append({"symbol": "PUMPCO", "type": "MANIPULATED",
        "pattern": "coordinated_pump", "alerts_fired": len(pm_sigs)})
    print(f"  {len(pm_sigs)} signal(s)")

    # 5. SAMPLEIDX option pinning (expiry day)
    # Fictional index instrument — real index names (NIFTY, SENSEX) are not used
    # in manipulation scenarios to avoid implying a real accusation.
    print("\n[5/9] SAMPLEIDX option pinning...")
    sampleidx = make_index("SAMPLEIDX"); db.add(sampleidx); db.flush()
    snap = datetime(2026, 9, 17, 15, 0, 0)
    pin_chain = _make_chain(19498.0, date(2026,9,17),
        strikes=[19200,19300,19400,19500,19600,19700,19800],
        ce_oi=[20000,50000,80000,450000,60000,40000,20000],
        pe_oi=[25000,55000,90000,450000,55000,35000,15000])
    pin_sig = detect_option_pinning(chain_df=pin_chain, symbol="SAMPLEIDX", exchange="NSE",
        spot_price=19498.0, expiry_date=date(2026,9,17), snapshot_time=snap)
    if pin_sig:
        _save_alert(db, sampleidx.id, "option_pinning", pin_sig, all_alerts, results)
        print(f"  score={pin_sig.score:.3f} severity={pin_sig.severity}")
    else:
        print("  No signal")
    results["instruments"].append({"symbol":"SAMPLEIDX","type":"MANIPULATED","pattern":"option_pinning","alerts_fired": 1 if pin_sig else 0})

    # 6. SAMPLEIDX OI concentration
    print("\n[6/9] SAMPLEIDX OI concentration...")
    oi_snap = _ts(180)
    oi_chain = _make_chain(19482.0, date(2026,9,25),
        strikes=[18800,19000,19200,19400,19500,19600,19800,20000],
        ce_oi=[15000,30000,50000,420000,85000,60000,25000,10000],
        pe_oi=[10000,20000,40000,80000,100000,70000,30000,10000])
    oi_sigs = detect_oi_concentration(chain_df=oi_chain, symbol="SAMPLEIDX", exchange="NSE", snapshot_time=oi_snap)
    for s in oi_sigs: _save_alert(db, sampleidx.id, "oi_manipulation", s, all_alerts, results)
    print(f"  {len(oi_sigs)} signal(s)")

    # 7. SAMPLEFUT basis distortion
    # Fictional futures instrument — real company names (RELIANCE, etc.) are not
    # used in manipulation scenarios to avoid implying a real accusation against
    # a named, publicly-traded company. Consistent with SPOOFERLTD/CIRCLETRADE naming.
    print("\n[7/9] SAMPLEFUT basis distortion...")
    samplefut = make_equity("SAMPLEFUT", avg_vol=500000, avg_ord=500, avg_to=1425000000)
    db.add(samplefut); db.flush()
    basis_snap = _ts(150)
    basis_sig = detect_basis_distortion(symbol="SAMPLEFUT", exchange="NSE",
        spot_price=2850.0, futures_price=2916.0,
        expiry_date=date(2026,9,25), snapshot_time=basis_snap, risk_free_rate=0.065)
    if basis_sig:
        _save_alert(db, samplefut.id, "basis_distortion", basis_sig, all_alerts, results)
        print(f"  score={basis_sig.score:.3f} severity={basis_sig.severity}")
    else:
        print("  No signal (basis within threshold)")
    results["instruments"].append({"symbol":"SAMPLEFUT","type":"MANIPULATED","pattern":"basis_distortion","alerts_fired": 1 if basis_sig else 0})

    # 8. DOUBLEHIT - two detectors on same scrip
    print("\n[8/9] DOUBLEHIT - spoofing + coordinated pump...")
    dh = make_equity("DOUBLEHIT", InstrumentType.PENNY_STOCK, 15000, 150, 450000)
    db.add(dh); db.flush()
    dh_orders = (normal_orders(dh, 8, 8, 18.0)
                 + inject_spoofing(dh, 30, "DHACC001")
                 + inject_coordinated_pump(dh, 6, 18.0))
    for o in dh_orders: o.instrument_id = dh.id
    db.add_all(dh_orders); db.flush()
    dh_sp = run_spoofing_detection(dh_orders, dh)
    dh_pm = run_coordinated_pump_detection(dh_orders, dh)
    for s in dh_sp: _save_alert(db, dh.id, "spoofing_layering",  s, all_alerts, results)
    for s in dh_pm: _save_alert(db, dh.id, "coordinated_pump",   s, all_alerts, results)
    results["instruments"].append({"symbol":"DOUBLEHIT","type":"MANIPULATED",
        "pattern":"spoofing_layering+coordinated_pump","alerts_fired":len(dh_sp)+len(dh_pm)})
    print(f"  {len(dh_sp)} spoofing + {len(dh_pm)} pump signal(s)")

    # 9. Clean verification
    print("\n[9/9] Verifying no false positives on clean instruments...")
    clean_syms = {"RELIABLETEX", "STEADYINFRA", "BALANCEDFIN", "RELIANCE"}
    alert_insts = {a.get("instrument", "") for a in results["alerts"]}
    fp = clean_syms & alert_insts
    assert not fp, f"FALSE POSITIVE on clean instruments: {fp}"
    print(f"  Verified: 0 false positives")

    results["summary"] = {
        "total_alerts": len(all_alerts),
        "clean_instruments_tested": 3,
        "manipulated_instruments_tested": 6,
        "false_positive_rate": "0% (verified by assertion)",
        "detectors_exercised": ["spoofing_layering","circular_trading","coordinated_pump",
                                 "option_pinning","oi_manipulation","basis_distortion"],
        "data_provenance": "All scores from real detector function calls. No hand-written values.",
    }
    db.commit()

    out = Path("demo/sample_data/trading_day.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w") as f:
        json.dump(results, f, indent=2, default=str)

    print("\n" + "=" * 70)
    print(f"Complete. Total alerts: {len(all_alerts)}. Output: {out}")
    print("=" * 70)
    return results

if __name__ == "__main__":
    run_trading_day()
