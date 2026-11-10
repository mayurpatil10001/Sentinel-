# Sentinel — Detection Methodology

**Version**: 0.3.0-SAMPLE  
**Date**: 2026-09-17  
**Environment**: Non-production, synthetic/sample data only  
**Contact**: Internal surveillance team

---

## 1. Purpose of This Document

This document describes what each detector in the Sentinel system does,
what data it requires, what thresholds it uses and why, and — critically —
what has and has not been validated against real data. It is the primary
reference for any question about how the system works or what it claims.

**What this system is**: A proof-of-concept order-level surveillance platform
for Indian equity and derivatives markets, with 6 pattern detectors, an alert
lifecycle manager, a SEBI SAR draft generator, and a real-time operational
console. All data in the current deployment is synthetic/mock.

**What this system is not**: A validated, production-ready surveillance system.
No detector has been validated against a confirmed manipulation case using
publicly available Indian market data. Threshold values are informed estimates,
not empirically calibrated values.

---

## 2. Legal and Regulatory Framework

The detectors are designed to identify patterns described in:

- **SEBI Act, 1992**, Section 11B and 12A
- **SEBI (Prohibition of Fraudulent and Unfair Trade Practices) Regulations,
  2003** (PFUTP), Regulations 3 and 4
  - Reg 3(a): Prohibition of manipulative or deceptive devices
  - Reg 3(b): Prohibition of price manipulation
  - Reg 4(1): Prohibition of market manipulation
  - Reg 4(2)(a)-(n): Specific prohibited practices including artificial volume,
    price ramping, spoofing, circular trades
- **SEBI Circular ISD/CIR/RR/AML/2/06** (2006): Prevention of market abuse
- **SEBI Circular SEBI/HO/ISD/ISD_OAED/P/CIR/2022/155**: ASM/GSM framework

The system uses PFUTP vocabulary throughout. "Spoofing," "layering,"
"circular trading," and "pump-and-dump" correspond directly to prohibited
patterns under PFUTP Reg 4(2).

---

## 3. Detectors

### 3.1 Spoofing / Layering (pp/detection/spoofing.py)

**What it detects**: An account places large orders on one side of the book
to create the appearance of supply/demand, then cancels them after the price
moves in the intended direction, and profits from trades on the opposite side.

**PFUTP basis**: Reg 4(2)(a) — placing orders not intended to be executed;
Reg 4(2)(n) — creating artificial appearance of trading activity.

**Data required**: Order lifecycle events (placed, modified, cancelled,
executed) with account identifiers, timestamps, prices, and quantities.
Bhavcopy OHLCV alone is insufficient — order-level data is essential.

**Algorithm**:
1. Group orders by account and time window.
2. Compute cancellation ratio = cancelled_value / placed_value.
3. Compute order size ratio = peak_order_size / instrument_30d_avg_order_size.
4. Compute price impact = |price_at_cancel - price_at_place| / price_at_place.
5. Check for opposite-side execution within the window (profit realisation).
6. Score = weighted combination of the above ratios.

**Thresholds** (label: HEURISTIC — informed by SEBI case review):
- cancellation_ratio >= 0.70 (flag if 70%+ of value cancelled)
- order_size_multiplier >= 3.0 (peak order >= 3x instrument baseline)
- min_price_impact >= 0.005 (0.5% minimum price move)
- Score >= 0.65 triggers HIGH; >= 0.80 triggers CRITICAL

**Validation status**: NOT VALIDATED against confirmed manipulation.
Negative control tested on 252 days of NSE bhavcopy proxy: 0% false positive
on clean large-cap data. No confirmed positive case tested.

**Academic reference**: Aitken, Cumming & Zhan (2015), "High frequency trading
and end-of-day price dislocation," Journal of Banking & Finance. Cao, Chen &
Griffin (2005), "Informational content of option volume prior to takeovers,"
Journal of Business.

---

### 3.2 Circular Trading (pp/detection/circular_trading.py)

**What it detects**: A network of accounts trades the same scrip among
themselves in a circular pattern, creating artificial volume without genuine
change in beneficial ownership.

**PFUTP basis**: Reg 4(2)(b) — entering into transactions without change in
beneficial ownership; Reg 4(2)(c) — entering into fictitious transactions.

**Data required**: Account-level executed trade records with counterparty
identifiers, or at minimum account IDs on both buy and sell sides of the same
scrip in the same window.

**Algorithm**:
1. Build a directed graph: edge A→B if account A bought and account B sold
   the same scrip in the same session at matched prices.
2. Detect cycles in the graph (DFS-based cycle detection).
3. Compute volume concentration: what fraction of total scrip volume is
   accounted for by the cycle participants.
4. Score based on cycle length, volume concentration, and price stability
   across the cycle (manipulators typically trade near the same price to
   avoid P&L exposure).

**Thresholds** (label: HEURISTIC):
- min_cycle_volume_pct >= 0.25 (cycle must be >= 25% of total volume)
- min_cycle_participants >= 3
- max_price_variation_pct <= 0.02 (trades within 2% of each other)

**Validation status**: NOT VALIDATED. The Kavit Industries case (SEBI order
specifies BSE trading, 2019) is a publicly documented circular trading case,
but KIL is confirmed absent from NSE (two independent data sources, log-verified at
`backtest/results/KIL-2019_diagnostic_v2.log`). BSE-only status is probable
but not independently verified against BSE data, and account-level trade data
needed for full validation is not publicly available.

**Academic reference**: Aggarwal & Wu (2006), "Stock market manipulations,"
Journal of Business 79(4). SEBI Adjudication Order: Kavit Industries Limited
(February 28, 2025).

---

### 3.3 Coordinated Pump (pp/detection/coordinated_pump.py)

**What it detects**: Multiple accounts coordinate to buy a scrip, driving
the price up, then sell to retail investors who were attracted by the price
movement or by external promotion.

**PFUTP basis**: Reg 4(2)(e) — advancing or depressing the price of securities
by entering into series of transactions; Reg 3(b) — price manipulation.

**Data required**: Account-level buy orders with timestamps and quantities.
Volume profile over a rolling window. Ideally: price data for context.

**Algorithm**:
1. Count distinct accounts buying in a rolling window.
2. Compute volume surge: window_volume / baseline_daily_volume.
3. Compute price appreciation in the same window.
4. Check for subsequent sell-off: price drop after volume peak.
5. Score weights account concentration, volume surge, and price pattern.

**Thresholds** (label: UNVALIDATED GUESS — requires empirical calibration):
- min_coordinated_accounts >= 5
- volume_surge_multiplier >= 3.0 (3x normal daily volume in the window)
- min_price_appreciation >= 0.15 (15% in the window)
- Score >= 0.60 triggers MEDIUM; >= 0.75 triggers HIGH

**Validation status**: NOT VALIDATED. The Mauria Udyog / Hanif Shekh cluster
(NSE/BSE, 2017-2020) is a relevant case. Diagnostic (log: `backtest/results/PUMP-DUMP-2017-2020_diagnostic_v2.log`,
timestamp 2026-09-17T01:24:01Z) confirmed all 5 scrips are absent from NSE EQ bhavcopy
(2018-06-15, 1,494 rows fetched, HTTP 200) and absent from the NSE currently-listed symbol
master (2,571 symbols, HTTP 200). These instruments are confirmed not NSE-listed. Whether
they were positively listed on BSE has not been independently verified against BSE data.
Account-level coordination data is not in public archives for any confirmed case.

**Academic reference**: Comerton-Forde & Putniņš (2015), "Stock price
manipulation: Prevalence and determinants," Review of Finance 19(4).
Ye, Yao & Gai (2013), "The externalities of high frequency trading."

---

### 3.4 OI Manipulation (pp/detection/oi_manipulation.py)

**What it detects**: Abnormal concentration or sudden build-up of open interest
in a specific option strike or futures contract, inconsistent with normal hedging
or speculative patterns, suggesting an attempt to influence expiry settlement.

**PFUTP basis**: Reg 4(2)(a) — devices to inflate or depress prices;
Reg 4(2)(n) — creating false or misleading appearance of trading.

**Data required**: Option chain snapshots (strike prices, OI per strike,
implied volatility per strike, underlying spot price). Intraday OI change
data preferred; daily end-of-day OI is minimum viable.

**Thresholds** (label: HEURISTIC — informed by SEBI F&O surveillance circulars):
- oi_concentration_ratio >= 0.35 (single strike > 35% of total chain OI)
- oi_change_rate >= 2.0 (OI doubled in one session)
- iv_oi_divergence: IV drops while OI rises abnormally (selling volatility
  into manufactured OI)

**Validation status**: NOT VALIDATED against real confirmed case.
Breeze API integration for real historical option chain data: NOT YET DONE
(planned Phase 3). Current backtest uses synthetic option chain data only.

**Data limitation**: NSE provides historical bhavcopy for F&O but account-level
OI positions are not public. The detector can be run on aggregate OI data.

---

### 3.5 Basis Distortion (pp/detection/basis_distortion.py)

**What it detects**: The futures-spot basis (futures price minus spot price)
deviates significantly and persistently from the no-arbitrage theoretical
basis (risk-free rate + cost of carry). This signals that someone is
manipulating either the spot or the futures leg to create a temporarily
distorted basis.

**PFUTP basis**: Reg 4(2)(a) and Reg 4(2)(e) — distorting normal price
relationships between related instruments.

**Data required**: Intraday spot price for the underlying + futures price
for the same underlying, same session. Interest rate (used as cost of carry
proxy). Time to expiry.

**Thresholds** (label: HEURISTIC — derived from cost-of-carry theory):
- basis_z_score >= 3.0 (basis is 3+ standard deviations from rolling mean)
- min_duration_minutes >= 30 (must persist, not just be a tick glitch)
- theoretical_basis_breach >= 0.005 (0.5% beyond no-arbitrage bounds)

**Validation status**: NOT VALIDATED against confirmed case.
No public dataset of confirmed basis manipulation cases with intraday price
data is known to exist for Indian markets.

---

### 3.6 Option Pinning (pp/detection/option_pinning.py)

**What it detects**: Deliberate trading activity near an options expiry to
drive the underlying price toward a specific strike price ("pinning"), so
that options the manipulator is short expire worthless (or conversely, to
ensure options they are long expire in-the-money).

**PFUTP basis**: Reg 4(2)(a) — devices to manipulate price of a security
(underlying) to benefit derivatives positions.

**Data required**: Intraday underlying price + option chain with strike OI.
Must observe the price gravitating toward a high-OI strike near expiry.

**Thresholds** (label: HEURISTIC):
- proximity_to_strike_pct <= 0.005 (price within 0.5% of pin target near close)
- target_strike_oi_pct >= 0.20 (target strike is >= 20% of total chain OI)
- expiry_days_remaining <= 3 (only meaningful near expiry)

**Validation status**: NOT VALIDATED against confirmed case.
Academic evidence for option pinning exists for US markets (Ni, Pearson &
Poteshman 2005, Journal of Financial Economics) but no confirmed Indian case
is in scope of this backtest.

**Academic reference**: Ni, Pearson & Poteshman (2005), "Stock price clustering
on option expiration dates," Journal of Financial Economics 78(1):49-87.

---

## 4. What Is and Is Not Validated

| Detector | Negative Control (0% FP on clean NSE data) | Validated vs. Confirmed Manipulation |
|---|---|---|
| Spoofing / Layering | Yes (252 trading days) | No |
| Circular Trading | No | No |
| Coordinated Pump | No | No |
| OI Manipulation | No | No |
| Basis Distortion | No | No |
| Option Pinning | No | No |

**What "negative control tested" means**: The detector was run against 252
trading days of real NSE equity bhavcopy data for large-cap scrips (Reliance,
TCS, HDFC Bank, Infosys, ICICI Bank). Zero alerts were generated. This means
the detector does not trivially fire on normal market activity — it is a
necessary but not sufficient condition for usefulness.

**What "validated vs. confirmed manipulation" would mean**: Running the detector
against the exact data window of a confirmed SEBI enforcement case and
demonstrating it would have fired (true positive). This has not been achieved
for any detector, primarily because:
1. The relevant SEBI cases involve instruments confirmed absent from NSE
   archives (probable BSE-only, not reachable by the NSE bhavcopy fetcher).
2. Account-level order data for historical confirmed manipulation cases is
   not publicly available in India.
3. Both case investigations (KIL-2019 and Mauria Udyog cluster) involve scrips
   confirmed absent from NSE bhavcopy and NSE symbol master (two independent sources each,
   both log-verified at `backtest/results/KIL-2019_diagnostic_v2.log` and
   `backtest/results/PUMP-DUMP-2017-2020_diagnostic_v2.log`). These instruments are
   confirmed not NSE-listed; positive BSE listing has not been independently verified.

---

## 5. Backtest Results Summary (as of 2026-09-17)

Two SEBI cases were selected for backtesting:

**Case KIL-2019** (Kavit Industries Limited, circular trading, 2019):
- Verdict: UNTESTABLE
- Log: `backtest/results/KIL-2019_diagnostic_v2.log` (timestamp 2026-09-17T02:34:27Z)
- Evidence: Kavit Industries (KAVIT) absent from NSE EQ bhavcopy for probe dates across 2019 investigation window (2019-10-15 [1,494 rows] and 2019-08-16 [1,518 rows], HTTP 200 from archives.nseindia.com, RELIANCE used as connectivity control; BE series returned 0 rows). Independently confirmed absent from NSE currently-listed symbol master (2,571 symbols, HTTP 200). The 0-days-fetched result is NOT a fetch code bug.
- What is confirmed: KIL/KAVIT is not NSE-listed (two independent data sources).
- What is NOT confirmed: Positive BSE listing. No BSE scrip master lookup was performed. BSE-only status is the most probable explanation (and SEBI's order specifies BSE trading), but has not been independently verified against BSE data.
- Account-level circular trading data (account-to-account trade matching) is not public.

**Case PUMP-DUMP-2017-2020** (Mauria Udyog cluster, 2017-2020):
- Verdict: UNTESTABLE
- Log: `backtest/results/PUMP-DUMP-2017-2020_diagnostic_v2.log` (timestamp 2026-09-17T01:24:01Z)
- Evidence: All 5 scrips absent from NSE EQ bhavcopy 2018-06-15 (1,494 rows, HTTP 200
  from archives.nseindia.com, RELIANCE used as connectivity control). Independently
  confirmed absent from NSE currently-listed symbol master (2,571 symbols, HTTP 200).
  The 0-days-fetched result is NOT a fetch code bug.
- What is confirmed: These instruments are not NSE-listed (two independent data sources).
- What is NOT confirmed: Positive BSE listing. No BSE scrip master lookup was performed.
  The most probable explanation is BSE-only or delisted status, but this has not been
  independently verified against BSE data.
- Account-level manipulation data (coordinated buys below bulk deal threshold)
  is not in any public archive.

**Negative control** (clean large-cap NSE data):
- 252 trading days tested for spoofing/layering detector.
- False positive rate: 0%.

---

## 6. Known Limitations

1. **No real manipulation data**: All positive-case testing uses synthetic
   data generated by the demo pipeline. The system has not been confirmed
   to detect a real historical manipulation event.

2. **NSE-only data access**: The bhavcopy fetcher retrieves NSE equity series
   only. BSE instruments, NSE-SME instruments, and instruments requiring
   account-level data cannot be tested.

3. **Thresholds are estimates**: The majority of threshold values are labelled
   UNVALIDATED GUESS or HEURISTIC. They have not been calibrated against a
   distribution of real manipulation events vs. clean market activity.

4. **Account-level data gap**: Spoofing, circular trading, and coordinated
   pump all fundamentally require account-level order data. This data is
   available in real deployments (exchange feed) but is not publicly available
   for historical testing.

5. **Breeze API not yet integrated**: The three options/futures detectors
   (OI manipulation, basis distortion, option pinning) have not been tested
   against real historical option chain data. Breeze API integration is planned.

---

## 7. How to Interpret Alerts

An alert from this system means:
> The detector found a statistical pattern in the input data that is
> consistent with the described manipulation type, at a score above the
> configured threshold.

It does **not** mean:
- That manipulation actually occurred.
- That the threshold is correctly calibrated.
- That the explanation is legally sufficient for a SEBI filing.

The SEBI SAR draft generator produces a structured document for analyst
review. Every SAR draft requires analyst verification before any filing.
The system cannot auto-file with SEBI.

---

## 8. Citation Index

1. SEBI PFUTP Regulations, 2003 — https://www.sebi.gov.in/legal/regulations/
2. SEBI Act, 1992, Sections 11B, 12A
3. SEBI Circular SEBI/HO/ISD/ISD_OAED/P/CIR/2022/155 (ASM/GSM framework)
4. Aitken, Cumming & Zhan (2015), Journal of Banking & Finance 52:220-235
5. Aggarwal & Wu (2006), Journal of Business 79(4):1915-1953
6. Comerton-Forde & Putniņš (2015), Review of Finance 19(4):1493-1520
7. Ni, Pearson & Poteshman (2005), Journal of Financial Economics 78(1):49-87
8. Budhiraja, Gupta & Pathak (2025), "Buyback Abnormal Returns in Indian
   Markets," SSRN Working Paper — used as reference for post-event drift
   methodology in coordinated pump calibration
9. SEBI Adjudication Order: Kavit Industries Limited, Feb 28 2025
10. SEBI Interim Order: Mauria Udyog / Hanif Shekh cluster, Jun 19 2023
11. MacKinlay, A.C. (1997). Event Studies in Economics and Finance.
    Journal of Economic Literature, 35(1), 13-39.
12. Brown, S.J. & Warner, J.B. (1985). Using daily stock returns: The case of
    event studies. Journal of Financial Economics, 14(1), 3-31.

---

## 9. Harm Quantification / Restitution Engine

### 9.1 Purpose

Given a confirmed manipulation window (real SEBI adjudicated case or clearly
labeled synthetic scenario) plus daily price/volume data, the harm estimation
module estimates:
1. What price the instrument *likely* would have followed without the manipulation
   (the **counterfactual price path**).
2. The **monetary harm** to real trades executed at the inflated/deflated price
   during the event window.

> [!IMPORTANT]
> This module is a **downstream research tool**, not a detector. It only runs
> after manipulation has been legally confirmed (SEBI adjudication order) or is
> labeled synthetic. It does NOT make new allegations.

---

### 9.2 Methodology: Market-Model Event Study

**Primary reference**: MacKinlay (1997) — the canonical review of event-study
methodology as practiced in financial economics.  
**Test statistics**: Brown & Warner (1985) — establishes the t-statistic form
used here for daily-return data, including the forecast-error variance adjustment.

#### Estimation Window

Standard practice per MacKinlay (1997, p. 15) uses 120–250 trading days before
the event window for OLS parameter estimation. Sentinel defaults to **200 trading
days** and documents any deviation.

> [!IMPORTANT]
> The estimation window must not overlap the event (manipulation) window.
> Mixing contaminated event-period data into the estimation window would bias
> the alpha/beta estimates and understate the abnormal return — a known
> methodological failure mode called "contaminated estimation window."

#### Market Model (OLS Regression)

The market model assumes a linear relationship between stock and market returns:

```
R_stock(t) = alpha + beta × R_market(t) + epsilon(t)
```

Fitted by **OLS** (statsmodels) over the estimation window. Parameters:
- `alpha` — stock-specific drift (estimated, not assumed zero)
- `beta` — market sensitivity (estimated, not assumed 1.0)
- `sigma_hat` — OLS residual standard deviation (used in CI calculation)

#### Abnormal Return

```
AR(t) = R_stock(t) – (alpha_hat + beta_hat × R_market(t))
```

For each day in the event window. Uses **actual** market returns during the
event window — not an assumed-flat or assumed-zero market, as that would
attribute all market-driven price changes to manipulation.

#### Cumulative Abnormal Return (CAR)

```
CAR = sum(AR(t))  for t in event window
```

#### Confidence Interval (Brown & Warner 1985)

The variance of CAR is estimated following Brown & Warner (1985, Eq. 5):

```
Var(CAR) = T_event × sigma_hat² × (1 + 1/T_est
           + sum((R_mkt_event(t) – R_mkt_bar)²) / SS_mkt_est)
```

Where:
- `T_event` = event window length (trading days)
- `T_est` = estimation window length
- `R_mkt_bar` = mean market return in estimation window
- `SS_mkt_est` = sum of squared market return deviations in estimation window

The third term corrects for **forecast-period uncertainty**: if market returns
during the event window deviate substantially from estimation-period returns,
the out-of-sample forecast error is larger.

t-statistic:
```
t = CAR / sqrt(Var(CAR))     [df = T_est - 2]
```

95% Confidence Interval:
```
CI = CAR ± t_crit(df=T_est-2, p=0.025) × sqrt(Var(CAR))
```

> [!WARNING]
> **Single-instrument test only.** Cross-sectional aggregation (portfolio event
> studies) would require additional adjustments for event-clustering and
> cross-sectional dependence per MacKinlay (1997, Section 4.4). This module
> is currently single-instrument only — explicitly disclosed.

#### Counterfactual Price Path

The counterfactual price path reconstructs what the stock *likely* would have
traded at without the manipulation:

```
P_cf(t) = P_pre_event × product((1 + E[R(s)]) for s in [1..t])
```

where `E[R(s)] = alpha_hat + beta_hat × R_mkt(s)` is the market-model
predicted return for each event-window day.

The CI bounds on the CAR are propagated to the price path, yielding
`P_cf_low(t)` and `P_cf_high(t)` for each day — so every harm figure
derived from the path is a **range**, not a point estimate.

---

### 9.3 Per-Trade Harm Estimation

Given a counterfactual price path and a set of trade records:

**Buyer harm** (stock was inflated by manipulation, buyer overpaid):
```
harm(trade) = (actual_price – cf_price) × quantity
```

**Seller harm** (stock was deflated by manipulation, seller under-received):
```
harm(trade) = (cf_price – actual_price) × quantity
```

Both buyer and seller harm are computed as ranges:
```
harm_low  = harm at worst plausible counterfactual (CI bound)
harm_high = harm at best plausible counterfactual (CI bound)
```

Aggregated to account level and instrument level.

> [!CAUTION]
> **HARD RULE — NEVER PRESENT A POINT ESTIMATE WITHOUT ITS RANGE**:  
> Every output from this module carries `harm_low`, `harm_central`, and  
> `harm_high` with equal visual weight. The central estimate is not  
> "the answer" — it is the midpoint of a statistically derived range.  
> SEBI's own disgorgement calculations use point estimates because they  
> have the full transaction record plus independent expert review. This  
> module does not.

---

### 9.4 Guard System

The module includes a hard-coded allow-list (`app/harm_estimation/guards.py`)
that prevents execution against any instrument not on one of two lists:

1. **SEBI-confirmed cases** — instruments where SEBI has issued an
   adjudication/final order legally confirming manipulation occurred.
   Running harm estimation here answers "how much?" not "did it happen?"
   (the legal order has already answered the latter).

2. **Explicitly-labeled synthetic scenarios** — scenario IDs must carry
   the `SYNTHETIC_` prefix. This prefix appears in every output, so it can
   never be confused with a finding about a real entity.

Every call — including refusals — is written to `app/harm_estimation/guard_audit.log`
for a paper trail. The guard cannot be silently bypassed.

---

### 9.5 Validation Status (Honest)

| Validation Type | Status |
|---|---|
| Synthetic known-answer: injected CAR=+30% recovered within 95% CI | ✅ Verified (tests/test_harm_estimation.py) |
| Synthetic: large effect correctly flagged as statistically significant | ✅ Verified |
| Synthetic: zero-injection produces CAR ≈ 0 (no false detection) | ✅ Verified |
| CI widens with longer event window (Brown & Warner scaling) | ✅ Verified |
| Guard: SEBI confirmed cases allowed | ✅ Verified |
| Guard: unlisted instruments refused | ✅ Verified |
| Guard: bypass_guard=True is logged, not silent | ✅ Verified |
| Real case: KIL-2019 (BSE scrip) | ⚠️ Guard-approved; execution blocked by BSE data gap |
| Real case: PUMP-DUMP-2017-2020 (BSE scrip) | ⚠️ Guard-approved; execution blocked by BSE data gap |
| Production validation on real exchange data | ❌ Not done — requires BSE bhavcopy fetcher |

> [!WARNING]
> **BSE Data Gap — Real Cases Cannot Currently Be Run**:  
> Both confirmed SEBI cases (KIL-2019, PUMP-DUMP-2017-2020) involve instruments
> that are absent from the NSE bhavcopy feed (log-verified by  
> `backtest/results/KIL-2019_diagnostic_v2.log` and  
> `backtest/results/PUMP-DUMP-2017-2020_diagnostic_v2.log`). The harm estimation
> module requires a historical price series — which means real-case execution
> requires BSE bhavcopy access, which is not yet in the Sentinel pipeline.  
>  
> The methodology has been validated on synthetic data only. The module will
> fail loudly (not silently) when given an empty price series.

---

### 9.6 Microstructure Warning

Thinly-traded instruments (< 50,000 shares/day average) are subject to
bid/ask bounce, which inflates observed return variance and biases CAR
estimates upward. Per MacKinlay (1997, p. 24):

> "thin-stock results should be interpreted with caution due to
> non-synchronous trading and bid/ask effects."

The module sets `microstructure_warning=True` for illiquid instruments and
discloses this in every output. The CI is wider (more conservative) for
illiquid instruments due to higher sigma_hat — this is a feature, not a bug.

---

### 9.7 What This Module Does NOT Do

- Does **not** make new manipulation allegations.
- Does **not** produce a SEBI-ready disgorgement figure — SEBI's own
  disgorgement uses the full account-level transaction record, which this
  pipeline does not have for historical cases.
- Does **not** run against live/currently-listed instruments not on the
  allow-list.
- Does **not** produce a point estimate without a confidence interval.
- Does **not** assume market returns were flat during the manipulation
  period — actual market index returns are always used in AR computation.

