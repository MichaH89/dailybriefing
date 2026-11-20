#!/usr/bin/env python3
"""Prüft out/briefing.md vor dem Versand.

- Jeder Link muss in out/items.json stehen oder in out/verified_urls.txt (vom Agenten im Lauf geöffnete
  bzw. in Suchergebnissen gesehene URLs, eine pro Zeile).
- Lesezeit (230 Wörter/Minute) gegen das Limit aus config.yaml.
- Wörter aus redaktion/streichliste.md (Abschnitt Floskeln) dürfen nicht vorkommen.
Exit-Code 1 bei Fehlern.
"""
import json
import re
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent


def main() -> int:
    md = (ROOT / "out" / "briefing.md").read_text()
    config = yaml.safe_load((ROOT / "config.yaml").read_text())
    max_words = int(config.get("max_words", 1250))

    known = {x["url"] for x in json.loads((ROOT / "out" / "items.json").read_text())["items"]}
    verified = ROOT / "out" / "verified_urls.txt"
    if verified.exists():
        known |= {l.strip() for l in verified.read_text().splitlines() if l.strip()}

    errors = []
    urls = re.findall(r"\]\((https?://[^)\s]+)\)", md)
    unknown = sorted({u for u in urls if u not in known})
    for u in unknown:
        errors.append(f"Link nicht belegt: {u}")

    words = len(md.split())
    if words > max_words * 1.15:
        errors.append(f"Zu lang: {words} Wörter (Ziel {max_words}, ca. {words / 230:.1f} Min)")

    stop = (ROOT / "redaktion" / "streichliste.md").read_text()
    section = stop.split("## Floskeln und Füllwörter")[1].split("##")[0]
    terms = [t.strip() for line in section.splitlines() if line.startswith("- ")
             for t in line[2:].split(",") if len(t.strip()) > 3 and "(" not in t]
    body = md.lower()
    for t in terms:
        if re.search(rf"\b{re.escape(t.lower())}\b", body):
            errors.append(f"Streichliste: „{t}“")

    print(f"{len(set(urls))} Links, {words} Wörter (ca. {words / 230:.1f} Min)")
    for e in errors:
        print("FEHLER:", e)
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
