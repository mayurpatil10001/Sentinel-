"""
scripts/seed_real_instruments.py
=================================
Replaces fake test instruments with real NSE symbols.

Nifty50 constituents (as of late 2026) + Nifty Bank + key indices.
Run once to seed; subsequent runs skip existing symbols (upsert-safe).

Usage:
    python scripts/seed_real_instruments.py [--clear]

Flags:
    --clear   Delete ALL existing instruments and alerts before inserting
              (use on first run to remove fake seed data)
"""

import argparse
import sys
import pathlib

# Make sure project root is on path
sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))

from app.db.session import init_db, SessionLocal
from app.db.models import Instrument, Alert, Order, Trade, InstrumentType

# ─── Real NSE Equities (Nifty50 constituents) ───────────────────────────────
NIFTY50_EQUITIES = [
    "RELIANCE", "TCS", "HDFCBANK", "ICICIBANK", "INFY",
    "HINDUNILVR", "ITC", "SBIN", "BHARTIARTL", "KOTAKBANK",
    "LT", "AXISBANK", "BAJFINANCE", "HCLTECH", "ASIANPAINT",
    "MARUTI", "ULTRACEMCO", "TITAN", "SUNPHARMA", "WIPRO",
    "NESTLEIND", "POWERGRID", "NTPC", "ONGC", "COALINDIA",
    "DRREDDY", "CIPLA", "JSWSTEEL", "TATASTEEL", "ADANIPORTS",
    "APOLLOHOSP", "INDUSINDBK", "BAJAJFINSV", "DIVISLAB", "TECHM",
    "GRASIM", "LTIM", "HINDALCO", "BPCL", "TATACONSUM",
    "EICHERMOT", "SHREECEM", "M&M", "TATAMOTORS", "HEROMOTOCO",
    "BRITANNIA", "BAJAJ-AUTO", "SBILIFE", "HDFCLIFE", "UPL",
]

# Additional high-volume / surveillance-important stocks
ADDITIONAL_EQUITIES = [
    "ZOMATO", "PAYTM", "NYKAA", "DELHIVERY", "IRCTC",
    "ADANIENT", "ADANIPOWER", "ADANIGREEN", "ATGL", "HINDPETRO",
    "IOC", "PNB", "BANKBARODA", "CANBK", "FEDERALBNK",
    "YESBANK", "IDEA", "VODAFONE", "RBLBANK", "IDFCFIRSTB",
    "TATAPOWER", "JSPL", "VEDL", "SAIL", "NMDC",
]

# Indices (traded as derivatives)
INDICES = [
    ("NIFTY50", "NIFTY50-INDEX"),
    ("BANKNIFTY", "NIFTYBANK-INDEX"),
    ("FINNIFTY", "NIFTYFIN-INDEX"),
    ("MIDCPNIFTY", "MIDCPNIFTY-INDEX"),
]

# Futures (front month — symbol without expiry suffix for the instrument record)
NIFTY_FUTURES = [
    ("NIFTY", "FUTURE"),
    ("BANKNIFTY", "FUTURE"),
    ("FINNIFTY", "FUTURE"),
]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--clear", action="store_true",
                        help="Delete all existing instruments/alerts/orders/trades first")
    args = parser.parse_args()

    init_db()
    db = SessionLocal()

    try:
        if args.clear:
            print("⚠️  Clearing all existing alerts, trades, orders, instruments...")
            db.query(Alert).delete()
            db.query(Trade).delete()
            db.query(Order).delete()
            db.query(Instrument).delete()
            db.commit()
            print("   Done.")

        inserted = 0
        skipped = 0

        # ── Nifty50 Equities ──────────────────────────────────────────────────
        all_equities = list(dict.fromkeys(NIFTY50_EQUITIES + ADDITIONAL_EQUITIES))
        for sym in all_equities:
            existing = db.query(Instrument).filter(
                Instrument.symbol == sym,
                Instrument.exchange == "NSE",
                Instrument.instrument_type == InstrumentType.EQUITY,
            ).first()
            if existing:
                skipped += 1
                continue
            inst = Instrument(
                symbol=sym,
                exchange="NSE",
                instrument_type=InstrumentType.EQUITY,
                underlying_symbol=sym,
            )
            db.add(inst)
            inserted += 1

        # ── Indices ───────────────────────────────────────────────────────────
        for display_name, fyers_suffix in INDICES:
            existing = db.query(Instrument).filter(
                Instrument.symbol == display_name,
                Instrument.instrument_type == InstrumentType.INDEX,
            ).first()
            if existing:
                skipped += 1
                continue
            inst = Instrument(
                symbol=display_name,
                exchange="NSE",
                instrument_type=InstrumentType.INDEX,
                underlying_symbol=display_name,
            )
            db.add(inst)
            inserted += 1

        # ── Index Futures (generic records for detection pipeline) ────────────
        for sym, itype in NIFTY_FUTURES:
            existing = db.query(Instrument).filter(
                Instrument.symbol == sym,
                Instrument.instrument_type == InstrumentType.FUTURE,
            ).first()
            if existing:
                skipped += 1
                continue
            inst = Instrument(
                symbol=sym,
                exchange="NSE",
                instrument_type=InstrumentType.FUTURE,
                underlying_symbol=sym,
            )
            db.add(inst)
            inserted += 1

        db.commit()
        print(f"✅  Seeded {inserted} real NSE instruments (skipped {skipped} existing).")

        # Show summary
        total = db.query(Instrument).count()
        print(f"   Total instruments in DB: {total}")

    except Exception as e:
        db.rollback()
        print(f"❌  Error: {e}")
        raise
    finally:
        db.close()


if __name__ == "__main__":
    main()
