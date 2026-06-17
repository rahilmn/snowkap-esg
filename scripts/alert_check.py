"""C#7 — production alert check (Slack on threshold breach).

Polls the app's `/metrics` + `/health/ready` (both unauthenticated) and posts
to a Slack webhook when anything breaches. Lots of good signal already exists
in /metrics — this is the missing piece that actually NOTIFIES on it.

Fires on:
  * Opus downgrade            snowkap_llm_opus_active == 0  (silent gpt-4.1 fallback)
  * Cron failed               snowkap_scheduler_last_status == 0
  * Cron missed               snowkap_scheduler_last_run_seconds older than 8d
  * NewsAPI budget low        snowkap_newsapi_budget{pool="remaining"} < 200
  * Readiness down            GET /health/ready != 200 (DB loss)

Run on a cron — Railway cron, a GitHub Action (schedule:), or the in-process
APScheduler. No-op (prints) when SLACK_WEBHOOK_URL is unset, so it's safe to
wire up before you have a webhook.

Usage:
    SNOWKAP_BASE_URL=https://snowkap-esg-production.up.railway.app \\
    SLACK_WEBHOOK_URL=https://hooks.slack.com/services/... \\
        python scripts/alert_check.py

Exit code 1 when any alert fired (so a CI/cron run is marked failed too).
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
import urllib.error
import urllib.request

_BASE = os.environ.get("SNOWKAP_BASE_URL", "http://localhost:8000").rstrip("/")
_SLACK = os.environ.get("SLACK_WEBHOOK_URL", "").strip()
_SCHED_MAX_AGE_S = float(os.environ.get("ALERT_SCHED_MAX_AGE_HOURS", "192")) * 3600  # 8d
_BUDGET_MIN = int(os.environ.get("ALERT_NEWSAPI_MIN_REMAINING", "200"))

_LINE_RE = re.compile(r"^([a-zA-Z_:][\w:]*)(\{[^}]*\})?\s+([-\d.eE+]+)$")
_LABEL_RE = re.compile(r'(\w+)="([^"]*)"')


def _get(path: str, timeout: int = 20) -> tuple[int, str]:
    req = urllib.request.Request(_BASE + path, headers={"User-Agent": "snowkap-alert"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")


def _parse_metrics(text: str) -> list[tuple[str, dict, float]]:
    out: list[tuple[str, dict, float]] = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        m = _LINE_RE.match(line)
        if not m:
            continue
        labels = dict(_LABEL_RE.findall(m.group(2) or ""))
        try:
            out.append((m.group(1), labels, float(m.group(3))))
        except ValueError:
            pass
    return out


def check() -> list[str]:
    alerts: list[str] = []
    try:
        st, body = _get("/health/ready")
        if st != 200:
            alerts.append(f":red_circle: readiness {st} — {body[:120]}")
    except Exception as e:  # noqa: BLE001
        alerts.append(f":red_circle: /health/ready unreachable: {e}")

    try:
        st, mtext = _get("/metrics")
        metrics = _parse_metrics(mtext) if st == 200 else []
        if st != 200:
            alerts.append(f":red_circle: /metrics returned {st}")
    except Exception as e:  # noqa: BLE001
        alerts.append(f":red_circle: /metrics unreachable: {e}")
        metrics = []

    now = time.time()
    for name, labels, val in metrics:
        if name == "snowkap_llm_opus_active" and val == 0:
            alerts.append(
                f":warning: Opus inactive — reasoning_heavy on "
                f"{labels.get('model', '?')} (gpt-4.1 fallback)"
            )
        elif name == "snowkap_scheduler_last_status" and val == 0:
            alerts.append(f":warning: cron `{labels.get('job', '?')}` last run FAILED")
        elif name == "snowkap_scheduler_last_run_seconds" and val > 0 and (now - val) > _SCHED_MAX_AGE_S:
            alerts.append(
                f":warning: cron `{labels.get('job', '?')}` last ran "
                f"{(now - val) / 86400:.1f}d ago (missed run?)"
            )
        elif name == "snowkap_newsapi_budget" and labels.get("pool") == "remaining" and val < _BUDGET_MIN:
            alerts.append(f":warning: NewsAPI budget low: {int(val)} tokens remaining (< {_BUDGET_MIN})")
    return alerts


def post_slack(alerts: list[str]) -> None:
    text = "*Snowkap ESG — production alerts*\n" + "\n".join(alerts)
    if not _SLACK:
        print("[alert_check] SLACK_WEBHOOK_URL unset — would post:\n" + text)
        return
    data = json.dumps({"text": text}).encode()
    req = urllib.request.Request(
        _SLACK, data=data, headers={"Content-Type": "application/json"}
    )
    urllib.request.urlopen(req, timeout=15).read()
    print(f"[alert_check] posted {len(alerts)} alert(s) to Slack")


def main() -> None:
    alerts = check()
    if alerts:
        post_slack(alerts)
        sys.exit(1)
    print("[alert_check] all green")


if __name__ == "__main__":
    main()
