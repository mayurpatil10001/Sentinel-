# Sentinel — Market Surveillance & Early-Stage Manipulation Detection

[![Test Suite](https://img.shields.io/badge/tests-171%20passing-brightgreen.svg)]()
[![Python](https://img.shields.io/badge/python-3.11-blue.svg)]()
[![License](https://img.shields.io/badge/license-MIT-blue.svg)]()
[![Regulatory Context](https://img.shields.io/badge/SEBI-PFUTP%202003-orange.svg)]()

A modular, multi-asset market surveillance engine tailored to Indian capital markets (NSE/BSE). Sentinel is designed to detect early-stage market manipulation across **equities, penny stocks, index derivatives, and equity options** before artificial volume and price distortion hit retail participants. Every detection is backed by an **auditable, tamper-evident evidence log** and standardized **draft Suspicious Activity Reports (SAR)** referencing SEBI (Prohibition of Fraudulent and Unfair Trade Practices) Regulations, 2003.

---

## Current Project Status

All core development phases are built, integrated, and verified against a comprehensive 171-test suite.

| Phase | Component | Key Modules | Test Suite | Tests |
|---|---|---|---|---|
| **Phase 1** | **Data Ingestion** | `data/ingest/nse_bhavcopy.py`<br>`data/ingest/nse_bulk_deals.py`<br>`data/ingest/nse_option_chain.py`<br>`data/ingest/broker_order_stream.py`<br>`data/ingest/errors.py` | `tests/test_ingest_phase1.py` | 30 |
| **Phase 2** | **Multi-Account Coordination** | `app/detection/circular_trading.py`<br>`app/detection/coordinated_pump.py` | `tests/test_detection_phase2.py` | 19 |
| **Phase 3** | **Derivatives-Specific Detectors** | `app/detection/oi_manipulation.py`<br>`app/detection/basis_distortion.py`<br>`app/detection/option_pinning.py` | `tests/test_detection_phase3.py` | 27 |
| **Phase 4** | **Machine Learning Layer** | `app/ml/features.py`<br>`app/ml/scorer.py` | `tests/test_ml_phase4.py` | 29 |
| **Phase 5** | **Alert Lifecycle & SEBI SAR** | `app/alerts/manager.py`<br>`app/alerts/sebi_report.py`<br>`app/api/routes.py` | `tests/test_alerts_phase5.py` | 29 |
| **Phase 5b** | **PII Protection & Audit Logging** | `app/security/pii.py`<br>`app/security/retention.py`<br>`app/security/access_log.py` | `tests/test_security_pii.py` | 37 |
| **Total** | **Full System Coverage** | **All Modules Integrated** | **6 Test Suites** | **171 Passed** |

---

## Architecture Overview

```
                      MARKET DATA INGESTION
 ┌─────────────────────────────────────────────────────────────┐
 │  NSE Bhavcopy (EOD OHLCV)  │  NSE Bulk & Block Deals (CSV)  │
 │  NSE Live Option Chain     │  Broker Stream (Kite Connect)  │
 └──────────────────────────────┬──────────────────────────────┘
                                │
                                ▼
                       SQL DATABASE LAYER
 ┌─────────────────────────────────────────────────────────────┐
 │  Instruments  │  Orders (with Salted Hash)  │  Trades       │
 └──────────────────────────────┬──────────────────────────────┘
                                │
                                ▼
                       DETECTION ENGINES
 ┌─────────────────────────────────────────────────────────────┐
 │ • Spoofing & Layering (Liquidity-normalized order book)     │
 │ • Circular Trading (Johnson's algorithm on trade graphs)   │
 │ • Coordinated Pump (Multi-account volume & price bursts)    │
 │ • OI Manipulation (Concentration & OI-IV decoupling)        │
 │ • Basis Distortion (Cash-futures cost-of-carry fair value) │
 │ • Option Pinning (Expiry spot clustering & Max-Pain)        │
 └──────────────────────────────┬──────────────────────────────┘
                                │
                                ▼
                     ML SCORING LAYER (v1)
 ┌─────────────────────────────────────────────────────────────┐
 │ • 20-Dimensional Signal Feature Vector                      │
 │ • Isolation Forest Anomaly Detection (Liu et al., 2008)     │
 │ • Weighted Expert Baseline (Dual Scoring)                   │
 └──────────────────────────────┬──────────────────────────────┘
                                │
                                ▼
                   ALERT LIFECYCLE & GOVERNANCE
 ┌─────────────────────────────────────────────────────────────┐
 │ • Deduplication & State Transitions (Open/Investigating/...)│
 │ • 3-Tier Escalation Thresholds                              │
 │ • 8-Section Draft SAR Generator (SEBI PFUTP 2003 / ISD)     │
 └──────────────────────────────┬──────────────────────────────┘
                                │
                                ▼
                   SECURITY, PII & COMPLIANCE
 ┌─────────────────────────────────────────────────────────────┐
 │ • Salted SHA-256 Hashed Account IDs (`account_id_hash`)     │
 │ • Tamper-evident Evidence Log Access Audit (`access_log.py`)│
 │ • 7-Year Data Retention Enforcement (`retention.py`)        │
 └─────────────────────────────────────────────────────────────┘
```

---

## Core Detection Capabilities

### 1. Spoofing and Layering (`app/detection/spoofing.py`)
- **Mechanism**: Tracks high cancellation ratios, asymmetric order placement depth, and rapid order lifespans before execution.
- **Normalization**: Normalized against instrument rolling median order size and average daily volume (ADV) to avoid penalizing volatile small caps or high-frequency market making on large caps.
- **Regulatory Reference**: SEBI PFUTP Reg 4(2)(a), (b), (g).

### 2. Circular / Wash Trading (`app/detection/circular_trading.py`)
- **Mechanism**: Builds directed trade graphs across accounts and applies **Johnson's Cycle-Finding Algorithm** (`networkx.simple_cycles`) to detect closed trading rings ($A \rightarrow B \rightarrow C \rightarrow A$).
- **Filtering**: Computes gross vs. net inventory change across ring participants (flags near-zero net changes with high gross turnover).
- **Illiquidity Protection**: Automatically discounts anomaly scores and flags warnings for illiquid stocks where natural counterparty matching can mirror circular loops.
- **Regulatory Reference**: SEBI PFUTP Reg 4(2)(a), (b).

### 3. Coordinated Pump Detection (`app/detection/coordinated_pump.py`)
- **Mechanism**: Detects synchronized buy-side aggression across 3+ independent accounts within rolling time windows, combined with abnormal price movement and volume expansion ($\ge 5\times$).
- **Participant Profiling**: Identifies dormant accounts suddenly reactivated to participate in volume spikes.
- **Regulatory Reference**: SEBI PFUTP Reg 4(2)(d), (e).

### 4. Derivatives: Open Interest Manipulation (`app/detection/oi_manipulation.py`)
- **OI Concentration**: Flags strikes holding $\ge 35\%$ of total contract OI, with dynamic weighting penalizing deep out-of-the-money (OTM) concentration over natural at-the-money (ATM) clustering.
- **OI-IV Decoupling**: Tracks rapid open interest surges accompanied by sharp drops in implied volatility (IV), indicating aggressive unhedged naked option writing.
- **Regulatory Reference**: SEBI PFUTP Reg 4(2)(h).

### 5. Derivatives: Cash-Futures Basis Distortion (`app/detection/basis_distortion.py`)
- **Mechanism**: Evaluates the deviation between actual futures prices and theoretical fair value via the Cost-of-Carry model:
  $$\text{Fair Value} = \text{Spot} \times \left(1 + r \times \frac{\text{DTE}}{365}\right)$$
- **Auditability**: Records the risk-free rate ($r = 6.5\%$ default) and deviation metrics directly on the signal for historical audit reproducibility.
- **Regulatory Reference**: SEBI PFUTP Reg 4(2)(h).

### 6. Derivatives: Expiry-Day Option Pinning (`app/detection/option_pinning.py`)
- **Mechanism**: Detects underlying spot price artificial pinning within 0.5% of high-OI strikes within 48 hours of contract expiry.
- **Max-Pain Analysis**: Computes the option chain Max-Pain strike and includes explicit false-positive warnings regarding natural market maker delta/gamma hedging.
- **Regulatory Reference**: SEBI PFUTP Reg 4(2)(h).

---

## Machine Learning & Dual Scoring (`app/ml/`)

Rather than relying on black-box predictions, Sentinel uses a transparent dual-scoring framework:

1. **20-Dimensional Feature Extraction (`app/ml/features.py`)**:
   - Captures pattern scores, participation breadth, volume multiples, and detector co-occurrence counts.
   - Schema versioning (`SCHEMA_VERSION = 1`) ensures saved models reject incompatible feature sets on load.
2. **Isolation Forest Anomaly Scorer (`app/ml/scorer.py`)**:
   - Uses unsupervised tree-based space partitioning (Liu et al., 2008) suited for unlabelled financial anomaly detection.
3. **Expert Weighted Baseline**:
   - Combines the Isolation Forest score with a deterministic weighted heuristic baseline, ensuring the engine remains functional and interpretable even prior to model training.

> **Mandatory Disclosure**: Anomaly scores reflect statistical divergence from regular trading baselines. Every explanation string carries the mandatory disclaimer:
> *"This is an anomaly score, NOT a manipulation probability or legal determination."*

---

## Regulatory Reporting & Alert Governance (`app/alerts/`)

### 3-Tier Escalation Framework
- **Tier 1 (Internal Analyst Queue)**: Score $\ge 0.45$, Severity $\ge \text{Medium}$. Assigned for routine surveillance analysis.
- **Tier 2 (Supervisor Review)**: Score $\ge 0.70$, Severity $\ge \text{High}$. Requires supervisory review within 2 business days.
- **Tier 3 (Draft SAR / SEBI Referral)**: Score $\ge 0.85$, Severity $= \text{Critical}$. Generates draft regulatory filing; human sign-off mandatory.

### Draft Suspicious Activity Report (SAR)
Formats alerts into an 8-section standardized dossier matching the SEBI Integrated Surveillance Department (ISD) format:
1. Reference & Filing Metadata
2. Target Entity & Instrument Details
3. Alleged PFUTP Violations & Regulatory Clauses
4. Quantitative Evidence Log Summary
5. Trade & Order Execution Timeline
6. Coordinated Accounts & Entity Network
7. Pattern-Specific Explanations & False Positive Notes
8. Compliance Officer Sign-off & Submission Checklist

*Note: Sentinel does not auto-file reports to SEBI. Draft SARs must be vetted by authorized compliance personnel before submission via the SEBI SCORES portal.*

---

## Security, PII & Compliance (`app/security/`)

- **Salted PII Hashing (`app/security/pii.py`)**:
  - Client account identifiers are converted to salted SHA-256 digests (`account_id_hash`) using an environment-provided salt (`ACCOUNT_ID_SALT`).
  - Dual-column architecture maintains `account_id` alongside `account_id_hash` for regulatory compliance access while protecting logs and external reports.
- **Evidence Access Auditing (`app/security/access_log.py`)**:
  - Access to raw order/trade evidence logs is recorded in an immutable audit table (`evidence_access_log`) capturing user ID, IP address, justification reason, and access timestamp.
- **Data Retention Enforcement (`app/security/retention.py`)**:
  - Implements scheduled pruning for historical orders, trades, and alerts past the statutory threshold (7-year reasoned estimate, 2,555 days, benchmarked against SEBI Stock Brokers Regulations 1992 Reg 17).
  - Dry-run capability provided for pre-execution compliance review.

---

## Repository Structure

```
Sentinel/
├── app/
│   ├── alerts/
│   │   ├── manager.py            # Deduplication, escalation & state machine
│   │   └── sebi_report.py        # 8-section SEBI draft SAR formatter
│   ├── api/
│   │   └── routes.py             # FastAPI endpoints (health, detection, alerts, SAR)
│   ├── db/
│   │   ├── models.py             # SQLAlchemy models (Order, Trade, Instrument, Alert)
│   │   └── session.py            # Engine & session management
│   ├── detection/
│   │   ├── basis_distortion.py   # Cash-futures basis vs. cost-of-carry
│   │   ├── circular_trading.py   # Johnson's algorithm trade ring detection
│   │   ├── coordinated_pump.py   # Multi-account pump detection
│   │   ├── evidence.py           # Raw audit evidence slice builder
│   │   ├── oi_manipulation.py    # OI concentration & OI-IV decoupling
│   │   ├── option_pinning.py     # Expiry-day strike pinning detection
│   │   └── spoofing.py           # Liquidity-normalized spoofing/layering
│   ├── ml/
│   │   ├── features.py           # 20-feature extraction pipeline (versioned)
│   │   └── scorer.py             # Isolation Forest + weighted baseline scorer
│   ├── schemas/
│   │   └── schemas.py            # Pydantic request/response validation
│   ├── security/
│   │   ├── access_log.py         # Evidence access audit logging
│   │   ├── pii.py                # Salted SHA-256 account ID hashing
│   │   └── retention.py          # 7-year regulatory retention policy enforcement
│   └── main.py                   # Application entrypoint
├── data/
│   └── ingest/
│       ├── broker_order_stream.py # Kite Connect WebSocket order stream adapter
│       ├── errors.py              # Strongly-typed ingest exception hierarchy
│       ├── nse_bhavcopy.py        # EOD OHLCV and delivery data parser
│       ├── nse_bulk_deals.py      # Bulk and block deal disclosures
│       └── nse_option_chain.py    # Live option chain scraper with session handshake
├── demo/
│   ├── generate_synthetic_orderflow.py # Realistic order flow generator with spoofing
│   └── run_demo.py               # End-to-end pipeline verification script
├── tests/
│   ├── test_alerts_phase5.py     # Alert manager & SAR tests (29 tests)
│   ├── test_detection_phase2.py  # Multi-account detection tests (19 tests)
│   ├── test_detection_phase3.py  # Derivatives detector tests (27 tests)
│   ├── test_ingest_phase1.py     # Ingestion tests (30 tests)
│   ├── test_ml_phase4.py         # ML feature & scoring tests (29 tests)
│   └── test_security_pii.py      # PII, retention & audit tests (37 tests)
├── pytest.ini                    # Pytest configuration & test markers
└── requirements.txt              # Core runtime & analysis dependencies
```

---

## API Reference

The FastAPI server exposes endpoints for operational surveillance and compliance queries:

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/health` | Health check endpoint confirming API status. |
| `POST` | `/detect/spoofing` | Runs spoofing detection over stored order data for an instrument. |
| `GET` | `/alerts` | Retrieves surveillance alerts with filtering and pagination. |
| `GET` | `/alerts/{id}/evidence-log` | Exports the exact, auditable order slice behind an alert. |

Interactive OpenAPI documentation is available at `http://127.0.0.1:8000/docs`.

---

## Getting Started

### 1. Prerequisites
- Python 3.11+
- SQLite (default local) or PostgreSQL (production)

### 2. Installation
```bash
git clone https://github.com/mayurpatil10001/Sentinel-.git
cd Sentinel
python -m venv .venv
# On Windows:
.venv\Scripts\activate
# On Linux/macOS:
source .venv/bin/activate

pip install -r requirements.txt
```

### 3. Environment Configuration
Create a `.env` file or export required environment variables:
```bash
# Salt for SHA-256 client account ID pseudonymization (REQUIRED)
export ACCOUNT_ID_SALT="your-cryptographic-salt-value"

# Optional: Zerodha Kite Connect credentials for live broker stream
export KITE_API_KEY="your_api_key"
export KITE_ACCESS_TOKEN="your_access_token"
```

### 4. Running Tests
Run the entire unit and integration test suite (171 tests):
```bash
pytest
```
*Note: Live network integration tests (NSE website fetch) are marked with `@pytest.mark.live` and excluded by default in CI to avoid rate limits. To run them:*
```bash
pytest -m live
```

### 5. Running the Demo & API Server
```bash
# Run synthetic order flow generation & detection demo
python demo/run_demo.py

# Launch FastAPI surveillance server
uvicorn app.main:app --reload
```

---

## Design Principles & Limitations

1. **No Silent Fallbacks**: Network failures, malformed exchange files, or missing fields raise typed exceptions (`IngestError` subclasses). The system never silently generates synthetic placeholders during production ingestion.
2. **Order-Level Focus**: Trades represent post-execution outcomes. Sentinel prioritizes raw order lifecycles (placed $\rightarrow$ modified $\rightarrow$ cancelled) to identify manipulation patterns before execution occurs.
3. **Unvalidated Threshold Notice**: Detection thresholds are documented as `HEURISTIC` or `UNVALIDATED GUESS`. Production deployment requires calibration against exchange historical order books and regulatory enforcement precedents.
4. **Broker Boundary Constraint**: A single broker adapter (e.g. Kite Connect) has visibility only into its own clients' order book. Market-wide surveillance requires exchange or clearing corporation feed integration.
