"""
Runs the full pipeline once, standalone, without needing the API server:
synthetic order flow -> spoofing detection -> alert -> evidence log.

Usage:
    python demo/run_demo.py
"""

import json
import sys
import os

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.db.session import init_db, SessionLocal
from app.db.models import Alert, Instrument, Order
from app.detection.spoofing import run_spoofing_detection
from app.detection.evidence import build_evidence_log
from demo.generate_synthetic_orderflow import generate_demo_dataset


def main():
    print("=" * 70)
    print("SENTINEL — end-to-end demo (synthetic data)")
    print("=" * 70)

    init_db()
    db = SessionLocal()

    instrument, orders = generate_demo_dataset()
    db.add(instrument)
    db.flush()  # get instrument.id populated
    for o in orders:
        o.instrument_id = instrument.id
    db.add_all(orders)
    db.commit()

    print(
        f"\nGenerated {len(orders)} order events for {instrument.symbol} "
        f"({instrument.exchange}), instrument avg order size baseline = "
        f"{instrument.avg_order_size_30d}"
    )

    signals = run_spoofing_detection(orders, instrument)

    if not signals:
        print("\nNo spoofing/layering patterns detected.")
        return

    print(f"\n{len(signals)} spoofing/layering signal(s) detected:\n")

    for sig in signals:
        alert = Alert(
            instrument_id=instrument.id,
            pattern_type="spoofing_layering",
            severity=sig.severity,
            score=round(sig.score, 3),
            accounts_involved=[sig.account_id],
            window_start=sig.window_start,
            window_end=sig.window_end,
            explanation=sig.explanation,
        )
        db.add(alert)
        db.commit()
        db.refresh(alert)

        print("-" * 70)
        print(f"ALERT {alert.id}")
        print(f"  Pattern:   {alert.pattern_type}")
        print(f"  Severity:  {alert.severity}   Score: {alert.score}")
        print(f"  Account:   {sig.account_id}")
        print(f"  Window:    {sig.window_start} -> {sig.window_end}")
        print(f"  Explain:   {alert.explanation}")

        evidence = build_evidence_log(db, alert)
        print(f"\n  Evidence log ({len(evidence.rows)} raw order rows):")
        for row in evidence.rows:
            print(
                f"    {row['timestamp']} | {row['side']:4s} | {row['status']:9s} | "
                f"qty={row['quantity']:5d} | price={row['price']:.2f} | "
                f"exch={row['exchange']} | acct={row['account_id']}"
            )

        evidence_path = f"/tmp/evidence_{alert.id}.json"
        with open(evidence_path, "w") as f:
            json.dump(
                {
                    "alert_id": evidence.alert_id,
                    "pattern_type": evidence.pattern_type,
                    "instrument_symbol": evidence.instrument_symbol,
                    "exchange": evidence.exchange,
                    "window_start": evidence.window_start,
                    "window_end": evidence.window_end,
                    "accounts_involved": evidence.accounts_involved,
                    "severity": evidence.severity,
                    "score": evidence.score,
                    "explanation": evidence.explanation,
                    "rows": evidence.rows,
                    "generated_at": evidence.generated_at,
                    "disclaimer": evidence.disclaimer,
                },
                f,
                indent=2,
            )
        print(f"\n  Full evidence log written to: {evidence_path}")

    db.close()
    print("\n" + "=" * 70)
    print("Demo complete.")


if __name__ == "__main__":
    main()
