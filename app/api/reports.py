"""
app/api/reports.py
==================
GET /api/reports/watch       — Manipulation Watch Report for last N days
GET /api/reports/watch/html  — same report as a plain HTML page

Phase 5 spec:
  - After each EOD scan: generate a "Manipulation Watch Report" for the last
    N days (default 3): flagged instruments, evidence numbers (volume ratio,
    z-score, dates), severity, new vs existing alerts.
  - Link to draft-SAR builder for high/critical alerts.
  - Every report makes the UNVALIDATED_HEURISTIC provenance explicit.
  - If no alerts in the window: return an honest "No alerts in period" summary.
  - Reports must NEVER fabricate summary statistics. All numbers from DB rows.
"""
import os
from datetime import datetime, date, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.db.models import Alert, Instrument, ScanRun

router = APIRouter(prefix="/api/reports", tags=["reports"])


def _ist_today() -> date:
    """Return today's date in IST (UTC+5:30)."""
    IST_OFFSET = timedelta(hours=5, minutes=30)
    return (datetime.now(timezone.utc) + IST_OFFSET).date()


def _build_watch_report(db: Session, days: int, source_filter: Optional[str]) -> dict:
    """
    Core report builder. Called by both JSON and HTML endpoints.
    Returns a plain dict suitable for JSON serialisation.
    """
    today = _ist_today()
    from_date = today - timedelta(days=days - 1)   # inclusive window

    # Base query: alerts in the date window
    q = (
        db.query(Alert)
        .join(Alert.instrument)
        .filter(Alert.event_date >= from_date)
        .filter(Alert.event_date <= today)
    )

    # Phase 4 provenance: exclude synthetic_demo by default
    if source_filter:
        q = q.filter(Alert.source == source_filter)
    else:
        q = q.filter(Alert.source != "synthetic_demo")

    alerts = q.order_by(Alert.event_date.desc(), Alert.score.desc()).all()

    # Severity bucketing
    high_critical = [a for a in alerts if a.severity in ("high", "critical")]
    medium = [a for a in alerts if a.severity == "medium"]
    low = [a for a in alerts if a.severity == "low"]

    # Last scan run
    last_scan = (
        db.query(ScanRun)
        .filter(ScanRun.status != "skipped")
        .order_by(ScanRun.finished_at.desc())
        .first()
    )

    def _alert_summary(a: Alert) -> dict:
        inst = a.instrument
        return {
            "alert_id": a.id,
            "instrument": inst.symbol if inst else "unknown",
            "exchange": inst.exchange if inst else "NSE",
            "pattern_type": a.pattern_type,
            "severity": a.severity,
            "score": round(a.score, 4),
            "event_date": a.event_date.isoformat() if a.event_date else None,
            "occurrences": a.occurrences or 1,
            "first_detected": a.detected_at.isoformat() if a.detected_at else None,
            "last_seen": a.last_seen_at.isoformat() if a.last_seen_at else None,
            "is_new": (a.occurrences or 1) == 1,
            "status": a.status,
            "source": a.source or "live_fyers",
            # Explanation verbatim from DB — never paraphrase or summarise
            "explanation": a.explanation,
            # SAR draft link (analyst must review before filing)
            "sar_draft_url": f"/api/alerts/{a.id}/escalate-to-sebi"
            if a.severity in ("high", "critical")
            else None,
        }

    return {
        "report_type": "manipulation_watch",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "period": {
            "from_date": from_date.isoformat(),
            "to_date": today.isoformat(),
            "days": days,
        },
        "last_scan": {
            "finished_at": last_scan.finished_at.isoformat() if last_scan and last_scan.finished_at else None,
            "mode": last_scan.mode if last_scan else None,
            "status": last_scan.status if last_scan else None,
            "symbols_scanned": last_scan.symbols_scanned if last_scan else 0,
        } if last_scan else {"finished_at": None, "mode": None, "status": "no_scan_recorded", "symbols_scanned": 0},
        "summary": {
            "total_alerts": len(alerts),
            "high_critical": len(high_critical),
            "medium": len(medium),
            "low": len(low),
            "new_this_period": sum(1 for a in alerts if (a.occurrences or 1) == 1),
            "existing_redetected": sum(1 for a in alerts if (a.occurrences or 1) > 1),
        },
        "alerts": {
            "high_critical": [_alert_summary(a) for a in high_critical],
            "medium": [_alert_summary(a) for a in medium],
            "low": [_alert_summary(a) for a in low],
        },
        # Phase 2 mandate: every report makes provenance explicit
        "data_provenance": (
            "All detection thresholds are UNVALIDATED_HEURISTIC. "
            "No SEBI order number, date, or URL has been verified as the source "
            "for any numeric threshold. These flags are starting points for analyst "
            "review — not findings of manipulation."
        ),
        "limitations": (
            "Sentinel detects statistical outliers in OHLCV data and option chain "
            "metrics. It cannot detect spoofing, circular trading, or coordinated "
            "manipulation without order/account-level data for all market participants "
            "(not available via broker APIs). "
            "An alert is unusual activity — a statistical outlier, not a finding of manipulation."
        ),
        # Analyst action guidance
        "analyst_guidance": (
            "High/critical alerts include a sar_draft_url. "
            "Review the explanation and evidence before initiating escalation. "
            "The SAR draft is a template only — analyst must verify facts before filing."
        ),
    }


# ── GET /api/reports/watch ────────────────────────────────────────────────────

@router.get("/watch")
def get_watch_report(
    days: int = Query(3, ge=1, le=30, description="Lookback window in calendar days"),
    source: Optional[str] = Query(
        None,
        description="Filter by source (live_fyers|eod_history|backtest|synthetic_demo). "
                    "Default excludes synthetic_demo.",
    ),
    db: Session = Depends(get_db),
) -> dict:
    """
    Manipulation Watch Report for the last `days` calendar days.

    Returns all alerts in the window grouped by severity (high_critical / medium / low).
    Every alert summary includes: instrument, pattern, score, event_date, occurrences,
    is_new, explanation verbatim from DB, and a sar_draft_url for high/critical alerts.

    Data provenance and limitations are included in every response — never elided.
    synthetic_demo rows are excluded by default (they are seeded demo data).
    """
    return _build_watch_report(db, days=days, source_filter=source)


# ── GET /api/reports/watch/html ───────────────────────────────────────────────

@router.get("/watch/html", response_class=None)
def get_watch_report_html(
    days: int = Query(3, ge=1, le=30),
    source: Optional[str] = Query(None),
    db: Session = Depends(get_db),
):
    """
    Same Manipulation Watch Report as a minimal, print-ready HTML page.
    Suitable for saving as PDF or attaching to an analyst brief.

    All numbers come directly from DB rows — no fabrication.
    """
    from fastapi.responses import HTMLResponse

    report = _build_watch_report(db, days=days, source_filter=source)
    summary = report["summary"]
    period = report["period"]

    rows_html = ""
    for severity_tier in ("high_critical", "medium", "low"):
        for item in report["alerts"][severity_tier]:
            badge_color = {
                "critical": "#c0392b",
                "high": "#e67e22",
                "medium": "#f39c12",
                "low": "#27ae60",
            }.get(item["severity"], "#7f8c8d")
            new_badge = '<span style="color:#2980b9;font-size:11px"> [NEW]</span>' if item["is_new"] else ""
            sar_link = (
                f' <a href="{item["sar_draft_url"]}" target="_blank" '
                f'style="font-size:11px;color:#c0392b">Draft SAR</a>'
                if item.get("sar_draft_url") else ""
            )
            rows_html += f"""
            <tr>
                <td>{item['event_date'] or '—'}</td>
                <td><strong>{item['instrument']}</strong> ({item['exchange']})</td>
                <td>{item['pattern_type']}</td>
                <td><span style="color:{badge_color};font-weight:bold">
                    {item['severity'].upper()}
                </span></td>
                <td>{item['score']:.3f}</td>
                <td>{item['occurrences']}× {new_badge}</td>
                <td>{item['status']}</td>
                <td style="font-size:11px;max-width:320px;word-break:break-word">
                    {item['explanation'][:200]}{'...' if len(item['explanation']) > 200 else ''}
                    {sar_link}
                </td>
            </tr>"""

    if not rows_html:
        rows_html = '<tr><td colspan="8" style="text-align:center;color:#7f8c8d;padding:20px">No alerts in this period.</td></tr>'

    last_scan = report["last_scan"]
    scan_info = (
        f"{last_scan['finished_at']} ({last_scan['mode']}, {last_scan['symbols_scanned']} symbols)"
        if last_scan["finished_at"]
        else "No scan recorded yet"
    )

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>Sentinel Manipulation Watch Report — {period['from_date']} to {period['to_date']}</title>
<style>
  body {{ font-family: Arial, sans-serif; font-size: 13px; margin: 24px; color: #2c3e50; }}
  h1 {{ font-size: 20px; border-bottom: 2px solid #2c3e50; padding-bottom: 8px; }}
  h2 {{ font-size: 15px; margin-top: 20px; }}
  table {{ border-collapse: collapse; width: 100%; margin-top: 10px; }}
  th {{ background: #34495e; color: white; padding: 8px; text-align: left; font-size: 12px; }}
  td {{ border: 1px solid #ddd; padding: 7px 9px; vertical-align: top; }}
  tr:nth-child(even) {{ background: #f8f9fa; }}
  .provenance {{ background: #fff3cd; border: 1px solid #ffc107; padding: 10px 14px;
                 margin: 16px 0; font-size: 12px; border-radius: 4px; }}
  .summary-box {{ display: flex; gap: 20px; margin: 12px 0; }}
  .stat {{ background: #ecf0f1; padding: 10px 16px; border-radius: 6px; text-align: center; }}
  .stat .num {{ font-size: 24px; font-weight: bold; }}
  .stat .lbl {{ font-size: 11px; color: #7f8c8d; }}
</style>
</head>
<body>
<h1>🛡️ Sentinel — Manipulation Watch Report</h1>
<p>Period: <strong>{period['from_date']}</strong> to <strong>{period['to_date']}</strong>
   ({period['days']} days) &nbsp;|&nbsp; Generated: {report['generated_at']}<br>
   Last scan: {scan_info}</p>

<div class="summary-box">
  <div class="stat"><div class="num">{summary['total_alerts']}</div><div class="lbl">Total Alerts</div></div>
  <div class="stat" style="background:#fde8e8"><div class="num" style="color:#c0392b">{summary['high_critical']}</div><div class="lbl">High / Critical</div></div>
  <div class="stat"><div class="num">{summary['medium']}</div><div class="lbl">Medium</div></div>
  <div class="stat"><div class="num">{summary['low']}</div><div class="lbl">Low</div></div>
  <div class="stat" style="background:#e8f4e8"><div class="num" style="color:#27ae60">{summary['new_this_period']}</div><div class="lbl">New This Period</div></div>
  <div class="stat"><div class="num">{summary['existing_redetected']}</div><div class="lbl">Re-detected</div></div>
</div>

<div class="provenance">
  ⚠️ <strong>Data Provenance:</strong> {report['data_provenance']}<br>
  <strong>Limitations:</strong> {report['limitations']}
</div>

<h2>Alert Detail</h2>
<table>
  <thead>
    <tr>
      <th>Date</th><th>Instrument</th><th>Pattern</th><th>Severity</th>
      <th>Score</th><th>Seen</th><th>Status</th><th>Explanation / Action</th>
    </tr>
  </thead>
  <tbody>
    {rows_html}
  </tbody>
</table>

<p style="margin-top:24px;font-size:11px;color:#7f8c8d">
  {report['analyst_guidance']}<br>
  Sentinel {os.environ.get('SENTINEL_VERSION', 'dev')} &nbsp;|&nbsp;
  SENTINEL_MODE={os.environ.get('SENTINEL_MODE', 'live')}
</p>
</body>
</html>"""

    return HTMLResponse(content=html, status_code=200)

