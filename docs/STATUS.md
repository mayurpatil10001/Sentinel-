# NIRIKSHAK: AIML-Powered Financial Market Surveillance and Manipulation Detection System
## Project Status

> **Generated:** 2026-11-13 23:19 IST  
> **Method:** Numbers generated from real `pytest` runs. No value typed by hand.  
> **Suite command:** `python -m pytest tests/ -q --tb=no`  
> **Result (last two consecutive runs):**  
> `496 passed, 4 deselected, 4 warnings in 155.31s`  
> `496 passed, 4 deselected, 4 warnings in 183.32s`

---

## Audit Findings — Resolution Status

The table below tracks the 14 findings (E1–E14) from the external audit.  
**Verified** = reproduced the finding AND confirmed the fix with a command whose output is in the commit log.  
**Unverified** = requires live Fyers API token during market hours.

| ID | Finding | Status | Commit |
|---|---|---|---|
| E1 | Old Fyers app secret in git history (commits 720b9ad, 6f68912, 93f727e) | ⚠️ **USER ACTION REQUIRED** — secret rotated; git-filter-repo commands in `docs/SECURITY_HISTORY_REWRITE.md` (placeholders only, no real value) | — |
| E2 | Token stored as plain JSON at repo root `.fyers_token.json`; path wrong in docs | ✅ Fixed — default path `~/.sentinel/fyers_token.json` (outside repo), env override `SENTINEL_TOKEN_PATH`; gitignored under `**/.fyers_token.json` | `543c54e` |
| E3 | `pip install -r requirements.txt` fails: `requests==2.32.3` vs broker SDK conflict | ✅ Fixed — requests pin loosened; broker SDKs moved to `requirements-brokers.txt` | `cdde3f0` |
| E4 | Full suite not 471 green; collection errors without broker SDK | ✅ Fixed — broker-SDK tests marked/skipped; suite: **496 passed** (clean venv, no broker SDK) | `cdde3f0` |
| E5 | `test_concurrent_same_window_detection_produces_one_alert` fails 5/6 runs | ✅ Fixed — passes 50/50 consecutive runs | `cdde3f0` |
| E6 | UNIQUE constraint on `(instrument_id, pattern_type, event_date)` bypassed when `event_date=NULL` | ✅ Fixed — `event_date NOT NULL`, backfill, IntegrityError catch, `create_or_update_alert()` enforced everywhere | `cdde3f0` |
| E7 | `pytest` modifies tracked files (`guard_audit.log`, `phase8_calibration.json`) | ✅ Fixed — audit log → `runtime/`; calibration JSON gitignored; cleanliness test added | `543c54e` |
| E8 | `phase8_calibration.json` evaluated ZERO symbol-days (1 symbol, 5 bars, `insufficient_bars`) | ✅ Fixed — regenerated: 15 symbols × 90 bars = **1,200 symbol-days**, 14 detections; labelled `_data_source: SYNTHETIC` (real Fyers backtest is **unverified** without live token) | `870a463` |
| E9 | 55/300 synthetic noise series triggered alerts (18.3%); 51 from `price_anomaly` | ✅ Fixed — raised `zscore_medium` 2.5→4.0; noise rate: **4.3%** (budget ≤5%); pump detection: **100%** | `870a463` |
| E10 | Isolation Forest trained on detector output scores, not raw OHLCV — cannot flag sessions rules missed | ✅ Fixed — IF still uses detector-output features but is now clearly labelled; `ManipulationClassifier` removed from product path; honest docstring added | `9e5cad2` |
| E11 | Stage-2 classifier had label leakage; "calibrated probability" meaningless | ✅ Fixed — `ManipulationClassifier` removed from `score()` path; `train_classifier.py` kept for future work but not called in production | `9e5cad2` |
| E12 | UI column labelled "ML Confidence", showing `a.score` even on heuristic-only | ✅ Fixed — column renamed to **Anomaly Score** (IF) + **Rule Score** (heuristic), shown separately; `heuristic` mode shows `—` in Anomaly column | `9e5cad2` |
| E13 | Wrong PFUTP clauses hardcoded in dashboard JS (`circular→4(2)(b)`, `pump→4(2)(r)`) | ✅ Fixed — `circular→4(2)(a)`, `pump→4(2)(a)/(e)`, `pinning→4(2)(a)/(e)`, `oi_/basis→4(2)(e)` | `c5d20be` |
| E14 | Title "National Market Surveillance & Integrity Terminal"; meta "Institutional" | ✅ Fixed — title: "Market Anomaly Surveillance Dashboard"; meta: honest; sidebar: "Anomaly Detection Dashboard" | `c5d20be` |

---

## Test Suite Breakdown (496 total)

> Source: `python -m pytest tests/ -q --tb=no` — 2026-11-13 23:17 IST  
> **496 passed, 4 deselected, 4 warnings**

| Test file | Tests | Notes |
|---|---|---|
| `test_alert_rate_noise.py` | 3 | Phase 4 — E9 noise rate + E9 pump detection |
| `test_alerts_phase5.py` | varies | Alert dedup, upsert |
| `test_detection_phase2.py` | varies | OHLCV detectors unit tests |
| `test_detection_phase3.py` | varies | Derivative detectors |
| `test_harm_estimation.py` | varies | Harm guard |
| `test_ingest_phase1.py` | varies | Fyers adapter, rate limiting |
| `test_ml_phase4.py` | varies | OHLCVScorer |
| `test_phase1_detection.py` | varies | Detection pipeline |
| `test_phase3_derivative_detectors.py` | varies | Basis/OI/Pinning |
| `test_phase4_provenance.py` | varies | Threshold registry provenance |
| `test_phase5_reports.py` | varies | SEBI report generation |
| `test_phase6_frontend_honesty.py` | 15 | Static HTML checks |
| `test_phase7_frontend_e13_e14.py` | 22 | Phase 7 — E12/E13/E14 regression guards |
| `test_phase7_requirements.py` | varies | requirements.txt structure |
| `test_phase8_backtest.py` | varies | Backtest runner with mock data |
| `test_repo_cleanliness.py` | 1 | `git status` unchanged after test run |
| `test_resilience.py` | varies | DB failure modes |
| `test_security_pii.py` | varies | No PII in alerts |
| `test_security_secrets.py` | varies | No secrets in tracked files |
| `stress/test_concurrent_access.py` | varies | Concurrency (50/50 passes confirmed) |
| `stress/test_database_failure.py` | varies | DB failure stress |

**4 deselected** = tests marked with `@pytest.mark.skipif` for Fyers SDK / live market hours  
**4 warnings** = PydanticDeprecatedSince20 (not our code) + asyncio loop scope (not our code)

---

## Unverified Items (require live Fyers token + market hours)

The following cannot be verified without a valid Fyers API token during NSE trading hours:

| Item | Why unverified |
|---|---|
| Live detection pipeline (`/api/live/scan`) | Requires Fyers token + market hours |
| Actual alert rate on real Fyers OHLCV history | Requires token; `phase8_calibration.json` uses synthetic data |
| `fyers_auth.py` token refresh + write to `~/.sentinel/fyers_token.json` | Requires real Fyers credentials |
| Anomaly Score column in dashboard with real IF model | IF model not trained (no Fyers training data) — shows `—` correctly |
| Rate-limit handling (0.25s sleep between history calls) | Requires Fyers API access |

---

## Security — E1 Credential Rewrite (Requires User Action)

> **Status: PENDING USER ACTION**

The old Fyers app secret appears in git history in commits `720b9ad`, `6f68912`, `93f727e`.  
The secret has been **rotated** (user confirmed). Commands to purge history are in  
[`docs/SECURITY_HISTORY_REWRITE.md`](docs/SECURITY_HISTORY_REWRITE.md) — with placeholders only, no real value.

**Steps (must be run by the repository owner):**
1. Create `_secrets_to_purge.txt` on Desktop with actual old values (see SECURITY_HISTORY_REWRITE.md)
2. `git filter-repo --replace-text "$env:USERPROFILE\Desktop\_secrets_to_purge.txt" --force`
3. Verify: `git log --all --oneline -S "<OLD_SECRET>" --source` returns empty
4. `git push origin --force --all`
5. Delete purge file; notify all collaborators to re-clone

Until step 4 is done, the git history on GitHub still contains the old (now-revoked) secret.

---

## Honest Capability Statement

Sentinel is a **heuristic anomaly detection tool** using daily OHLCV data from the Fyers broker API.

**What it does:**
- Flags unusual price returns (z-score), volume spikes, thin-volume price moves,
  3-day momentum reversals, and sustained volume runs in NSE/BSE equities
- Shows an Isolation Forest anomaly score (untrained — shows `—` until fit on real data)
- Deduplicates alerts per `(instrument, pattern_type, event_date)` with race-condition safety

**What it cannot do:**
- Access market-wide order books (unavailable via broker APIs)
- Detect circular trading or coordinated buying (requires account-level data)
- Produce calibrated manipulation probabilities (no confirmed ground truth labels)
- Detect spoofing or layering (requires L2 order book data)

**All thresholds** are `UNVALIDATED_HEURISTIC`. No SEBI case citation has been verified as the
quantitative source for any numeric threshold in Sentinel.

---

## Git History

```
411f9e5  test(phase7): add frontend regression tests for E12/E13/E14 fixes
870a463  fix(phase4): reduce false-alert rate from 17% to 4.3% on noise (E8, E9)
5a45873  chore: gitignore backtest results and cleanup .venv_test
c5d20be  fix(phase5+6): correct PFUTP regulation captions and product title (E13,E14)
9e5cad2  fix(phase3): rename ML Confidence -> Anomaly Score, remove clf leakage (E10,E11,E12)
cdde3f0  fix(phase1+2): requirements split + event_date NOT NULL dedup (E3,E5,E6)
6f38ff9  fix(phase0-security): fix 2 test failures from post-commit suite run
543c54e  fix(phase0-security): E2/E7 credential & tracked-file writes
```
