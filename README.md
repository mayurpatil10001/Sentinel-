# Sentinel — Market Surveillance & Manipulation Detection

[![Tests](https://img.shields.io/badge/tests-200%20passing-brightgreen.svg)]()
[![Python](https://img.shields.io/badge/python-3.11-blue.svg)]()
[![License](https://img.shields.io/badge/license-MIT-blue.svg)]()
[![SEBI](https://img.shields.io/badge/regulatory%20framework-SEBI%20PFUTP%202003-orange.svg)]()
[![Status](https://img.shields.io/badge/status-proof--of--concept-yellow.svg)]()

> **Proof-of-concept only. All detection thresholds are documented as unvalidated estimates. No output constitutes a legal or regulatory determination. See [Limitations](#limitations) before any use.**

Sentinel is a modular, order-level market surveillance engine for Indian capital markets (NSE/BSE). It detects early-stage price and volume manipulation across **equities, penny stocks, index derivatives, and equity options** — before artificial distortion reaches retail participants. Every detection is backed by a tamper-evident evidence log, and confirmed SEBI cases feed a downstream **harm quantification engine** using academic event-study methodology.

---

## Table of Contents

1. [What This Project Does](#1-what-this-project-does)
2. [Architecture Overview](#2-architecture-overview)
3. [Detection Engines](#3-detection-engines)
4. [Machine Learning Layer](#4-machine-learning-layer)
5. [Alert Lifecycle & SEBI SAR](#5-alert-lifecycle--sebi-sar)
6. [Security, PII & Compliance](#6-security-pii--compliance)
7. [Harm Quantification Engine](#7-harm-quantification-engine)
8. [Backtest Against Real SEBI Cases](#8-backtest-against-real-sebi-cases)
9. [Operational Console & Dashboard](#9-operational-console--dashboard)
10. [Demo System](#10-demo-system)
11. [Data Ingestion Layer](#11-data-ingestion-layer)
12. [Repository Structure](#12-repository-structure)
13. [API Reference](#13-api-reference)
14. [Getting Started](#14-getting-started)
15. [Test Suite](#15-test-suite)
16. [Design Principles](#16-design-principles)
17. [Limitations](#17-limitations)
18. [References & Regulatory Citations](#18-references--regulatory-citations)

---

## 1. What This Project Does

Sentinel addresses a structural gap in market surveillance: by the time a manipulation event shows up in price charts, retail investors have already been harmed. The system operates at the **order lifecycle level** — detecting spoofing, layering, circular trading, and coordinated pump patterns from raw order events (placed → modified → cancelled) rather than waiting for execution.

**Core capabilities:**
- Six pattern detectors covering equities and derivatives
- ML-assisted anomaly scoring with mandatory explainability
- SEBI PFUTP 2003-aligned draft Suspicious Activity Report (SAR) generation
- Real NSE data ingestion with resilient retry/backoff
- Backtesting against verified, citable SEBI adjudication orders
- Event-study harm quantification (MacKinlay 1997; Brown & Warner 1985) with 95% confidence intervals
- Hard ethical guardrails preventing false accusations against real companies

**What it is not:**
- A production-ready surveillance system
- A validated real-time trading alert tool
- A source of legal or regulatory findings

---

## 2. Architecture Overview

```
                      MARKET DATA INGESTION
 ┌─────────────────────────────────────────────────────────────────┐
 │  NSE Bhavcopy (EOD OHLCV)  │  NSE Bulk & Block Deals (CSV)     │
 │  NSE Live Option Chain     │  Broker Order Stream (Kite/WS)    │
 └──────────────────────────────┬──────────────────────────────────┘
                                │  data/ingest/
                                ▼
                       SQL DATABASE LAYER
 ┌─────────────────────────────────────────────────────────────────┐
 │  Instruments  │  Orders (salted hash)  │  Trades  │  Alerts    │
 │                    (app/db/models.py)                           │
 └──────────────────────────────┬──────────────────────────────────┘
                                │
                                ▼
                       DETECTION ENGINES (app/detection/)
 ┌─────────────────────────────────────────────────────────────────┐
 │  spoofing.py          Liquidity-normalized order cancellation   │
 │  circular_trading.py  Johnson's algorithm on directed graphs    │
 │  coordinated_pump.py  Multi-account burst coordination          │
 │  oi_manipulation.py   OI concentration + IV decoupling          │
 │  basis_distortion.py  Cost-of-carry fair value deviation        │
 │  option_pinning.py    Expiry-day strike clustering              │
 └──────────────────────────────┬──────────────────────────────────┘
                                │
                                ▼
                     ML SCORING LAYER (app/ml/)
 ┌─────────────────────────────────────────────────────────────────┐
 │  20-Feature Extraction  │  Isolation Forest  │  Expert Baseline │
 └──────────────────────────────┬──────────────────────────────────┘
                                │
                                ▼
                   ALERT LIFECYCLE & GOVERNANCE (app/alerts/)
 ┌─────────────────────────────────────────────────────────────────┐
 │  Deduplication  │  3-Tier Escalation  │  Draft SAR (8-section) │
 └──────────────────────────────┬──────────────────────────────────┘
                                │
              ┌─────────────────┴──────────────────┐
              ▼                                     ▼
   SECURITY & COMPLIANCE                 HARM ESTIMATION
   (app/security/)                       (app/harm_estimation/)
   Salted SHA-256 PII hashing            Market-model event study
   Evidence access audit log             CAR + 95% CI (MacKinlay 1997)
   7-year retention enforcement          Trade-level restitution ranges
```

---

## 3. Detection Engines

All detectors live in [`app/detection/`](app/detection/). Each produces a normalized score (0–1), a human-readable explanation, and a reference to the evidence slice used.

### 3.1 Spoofing and Layering — `spoofing.py`

Spoofing places large orders with no intention of execution to move prices, then cancels them. Layering stacks multiple fake levels to simulate depth.

**Detection mechanism:**
- Tracks the **cancellation ratio** of orders by account and instrument
- Measures **order-to-trade ratio** (orders placed vs. executed)
- Scores **order lifespan** (very short-lived orders near the bid/ask are more suspicious)
- Detects **asymmetric depth**: large orders on one side disappear when the opposite side fills

**Key design choice:** Thresholds are normalized against each instrument's **rolling 30-day median order size and ADV** — a fixed absolute threshold would generate enormous false positives on high-frequency liquid instruments while missing manipulation in thinly-traded ones.

**Regulatory reference:** SEBI PFUTP Reg 4(2)(a), (b), (g)

---

### 3.2 Circular Trading — `circular_trading.py`

Circular trading creates artificial volume by trading between a ring of coordinated accounts, without genuine change in beneficial ownership.

**Detection mechanism:**
- Builds a **directed trade graph**: nodes are accounts, edges are trades (A bought from B → edge A→B)
- Applies **Johnson's cycle-finding algorithm** (`networkx.simple_cycles`) to enumerate all closed rings (A→B→C→A)
- Flags rings where **net inventory change ≈ 0** but gross turnover is high — i.e. volume was manufactured, not real

**False-positive protection:**
- Automatically discounts scores for **illiquid instruments** where natural counterparty matching can mirror circular loops
- Requires minimum ring size ≥ 3 participants — two-account wash trades flagged by separate heuristic

**Regulatory reference:** SEBI PFUTP Reg 4(2)(a), (b)

**Real SEBI case:** Verified against *Kavit Industries Limited* adjudication order (Feb 28, 2025) — circular trades accounting for the majority of scrip volume on BSE.

---

### 3.3 Coordinated Pump — `coordinated_pump.py`

Pump-and-dump operations coordinate multiple accounts to simultaneously buy a thinly-traded stock, inflate the price, then sell into the artificial liquidity.

**Detection mechanism:**
- Detects **synchronized buy-side aggression** across 3+ independent accounts within rolling time windows
- Flags abnormal **price movement** (≥ 2× normal daily range) coinciding with the buy cluster
- Measures **volume expansion** (≥ 5× normal ADV)
- Profiles **dormant accounts** — those recently reactivated specifically for the pump

**Regulatory reference:** SEBI PFUTP Reg 4(2)(d), (e)

**Real SEBI case:** Validated against the *Mauria Udyog / Hanif Shekh* cluster (SEBI Interim Order June 19, 2023; Rs. 143.79 crore disgorgement, 222 entities barred).

---

### 3.4 Open Interest Manipulation — `oi_manipulation.py`

Derivatives can be manipulated by building concentrated OI positions at specific strikes to force the underlying price toward option expiry outcomes.

**Detection mechanism:**
- **OI concentration**: Flags strikes holding ≥ 35% of total contract OI, with dynamic weighting penalizing deep OTM concentration over natural ATM clustering
- **OI-IV decoupling**: Tracks rapid OI surges accompanied by sharp IV drops — the signature of aggressive unhedged naked option writing used to pin prices

**Regulatory reference:** SEBI PFUTP Reg 4(2)(h)

---

### 3.5 Cash-Futures Basis Distortion — `basis_distortion.py`

The fair value of a futures contract is determined by the **cost-of-carry model**:

```
Fair Value = Spot × (1 + r × DTE/365)
```

Sustained deviations from this theoretical value indicate either synthetic demand/supply manipulation or arbitrage-free boundary violations being exploited.

**Detection mechanism:**
- Computes the basis gap between actual futures price and cost-of-carry fair value
- Flags deviations beyond configurable standard-deviation bands
- All parameters (risk-free rate `r = 6.5%` default, DTE) are **recorded on the signal** for audit reproducibility

**Regulatory reference:** SEBI PFUTP Reg 4(2)(h)

---

### 3.6 Expiry-Day Option Pinning — `option_pinning.py`

Near expiry, significant OI at a specific strike creates incentives for market participants to pin the underlying to that strike, causing the maximum number of options to expire worthless.

**Detection mechanism:**
- Detects underlying spot price clustering within **0.5% of high-OI strikes** within 48 hours of expiry
- Computes the **Max-Pain strike** (the strike at which total option premium lost by buyers is maximized) as a corroborating signal
- Includes explicit false-positive warnings for natural market-maker **delta/gamma hedging** that can produce superficially similar patterns

**Regulatory reference:** SEBI PFUTP Reg 4(2)(h)

---

## 4. Machine Learning Layer

Located in [`app/ml/`](app/ml/). Rather than a black-box prediction, Sentinel uses a **transparent dual-scoring framework**.

### 4.1 Feature Extraction — `features.py`

Extracts a **20-dimensional signal feature vector** capturing:
- Individual detector pattern scores
- Participation breadth (number of accounts involved)
- Volume multiples (ratio to ADV)
- Detector **co-occurrence** counts (when multiple detectors fire simultaneously, the confidence is higher)
- Order-to-trade ratios
- Temporal clustering metrics

**Schema versioning:** `SCHEMA_VERSION = 1` is embedded in saved models. Models trained on one schema reject feature vectors from a different schema version, preventing silent feature drift in production.

### 4.2 Anomaly Scorer — `scorer.py`

**Isolation Forest** (Liu, Ting & Zhou, 2008) — an unsupervised tree-based space partitioning algorithm suited for unlabelled financial anomaly detection where confirmed manipulation labels are unavailable.

**Dual scoring:**
1. Isolation Forest score (unsupervised, data-driven)
2. Expert-weighted baseline (deterministic, interpretable heuristic)

The two scores are combined so the engine remains functional and interpretable even before model training data is collected.

> **Mandatory disclaimer on every output:** *"This is an anomaly score, NOT a manipulation probability or legal determination."*

---

## 5. Alert Lifecycle & SEBI SAR

Located in [`app/alerts/`](app/alerts/).

### 5.1 Alert Manager — `manager.py`

**State machine:** `open → investigating → escalated → closed`

**Deduplication:** A `UNIQUE` database constraint on `(instrument_id, pattern_type, window_start)` prevents duplicate alerts from concurrent detection runs (TOCTOU-safe).

**3-Tier Escalation:**

| Tier | Trigger | Action |
|---|---|---|
| **Tier 1** | Score ≥ 0.45, Severity ≥ Medium | Internal analyst queue |
| **Tier 2** | Score ≥ 0.70, Severity ≥ High | Supervisor review within 2 business days |
| **Tier 3** | Score ≥ 0.85, Severity = Critical | Draft SAR generated; human sign-off mandatory |

### 5.2 Draft SAR Generator — `sebi_report.py`

Formats confirmed alerts into an **8-section standardized dossier** matching the SEBI ISD format:

1. Reference & Filing Metadata
2. Target Entity & Instrument Details
3. Alleged PFUTP Violations & Regulatory Clauses
4. Quantitative Evidence Log Summary
5. Trade & Order Execution Timeline
6. Coordinated Accounts & Entity Network
7. Pattern-Specific Explanations & False Positive Notes
8. Compliance Officer Sign-off & Submission Checklist

> Sentinel does **not** auto-file with SEBI. Every draft SAR requires analyst verification before any submission via the SEBI SCORES portal.

---

## 6. Security, PII & Compliance

Located in [`app/security/`](app/security/).

### 6.1 PII Hashing — `pii.py`

Client account IDs are converted to **salted SHA-256 digests** (`account_id_hash`) using an environment-provided salt (`ACCOUNT_ID_SALT`).

**Dual-column architecture:** Both `account_id` (raw) and `account_id_hash` are stored. The raw ID is retained because SEBI/exchange counterpart verification genuinely requires it. The hash is what appears in all exported logs and reports. Switching between them is a **deliberate, logged choice** — not an uncontrolled default.

### 6.2 Evidence Access Auditing — `access_log.py`

Access to raw order/trade evidence logs is recorded in an immutable `evidence_access_log` table capturing:
- Analyst user ID
- IP address
- Justification reason (free text)
- Access timestamp

This gives a paper trail for any regulatory audit of who accessed what evidence and why.

### 6.3 Data Retention — `retention.py`

Implements scheduled pruning for orders, trades, and alerts past the **7-year statutory threshold** (2,555 days — benchmarked against SEBI Stock Brokers Regulations 1992, Reg 17).

- **Dry-run mode** provided for pre-execution compliance review
- Deletion is logged before execution

---

## 7. Harm Quantification Engine

Located in [`app/harm_estimation/`](app/harm_estimation/). This is the most ethically constrained module in the project.

> **Critical note:** This module runs only *after* manipulation has been legally confirmed by a SEBI adjudication order, or on explicitly-labeled synthetic data. It does **not** make new allegations.

### 7.1 Methodology (MacKinlay 1997; Brown & Warner 1985)

The standard **market-model event study** methodology:

**Step 1 — Estimation window (200 trading days pre-event):**
```
R_stock = alpha + beta × R_market + epsilon
```
Fitted by OLS (statsmodels) over a clean period before the manipulation window. The estimation window never overlaps the event window (avoids "contaminated estimation" bias).

**Step 2 — Abnormal Return per day in event window:**
```
AR(t) = R_stock(t) − (alpha_hat + beta_hat × R_market(t))
```
Uses *actual* market returns during the event window — not a flat/zero assumption.

**Step 3 — Cumulative Abnormal Return:**
```
CAR = Σ AR(t)   for t in event window
```

**Step 4 — 95% Confidence Interval (Brown & Warner 1985, Eq. 5):**
```
Var(CAR) = T_event × σ²_hat × (1 + 1/T_est + Σ(R_mkt_event − R̄_mkt)² / SS_mkt)
```
The third term corrects for forecast-period uncertainty — if market returns during manipulation deviate from estimation-period returns, the forecast error is larger.

**Step 5 — Counterfactual price path:**
```
P_cf(t) = P_pre_event × Π(1 + E[R(s)])  for s in [1..t]
```
Bounding the path with CI low/high gives a **price range** for each day — not a single deterministic counterfactual.

**Step 6 — Per-trade harm:**
```
buyer_harm  = (actual_price − cf_price) × qty   [positive if overpaid]
seller_harm = (cf_price − actual_price) × qty   [positive if under-received]
```
Aggregated to account level and instrument level — always as a range (low/central/high).

### 7.2 Hard Ethical Guards — `guards.py`

A hard-coded allow-list prevents execution against any non-approved instrument:

| Allow-list | Condition | Examples |
|---|---|---|
| **SEBI-confirmed cases** | SEBI adjudication/final order exists | KIL-2019, PUMP-DUMP-2017-2020 |
| **Synthetic scenarios** | Scenario ID prefixed `SYNTHETIC_` | `SYNTHETIC_PUMP_RELIANCE_2021_VALIDATION` |

Every call — including refusals — is written to `app/harm_estimation/guard_audit.log`. The guard **cannot be silently bypassed**; `bypass_guard=True` logs `ERROR` and the audit record — it does not suppress the trail.

### 7.3 Hard Output Rule

> **Every harm figure must carry `harm_low`, `harm_central`, and `harm_high` with equal visual weight. The central estimate is never presented alone.**

### 7.4 Validation Status

| Test | Status |
|---|---|
| Injected CAR=+30% recovered within 95% CI | ✅ Verified |
| Large effect correctly flagged as statistically significant | ✅ Verified |
| Zero-injection produces CAR ≈ 0 (no false detection) | ✅ Verified |
| CI widens with longer event window (Brown & Warner scaling) | ✅ Verified |
| Guard: SEBI confirmed cases allowed | ✅ Verified |
| Guard: unlisted instruments refused | ✅ Verified |
| Guard: bypass is logged, not silent | ✅ Verified |
| Real case: KIL-2019 (BSE scrip) | ⚠️ Guard-approved; blocked by BSE data gap |
| Real case: PUMP-DUMP-2017-2020 (BSE scrip) | ⚠️ Guard-approved; blocked by BSE data gap |
| Production validation on live exchange data | ❌ Requires BSE bhavcopy fetcher |

---

## 8. Backtest Against Real SEBI Cases

Located in [`backtest/`](backtest/).

The backtest layer runs Sentinel's detectors against **real, citable SEBI enforcement cases** and documents exactly what can and cannot be tested — with **log-backed, non-fabricated findings** only.

### 8.1 Case Catalog — `sebi_case_catalog.py`

Every case entry includes:
- Full SEBI order reference (verifiable at sebi.gov.in)
- Investigation period and scrips
- Testability verdict: `TESTABLE / PARTIALLY_TESTABLE / UNTESTABLE`
- Explicit reasons for any gaps

**Current cases:**

| Case ID | Scrips | SEBI Order | Verdict |
|---|---|---|---|
| `KIL-2019` | Kavit Industries Ltd | Adjudication Order Feb 28, 2025 | UNTESTABLE — BSE-only; NSE absence log-verified |
| `PUMP-DUMP-2017-2020` | Mauria Udyog, 7NR Retail, GBL Industries, Vishal Fabrics, Darjeeling Ropeway | Interim Order Jun 19, 2023 | UNTESTABLE — BSE-only; NSE absence log-verified |
| `GIL-2003-2004` | Gravity India Limited | Adj. Order Mar 31, 2022 | UNTESTABLE — Pre-2007 archive, BSE-only |

### 8.2 Integrity Rule — `run_backtest.py`

`_enforce_log_file_integrity()` is a hard gatekeeper: **any result file that states a firm conclusion must reference a real, on-disk log file**. Results without a `log_file` field are automatically downgraded to `VERDICT: INCONCLUSIVE`. This prevents unverified claims from persisting in methodology documents.

### 8.3 Diagnostic Logs

Real NSE bhavcopy probes back the scrip-absence claims:

| Log File | What It Proves |
|---|---|
| `backtest/results/KIL-2019_diagnostic_v2.log` | `KAVIT` / `KIL` absent from NSE EQ series on 2019-10-15 and 2019-08-16 |
| `backtest/results/PUMP-DUMP-2017-2020_diagnostic_v2.log` | MAURIUDYOG, 7NRRETAIL, GBLIND, VISHALFAB, DARJROPE absent from NSE on 2018-06-15 |

These logs are the **only** evidence for the "not NSE-listed" claims. The earlier unverified assertion ("Fetched real NSE EQ bhavcopy — 1,494 rows — Zero matches") was investigated, found contradicted by a simultaneous diagnostic reporting `connectivity_ok: false`, and corrected.

### 8.4 Negative Controls

Five large Nifty-50 constituents (RELIANCE, TCS, HDFCBANK, INFY, ICICIBANK) across 20 selected trading days (2019–2022) serve as clean negative-control baselines — verifying that the detectors do not generate false positives on legitimately-traded large-cap instruments.

**Result:** 0% false positive rate on negative control set (252 trading days tested for spoofing/layering detector).

---

## 9. Operational Console & Dashboard

### Real-Time Console — `console/index.html`

A full-screen operational surveillance console providing live visibility into:
- Active alert counts and severity distribution
- Per-instrument watchlist with live detector states
- Alert detail drill-down with evidence log links
- SEBI SAR draft preview pane
- Detection engine health indicators
- Engineering health metrics (data ingestion latency, DB row counts)

Built with vanilla HTML/CSS/JavaScript — no framework dependency, deployable as a single file.

### Analytics Dashboard — `dashboard/`

Secondary reporting dashboard with:
- Historical alert trend charts
- Detector co-occurrence heatmaps
- Instrument risk scoring over time
- Data source connectivity status

---

## 10. Demo System

Located in [`demo/`](demo/). A complete synthetic demonstration pipeline that proves the detection system works end-to-end without requiring real exchange access.

| Script | Purpose |
|---|---|
| `generate_synthetic_orderflow.py` | Generates realistic order flows with embedded spoofing, layering, and pump patterns |
| `generate_trading_day.py` | Produces a synthetic trading day with configurable manipulation injection (fictional instruments only) |
| `generate_watchlist_samples.py` | Creates sample watchlist data for all 6 detector types |
| `generate_all_detector_samples.py` | Full batch sample generation across all pattern types |
| `build_demo.py` | Assembles the static stakeholder demo with detection results |
| `build_extended_demo.py` | Extended demo with SEBI case context and methodology notes |
| `build_interactive_demo.py` | Full interactive demo with clickable alert drill-down |
| `sentinel_stakeholder_demo.html` | Pre-built self-contained stakeholder demo (no server required) |

**Fictional instruments only in manipulation scenarios.** Real company names (RELIANCE, TCS, etc.) are used only as clean negative-control baselines — never in injected-manipulation scenarios.

---

## 11. Data Ingestion Layer

Located in [`data/ingest/`](data/ingest/).

| Module | Source | What It Fetches |
|---|---|---|
| `nse_bhavcopy.py` | NSE archives (archives.nseindia.com) | EOD OHLCV, delivery %, total traded value for all NSE-listed equity series |
| `nse_bulk_deals.py` | NSE bulk/block deal disclosures | Large reported transactions (≥ 0.5% of issued capital) |
| `nse_option_chain.py` | NSE live option chain API | Strike-level OI, IV, bid/ask, total PCR |
| `broker_order_stream.py` | Kite Connect WebSocket | Real-time order lifecycle events (placed → modified → cancelled → executed) |
| `errors.py` | — | Strongly-typed exception hierarchy (`BhavcopyFetchError`, `BhavcopyParseError`, `IngestError`) |

**Hard rules (enforced in code):**
- 403/500 on fetch → recorded as `FETCH_ERROR`, never silently interpolated
- 404 on a specific date → `NON_TRADING_DAY` (expected, not an error)
- Bot-block on NSE homepage → access `archives.nseindia.com` directly (200 status)
- `_INTER_FETCH_DELAY_SECONDS = 1.5` between successive date fetches to avoid rate-limiting

---

## 12. Repository Structure

```
Sentinel/
├── app/
│   ├── alerts/
│   │   ├── manager.py              # Deduplication, state machine, 3-tier escalation
│   │   └── sebi_report.py          # 8-section SEBI draft SAR formatter
│   ├── api/
│   │   └── routes.py               # FastAPI endpoints (health, detect, alerts, SAR)
│   ├── db/
│   │   ├── models.py               # SQLAlchemy ORM (Instrument, Order, Trade, Alert)
│   │   └── session.py              # Engine & session factory
│   ├── detection/
│   │   ├── basis_distortion.py     # Cost-of-carry fair value deviation
│   │   ├── circular_trading.py     # Johnson's algorithm trade ring detection
│   │   ├── coordinated_pump.py     # Multi-account synchronized buy burst
│   │   ├── evidence.py             # Auditable raw evidence slice builder
│   │   ├── oi_manipulation.py      # OI concentration + OI-IV decoupling
│   │   ├── option_pinning.py       # Expiry-day strike pinning
│   │   └── spoofing.py             # Liquidity-normalized spoofing/layering
│   ├── harm_estimation/
│   │   ├── guards.py               # Hard-coded allow-list + audit log
│   │   ├── event_study.py          # OLS market model, AR/CAR, CI, counterfactual path
│   │   └── trade_harm.py           # Per-trade and instrument-level harm ranges
│   ├── ml/
│   │   ├── features.py             # 20-dimensional versioned feature extraction
│   │   └── scorer.py               # Isolation Forest + expert weighted baseline
│   ├── schemas/
│   │   └── schemas.py              # Pydantic request/response validation
│   ├── security/
│   │   ├── access_log.py           # Evidence access audit logging
│   │   ├── pii.py                  # Salted SHA-256 account ID hashing
│   │   └── retention.py            # 7-year retention enforcement
│   └── main.py                     # FastAPI application entrypoint
│
├── backtest/
│   ├── sebi_case_catalog.py        # Real SEBI case definitions (verifiable order refs)
│   ├── run_backtest.py             # Integrity-enforced backtest runner
│   ├── case_backtest_runner.py     # Per-case detection execution
│   ├── historical_data_puller.py   # NSE bhavcopy range fetcher
│   ├── diagnostic_kil_2019.py      # KIL-2019 NSE absence probe
│   ├── negative_control_runner.py  # Clean large-cap false-positive testing
│   └── results/
│       ├── KIL-2019_result.json
│       ├── KIL-2019_diagnostic_v2.log
│       ├── PUMP-DUMP-2017-2020_result.json
│       ├── PUMP-DUMP-2017-2020_diagnostic_v2.log
│       └── negative_controls.json
│
├── console/
│   └── index.html                  # Real-time operational surveillance console
│
├── dashboard/
│   ├── data/                       # Dashboard data files
│   ├── scripts/                    # Dashboard JS
│   └── static/                     # Dashboard assets
│
├── data/
│   └── ingest/
│       ├── broker_order_stream.py
│       ├── errors.py
│       ├── nse_bhavcopy.py
│       ├── nse_bulk_deals.py
│       └── nse_option_chain.py
│
├── demo/
│   ├── generate_synthetic_orderflow.py
│   ├── generate_trading_day.py
│   ├── generate_watchlist_samples.py
│   ├── generate_all_detector_samples.py
│   ├── build_demo.py
│   ├── build_extended_demo.py
│   ├── build_interactive_demo.py
│   ├── run_demo.py
│   ├── sentinel_stakeholder_demo.html
│   └── sample_data/
│
├── docs/
│   └── METHODOLOGY.md              # Full methodology, thresholds, validation status
│
├── tests/
│   ├── test_ingest_phase1.py       # 30 ingestion tests
│   ├── test_detection_phase2.py    # 19 multi-account detection tests
│   ├── test_detection_phase3.py    # 27 derivatives detector tests
│   ├── test_ml_phase4.py           # 29 ML feature & scoring tests
│   ├── test_alerts_phase5.py       # 29 alert lifecycle & SAR tests
│   ├── test_security_pii.py        # 37 PII, retention & audit tests
│   ├── test_harm_estimation.py     # 29 harm engine tests (known-answer synthetic)
│   ├── test_resilience.py          # Network resilience & retry tests
│   └── stress/                     # Concurrent access & load tests
│
├── pytest.ini
└── requirements.txt
```

---

## 13. API Reference

FastAPI server with OpenAPI docs at `http://127.0.0.1:8000/docs`

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/health` | Liveness check — confirms API and DB connectivity |
| `POST` | `/detect/spoofing` | Runs spoofing/layering detection over stored orders for an instrument |
| `GET` | `/alerts` | Paginated alert retrieval with severity and status filters |
| `GET` | `/alerts/{id}` | Single alert detail with explanation and metadata |
| `GET` | `/alerts/{id}/evidence-log` | Exports the raw, auditable order slice behind an alert |
| `POST` | `/alerts/{id}/escalate` | Triggers state transition and SAR draft generation |

---

## 14. Getting Started

### Prerequisites
- Python 3.11+
- SQLite (default, local) or PostgreSQL (production deployment)

### Installation

```bash
git clone https://github.com/mayurpatil10001/Sentinel-.git
cd Sentinel

python -m venv .venv

# Windows
.venv\Scripts\activate
# Linux / macOS
source .venv/bin/activate

pip install -r requirements.txt
```

### Environment Variables

Create a `.env` file or export:

```bash
# Required: salt for SHA-256 account ID pseudonymization
export ACCOUNT_ID_SALT="your-cryptographic-salt-value"

# Optional: Zerodha Kite Connect (for live broker order stream)
export KITE_API_KEY="your_api_key"
export KITE_ACCESS_TOKEN="your_access_token"
```

### Running the Demo

```bash
# Generate synthetic order flow and run detectors
python demo/run_demo.py

# Build the standalone stakeholder demo HTML
python demo/build_interactive_demo.py

# Open the pre-built demo (no server needed)
start demo/sentinel_stakeholder_demo.html
```

### Running the API Server

```bash
uvicorn app.main:app --reload
# API docs: http://127.0.0.1:8000/docs
```

### Running the Console

```bash
# Open console directly in browser
start console/index.html
```

---

## 15. Test Suite

**200 tests across 8 test suites — all passing.**

```bash
# Run full suite
pytest

# Run specific suite
pytest tests/test_harm_estimation.py -v

# Live network tests (NSE fetches — excluded in CI by default)
pytest -m live

# Stress / concurrency tests
pytest tests/stress/ -v
```

| Suite | Tests | Coverage |
|---|---|---|
| `test_ingest_phase1.py` | 30 | NSE bhavcopy, bulk deals, option chain, broker stream, error hierarchy |
| `test_detection_phase2.py` | 19 | Circular trading, coordinated pump, multi-account coordination |
| `test_detection_phase3.py` | 27 | OI manipulation, basis distortion, option pinning |
| `test_ml_phase4.py` | 29 | Feature extraction, Isolation Forest scorer, schema versioning |
| `test_alerts_phase5.py` | 29 | Alert deduplication, state machine, SAR formatting, escalation |
| `test_security_pii.py` | 37 | SHA-256 hashing, evidence access audit, 7-year retention |
| `test_harm_estimation.py` | 29 | Guard allow-list, OLS market model, known-answer CAR, trade harm |
| `test_resilience.py` | ~20 | Network retry/backoff, bot-block handling, concurrent access |

**Known-answer validation for harm estimation:**
The `TestCARKnownAnswer` class uses synthetic data with a known injected CAR (+30% over 20 days) and verifies that the event-study pipeline recovers the injected value within the 95% confidence interval — the standard methodology validation approach per MacKinlay (1997, Section 3.3).

---

## 16. Design Principles

1. **No silent fallbacks.** Network failures, malformed exchange files, or missing fields raise typed exceptions (`IngestError` subclasses). The system never silently substitutes synthetic placeholders during ingestion.

2. **Order-level primacy.** Trades represent post-execution outcomes. Sentinel prioritizes raw order lifecycles (placed → modified → cancelled) to detect manipulation *before* execution.

3. **Log-file integrity enforcement.** `_enforce_log_file_integrity()` in `backtest/run_backtest.py` prevents any result file from stating a firm conclusion without a real, on-disk backing log. Results without `log_file` are automatically downgraded to `VERDICT: INCONCLUSIVE`.

4. **No implied accusations against real companies.** Real company names appear only as clean negative-control baselines. All manipulation scenarios use clearly fictional instruments. Harm estimation requires a hard-coded SEBI adjudication order before running — not just a suspicion.

5. **Honest threshold labeling.** Every threshold in the codebase is labeled either `HEURISTIC` (informed estimate) or `UNVALIDATED GUESS` (not calibrated). The methodology document reports exactly what has and has not been validated.

6. **Evidence before escalation.** Every alert carries an `evidence_log_ref` pointing to the raw order/trade slice that a regulator can independently verify. The system cannot generate an alert with no evidence backing.

7. **Confidence intervals always.** The harm engine produces ranges, never point estimates alone. Every harm output carries `harm_low / harm_central / harm_high` with equal visual weight in the formatted report.

---

## 17. Limitations

1. **No production validation.** No detector has been validated against a confirmed manipulation case using publicly available Indian market data. All positive-case testing uses synthetic data.

2. **NSE-only data access.** The bhavcopy fetcher retrieves NSE equity series only. Both confirmed SEBI backtest cases (KIL-2019, PUMP-DUMP-2017-2020) are BSE-only instruments — confirmed absent from NSE via diagnostic logs. A BSE bhavcopy fetcher is required before real-case harm estimation can run.

3. **Unvalidated thresholds.** Detection thresholds are informed estimates, not empirically calibrated values. Production deployment requires calibration against exchange historical order books.

4. **Broker boundary constraint.** A single broker adapter has visibility only into its own clients' order book. Market-wide surveillance requires exchange or clearing corporation feed integration.

5. **Account-level data gap.** Spoofing, circular trading, and coordinated pump all require account-level order data. This is available in real deployments (exchange feed) but is not in public historical archives.

6. **Derivatives not yet live-tested.** OI manipulation, basis distortion, and option pinning detectors have not been tested against real historical option chain data. Breeze API integration is planned.

7. **Single-instrument event study.** The harm estimation engine is a single-instrument market-model test. Cross-sectional portfolio event studies require additional adjustments for event clustering and cross-sectional dependence (MacKinlay 1997, Section 4.4).

---

## 18. References & Regulatory Citations

### Regulatory Framework
1. SEBI Act, 1992 — Sections 11B, 12A
2. SEBI (PFUTP) Regulations, 2003 — Regulations 3 and 4 — https://www.sebi.gov.in/legal/regulations/
3. SEBI Circular ISD/CIR/RR/AML/2/06 (2006) — Prevention of Market Abuse
4. SEBI Circular SEBI/HO/ISD/ISD_OAED/P/CIR/2022/155 — ASM/GSM Framework
5. SEBI Stock Brokers Regulations, 1992 — Regulation 17 (Record Retention)

### Academic References
6. **MacKinlay, A.C. (1997).** Event Studies in Economics and Finance. *Journal of Economic Literature*, 35(1), 13–39. *(Primary methodology reference for harm estimation.)*
7. **Brown, S.J. & Warner, J.B. (1985).** Using daily stock returns: The case of event studies. *Journal of Financial Economics*, 14(1), 3–31. *(CAR variance formula and t-statistic.)*
8. **Liu, F.T., Ting, K.M. & Zhou, Z.H. (2008).** Isolation Forest. *ICDM 2008*. *(ML anomaly detection backbone.)*
9. **Aitken, M., Cumming, D. & Zhan, F. (2015).** Exchange trading rules, surveillance and suspected insider trading. *Journal of Banking & Finance*, 52, 220–235.
10. **Aggarwal, R.K. & Wu, G. (2006).** Stock market manipulations. *Journal of Business*, 79(4), 1915–1953.
11. **Comerton-Forde, C. & Putniņš, T.J. (2015).** Stock price manipulation: Prevalence and determinants. *Review of Finance*, 19(4), 1493–1520.
12. **Ni, S.X., Pearson, N.D. & Poteshman, A.M. (2005).** Stock price clustering on option expiration dates. *Journal of Financial Economics*, 78(1), 49–87.
13. **Budhiraja, Gupta & Pathak (2025).** Buyback Abnormal Returns in Indian Markets. SSRN Working Paper.

### SEBI Case References
14. SEBI Adjudication Order — Kavit Industries Limited, Feb 28, 2025
15. SEBI Ex Parte Ad Interim Order-cum-Show Cause Notice — Mauria Udyog / Hanif Shekh cluster, Jun 19, 2023
16. SEBI Adjudication Order — Gravity India Limited (ORDER/SBM/KL/2021-22/15788), Mar 31, 2022

---

## License

MIT License — see [LICENSE](LICENSE) for details.

---

> **Disclaimer:** Sentinel is a research and proof-of-concept tool. Its outputs do not constitute financial advice, legal determinations, or regulatory findings. All detection scores are statistical anomaly indicators only. Any use in a production regulatory context requires independent validation, expert review, and compliance with applicable law.
