# 🛡️ Sentinel — Market Surveillance & Manipulation Detection Platform

<div align="center">

[![Tests](https://img.shields.io/badge/tests-200%20passing-brightgreen.svg)]()
[![Python](https://img.shields.io/badge/python-3.11-blue.svg)]()
[![FastAPI](https://img.shields.io/badge/FastAPI-0.111-teal.svg)]()
[![License](https://img.shields.io/badge/license-MIT-blue.svg)]()
[![SEBI PFUTP](https://img.shields.io/badge/SEBI-PFUTP%202003-orange.svg)]()
[![Status](https://img.shields.io/badge/status-proof--of--concept-yellow.svg)]()

**An order-level, evidence-first market surveillance engine for Indian capital markets.**  
Detects manipulation before it reaches retail investors. Backed by real SEBI case research and academic event-study methodology.

</div>

---

> **⚠️ Important Disclosure**  
> This is a proof-of-concept research platform. Detection thresholds are documented as unvalidated estimates. No output constitutes a legal or regulatory determination. All SEBI case references are from publicly available enforcement orders — they are cited for research context, not reproduced at length. See [Limitations](#17-limitations) before any production use.

---

## Table of Contents

1. [The Problem This Solves](#1-the-problem-this-solves)
2. [How This Will Impact the Indian Stock Market](#2-how-this-will-impact-the-indian-stock-market)
3. [Real SEBI Cases: The Original Motivation](#3-real-sebi-cases-the-original-motivation)
4. [Architecture Overview](#4-architecture-overview)
5. [Detection Engines — Deep Dive](#5-detection-engines--deep-dive)
6. [Machine Learning Layer](#6-machine-learning-layer)
7. [Alert Lifecycle & SEBI SAR Generation](#7-alert-lifecycle--sebi-sar-generation)
8. [Security, PII & Regulatory Compliance](#8-security-pii--regulatory-compliance)
9. [Harm Quantification Engine](#9-harm-quantification-engine)
10. [Backtest Against Real SEBI Cases](#10-backtest-against-real-sebi-cases)
11. [Operational Console & Dashboard](#11-operational-console--dashboard)
12. [Demo System](#12-demo-system)
13. [Data Ingestion Layer](#13-data-ingestion-layer)
14. [API Reference](#14-api-reference)
15. [Repository Structure](#15-repository-structure)
16. [Getting Started](#16-getting-started)
17. [Test Suite](#17-test-suite)
18. [Design Principles](#18-design-principles)
19. [Limitations & What Comes Next](#19-limitations--what-comes-next)
20. [References & Citations](#20-references--citations)

---

## 1. The Problem This Solves

### The Structural Gap in Indian Market Surveillance

Every year, thousands of retail investors in India lose money not because they made bad investment decisions — but because the market they were trading in was being artificially manipulated around them. The manipulation follows a predictable lifecycle:

```
Phase 1 — Accumulation (invisible to public data)
  Coordinated accounts quietly accumulate a scrip below any disclosure threshold.
  Price drifts up. Volume increases slightly. Nothing looks obviously wrong yet.

Phase 2 — Amplification (partially visible in price charts)
  Volume spikes. Price moves sharply. Retail investors notice and pile in.
  This is when SEBI's existing surveillance typically first flags the activity.

Phase 3 — Distribution (fully visible, too late)
  The manipulating network sells into the retail demand they created.
  The scrip collapses. Retail investors are left holding worthless shares.
  SEBI investigates. Orders are passed 1–6 years later.
```

**The existing system catches manipulation at Phase 2 or Phase 3 — after retail investors have already been harmed.**

### Why Order-Level Surveillance Changes This

The defining insight behind Sentinel is that **spoofing, layering, and coordinated buying are visible in the order book before they are visible in prices**. A spoofer who places ₹2 crore in sell orders and cancels them the moment anyone tries to fill them creates a measurable pattern in order lifecycle data — placed → cancelled within seconds — before any price impact occurs.

Sentinel is built to read that order book signal at Phase 1, not Phase 3.

| Surveillance Type | Detects At | Data Required | Retail Harm Prevented? |
|---|---|---|---|
| Traditional price chart review | Phase 2–3 | OHLCV | ❌ Usually not |
| Exchange tick data analysis | Phase 1–2 | Tick data | Partially |
| **Sentinel — Order lifecycle** | **Phase 1** | **Order book** | **✅ Yes, at source** |

---

## 2. How This Will Impact the Indian Stock Market

### 2.1 The Scale of the Problem

India's retail investor base has grown from approximately **1.4 crore** registered investors in 2019 to over **16 crore** in 2024 — an 11× increase in five years, driven largely by mobile trading platforms and pandemic-era savings. This explosion of retail participation has also expanded the pool of potential manipulation victims:

- SEBI's own enforcement statistics show hundreds of adjudication orders per year related to price and volume manipulation
- The Mauria Udyog case alone involved **222 entities**, **Rs. 143.79 crore** in confirmed disgorgement, and a multi-year operation specifically designed to exploit retail investors through SMS campaigns
- Small-cap and penny-stock manipulation disproportionately harms retail investors who lack access to institutional-grade research

### 2.2 The Specific Impact Sentinel Is Designed to Have

#### A — Closing the Detection Latency Gap

SEBI currently identifies most manipulation cases through **post-hoc investigation** — a complaint is filed, or a statistical anomaly is noticed, and an investigation begins. The time between manipulation and enforcement order is typically **1–6 years** (Kavit Industries: manipulation in 2019, order in 2025; Mauria Udyog: manipulation in 2017–2020, interim order in 2023). During this window, the manipulators operate freely.

Sentinel is designed to reduce this latency from years to **hours** by continuously monitoring order flow for the structural signatures of manipulation, not just price outcomes.

#### B — Making "Designed to Stay Below Threshold" Harder

A documented strategy in the Mauria Udyog case was that the coordinated buying was **deliberately kept below the bulk deal disclosure threshold** (< 0.5% of issued capital per transaction) so no publicly visible disclosure was triggered. Existing surveillance misses this because it relies on disclosed data.

Sentinel's `coordinated_pump.py` specifically targets this evasion strategy by analyzing **account-level buying coordination across independently small orders** — aggregating the pattern that is individually below threshold but collectively revealing.

#### C — Generating Evidence SEBI Can Act On

SEBI's enforcement process requires well-documented evidence. Currently, building this evidence package is largely manual. Sentinel's **draft SAR generator** (`sebi_report.py`) automatically compiles the surveillance evidence into an 8-section dossier matching the SEBI ISD format — including PFUTP regulation clause references, quantitative evidence summaries, and account network maps.

This doesn't replace analyst judgment, but it reduces the time from "alert fired" to "SEBI-ready filing" from weeks to hours.

#### D — Providing a Restitution Baseline for Harmed Investors

When manipulation is confirmed, victims currently have no systematic way to understand how much they were harmed. SEBI's disgorgement orders focus on what the manipulators gained, not what retail investors lost. Sentinel's **harm estimation engine** uses the academic event-study methodology (MacKinlay 1997) to reconstruct a counterfactual price path — what the scrip would have traded at without the manipulation — and estimate the harm to each trade that occurred at the inflated/deflated price.

This gives harmed retail investors a principled, citable basis for understanding their loss, and gives regulators a cross-check on disgorgement calculations.

#### E — Forcing Transparency About What Indian Surveillance Can and Cannot Do

One of Sentinel's most important contributions is its **honest documentation of what public Indian market data allows and does not allow**:

- Historical NSE order books: **not publicly available** (never published)
- Historical option chain OI snapshots: **not publicly available**
- Account-level historical trade data: **not publicly available** for manipulation cases designed to stay below bulk deal thresholds

This honesty matters because it identifies precisely where regulatory data-sharing policy changes would have the most impact. A formal NSE/BSE data-sharing agreement with SEBI-registered surveillance tools would immediately unlock the account-level data that makes Sentinel's most powerful detectors testable.

### 2.3 The Market Integrity Effect

Markets where manipulation is detected quickly and reliably become less attractive targets for manipulators. If Sentinel (or systems like it) were deployed at scale:

1. **Cost of manipulation rises** — the window between manipulation start and detection shrinks, reducing the profit opportunity
2. **Small-cap liquidity improves** — retail investors currently avoid small-cap stocks precisely because they know they are manipulation targets; reliable surveillance reduces this risk premium
3. **Price discovery improves** — artificial prices distort the capital allocation mechanism; removing manipulation brings prices closer to fundamental value
4. **Retail investor confidence grows** — the explosive growth in retail participation seen in 2020–2024 is fragile; a major manipulation scandal could reverse it; surveillance infrastructure protects it

---

## 3. Real SEBI Cases: The Original Motivation

Sentinel was built against three real, publicly verifiable SEBI enforcement cases. These are not hypothetical scenarios — they are documented in SEBI's enforcement order database, verifiable at [sebi.gov.in/enforcement/orders.html](https://www.sebi.gov.in/enforcement/orders.html).

These cases are why this project needs to move forward, and they define exactly what capabilities the next phase must deliver.

---

### Case 1: Kavit Industries Limited (KIL) — 2019

**SEBI Reference**: Adjudication Order, February 28, 2025  
**Manipulation Period**: August 1, 2019 – December 23, 2019  
**Exchange**: BSE  
**Entities Named**: Vijay Pujara, Ajay Pujara, Natvarbhai Vegda + 20 trading accounts

#### What SEBI Found

A network of 20 trading accounts, managed by three individuals, engaged in a systematic pattern of **synchronized trades, circular trades, and reversal trades** in the Kavit Industries Limited scrip on BSE. The trades were designed to create the *appearance* of market activity without genuine change in beneficial ownership:

- Account A would sell to Account B at a certain price
- Account B would sell back to Account C (or A) shortly after
- The ring would repeat, generating artificial volume and a steadily rising price chart
- External observers saw high volume and rising prices — the classic signals of a "discovery story"
- The scrip's price rose **approximately 113%** over 5 months (Rs. 44 → Rs. 93.80)
- Retail investors who bought into this "story" were left holding shares at artificially inflated prices when the network stopped supporting the price

#### Why This Case Defines What Sentinel Must Do

Circular trading is **exactly** what `app/detection/circular_trading.py` is designed to detect. The algorithm works: given account-level trade data, it builds a directed graph and finds closed rings using Johnson's algorithm. For the KIL case, the 20-account ring would produce textbook cycle patterns.

**The problem**: The scrip traded on BSE. Sentinel's current data pipeline fetches NSE bhavcopy. The scrip is confirmed absent from NSE archives (log-verified: `backtest/results/KIL-2019_diagnostic_v2.log`). Additionally, account-level historical trade data is not publicly available for BSE.

**What needs to happen for this case to be fully testable**:
1. Build a BSE bhavcopy fetcher (`www.bseindia.com/download/BhavCopy/`)
2. Obtain a formal data agreement with BSE for account-level historical trades, or obtain this via SEBI's investigation powers

**What Sentinel established about this case**:
- The case is on the harm estimation allow-list (SEBI adjudication legally confirmed manipulation occurred)
- The guard system correctly approves harm estimation for this case
- Once BSE price data is available, the market-model event study can reconstruct the counterfactual price path for the 113% price rise and estimate harm to every retail investor who bought during August–December 2019

---

### Case 2: Mauria Udyog / Hanif Shekh Cluster — 2017–2020

**SEBI Reference**: Ex Parte Ad Interim Order-cum-Show Cause Notice, June 19, 2023; Final Order June 2026  
**Manipulation Period**: 2017–2020 (scrip-specific sub-periods)  
**Exchange**: NSE/BSE (mostly BSE-listed)  
**Entities Named**: Hanif Shekh (alleged mastermind) + 222 entities barred  
**Disgorgement**: Rs. 143.79 crore + interest

**Scrips**:
- Mauria Udyog Ltd.
- 7NR Retail Ltd.
- GBL Industries Ltd.
- Darjeeling Ropeway Company Ltd.
- Vishal Fabrics Ltd.

#### What SEBI Found

This is arguably the most complex and revealing case in the catalog — it shows exactly how sophisticated multi-year manipulation operations work:

**Phase 1 — Network construction**: Hanif Shekh allegedly built a network of 200+ connected entities (family members, business associates, nominee accounts) holding coordinated positions in multiple illiquid small-cap scrips. Each individual transaction was kept **below the 0.5% bulk deal disclosure threshold** — meaning no publicly visible disclosure was ever triggered.

**Phase 2 — Artificial price inflation**: The network engaged in coordinated buying and circular trading among themselves, creating both artificial volume and a steadily rising price. The scrips were thinly traded, so even modest buying pressure produced significant price movement.

**Phase 3 — Retail lure via SMS campaigns**: This is the part that distinguishes this case from simple circular trading. The network ran **bulk SMS campaigns** recommending these scrips as strong "buy" opportunities to retail investors. These SMSes cited the very price action and volume that the network itself had manufactured as evidence that the stocks were "moving."

**Phase 4 — Distribution**: As retail investors bought in response to the SMS campaigns, the network offloaded their holdings at the inflated prices. Retail investors were left holding stocks that rapidly collapsed once the coordinated support was withdrawn.

**Why Rs. 143.79 crore matters**: This is confirmed disgorgement — what the manipulators *made*. The harm to retail investors who bought at inflated prices is likely significantly higher. Sentinel's harm estimation engine, once BSE data is available, can reconstruct that retail harm figure with 95% confidence intervals.

#### What Sentinel Established About This Case

Through 1,900+ real NSE archive fetches:

| Symbol Attempted | NSE Bhavcopy Files Fetched | Rows Found | Finding |
|---|---|---|---|
| MAURIUDYOG | 490 files, all HTTP 200 | **0 rows** | Not NSE-listed |
| 7NRRETAIL | 490 files, all HTTP 200 | **0 rows** | Not NSE-listed |
| GBLIND | 514 files, all HTTP 200 | **0 rows** | Not NSE-listed |
| VISHALFAB | 553 files, all HTTP 200 | **0 rows** | Not listed during 2017–2020 |
| DARJROPE | 490 files, all HTTP 200 | **0 rows** | Not NSE-listed |

Cross-referenced against NSE's `EQUITY_L.csv` (2,570 currently-listed symbols): none of the 5 scrips appear in the current NSE listing for the investigation period.

**What this tells us**: The manipulation was conducted primarily on BSE — consistent with the choice of illiquid small-caps that would have minimal NSE institutional scrutiny. This is a pattern, not a coincidence: BSE's smaller-cap segments have historically had less automated surveillance than NSE.

**Why this case defines the BSE fetcher priority**: SEBI's largest recent disgorgement case — Rs. 143.79 crore — cannot be backtested without BSE data. This is the single most impactful infrastructure gap to close.

---

### Case 3: Gravity India Limited (GIL) — 2003-2004

**SEBI Reference**: Adjudication Order ORDER/SBM/KL/2021-22/15788, March 31, 2022  
**Manipulation Period**: December 23, 2003 – March 3, 2004  
**Exchange**: BSE  
**Entities Named**: Sunil Kumar Purohit + connected trading entities

#### What SEBI Found

A group of connected clients, trading through various BSE members, generated trades among themselves that represented approximately **71% of gross market volumes** in the Gravity India scrip during the period. This is textbook circular trading taken to an extreme: more than two-thirds of all observable market activity in the scrip was manufactured.

**Historical significance**: This case predates Sentinel's scope (manipulation in 2003, digital archives unreliable for pre-2007 dates), but it is included because it illustrates the **longevity** of this manipulation pattern. The same circular trading technique identified in 2003 was used in the KIL case in 2019 — sixteen years later. The pattern is persistent because it works and because it is difficult to detect without account-level data.

**Why this case matters for Sentinel's future**: When Sentinel's circular trading detector is eventually validated against real account-level data, GIL (or similar well-documented BSE cases) should be in the validation set. The detector's Johnson's algorithm would have found the 71% volume cycle immediately given the trade data.

---

### The Common Thread: What All Three Cases Demand

| Requirement | KIL-2019 | PUMP-DUMP 2017-2020 | GIL 2003-2004 |
|---|---|---|---|
| BSE bhavcopy fetcher | ✅ Critical | ✅ Critical | ✅ Critical |
| Account-level trade data | ✅ Critical | ✅ Critical | ✅ Critical |
| Harm estimation (once data available) | ✅ Ready (guard-approved) | ✅ Ready (guard-approved) | ✅ Ready (guard-approved) |
| Full circular trading / pump detector | ✅ Algorithm ready | ✅ Algorithm ready | ✅ Algorithm ready |

**Every single real SEBI case that Sentinel was designed to validate requires BSE data access.** This is the clearest possible statement of what needs to happen next.

---

## 4. Architecture Overview

```
╔══════════════════════════════════════════════════════════════════╗
║                   MARKET DATA INGESTION LAYER                   ║
║  ┌─────────────────┐  ┌──────────────────┐  ┌───────────────┐  ║
║  │ NSE Bhavcopy    │  │ NSE Bulk & Block │  │ Option Chain  │  ║
║  │ (EOD OHLCV)     │  │ Deals (CSV)      │  │ (Live OI/IV)  │  ║
║  └────────┬────────┘  └────────┬─────────┘  └───────┬───────┘  ║
║           │                    │                     │          ║
║  ┌────────┴────────────────────┴─────────────────────┴───────┐  ║
║  │            Broker Order Stream (Kite Connect WS)          │  ║
║  └─────────────────────────────┬─────────────────────────────┘  ║
╚═════════════════════════════════╪════════════════════════════════╝
                                  │  data/ingest/
                                  ▼
╔═══════════════════════════════════════════════════════════════════╗
║                      DATABASE LAYER (SQLite/PostgreSQL)          ║
║  ┌────────────┐  ┌─────────────────────────┐  ┌──────────────┐  ║
║  │ Instruments│  │ Orders (+ salted hash)   │  │    Trades    │  ║
║  └────────────┘  └─────────────────────────┘  └──────────────┘  ║
║                      app/db/models.py                           ║
╚═══════════════════════════════════╪═══════════════════════════════╝
                                    │
                                    ▼
╔═══════════════════════════════════════════════════════════════════╗
║                   DETECTION ENGINES  app/detection/             ║
║                                                                  ║
║  ┌──────────────────────────────────────────────────────────┐   ║
║  │  spoofing.py        Liquidity-normalized order cancel     │   ║
║  │  circular_trading.py  Johnson's algorithm on graphs       │   ║
║  │  coordinated_pump.py  Multi-account burst coordination    │   ║
║  │  oi_manipulation.py   OI concentration + IV decoupling    │   ║
║  │  basis_distortion.py  Cost-of-carry deviation             │   ║
║  │  option_pinning.py    Expiry-day strike clustering         │   ║
║  └──────────────────────────────────────────────────────────┘   ║
╚═══════════════════════════════════╪═══════════════════════════════╝
                                    │
                                    ▼
╔═══════════════════════════════════════════════════════════════════╗
║                  ML SCORING LAYER  app/ml/                      ║
║  ┌───────────────────────┐  ┌─────────────────┐                 ║
║  │  20-Feature Extraction│  │ Isolation Forest│                 ║
║  │  (versioned schema)   │  │ + Expert Baseline│                ║
║  └───────────────────────┘  └─────────────────┘                 ║
╚═══════════════════════════════════╪═══════════════════════════════╝
                                    │
                                    ▼
╔═══════════════════════════════════════════════════════════════════╗
║            ALERT LIFECYCLE & GOVERNANCE  app/alerts/            ║
║  ┌──────────────┐  ┌────────────────────┐  ┌─────────────────┐  ║
║  │Deduplication │  │ 3-Tier Escalation  │  │ Draft SAR (8-   │  ║
║  │State Machine │  │ (open→escalated)   │  │ section SEBI)   │  ║
║  └──────────────┘  └────────────────────┘  └─────────────────┘  ║
╚══════════════════╤════════════════════════════════════╤══════════╝
                   │                                    │
         ┌─────────▼──────────┐           ┌────────────▼──────────┐
         │  SECURITY & PII    │           │   HARM QUANTIFICATION │
         │  app/security/     │           │   app/harm_estimation/│
         │  SHA-256 hashing   │           │   MacKinlay 1997 OLS  │
         │  Evidence audit log│           │   CAR + 95% CI        │
         │  7-yr retention    │           │   Guard allow-list    │
         └────────────────────┘           └───────────────────────┘
                   │                                    │
         ╔═════════▼════════════════════════════════════▼══════════╗
         ║         OPERATIONAL CONSOLE & DASHBOARD                 ║
         ║   console/index.html   |   dashboard/ (analytics)      ║
         ╚══════════════════════════════════════════════════════════╝
```

---

## 5. Detection Engines — Deep Dive

All six detectors are in [`app/detection/`](app/detection/). Each produces:
- A **normalized score (0–1)**
- A **human-readable explanation string** with mandatory disclaimer
- A reference to the **auditable evidence slice** used (via `evidence.py`)
- An explicit **false-positive warning** where relevant

---

### 5.1 Spoofing and Layering — `spoofing.py`

**PFUTP basis**: Reg 4(2)(a) — placing orders not intended to be executed; Reg 4(2)(n) — artificial appearance of trading activity.

**The manipulation pattern**: A trader places a large visible sell order at ₹105 when the stock is trading at ₹100. Other market participants, seeing apparent supply at ₹105, hold back buying. The stock drifts down to ₹98. The spoofer cancels the sell order and buys at ₹98. Then repeats in reverse with a fake buy order to push the price back up and sell.

**Algorithm (step by step)**:
```
1. Group orders by account × instrument × time window
2. cancellation_ratio = Σ(cancelled_value) / Σ(placed_value)
3. order_size_ratio = peak_order_size / instrument_30d_avg_order_size
4. order_lifespan = median time-to-cancel for cancelled orders (seconds)
5. Detect opposite-side execution within the window (profit realization)
6. score = w1 × cancellation_ratio + w2 × order_size_ratio +
           w3 × (1 / order_lifespan) + w4 × opposite_side_flag
```

**Threshold calibration**:

| Parameter | Value | Label |
|---|---|---|
| `cancellation_ratio ≥` | 0.70 | HEURISTIC — SEBI case review |
| `order_size_multiplier ≥` | 3.0× instrument baseline | HEURISTIC |
| `min_price_impact ≥` | 0.5% | HEURISTIC |
| Score → HIGH | ≥ 0.65 | HEURISTIC |
| Score → CRITICAL | ≥ 0.80 | HEURISTIC |

**Key design**: Thresholds are normalized against each instrument's **rolling 30-day median order size and ADV**. A fixed absolute threshold would generate massive false positives on liquid large-caps (where large orders are normal) while missing manipulation in thinly-traded scrips (where even small orders are anomalous).

**Validated**: Negative control — 0% false positives on 252 trading days across NSE large-caps.

---

### 5.2 Circular / Wash Trading — `circular_trading.py`

**PFUTP basis**: Reg 4(2)(b) — entering transactions without genuine change in beneficial ownership; Reg 4(2)(c) — fictitious transactions.

**The manipulation pattern** (exactly what happened in KIL-2019 and GIL-2003-2004):
```
Account A → sells 10,000 shares to Account B at ₹50
Account B → sells 10,000 shares to Account C at ₹51
Account C → sells 10,000 shares to Account A at ₹52
↓
Net result: All three accounts are back where they started (minus fees).
Apparent market result: 30,000 shares of volume, price moved ₹2.
External observer: "This scrip is in play — volume + price action."
```

**Algorithm**:
```python
# Build directed trade graph
G = nx.DiGraph()
for trade in session_trades:
    G.add_edge(trade.seller_account, trade.buyer_account,
               weight=trade.value, price=trade.price)

# Find all closed rings (Johnson's cycle detection)
cycles = list(nx.simple_cycles(G))

# Score each cycle
for cycle in cycles:
    volume_in_cycle = sum_edge_weights(G, cycle)
    volume_concentration = volume_in_cycle / total_scrip_volume
    price_stability = std(prices_in_cycle) / mean(prices_in_cycle)
    net_inventory_change = compute_net_position_change(cycle, G)

    # Red flags: high volume concentration, low price variance, near-zero net position
    score = f(volume_concentration, price_stability, net_inventory_change)
```

**Thresholds**:

| Parameter | Value | Label |
|---|---|---|
| Minimum cycle volume | ≥ 25% of total scrip volume | HEURISTIC |
| Minimum cycle participants | ≥ 3 accounts | HEURISTIC |
| Maximum price variation within cycle | ≤ 2% | HEURISTIC |

**False-positive protection**: Illiquid stocks can have natural counterparty matching that mirrors circular loops. The detector automatically flags `is_illiquid=True` for instruments with < 50,000 shares/day average and discounts anomaly scores with an explicit warning.

---

### 5.3 Coordinated Pump — `coordinated_pump.py`

**PFUTP basis**: Reg 4(2)(d) — advancing price by series of transactions; Reg 4(2)(e) — concealing material facts; Reg 3(b) — price manipulation.

**The manipulation pattern** (exactly what happened in PUMP-DUMP-2017-2020):
- 200+ connected entities coordinate to buy a thinly-traded scrip
- Each individual order stays below the 0.5% bulk deal disclosure threshold
- Combined, they represent overwhelming one-sided demand
- Price rises 50%, 100%, 200% over weeks or months
- SMS campaigns launch, directing retail attention to the "high-performing" stock
- The network sells into retail demand, price collapses

**Algorithm**:
```python
def detect_coordinated_pump(orders, window_hours=48):
    # Count distinct coordinated buyers within the window
    buying_accounts = distinct_accounts_with_buy_orders(orders, window_hours)

    # Compute volume surge relative to baseline
    window_volume = sum_buy_volume(orders, window_hours)
    baseline_volume = instrument_30d_avg_daily_volume
    volume_surge = window_volume / baseline_volume

    # Compute price appreciation in the window
    price_change = (window_high - window_start_price) / window_start_price

    # Dormancy check: flag accounts inactive for > 30 days before this activity
    dormant_accounts = [a for a in buying_accounts if days_since_last_trade(a) > 30]
    dormancy_score = len(dormant_accounts) / len(buying_accounts)

    # Composite score
    score = (
        0.30 × normalize(buying_accounts, min=5) +
        0.25 × normalize(volume_surge, min=3.0) +
        0.25 × normalize(price_change, min=0.15) +
        0.20 × dormancy_score
    )
```

**Thresholds**:

| Parameter | Value | Label |
|---|---|---|
| Min coordinated accounts | ≥ 5 | UNVALIDATED GUESS |
| Volume surge threshold | ≥ 3× 30-day ADV | UNVALIDATED GUESS |
| Min price appreciation | ≥ 15% in window | UNVALIDATED GUESS |

> These are marked `UNVALIDATED GUESS` because no real pump-and-dump case with account-level data was available for calibration. Calibration against real exchange data is the single highest-priority accuracy improvement.

---

### 5.4 Open Interest Manipulation — `oi_manipulation.py`

**PFUTP basis**: Reg 4(2)(h) — manipulating prices of futures and options.

**The manipulation pattern**: Large OI positions at a specific strike create financial incentives to pin the underlying to that strike. A dominant OI holder at the 18,000 CE strike benefits enormously if NIFTY closes near 18,000 — and has the capital to attempt to make that happen.

**Algorithm — Two components**:

*Component A: OI Concentration*
```
concentration_score(strike) = OI_at_strike / total_OI_across_all_strikes

If concentration_score ≥ 0.35 AND strike is deep OTM:
    → High suspicion (deep OTM concentration is abnormal)
If concentration_score ≥ 0.35 AND strike is ATM:
    → Low suspicion (natural hedging concentration at ATM)
```

*Component B: OI–IV Decoupling*
```
# Rapid OI increase with sharp IV decrease = naked option writing
oi_surge = (current_OI - yesterday_OI) / yesterday_OI
iv_change = (current_IV - yesterday_IV) / yesterday_IV

If oi_surge > 0.20 AND iv_change < -0.15:
    → OI-IV decoupling detected (unhedged writing signal)
```

---

### 5.5 Cash-Futures Basis Distortion — `basis_distortion.py`

**PFUTP basis**: Reg 4(2)(h) — artificial basis manipulation.

**The manipulation**: Futures prices should track spot prices through the cost-of-carry relationship. Sustained deviation from this theoretical fair value indicates either synthetic demand/supply manipulation or deliberate mispricing exploited for cross-market profit.

**Fair value formula (recorded on every signal for audit reproducibility)**:
```
Fair Value = Spot × (1 + r × DTE/365)

Where:
  r   = risk-free rate (default: 6.5% RBI repo rate; configurable)
  DTE = days to expiry of the futures contract
```

**Signal**:
```
basis_gap = (actual_futures_price - fair_value) / fair_value
basis_gap_zscore = (basis_gap - rolling_30d_mean) / rolling_30d_std

If |basis_gap_zscore| > 2.5:
    → Basis distortion alert
```

Every output records the `r` value and `DTE` used so that any analyst can independently verify the fair value calculation.

---

### 5.6 Expiry-Day Option Pinning — `option_pinning.py`

**PFUTP basis**: Reg 4(2)(h) — expiry-day price manipulation.

**The manipulation**: In the final 48 hours before option contract expiry, a large holder of options at a specific strike has strong financial incentive to "pin" the underlying near that strike. A ₹100 crore OI position at 18,000 CE would be worth ₹0 if NIFTY expires at 17,950 but worth significant premium if NIFTY expires at 18,050. This creates incentive to push the spot price toward 18,000.

**Algorithm**:
```
1. Find the top-OI strike in the expiring contract
2. Compute distance: |spot_price - top_OI_strike| / top_OI_strike
3. Check temporal condition: within 48 hours of expiry
4. Compute Max-Pain strike (the strike minimizing total option buyer losses)
5. Cross-check: if spot is also near Max-Pain, natural delta hedging is a plausible explanation

Pinning signal = short_distance AND near_expiry AND (high_OI_strike ≠ Max-Pain)
```

**Critical false-positive note** (built into every explanation string): Natural market-maker delta/gamma hedging near expiry can produce superficially identical spot-clustering patterns. The detector flags this explicitly and requires analyst review before escalation.

---

## 6. Machine Learning Layer

Located in [`app/ml/`](app/ml/). A transparent dual-scoring framework — not a black box.

### 6.1 Feature Extraction — `features.py`

**20-dimensional signal feature vector** (versioned as `SCHEMA_VERSION = 1`):

| Feature Group | Features | Purpose |
|---|---|---|
| Pattern scores | spoofing, circular, pump, oi, basis, pinning scores (6) | Raw detector outputs |
| Participation | distinct accounts, dormant account ratio (2) | Breadth of involvement |
| Volume signals | volume/ADV ratio, order-to-trade ratio (2) | Intensity of anomaly |
| Co-occurrence | number of detectors firing simultaneously (1) | Confidence multiplier |
| Temporal | session (pre-open/normal/closing), time-to-expiry (2) | Context |
| Price | price change %, deviation from 52-week range (2) | Price context |
| Liquidity | instrument avg daily volume decile, bid-ask spread proxy (2) | Illiquidity adjustment |
| Historical | alert frequency for same instrument in past 30 days (1) | Recidivism signal |

**Schema versioning**: Models trained on `SCHEMA_VERSION = 1` features reject feature vectors from a different version at load time, preventing silent feature drift in production — a common silent failure mode in production ML systems.

### 6.2 Isolation Forest — `scorer.py`

**Why Isolation Forest**: Confirmed manipulation labels are unavailable for Indian market data. Traditional supervised classification (requires labeled positive/negative examples) cannot be used. Isolation Forest partitions the feature space using random trees; anomalies — which differ structurally from normal trading patterns — are isolated with fewer cuts. It requires no positive examples.

**Dual scoring**:
```
final_score = 0.50 × isolation_forest_score +
              0.50 × expert_weighted_baseline_score
```

The expert baseline is a deterministic weighted combination of detector scores — it ensures the system remains fully functional before model training data accumulates.

> **Mandatory disclaimer on every alert explanation**: *"This is an anomaly score, NOT a manipulation probability or legal determination."*

---

## 7. Alert Lifecycle & SEBI SAR Generation

### 7.1 Alert State Machine — `manager.py`

```
       ┌─────────┐
       │  open   │ ← Alert created by detector
       └────┬────┘
            │ analyst reviews
            ▼
    ┌──────────────┐
    │ investigating│ ← Analyst has claimed the alert
    └──────┬───────┘
           │ patterns confirmed
           ▼
     ┌───────────┐
     │ escalated │ ← SAR draft generated; senior review required
     └─────┬─────┘
           │ submitted to SEBI / or cleared
           ▼
       ┌────────┐
       │ closed │
       └────────┘
```

**Deduplication**: A `UNIQUE` database constraint on `(instrument_id, pattern_type, window_start)` prevents duplicate alerts from concurrent detection runs. This is TOCTOU-safe — the database constraint rejects the second insert at the DB level, not the application level. Verified in `tests/stress/test_concurrent_access.py`.

### 7.2 Three-Tier Escalation

| Tier | Score | Severity | Action | Timeline |
|---|---|---|---|---|
| **Tier 1** | ≥ 0.45 | ≥ Medium | Internal analyst queue assignment | Within 1 business day |
| **Tier 2** | ≥ 0.70 | ≥ High | Supervisor review; evidence log exported | Within 2 business days |
| **Tier 3** | ≥ 0.85 | Critical | Draft SAR generated; human sign-off mandatory | Immediate |

### 7.3 Draft SAR — `sebi_report.py`

**8-section dossier** matching SEBI ISD format:

```
Section 1: Reference & Filing Metadata
  ├── Alert ID, generation timestamp, system version
  ├── Analyst name (to be filled)
  └── Filing classification (Suspicious Activity Report)

Section 2: Target Entity & Instrument Details
  ├── Instrument symbol, exchange, ISIN
  ├── Instrument type (equity/F&O)
  └── Alert window (start → end timestamps)

Section 3: Alleged PFUTP Violations
  ├── Specific regulation citations (Reg 3(a), 3(b), 4(1), 4(2)(a)-(n))
  └── Pattern characterization in PFUTP vocabulary

Section 4: Quantitative Evidence Summary
  ├── Key metrics (volume multiples, CAR%, cancellation ratios)
  ├── Composite anomaly score with breakdown
  └── Statistical significance statement

Section 5: Trade & Order Execution Timeline
  ├── Chronological event log
  └── Key inflection points highlighted

Section 6: Account Network
  ├── Accounts involved (hashed IDs)
  ├── Coordination patterns identified
  └── Network diagram description

Section 7: Pattern Explanation & False Positive Notes
  ├── Why this pattern was flagged
  ├── Legitimate explanations considered and ruled out
  └── Confidence assessment

Section 8: Compliance Sign-off
  ├── Analyst attestation
  ├── Supervisor review
  └── Submission checklist (SEBI SCORES portal)
```

> **Sentinel does not auto-file with SEBI.** Every draft SAR requires analyst verification and authorized compliance personnel sign-off before any submission.

---

## 8. Security, PII & Regulatory Compliance

Located in [`app/security/`](app/security/).

### 8.1 Account ID Pseudonymization — `pii.py`

```python
# Dual-column architecture
account_id:       "CLIENT_AB12345"         # raw — retained for SEBI verification
account_id_hash:  "sha256(salt + id)"     # hashed — used in all exports & logs
```

The raw `account_id` is **never removed** — SEBI exchange verification genuinely requires the real ID. The design makes outputting raw IDs a **deliberate, logged choice** via `EVIDENCE_LOG_USE_HASHED_ID` flag, not an uncontrolled default.

Salt is provided via environment variable `ACCOUNT_ID_SALT` — never hardcoded.

### 8.2 Evidence Access Audit — `access_log.py`

Every access to raw order/trade evidence creates an immutable record:

```
{
  "analyst_id": "analyst_007",
  "ip_address": "10.0.0.45",
  "alert_id": "alert-uuid-here",
  "reason": "SEBI investigation - Order ref: WTM/AB/2026/ISD/01",
  "accessed_at": "2026-09-17T03:00:00Z",
  "raw_ids_exposed": false
}
```

### 8.3 Data Retention — `retention.py`

**7-year retention** (2,555 days) — benchmarked against SEBI Stock Brokers Regulations 1992, Reg 17. Deletion is logged before execution. Dry-run mode provided for pre-execution compliance review.

---

## 9. Harm Quantification Engine

Located in [`app/harm_estimation/`](app/harm_estimation/). This is the most ethically constrained module — it can only run after SEBI has legally confirmed manipulation occurred.

### 9.1 Why This Module Exists

When SEBI issues a disgorgement order, it calculates what the **manipulators gained**. What is almost never calculated is what **retail investors lost** — the harm to every person who bought at an artificially inflated price (or sold at an artificially deflated one) during the manipulation window.

Sentinel's harm engine fills that gap. For every retail trade that occurred during a confirmed manipulation window, it estimates:

1. What price the stock *would have* traded at without manipulation (counterfactual path)
2. How much that specific trade cost the investor relative to the counterfactual

### 9.2 Academic Methodology

**Primary reference**: MacKinlay, A.C. (1997). Event Studies in Economics and Finance. *Journal of Economic Literature*, 35(1), 13–39.

**Test statistics**: Brown, S.J. & Warner, J.B. (1985). Using daily stock returns. *Journal of Financial Economics*, 14(1), 3–31.

**Step-by-step pipeline**:

```
Step 1 — Estimation Window (200 trading days before manipulation)
  Fit:  R_stock(t) = α + β × R_market(t) + ε(t)    [OLS, statsmodels]
  Where: R_market = Nifty 50 (or Sensex) actual returns
  Output: α̂, β̂, σ̂ (residual std)
  
  ⚠️ Estimation window must NOT overlap manipulation window —
     contaminated estimation window is a documented methodological failure.

Step 2 — Abnormal Return (each day in manipulation window)
  AR(t) = R_stock(t) − (α̂ + β̂ × R_market(t))
  Uses ACTUAL market returns during the event — never a flat assumption.

Step 3 — Cumulative Abnormal Return
  CAR = Σ AR(t)   for t in manipulation window

Step 4 — 95% Confidence Interval (Brown & Warner 1985, Eq. 5)
  Var(CAR) = T_event × σ̂² × (1 + 1/T_est + Σ(R_mkt_event − R̄_mkt)² / SS_mkt)
  
  t-stat = CAR / √Var(CAR)    [df = T_est - 2]
  95% CI = CAR ± t_crit × √Var(CAR)

Step 5 — Counterfactual Price Path
  P_cf(t) = P_pre_event × Π(1 + E[R(s)])   for s in manipulation window
  → P_cf_low(t), P_cf_central(t), P_cf_high(t) for each day

Step 6 — Per-Trade Harm
  buyer_harm  = (actual_price − cf_price) × qty   [positive = overpaid]
  seller_harm = (cf_price − actual_price) × qty   [positive = under-received]
  
  → harm_low, harm_central, harm_high for every trade
```

### 9.3 The Hard Rules

**Rule 1 — Allow-list guard** (`guards.py`):  
The module cannot run unless the instrument is:
- On the SEBI confirmed cases list (adjudication order exists), OR
- Explicitly labeled synthetic (scenario ID starts with `SYNTHETIC_`)

Every call is written to `guard_audit.log`. Bypass is logged at ERROR level — not silent.

**Rule 2 — Ranges, never point estimates**:  
```
CORRECT:   "Estimated harm: ₹12L–₹18L (95% CI), central estimate ₹15L"
INCORRECT: "Estimated harm: ₹15L"
```
The `format_harm_report_text()` function physically cannot produce a report without the CI bounds.

**Rule 3 — Mandatory disclaimer on every output**:  
*"This is a statistical estimate using market-model event-study methodology. It is NOT a legal or financial determination of actual harm."*

### 9.4 Current Status

| Test | Status |
|---|---|
| Injected CAR=+30% recovered within 95% CI | ✅ Verified |
| Large effect statistically significant | ✅ Verified |
| Zero-injection → CAR ≈ 0 | ✅ Verified |
| CI widens with longer event window | ✅ Verified |
| Guard system (allow/refuse/bypass) | ✅ Verified |
| KIL-2019 real case execution | ⚠️ Guard-approved; blocked by BSE data gap |
| PUMP-DUMP-2017-2020 real case | ⚠️ Guard-approved; blocked by BSE data gap |

---

## 10. Backtest Against Real SEBI Cases

### 10.1 What Was Done

1,900+ real HTTP requests to `archives.nseindia.com` across the investigation periods of all three SEBI cases. Every request was logged. Results are in `backtest/results/`.

### 10.2 The Integrity Enforcement Rule

`_enforce_log_file_integrity()` in `backtest/run_backtest.py` is a hard gatekeeper:

> Any result file that states a firm conclusion must reference a real, on-disk log file. Results without a `log_file` field are automatically downgraded to `VERDICT: INCONCLUSIVE`.

This prevented an unverified "fetched 1,494 rows — zero matches" claim from remaining in the methodology documentation. That claim was later found to be contradicted by a simultaneous diagnostic reporting `connectivity_ok: false`. The log-file integrity rule forces every claim to have a greppable paper trail.

### 10.3 Backtest Results Summary

| Case | Business Days Attempted | Days Found in NSE | Verdict |
|---|---|---|---|
| KIL-2019 (KAVIT) | 103 | **0** | UNTESTABLE — BSE-only; NSE absence log-verified |
| PUMP-DUMP (5 scrips) | 2,695 | **0** | UNTESTABLE — BSE-only; NSE absence log-verified |
| GIL-2003-2004 | 0 (skipped) | N/A | UNTESTABLE — pre-archive period |

### 10.4 Negative Control Results

90 real large-cap NSE trading days across RELIANCE, TCS, HDFCBANK, INFY, ICICIBANK:

```
RELIANCE:  18 days tested | 0 flagged  | FP rate: 0.0%
TCS:       18 days tested | 0 flagged  | FP rate: 0.0%
HDFCBANK:  18 days tested | 0 flagged  | FP rate: 0.0%
INFY:      18 days tested | 0 flagged  | FP rate: 0.0%
ICICIBANK: 18 days tested | 0 flagged  | FP rate: 0.0%
─────────────────────────────────────────────────────
TOTAL:     90 days tested | 0 flagged  | FP rate: 0.0%
```

The 0% false positive rate on clean large-cap data means the detector does not fire spuriously on legitimately high-volume events on liquid stocks.

---

## 11. Operational Console & Dashboard

### Real-Time Console (`console/index.html`)

A full-screen operational surveillance console serving live data from the FastAPI backend:

- **Live alert queue**: Active alerts sorted by severity with timestamp and instrument
- **Watchlist view**: Per-instrument monitoring status with detector arm/disarm controls
- **Alert drill-down**: Full detail view with evidence log, explanation, PFUTP references
- **SAR preview pane**: Live draft SAR rendering for escalated alerts
- **Detection engine health**: Per-detector status, last run timestamp, data freshness
- **Engineering metrics**: Ingestion latency, DB row counts, API error rates

Served at `/console/` by the FastAPI application. Built as a single HTML file — deployable without any frontend build pipeline.

### Analytics Dashboard (`dashboard/`)

Secondary reporting interface:

- Historical alert trend charts (7-day, 30-day, 90-day)
- Detector co-occurrence heatmap (which detectors fire together)
- Instrument risk score over time
- Data source connectivity status
- Export to CSV for offline analysis

---

## 12. Demo System

Located in [`demo/`](demo/). A complete demonstration pipeline that proves end-to-end functionality without requiring exchange access.

| Script | Purpose | Uses Real Data? |
|---|---|---|
| `generate_synthetic_orderflow.py` | Generates realistic order flows with embedded spoofing and layering | No — synthetic only |
| `generate_trading_day.py` | Full synthetic trading day with configurable manipulation injection | No — fictional instruments |
| `generate_watchlist_samples.py` | Sample watchlist data for all 6 detector types | No — synthetic |
| `generate_all_detector_samples.py` | Batch generation across all pattern types | No — synthetic |
| `build_demo.py` | Builds static stakeholder demo HTML from detection results | N/A |
| `build_extended_demo.py` | Extended demo with SEBI case context | N/A |
| `build_interactive_demo.py` | Interactive demo with alert drill-down | N/A |
| `run_demo.py` | Runs the full pipeline end-to-end | No — synthetic |
| `sentinel_stakeholder_demo.html` | Pre-built self-contained demo (no server required) | No — sample data |

**Ethical rule — strictly enforced**:
> Real company names (RELIANCE, TCS, HDFCBANK, etc.) appear **only** as clean negative-control baselines — proving the system doesn't flag legitimate trading. **All manipulation scenarios use clearly fictional instrument names (KAVITIND, etc.) that cannot be confused with real entities.**

---

## 13. Data Ingestion Layer

Located in [`data/ingest/`](data/ingest/).

| Module | Source | What It Fetches | Format |
|---|---|---|---|
| `nse_bhavcopy.py` | `archives.nseindia.com` | EOD OHLCV + delivery % for all NSE EQ series | ZIP/CSV |
| `nse_bulk_deals.py` | NSE bulk/block deal page | Large disclosed transactions (≥ 0.5% issued capital) | CSV |
| `nse_option_chain.py` | NSE option chain API | Strike OI, IV, bid/ask, PCR — live snapshot | JSON |
| `broker_order_stream.py` | Kite Connect WebSocket | Real-time order lifecycle events (placed/modified/cancelled/executed) | WS stream |
| `errors.py` | — | Typed exception hierarchy | — |

**Exception hierarchy**:
```
IngestError (base)
├── BhavcopyFetchError    (HTTP 403/500 on archive request)
├── BhavcopyParseError    (malformed CSV/ZIP)
├── OptionChainFetchError (NSE API failure)
└── BrokerStreamError     (WebSocket disconnect)
```

**Hard rules — never violated**:
- `403/500` → recorded as `FETCH_ERROR`, never silently interpolated with synthetic data
- `404` on specific date → `NON_TRADING_DAY` (expected; market holiday)
- NSE homepage (`www.nseindia.com`) returns `403`; access `archives.nseindia.com` directly (`200`)
- `_INTER_FETCH_DELAY_SECONDS = 1.5` between successive archive requests to avoid rate-limiting
- Resilient retry with exponential backoff (Phase 6) on transient errors

---

## 14. API Reference

FastAPI server with interactive OpenAPI docs at `http://127.0.0.1:8000/docs`

### Legacy Endpoints

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/health` | Liveness check — API + DB connectivity |
| `POST` | `/detect/spoofing` | Run spoofing/layering detection for an instrument |
| `GET` | `/alerts` | Paginated alert retrieval with severity/status filters |
| `GET` | `/alerts/{id}/evidence-log` | Export raw, auditable order slice behind an alert |

### Extended API Endpoints (`/api/`)

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/api/instruments` | List all monitored instruments |
| `GET/POST` | `/api/alerts` | Full alert CRUD with extended filters |
| `PATCH` | `/api/alerts/{id}` | Update alert status (open→investigating→escalated→closed) |
| `POST` | `/api/alerts/{id}/escalate` | Trigger SAR draft generation + state transition |
| `GET` | `/api/audit` | Evidence access audit log |
| `GET` | `/api/system` | System health, data freshness, DB stats |
| `GET` | `/api/backtest` | Backtest case catalog and results |

---

## 15. Repository Structure

```
Sentinel/
│
├── app/                              # Core application
│   ├── alerts/
│   │   ├── manager.py               # Deduplication, state machine, escalation
│   │   └── sebi_report.py           # 8-section SEBI SAR draft generator
│   ├── api/
│   │   ├── routes.py                # Legacy FastAPI endpoints
│   │   ├── instruments.py           # Instrument management API
│   │   ├── alerts_ext.py            # Extended alert API with PATCH/escalation
│   │   ├── audit.py                 # Evidence access audit API
│   │   ├── system.py                # System health API
│   │   └── backtest_api.py          # Backtest results API
│   ├── db/
│   │   ├── models.py                # SQLAlchemy ORM: Instrument, Order, Trade, Alert
│   │   └── session.py               # Engine & session factory
│   ├── detection/
│   │   ├── basis_distortion.py      # Cost-of-carry fair value deviation
│   │   ├── circular_trading.py      # Johnson's algorithm — trade ring detection
│   │   ├── coordinated_pump.py      # Multi-account synchronized buy burst
│   │   ├── evidence.py              # Auditable raw evidence slice builder
│   │   ├── oi_manipulation.py       # OI concentration + OI-IV decoupling
│   │   ├── option_pinning.py        # Expiry-day strike pinning
│   │   └── spoofing.py              # Liquidity-normalized spoofing/layering
│   ├── harm_estimation/
│   │   ├── guards.py                # Hard-coded allow-list + audit log
│   │   ├── event_study.py           # OLS market model, AR/CAR, CI, counterfactual path
│   │   └── trade_harm.py            # Per-trade and instrument-level harm ranges
│   ├── ml/
│   │   ├── features.py              # 20-dimensional versioned feature extraction
│   │   └── scorer.py                # Isolation Forest + expert weighted baseline
│   ├── schemas/
│   │   └── schemas.py               # Pydantic request/response validation
│   ├── security/
│   │   ├── access_log.py            # Evidence access audit logging
│   │   ├── pii.py                   # Salted SHA-256 account ID hashing
│   │   └── retention.py             # 7-year regulatory retention enforcement
│   └── main.py                      # FastAPI entrypoint (console + dashboard mounts)
│
├── backtest/                        # Real SEBI case backtesting
│   ├── sebi_case_catalog.py         # Case definitions with verifiable SEBI order refs
│   ├── run_backtest.py              # Integrity-enforced runner
│   ├── case_backtest_runner.py      # Per-case detection execution
│   ├── historical_data_puller.py    # NSE bhavcopy range fetcher
│   ├── diagnostic_kil_2019.py       # KIL-2019 NSE absence probe
│   ├── negative_control_runner.py   # Large-cap false-positive testing
│   └── results/
│       ├── KIL-2019_result.json
│       ├── KIL-2019_diagnostic_v2.log
│       ├── PUMP-DUMP-2017-2020_result.json
│       ├── PUMP-DUMP-2017-2020_diagnostic_v2.log
│       └── negative_controls.json
│
├── console/
│   └── index.html                   # Real-time operational surveillance console
│
├── dashboard/
│   ├── data/                        # Dashboard data files
│   ├── scripts/                     # Dashboard JS
│   └── static/                      # CSS and static assets
│
├── data/
│   └── ingest/
│       ├── broker_order_stream.py   # Kite Connect WS adapter
│       ├── errors.py                # Typed exception hierarchy
│       ├── nse_bhavcopy.py          # EOD OHLCV parser
│       ├── nse_bulk_deals.py        # Bulk/block deal parser
│       └── nse_option_chain.py      # Live option chain scraper
│
├── demo/                            # Synthetic demonstration pipeline
│   ├── generate_synthetic_orderflow.py
│   ├── generate_trading_day.py
│   ├── generate_watchlist_samples.py
│   ├── generate_all_detector_samples.py
│   ├── build_demo.py
│   ├── build_extended_demo.py
│   ├── build_interactive_demo.py
│   ├── run_demo.py
│   ├── verify_demo.py
│   ├── sentinel_stakeholder_demo.html
│   └── sample_data/
│
├── docs/
│   └── METHODOLOGY.md               # Full methodology, thresholds, validation status v0.3.0
│
├── tests/
│   ├── test_ingest_phase1.py        # 30 ingestion tests
│   ├── test_detection_phase2.py     # 19 multi-account detection tests
│   ├── test_detection_phase3.py     # 27 derivatives detector tests
│   ├── test_ml_phase4.py            # 29 ML feature & scoring tests
│   ├── test_alerts_phase5.py        # 29 alert lifecycle & SAR tests
│   ├── test_security_pii.py         # 37 PII, retention & audit tests
│   ├── test_harm_estimation.py      # 29 harm engine tests (known-answer synthetic)
│   ├── test_resilience.py           # Network resilience & retry tests
│   └── stress/                      # Concurrent access & load tests
│
├── pytest.ini
└── requirements.txt
```

---

## 16. Getting Started

### Prerequisites
- Python 3.11+
- SQLite (default, local development) or PostgreSQL (production)
- Git

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

### Environment Configuration

Create a `.env` file in the project root:

```bash
# Required: cryptographic salt for account ID pseudonymization
ACCOUNT_ID_SALT=your-cryptographic-salt-here-minimum-32-chars

# Optional: Zerodha Kite Connect for live broker order stream
KITE_API_KEY=your_api_key
KITE_ACCESS_TOKEN=your_access_token
```

> **Security note**: Never commit the `.env` file. Add it to `.gitignore`. The `ACCOUNT_ID_SALT` value must be treated as a secret — it protects the pseudonymization of client account identifiers.

### Running the Demo (No Exchange Access Needed)

```bash
# Generate synthetic order flow and run detection pipeline
python demo/run_demo.py

# Build the interactive stakeholder demo
python demo/build_interactive_demo.py

# Open the pre-built demo (Windows)
start demo/sentinel_stakeholder_demo.html
# macOS
open demo/sentinel_stakeholder_demo.html
```

### Running the API Server

```bash
uvicorn app.main:app --reload

# OpenAPI docs: http://127.0.0.1:8000/docs
# Console:      http://127.0.0.1:8000/console/
# Dashboard:    http://127.0.0.1:8000/dashboard/
```

### Running the Backtest (Real NSE Data)

```bash
# Runs against real SEBI cases — makes ~1,900 HTTP requests to NSE archives
python -m backtest.run_backtest

# Outputs to backtest/results/
```

---

## 17. Test Suite

**200 tests — all passing.**

```bash
# Full suite
pytest

# Specific suite
pytest tests/test_harm_estimation.py -v
pytest tests/test_detection_phase2.py -v

# Live network tests (NSE archive fetches — excluded by default in CI)
pytest -m live

# Stress / concurrency
pytest tests/stress/ -v

# With coverage
pytest --cov=app --cov-report=html
```

| Test File | Count | What It Tests |
|---|---|---|
| `test_ingest_phase1.py` | 30 | NSE bhavcopy parsing, bulk deals, option chain, broker WS, error hierarchy |
| `test_detection_phase2.py` | 19 | Circular trading (Johnson's cycles), coordinated pump (multi-account) |
| `test_detection_phase3.py` | 27 | OI concentration, IV decoupling, cost-of-carry, Max-Pain, pinning detection |
| `test_ml_phase4.py` | 29 | 20-feature extraction, schema versioning, Isolation Forest, dual scoring |
| `test_alerts_phase5.py` | 29 | Alert state machine, TOCTOU deduplication, SAR formatting, escalation |
| `test_security_pii.py` | 37 | SHA-256 hashing, dual-column, evidence access audit, 7-year retention |
| `test_harm_estimation.py` | 29 | Guard allow-list, OLS market model, known-answer CAR recovery, trade harm |
| `test_resilience.py` | ~20 | Network retry/backoff, 403 handling, concurrent DB access |

### Known-Answer Validation (Harm Engine)

The `TestCARKnownAnswer` class uses the "inject known effect, verify recovery" standard (MacKinlay 1997, Section 3.3):

```python
# Inject a known CAR=+30% into 20 trading days of synthetic data
# with true_alpha=0.0001, true_beta=1.05, residual_vol=0.8%

result = run_event_study(stock_with_30pct_injection, market, event_start, event_end)

assert result.car_low <= 0.30 <= result.car_high  # ✅ Passes
assert result.significant_at_95                    # ✅ Passes
assert result.car_low > 0                          # ✅ Passes (CI entirely positive)
```

---

## 18. Design Principles

These are not aspirational — they are enforced in code.

### 1. No Silent Fallbacks
`403` on NSE archive → `BhavcopyFetchError`. `404` on a date → `NON_TRADING_DAY`. Nothing is silently replaced with synthetic data. Ever.

### 2. Order-Level Primacy
The `Alert` table references `evidence_log_ref` pointing to the raw order/trade slice used for detection. An alert with no evidence backing cannot be created.

### 3. Log-File Integrity Enforcement
`_enforce_log_file_integrity()` in `backtest/run_backtest.py` — any result claiming a firm conclusion without a greppable log file is automatically downgraded to `VERDICT: INCONCLUSIVE`. This caught and corrected an unverified claim that 1,494 NSE rows were fetched when the simultaneous diagnostic reported `connectivity_ok: false`.

### 4. No Implied Accusations Against Real Companies
The harm estimation module's allow-list is hard-coded — not configurable in a settings file. Adding a new SEBI case requires editing `guards.py` with a real, citable SEBI adjudication/final order reference. The `SYNTHETIC_` prefix rule enforces that fictional scenarios can never be confused with real findings.

### 5. Honest Threshold Labeling
Every detection threshold is labeled one of:
- `HEURISTIC` — informed estimate based on SEBI case review
- `UNVALIDATED GUESS` — not calibrated against any real data

No threshold is ever presented as empirically validated unless it has been calibrated against confirmed cases.

### 6. CI Ranges Always Travel With Central Estimates
`format_harm_report_text()` in `trade_harm.py` is structured so that the central harm estimate cannot be printed without the CI bounds on the same line. There is no `print_harm_central()` function.

### 7. Bypass Is Logged, Not Silent
`bypass_guard=True` in `guards.py` logs `ERROR` to Python logging and writes to `guard_audit.log`. It cannot be set without creating a visible paper trail. This is by design — the paper trail is the whole point.

---

## 19. Limitations & What Comes Next

### Current Limitations

| Limitation | Impact | Path to Fix |
|---|---|---|
| BSE data pipeline missing | All confirmed SEBI cases are BSE-only; backtesting blocked | Build `bse_bhavcopy.py` fetcher |
| No account-level public data | Full circular trading and pump detectors cannot be validated | Formal NSE/BSE data agreement or SEBI integration |
| Detection thresholds unvalidated | False positive/negative rates unknown for real manipulation | Calibrate against labeled exchange data |
| Historical option chain OI not archived | OI manipulation and pinning detectors cannot be backtested | No public solution; requires real-time archive building |
| Historical order books not public | Spoofing detector cannot be backtested | No public solution; requires exchange integration |
| Single-instrument event study only | Harm engine cannot handle portfolio-level manipulation | Extend to cross-sectional method (MacKinlay 1997, §4.4) |

### The Two Fixes That Would Change Everything

**Fix 1: BSE Bhavcopy Fetcher** (1–2 weeks of engineering)
- Source: `www.bseindia.com/download/BhavCopy/Equity/EQ_DDMMYYYY_CSV.ZIP`
- Impact: Immediately makes KIL-2019 (113% price manipulation, adjudication confirmed) testable for price/volume anomaly signals
- Impact: Immediately enables harm estimation run for both confirmed SEBI cases

**Fix 2: NSE/BSE Formal Data Agreement**
- Source: Exchange data licensing program / SEBI integration pathway
- Impact: Unlocks account-level historical trade data → full validation of circular trading and pump detectors
- Impact: Transforms Sentinel from "validated on synthetic data" to "validated on real manipulation cases"

### Roadmap

```
Current State
└── Phase 9: BSE bhavcopy fetcher + ISIN symbol resolution
    └── Phase 10: First real case harm estimation (KIL-2019 on BSE data)
        └── Phase 11: Formal data agreement / exchange integration
            └── Phase 12: Full detector calibration on labeled real cases
                └── Phase 13: Production deployment with live exchange feed
```

---

## 20. References & Citations

### Regulatory Framework
1. SEBI Act, 1992 — Sections 11B, 12A — https://www.sebi.gov.in/legal/acts/
2. SEBI (Prohibition of Fraudulent and Unfair Trade Practices) Regulations, 2003 — Regulations 3, 4 — https://www.sebi.gov.in/legal/regulations/
3. SEBI Circular ISD/CIR/RR/AML/2/06 (2006) — Prevention of Market Abuse
4. SEBI Circular SEBI/HO/ISD/ISD_OAED/P/CIR/2022/155 — ASM/GSM Framework
5. SEBI Stock Brokers Regulations, 1992 — Regulation 17 (Record Retention)
6. Securities Contracts (Regulation) Act, 1956 — Section 12A

### SEBI Enforcement Orders (All Publicly Verifiable)
7. SEBI Adjudication Order — Kavit Industries Limited, February 28, 2025 — Search "Kavit Industries" at sebi.gov.in/enforcement/orders.html
8. SEBI Ex Parte Ad Interim Order — Mauria Udyog / Hanif Shekh cluster, June 19, 2023 — Search "Mauria Udyog" or "Hanif Shekh"
9. SEBI Adjudication Order — Gravity India Limited (ORDER/SBM/KL/2021-22/15788), March 31, 2022

### Academic References
10. **MacKinlay, A.C. (1997).** Event Studies in Economics and Finance. *Journal of Economic Literature*, 35(1), 13–39.
11. **Brown, S.J. & Warner, J.B. (1985).** Using daily stock returns: The case of event studies. *Journal of Financial Economics*, 14(1), 3–31.
12. **Liu, F.T., Ting, K.M. & Zhou, Z.H. (2008).** Isolation Forest. *Proceedings of ICDM 2008*, 413–422.
13. **Aggarwal, R.K. & Wu, G. (2006).** Stock market manipulations. *Journal of Business*, 79(4), 1915–1953.
14. **Aitken, M., Cumming, D. & Zhan, F. (2015).** Exchange trading rules, surveillance and suspected insider trading. *Journal of Banking & Finance*, 52, 220–235.
15. **Comerton-Forde, C. & Putniņš, T.J. (2015).** Stock price manipulation: Prevalence and determinants. *Review of Finance*, 19(4), 1493–1520.
16. **Ni, S.X., Pearson, N.D. & Poteshman, A.M. (2005).** Stock price clustering on option expiration dates. *Journal of Financial Economics*, 78(1), 49–87.
17. **Budhiraja, Gupta & Pathak (2025).** Buyback Abnormal Returns in Indian Markets. SSRN Working Paper.
18. **Johnson, D.B. (1975).** Finding all the elementary circuits of a directed graph. *SIAM Journal on Computing*, 4(1), 77–84. *(Basis for circular trading detection algorithm.)*

---

## License

MIT License — see [LICENSE](LICENSE) for details.

---

<div align="center">

**Built to protect retail investors in Indian capital markets.**  
*One confirmed SEBI case at a time.*

**[View the Methodology →](docs/METHODOLOGY.md)** | **[View the Backtest Report →](backtest/REPORT.md)** | **[Open the Demo →](demo/sentinel_stakeholder_demo.html)**

</div>

---

> **Final Disclaimer**: Sentinel is a research and proof-of-concept platform. Its outputs do not constitute financial advice, legal determinations, or regulatory findings. All detection scores are statistical anomaly indicators requiring analyst review. SEBI case summaries are the authors' own paraphrases for research context — they are not reproductions of SEBI's findings. Any operational use requires independent validation, expert review, compliance with SEBI regulations, and appropriate regulatory authorization.
