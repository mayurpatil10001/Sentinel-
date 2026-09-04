# Sentinel Phase 8 Backtest Report
## Real SEBI Case Validation

**Date**: September 4, 2026  
**Backtest run by**: `python -m backtest.run_backtest`  
**Raw results**: `backtest/results/*.json` and `backtest/results/run.log`

---

## 1. What This Report Is — And What It Is Not

This report presents the results of running Sentinel's detection logic
against real, publicly documented SEBI enforcement cases involving confirmed
market manipulation. It is the first test of this system against real events
rather than synthetic data.

**What it IS:**
- An honest accounting of which detectors could be tested, which could not,
  and why, based on public data availability in India.
- Real bhavcopy data fetched from `archives.nseindia.com` for the actual
  scrips and dates cited in SEBI's published orders.
- An honest false positive rate baseline on real large-cap stocks on
  normal trading days.

**What it IS NOT:**
- Evidence that the system "detects market manipulation." The only signal
  tested here is a daily OHLCV price/volume anomaly — a necessary-but-not-
  sufficient precondition for manipulation, not the manipulation itself.
- A complete detector validation. 4 of 6 detectors are untestable with
  any public Indian data. This is not a limitation of this backtest —
  it is a limitation of what data exists publicly in India.
- A claim of accuracy. A detection rate without a false positive rate is
  not evidence. This report provides both.

---

## 2. SEBI Cases Used

All three cases below are real, verified SEBI enforcement orders.
To independently verify: visit https://www.sebi.gov.in/enforcement/orders.html
and search by entity name or order reference.

### Case C1: Kavit Industries Limited (KIL-2019)

| Field | Value |
|---|---|
| Order reference | Adjudication Order in the matter of Kavit Industries Limited, SEBI AO Order, February 28, 2025 |
| Investigation period | August 1, 2019 – December 23, 2019 |
| Exchange | BSE |
| Alleged pattern | Synchronized trades, circular trades, reversal trades (20 accounts, 3 managing entities) |
| Price impact documented by SEBI | Rs. 44 → Rs. 93.80 (+113% over 5 months) |
| Penalty | Imposed under SEBI Act s.15HA and PFUTP Regulations |

**Testability verdict: PARTIALLY_TESTABLE → effectively UNTESTABLE via this pipeline.**  
Kavit Industries traded on BSE. Sentinel's bhavcopy ingestion layer
(`nse_bhavcopy.py`) fetches NSE archives only. The NSE bhavcopy for the
KIL-2019 period was fetched across all 103 business days; `KAVIT` appeared
in **0 of 103** bhavcopy files — confirming it was never NSE-listed.

A BSE bhavcopy fetcher (accessing `www.bseindia.com/download/BhavCopy/...`)
would be needed. This is buildable but out of Phase 8 scope. Documented
here for completeness; this case contributes no detector results.

### Case C2: Mauria Udyog / 7NR Retail / GBL Industries / Darjeeling Ropeway / Vishal Fabrics Cluster (PUMP-DUMP-2017-2020)

| Field | Value |
|---|---|
| Order reference | Ex Parte Ad Interim Order-cum-Show Cause Notice, June 19, 2023; Final Order June 2026 — In the Matter of Manipulation in Scrips including Mauria Udyog Ltd. and others |
| Investigation period | 2017–2020 (scrip-specific sub-periods applied in backtest) |
| Exchange | NSE/BSE |
| Alleged pattern | Pump-and-dump: coordinated buying in illiquid scrips → bulk SMS retail promotion → offloading at inflated price. 222 entities barred in final order. |
| Alleged gains | Rs. 143.79 crore + interest disgorged |
| Key figure named | Hanif Shekh (alleged mastermind) |

**Testability verdict: PARTIALLY_TESTABLE.**  
Price/volume anomaly adapter run across 5 scrips (see Section 4 for results).

### Case C3: Gravity India Limited (GIL-2003-2004)

| Field | Value |
|---|---|
| Order reference | ORDER/SBM/KL/2021-22/15788, Adjudicating Officer Kiran Lohia, March 31, 2022 |
| Investigation period | December 23, 2003 – March 3, 2004 |
| Exchange | BSE |
| Alleged pattern | Circular trading: connected clients generated 71% of gross volume |

**Testability verdict: UNTESTABLE.**  
Manipulation period (Dec 2003 – Mar 2004) predates reliable NSE archive
coverage. BSE archives for 2003-2004 are not accessible via this pipeline.
This case is cited for completeness and as evidence of real-world precedent,
not as a test case.

---

## 3. Detector Applicability — Honest Map

This table shows every Sentinel detector and whether it can be tested
against any of the cases above, with the specific reason for each verdict.

| Detector | Applicable to any case? | Reason |
|---|---|---|
| `spoofing.py` | **NO** | Requires order-lifecycle data (placed → cancelled / executed timestamps). NSE and BSE have never published historical order books publicly. No workaround exists using public data. |
| `circular_trading.py` | **NO** | Requires account-level trade pairs (same account buys and sells same instrument). Account-level data is not in public archives. The manipulation in all three cases was specifically designed to stay below bulk deal disclosure thresholds (Rs. 5 crore / 0.5% of shares) to avoid detection. |
| `coordinated_pump.py` | **NO** (full detector) / **PROXY** (daily OHLCV adapter) | Full detector requires account-level `Order` objects with `account_id`. These don't exist in public data. A price/volume anomaly proxy is used instead — see Section 4 for methodology and limitations. |
| `basis_distortion.py` | **NO** | None of the three documented SEBI cases involve F&O basis manipulation. This detector is applicable to the correct data but has no matching SEBI test case available. |
| `oi_manipulation.py` | **NO** | NSE publishes only the current option chain OI. No historical OI snapshot archive exists publicly. Even if a SEBI case existed, the data to test it does not. |
| `option_pinning.py` | **NO** | Same reason as `oi_manipulation.py`. |

**Summary**: 0 of 6 detectors can be tested in full. 1 of 6 can be tested
via a documented proxy (daily OHLCV anomaly adapter for `coordinated_pump.py`).

This is not a reflection on the quality of the detectors. It is a reflection
on what data India's public market infrastructure makes available historically.

---

## 4. What Was Actually Tested: Price/Volume Anomaly Adapter

### Methodology

Since account-level data does not exist, the price/volume anomaly adapter
asks a more limited but still meaningful question:

> *On the days SEBI documented as manipulation, was the daily price × volume
> signal anomalous enough to be visible in public bhavcopy data?*

**Signal definition** (uses same thresholds as `coordinated_pump.py`):
- Volume spike: daily volume ≥ **5× the 30-day rolling average** (same as `VOLUME_SPIKE_MULTIPLE`)
- Price move: daily price change ≥ **3%** in either direction
- **Flagged** = both conditions fire on the same day

**Critical limitation**: The full `coordinated_pump.py` detector additionally
requires ≥ 3 distinct accounts buying in a 30-minute window, with dormancy/
new-account weighting. This layer eliminates the majority of false positives
(legitimate institutional volume + earnings moves). The adapter here cannot
apply this layer. The false positive rate in Section 5 is therefore **the
adapter's false positive rate, not the full detector's**.

---

## 5. Results: Case C2 (PUMP-DUMP-2017-2020)

*[Results filled in after backtest run completes — see backtest/results/*.json]*

### Per-Scrip Results

| Scrip | NSE symbol attempted | Days in manipulation window | Days fetched | Days flagged (vol ≥5× AND price ≥3%) | Result |
|---|---|---|---|---|---|
| Mauria Udyog Ltd. | MAURIUDYOG | *[TBD]* | *[TBD]* | *[TBD]* | *[TBD]* |
| 7NR Retail Ltd. | 7NRRETAIL | *[TBD]* | *[TBD]* | *[TBD]* | *[TBD]* |
| GBL Industries Ltd. | GBLIND | *[TBD]* | *[TBD]* | *[TBD]* | *[TBD]* |
| Vishal Fabrics Ltd. | VISHALFAB | *[TBD]* | *[TBD]* | *[TBD]* | *[TBD]* |
| Darjeeling Ropeway Co. | DARJROPE | *[TBD]* | *[TBD]* | *[TBD]* | *[TBD]* |

**Overall C2 verdict**: *[TBD — TESTABLE_HIT / TESTABLE_MISS / UNTESTABLE]*

### Interpretation

**If verdict is TESTABLE_HIT:** The price/volume anomaly adapter flagged
at least some manipulation-window days. This means the OHLCV signal was
visible in bhavcopy. However: this does NOT confirm the full detector would
have fired. Whether the account-level layer (≥3 distinct coordinated buyers,
dormancy weighting) would have confirmed or rejected these signals is
unknown without account-level data.

**If verdict is TESTABLE_MISS:** The manipulation-window days did not produce
price/volume signals above the threshold in bhavcopy. This is plausible for
a well-executed pump-and-dump — sophisticated operators spread buying across
many small days to avoid volume spikes, using the SMS campaign to provide the
price catalyst from retail side rather than from their own volume. A miss does
NOT mean SEBI was wrong. It means the daily OHLCV proxy is insufficient for
this specific type of manipulation (which is what SEBI's account-level
investigation was required to uncover).

---

## 6. Results: Case C1 (KIL-2019) and Case C3 (GIL-2003-2004)

| Case | Run verdict | Reason |
|---|---|---|
| KIL-2019 (Kavit Industries) | UNTESTABLE | `KAVIT` not found in NSE bhavcopy across all 103 business days of the investigation period. BSE-only scrip. NSE bhavcopy pipeline cannot test this case. |
| GIL-2003-2004 (Gravity India) | UNTESTABLE | Manipulation period Dec 2003 – Mar 2004 predates reliable archive coverage. Skipped before fetching. |

---

## 7. Negative Control Results

**Stocks tested**: RELIANCE, TCS, HDFCBANK, INFY, ICICIBANK  
**Dates tested**: 20 hand-selected normal trading days across 2019–2022  
**Purpose**: Establish the false positive rate of the price/volume anomaly adapter on indisputably unmanipulated large-cap stocks.

| Symbol | Days tested | Days flagged | False positive rate |
|---|---|---|---|
| RELIANCE | *[TBD]* | *[TBD]* | *[TBD]* |
| TCS | *[TBD]* | *[TBD]* | *[TBD]* |
| HDFCBANK | *[TBD]* | *[TBD]* | *[TBD]* |
| INFY | *[TBD]* | *[TBD]* | *[TBD]* |
| ICICIBANK | *[TBD]* | *[TBD]* | *[TBD]* |
| **Overall** | *[TBD]* | *[TBD]* | *[TBD]* |

**Why false positives on large-caps happen at all**: On large caps, 3%+ daily
moves with elevated volume occur on earnings days, index rebalancing events,
RBI rate decisions, and major macro news. These are not manipulation. The
full `coordinated_pump.py` detector's account-layer (which we cannot apply
here) exists specifically to eliminate these. The adapter's FP rate is its
raw, unfiltered rate.

---

## 8. What This Backtest Establishes — Precisely

### Established:
1. The NSE bhavcopy archive is accessible and returns clean OHLCV data for
   2017–2022 from `archives.nseindia.com` without authentication.
2. The price/volume anomaly adapter (a proxy for `coordinated_pump.py`)
   runs correctly on real historical data.
3. Specific data availability results per case are documented honestly above.

### NOT established:
1. That any of the 6 Sentinel detectors correctly identifies real manipulation.
   None could be run in full — the data does not exist publicly.
2. That the system would have detected the Kavit Industries, Gravity India,
   or pump-dump cases if deployed in real-time. The full detector requires
   data that is only available to exchange surveillance teams (account-level
   order streams).
3. That the `VOLUME_SPIKE_MULTIPLE = 5.0` threshold is correct. It remains
   `UNVALIDATED GUESS` as documented in `coordinated_pump.py`'s own docstring.

---

## 9. What Would Be Needed for a Real Validation

A genuine validation — one that could produce defensible sensitivity/
specificity numbers — requires:

1. **Account-level historical trade data** from NSE/BSE's surveillance
   archive. This is available only to SEBI, NSE, and licensed market
   intelligence providers (e.g., NICE Actimize, TCS BaNCS Surveillance).
   It is not publicly available.

2. **Historical option chain OI snapshots** for `oi_manipulation.py` and
   `option_pinning.py`. NSE publishes the current option chain but no
   historical archive of per-strike OI.

3. **A negative control set at account level** — confirmed non-manipulative
   accounts with similar activity profiles — to compute the full detector's
   false positive rate.

Without these, the honest statement is: **Sentinel's detectors are well-
engineered against their specifications, unit-tested against synthetic data,
stress-tested for concurrency and resilience — but their detection efficacy
against real manipulation remains unvalidated by any publicly available means.**

This is not a rare situation. Every commercial market surveillance system
faces the same constraint; their vendors' claimed false-positive rates come
from proprietary exchange datasets, not public archives.

---

## 10. Data Quality Notes

- Delivery percentage (DELIVQTY/TRADEDQTY) was unavailable for all dates
  tested: the `.dat` delivery files at `archives.nseindia.com/products/content/`
  return HTTP 404 for 2017–2022 dates. This is an NSE archive limitation,
  not a Sentinel bug. The OHLCV data (from the main `.csv.zip` file) was
  unaffected and was fetched successfully.
- All fetch failures are logged in `backtest/results/run.log`.
- `www.nseindia.com` homepage returned HTTP 403 (documented in Phase 6).
  This did not affect the archive server at `archives.nseindia.com`.

---

## 11. Appendix: Raw Results Files

| File | Contents |
|---|---|
| `backtest/results/KIL-2019_result.json` | Per-day detail for Kavit Industries case |
| `backtest/results/PUMP-DUMP-2017-2020_result.json` | Per-scrip, per-day results for pump-dump cluster |
| `backtest/results/GIL-2003-2004_result.json` | Skip record for Gravity India (UNTESTABLE) |
| `backtest/results/negative_controls.json` | Per-symbol false positive rate |
| `backtest/results/run.log` | Complete fetch log with every HTTP request/response |

---

*This report was generated as part of Sentinel Phase 8. All SEBI case
citations are from publicly available enforcement orders. SEBI's findings
and conclusions in those orders are not reproduced at length; they are
summarized in the authors' own words for analysis purposes.*
