#!/usr/bin/env python3
"""Ruft alle RSS-Quellen aus sources.yaml ab und schreibt die Meldungen im Zeitfenster nach out/items.json.

Zeitfenster: von window_start_hour am vorherigen Werktag bis jetzt (montags ab Freitag).

Aufruf:
  python scripts/fetch_feeds.py            # schreibt out/items.json
  python scripts/fetch_feeds.py --report   # zusätzlich Übersicht pro Quelle
  python scripts/fetch_feeds.py --since 2026-11-19T06:00   # Fenster manuell setzen
"""
from __future__ import annotations
import argparse
import html
import json
import re
import sys
import warnings
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse
from zoneinfo import ZoneInfo

warnings.filterwarnings("ignore")

import feedparser  # noqa: E402
import requests  # noqa: E402
import yaml  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
UA = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124 Safari/537.36"
}
TRACKING_PARAMS = re.compile(r"^(utm_|mc_|fbclid|gclid|ref$|ref_src|cmpid|wt_mc|ito)", re.I)
TEASER_LEN = 500


def window_start(now: datetime, start_hour: int) -> datetime:
    """Vorheriger Werktag, start_hour Uhr. Montag → Freitag; Wochenende → Freitag."""
    days_back = {0: 3, 6: 2, 5: 1}.get(now.weekday(), 1)
    start = (now - timedelta(days=days_back)).replace(hour=start_hour, minute=0, second=0, microsecond=0)
    return start


def clean_url(url: str) -> str:
    p = urlparse(url)
    q = [(k, v) for k, v in parse_qsl(p.query, keep_blank_values=True) if not TRACKING_PARAMS.match(k)]
    return urlunparse(p._replace(query=urlencode(q), fragment=""))


def strip_html(text: str) -> str:
    text = re.sub(r"<[^>]+>", " ", text or "")
    text = html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def entry_date(e) -> datetime | None:
    for key in ("published_parsed", "updated_parsed", "created_parsed"):
        t = e.get(key)
        if t:
            return datetime(*t[:6], tzinfo=timezone.utc)
    return None


def fetch(src: dict) -> dict:
    result = {"name": src["name"], "url": src["url"], "ok": False, "error": None, "entries": []}
    try:
        r = requests.get(src["url"], headers=UA, timeout=20)
        r.raise_for_status()
        feed = feedparser.parse(r.content)
        if not feed.entries:
            result["error"] = "Feed leer oder nicht lesbar"
            return result
        result["ok"] = True
        result["entries"] = feed.entries
    except Exception as exc:  # noqa: BLE001
        result["error"] = f"{type(exc).__name__}: {str(exc)[:120]}"
    return result


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--report", action="store_true")
    ap.add_argument("--since", help="Fensterbeginn, ISO-Format in lokaler Zeit")
    ap.add_argument("--out", default=str(ROOT / "out" / "items.json"))
    args = ap.parse_args()

    config = yaml.safe_load((ROOT / "config.yaml").read_text())
    sources = yaml.safe_load((ROOT / "sources.yaml").read_text())["sources"]
    tz = ZoneInfo(config.get("timezone", "Europe/Berlin"))
    now = datetime.now(tz)
    start = (
        datetime.fromisoformat(args.since).replace(tzinfo=tz)
        if args.since
        else window_start(now, int(config.get("window_start_hour", 6)))
    )

    rss = [s for s in sources if s.get("type") == "rss"]
    pages = [s for s in sources if s.get("type") == "page"]

    with ThreadPoolExecutor(max_workers=12) as ex:
        results = list(ex.map(fetch, rss))

    items, seen, report = [], set(), []
    for src, res in zip(rss, results):
        kept = undated = 0
        for e in res["entries"]:
            link = e.get("link")
            if not link:
                continue
            d = entry_date(e)
            if d is None:
                undated += 1
                continue
            if d < start or d > now + timedelta(hours=1):
                continue
            url = clean_url(link)
            if url in seen:
                continue
            seen.add(url)
            kept += 1
            items.append(
                {
                    "source": src["name"],
                    "topic": src.get("topic"),
                    "lang": src.get("lang"),
                    "paywall_hint": bool(src.get("paywall")),
                    "title": strip_html(e.get("title", "")),
                    "url": url,
                    "published": d.astimezone(tz).isoformat(timespec="minutes"),
                    "teaser": strip_html(e.get("summary", ""))[:TEASER_LEN],
                    "categories": [t.get("term") for t in e.get("tags", []) if t.get("term")][:6],
                }
            )
        report.append(
            {"source": src["name"], "ok": res["ok"], "error": res["error"], "in_window": kept,
             "total": len(res["entries"]), "undated": undated}
        )

    items.sort(key=lambda x: x["published"], reverse=True)
    out = {
        "generated": now.isoformat(timespec="minutes"),
        "window": {"start": start.isoformat(timespec="minutes"), "end": now.isoformat(timespec="minutes")},
        "counts": {"items": len(items), "rss_sources": len(rss),
                   "rss_failed": sum(1 for r in report if not r["ok"])},
        "failed_sources": [{"source": r["source"], "error": r["error"]} for r in report if not r["ok"]],
        "page_sources": [{"name": p["name"], "url": p["url"], "topic": p.get("topic"),
                          "paywall_hint": bool(p.get("paywall"))} for p in pages],
        "source_report": report,
        "items": items,
    }
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(out, ensure_ascii=False, indent=1))

    print(f"Zeitfenster: {start:%a %d.%m. %H:%M} – {now:%a %d.%m. %H:%M}")
    print(f"{len(items)} Meldungen aus {len(rss) - out['counts']['rss_failed']}/{len(rss)} Feeds → {args.out}")
    if out["failed_sources"]:
        print("Nicht erreichbar: " + ", ".join(f["source"] for f in out["failed_sources"]))
    if args.report:
        for r in sorted(report, key=lambda r: -r["in_window"]):
            status = "OK  " if r["ok"] else "FAIL"
            extra = f"  ({r['undated']} ohne Datum)" if r["undated"] else ""
            print(f"  {status} {r['source']:28} {r['in_window']:3} im Fenster / {r['total']:4} gesamt{extra}"
                  + (f"  {r['error']}" if r["error"] else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
