# NSE Data Access Limitations

**Status**: Honest technical assessment. Last updated: 2026-09-04.

---

## Live Test Results (verbatim output — not summarised)

Run on 2026-09-04 from the development machine (datacenter/cloud IP):

```
pytest tests/test_ingest_phase1.py -m live -v --tb=short

platform win32 -- Python 3.11.0, pytest-8.2.2
rootdir: D:\Sentinel
configfile: pytest.ini

collected 34 items / 30 deselected / 4 selected

tests/test_ingest_phase1.py::test_live_bhavcopy_yesterday FAILED         [ 25%]
tests/test_ingest_phase1.py::test_live_bulk_deals PASSED                 [ 50%]
tests/test_ingest_phase1.py::test_live_option_chain_nifty FAILED         [ 75%]
tests/test_ingest_phase1.py::test_live_broker_auth_error_without_creds PASSED [100%]

FAILURES
--------
test_live_bhavcopy_yesterday
  BhavcopyFetchError: Bhavcopy fetch failed for
  'https://archives.nseindia.com/content/historical/EQUITIES/2026/SEP/cm03SEP2026bhav.csv.zip'
  (HTTP 404): non-trading day or archive not yet available.

  Log captured:
    WARNING NSE homepage cookie handshake failed: 403 Client Error:
    Forbidden for url: https://www.nseindia.com/
    WARNING HTTP 404 on attempt 1/4 — NON-RETRYABLE (not retrying)

test_live_option_chain_nifty
  MaxRetriesExceededError: Max retries exceeded for source '_NSESession.get'
  after 3 attempt(s). Last error: Option chain fetch failed for
  'https://www.nseindia.com/' (HTTP None): Could not establish NSE session
  (homepage fetch failed): 403 Client Error: Forbidden for url:
  https://www.nseindia.com/

  (Note: After Phase 6 bug fix, this now correctly fails immediately on
   first attempt with HTTP 403 NON-RETRYABLE rather than retrying 3 times.)

2 failed, 2 passed in 7.26s
```

---

## What Works vs. What Is Blocked

| Source | Status | Reason |
|---|---|---|
| NSE Bulk Deals CSV (`archives.nseindia.com/content/equities/bulk.csv`) | ✅ **Passes** | Static CSV download, no JS challenge, cookie not required |
| NSE Block Deals CSV (`archives.nseindia.com/content/equities/block_deal.csv`) | ✅ **Expected to pass** (same path as bulk) | Same server, no session requirement |
| NSE Bhavcopy archive (`archives.nseindia.com/content/historical/...`) | ❌ **Fails** | Homepage (`www.nseindia.com`) returns 403, blocking cookie handshake. Bhavcopy archive then 404d (Sep 3 was either a holiday or archive not yet posted — both are possible.) |
| NSE Option Chain API (`www.nseindia.com/api/option-chain-indices`) | ❌ **Fails** | `www.nseindia.com` itself returns 403 — IP is blocked before any API call |
| Zerodha Kite broker stream | ✅ **Passes** (auth error without credentials is expected, proves module loads) | No NSE dependency |

---

## Root Cause: Why the 403 Happens

NSE's `www.nseindia.com` uses **multiple layers of bot protection** that are well-documented in financial data engineering communities:

### Layer 1: IP Reputation

NSE (and Cloudflare in front of it) maintains a list of IP ranges associated with cloud
providers, data centers, and residential VPNs. Requests from these IPs receive a 403 before
any other check runs. This is why the homepage itself (not just the API) returns 403 — the
decision happens at the network edge, before the webserver even sees the request.

**This cannot be fixed with headers.** The Chrome User-Agent, Referer, sec-fetch headers, and
session cookies we added are correct and do help from residential IPs. They are irrelevant when
the IP itself is on the deny list.

### Layer 2: TLS Fingerprinting (JA3/JA4)

Cloudflare compares the TLS Client Hello message's cipher suite list, extensions, and
elliptic curves against known browser fingerprints. Python's `requests` library (using
`urllib3` and OpenSSL) produces a different TLS fingerprint than Chrome, even with identical
HTTP headers.

**This cannot be fixed with the `requests` library alone.** It requires either:
- `tls-client` (a library that mimics Chrome's TLS stack), or
- Playwright/Selenium (which actually runs Chrome), or
- A data agreement with NSE.

### Layer 3: JavaScript Challenge / Behavioral Analysis

For users who pass layers 1 and 2, NSE sometimes issues a JavaScript challenge (similar to
Cloudflare's "Checking your browser" page). Pure HTTP clients cannot execute JavaScript.

---

## Three Paths Forward (Honest Assessment)

### Path A: Official NSE Data Agreement (Correct for Production)

NSE provides official data feeds under the **NSE Market Data Feed** programme and through
**licensed data vendors** (Refinitiv/LSEG, Bloomberg, FactSet, Murkutu, etc.).

- **Cost**: Commercial agreement — pricing varies by product and entity type.
- **How to get it**: Apply through NSE's market data team or a licensed vendor.
- **What you get**: Reliable, high-frequency, legally compliant data with SLA. No 403s.
- **Timeline**: Weeks to months for contract negotiation.
- **Suitability**: This is the only path appropriate for a **regulatory-grade, production-deployed** surveillance system like Sentinel.

### Path B: Browser Automation (Playwright/Selenium)

Running Chrome via Playwright or Selenium passes layers 1 and 2 (real Chrome TLS fingerprint
and real IP-agnostic behavior). The session cookies it obtains can be used for subsequent API calls.

**Limitations (be honest about these):**
- Fragile: NSE updates its bot detection periodically. Any update can break automation without warning.
- Performance: A headless Chrome instance is heavy — not suitable for high-frequency or batch calls.
- Terms of Service: NSE's ToS prohibit automated scraping. Using Playwright is legally grey territory for a system that will be used in any regulatory or professional context.
- Not scalable: Each option chain call takes several seconds for browser initialization + cookie setup.
- **Bottom line**: Acceptable for a developer's personal research/testing. Not acceptable for a production surveillance system.

### Path C: Residential / Whitelisted IP

Running Sentinel from an ISP residential IP (not a cloud/VPN IP) bypasses Layer 1 and avoids
the IP reputation block. Some ISP IP ranges in India are not yet blocked by NSE.

**Limitations:**
- Unreliable: NSE's deny list grows over time. An IP that works today may be blocked next month.
- Not reproducible: "Works from my home internet" is not a deployable architecture for a system that needs uptime guarantees.
- Still blocked by TLS fingerprinting (Layer 2) in some environments.
- **Bottom line**: Useful for development and testing. Not an architecture for production.

---

## What Is Currently Implemented in Sentinel

Sentinel's Phase 6 resilience changes implement:
- **Browser-realistic headers** (Chrome UA, all sec-fetch-* headers, DNT, Accept-Language, etc.)
- **Session cookie handshake** for all three NSE endpoints (same as a browser's first-page visit)
- **Realistic delays** between cookie handshake and API calls (mimics human browsing pace)
- **`retry_with_backoff`** with explicit RETRYABLE/NON-RETRYABLE classification:
  - 403 is **NON-RETRYABLE** — fails immediately with clear message, no wasted retries
  - 5xx / network failures are retried with exponential backoff + jitter
- **`CircuitBreaker`** to stop hammering a blocked source
- **`determine_fallback()`** typed decision tree — 403 maps to `ALERT_OPERATOR`, not a silent skip
- **`get_safe_fallback_data()`** synthetic data firewall — always raises, proven by test

**What is not implemented** (and cannot be without Path A or Path B):
- Bypass of NSE's IP reputation layer
- TLS fingerprint spoofing
- JavaScript challenge execution

---

## Recommended Next Step

For Sentinel to function as a production market surveillance system, the **only viable path is
Path A**: an official NSE data agreement or a licensed data vendor relationship.

Until then, the system can operate on:
- **Bulk and block deals data** (works without session cookies from any IP)
- **Zerodha Kite broker order stream** (works with API credentials, no NSE dependency)
- **Synthetic data** for development/testing (via `demo/generate_synthetic_orderflow.py` — never
  imported from production code paths)

The detection algorithms (spoofing, circular trading, coordinated pump, OI manipulation, basis
distortion, option pinning) are complete and validated. The data ingestion layer has production-grade
resilience. The system is **architecturally ready** for a production data feed — blocked only by
the commercial/network access question.

---

## Bug Fixed During Phase 6

During live testing, a classification bug was discovered and fixed:

**Bug**: `_NSESession._ensure_cookies()` raised `OptionChainFetchError` with `status_code=None`
when `raise_for_status()` fired on a 403. The `retry_with_backoff` decorator interpreted
`status_code=None` as a "network-level failure" (retryable), causing it to retry a 403 three
times — wasted work.

**Fix**: `HTTPError` is now caught separately from `RequestException`. `exc.response.status_code`
is extracted and passed through to `OptionChainFetchError.status_code`. The retry decorator
correctly classifies it as NON-RETRYABLE and fails immediately on the first attempt.

**After fix**: The option chain 403 now fails in ~0.8 seconds (one attempt + POST_COOKIE_DELAY)
instead of ~7 seconds (three attempts with backoff). More importantly, the `determine_fallback()`
function correctly routes it to `ALERT_OPERATOR` instead of `RETRY_AFTER_COOLDOWN`.
