# Sentinel — Early-Stage Market Manipulation Detection (India)

A from-scratch surveillance system focused on **catching manipulation before it hits retail
investors**, across **equities, penny stocks, indices, futures, and options**, with every
alert backed by an **exportable raw evidence log** (exact timestamps, quantities, prices,
exchange, account) suitable for SEBI/exchange verification.

This is an MVP skeleton — one detection pattern (spoofing/layering) is fully wired
end-to-end so you can see the whole pipeline work. Everything else is scaffolded to
extend the same way.

## Why this is a fresh build, not a fork of ARGUS

ARGUS (referenced during design) is trade-level: it scores manipulation *after* trades
already executed. That structurally can't catch spoofing/layering early, because those
schemes are defined by orders that get **cancelled before execution** — by the time a
trade exists, the early-warning window is gone. Sentinel ingests **order lifecycle
events** (placed → modified → cancelled/executed) as the primary data unit, with trades
as a derived/secondary stream.

## Design principles

1. **Order-level ingestion, not just trade-level.** `orders` table tracks every
   placed/modified/cancelled/executed event with millisecond timestamps.
2. **One unified schema for all asset classes.** `instrument_type` (equity / index /
   future / option) plus optional `strike_price`, `expiry_date`, `option_type`,
   `open_interest`, `underlying_symbol` — so options/futures aren't bolted on later.
3. **Liquidity-normalized scoring.** Every instrument is scored against its own rolling
   baseline (avg daily volume, avg order size), not a fixed absolute threshold — so a
   penny stock and Reliance aren't judged by the same yardstick.
4. **Evidence-first, not just score-first.** Every alert can produce a raw, structured
   log slice via `/alerts/{id}/evidence-log` — this is the artifact meant for SEBI/
   exchange verification, separate from any narrative score.
5. **Start narrow, get precision right.** v1 implements spoofing/layering fully.
   Pump-and-dump, circular trading, and OI/IV divergence (options-specific) are
   scaffolded next — see ROADMAP below.

## What's real vs synthetic right now

- **Real**: schema design, detection logic, scoring, evidence-log generation, API.
- **Synthetic**: the demo data. NSE/BSE don't expose live public order-book feeds;
  real order-level data requires either your own broker order stream (e.g. Kite
  Connect — gives you *your own* orders only) or direct exchange/SEBI access (which
  is what a system like this would ultimately plug into on the regulator side).
  `demo/generate_synthetic_orderflow.py` produces realistic order-flow with an
  injected spoofing pattern so you can see detection work end-to-end today.

## Quick start

```bash
pip install -r requirements.txt
python demo/run_demo.py          # generates synthetic data, runs detection, prints alert + evidence log
uvicorn app.main:app --reload    # start the API
# http://127.0.0.1:8000/docs
```

## Project layout

```
app/
  db/            SQLAlchemy models: orders, trades, instruments, alerts, evidence
  ingest/        Data ingestion adapters (synthetic generator now; broker/exchange later)
  detection/     Detection engines (spoofing/layering implemented; others stubbed)
  api/           FastAPI routers (ingest, alerts, evidence-log)
  schemas/       Pydantic request/response models
demo/            Synthetic data generator + end-to-end demo runner
```

## Roadmap

- [x] Order-level schema across all asset classes
- [x] Spoofing/layering detector, liquidity-normalized
- [x] Evidence-log endpoint (raw trade/order slice for SEBI verification)
- [ ] Pump-and-dump detector (coordinated volume + social signal fusion)
- [ ] Circular trading detector (ring detection on account graph)
- [ ] Options/futures OI–price divergence detector (index/expiry manipulation)
- [ ] Real ingestion adapter: NSE/BSE bhavcopy + bulk/block deal files
- [ ] Real ingestion adapter: broker order stream (Kite Connect or similar)
- [ ] Backtest against SEBI historical enforcement orders (weak labels)
- [ ] Dashboard
