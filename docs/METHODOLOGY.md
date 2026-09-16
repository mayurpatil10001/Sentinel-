# Sentinel — Detection Methodology

**Version**: 0.2.0-SAMPLE  
**Date**: 2026-09-16  
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

**Validation status**: NOT VALIDATED. The Kavit Industries case (BSE, 2019)
is a publicly documented circular trading case but involves BSE-only data
not reachable by the current NSE bhavcopy fetcher. Account-level data
needed for full validation is not publicly available for any confirmed case.

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
(NSE/BSE, 2017-2020) is a relevant case but all 5 scrips in that case are
confirmed unavailable via NSE equity bhavcopy (either BSE-only or delisted).
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
1. The relevant SEBI cases involve BSE-listed instruments not reachable by
   the NSE bhavcopy fetcher.
2. Account-level order data for historical confirmed manipulation cases is
   not publicly available in India.
3. The one NSE-listed case attempted (Mauria Udyog cluster) involves scrips
   confirmed to be unavailable via NSE bhavcopy.

---

## 5. Backtest Results Summary (as of 2026-09-16)

Two SEBI cases were selected for backtesting:

**Case KIL-2019** (Kavit Industries Limited, BSE circular trading, 2019):
- Verdict: PARTIALLY_TESTABLE
- Reason: BSE-only instrument. NSE bhavcopy fetcher cannot retrieve data.
  Account-level trade data is not public. Only daily price/volume signals
  could theoretically be tested, but the relevant adapter is not implemented.

**Case PUMP-DUMP-2017-2020** (Mauria Udyog cluster, 2017-2020):
- Verdict: UNTESTABLE (confirmed by Phase 2 diagnostic)
- Reason: All 5 scrips (MAURIUDYOG, 7NRRETAIL, GBLIND, VISHALFAB, DARJROPE)
  return 0 days fetched from NSE bhavcopy. Diagnostic confirms this is not
  a fetch bug — the instruments are either BSE-only or delisted and thus
  genuinely not available via the NSE equity bhavcopy archive. The
  "likely delisted" label is confirmed correct.
- Account-level manipulation data (coordinated buys below bulk deal threshold)
  was by design not in any public archive.

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
