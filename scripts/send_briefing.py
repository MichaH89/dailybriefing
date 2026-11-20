#!/usr/bin/env python3
"""Wandelt out/briefing.md in eine HTML-Mail um und verschickt sie.

Versand per SMTP (Standard, mail.method: smtp) oder Gmail API (mail.method: gmail_api).
SMTP-Secrets: SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASSWORD
Gmail-API-Secrets: GMAIL_CLIENT_ID, GMAIL_CLIENT_SECRET, GMAIL_REFRESH_TOKEN
Lokal werden die Werte aus .env gelesen.
Empfänger aus config.yaml (test_mode entscheidet zwischen recipients_test und recipients_live).

Aufruf:
  python scripts/send_briefing.py                 # versenden
  python scripts/send_briefing.py --preview       # nur out/briefing.html schreiben, nichts senden
  python scripts/send_briefing.py --file X.md
"""
from __future__ import annotations

import argparse
import base64
import os
import re
import smtplib
import ssl
import sys
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formataddr, formatdate, make_msgid
from pathlib import Path
from zoneinfo import ZoneInfo

import markdown
import yaml

ROOT = Path(__file__).resolve().parent.parent

CSS = {
    "body": "margin:0;padding:0;background:#f4f4f2;",
    "wrap": "max-width:680px;margin:0 auto;padding:24px 20px;background:#ffffff;"
            "font-family:-apple-system,Segoe UI,Helvetica,Arial,sans-serif;font-size:15px;"
            "line-height:1.55;color:#1d1d1f;",
    "h1": "font-size:22px;margin:0 0 4px;",
    "h2": "font-size:17px;margin:28px 0 8px;padding-top:14px;border-top:1px solid #e5e5e5;",
    "h3": "font-size:15px;margin:18px 0 6px;",
    "p": "margin:0 0 10px;",
    "ul": "margin:0 0 12px;padding-left:20px;",
    "li": "margin:0 0 5px;",
    "a": "color:#0b57d0;text-decoration:none;",
    "strong": "font-weight:600;",
    "meta": "font-size:13px;color:#6e6e73;margin:0 0 16px;",
}


def inline_css(html: str) -> str:
    for tag in ("h1", "h2", "h3", "p", "ul", "ol", "li", "a", "strong"):
        style = CSS.get(tag, CSS.get("ul") if tag == "ol" else "")
        html = re.sub(rf"<{tag}(\s|>)", rf'<{tag} style="{style}"\1', html)
    return html


def render(md_text: str, title: str) -> str:
    body = markdown.markdown(md_text, extensions=["extra", "sane_lists"])
    body = inline_css(body)
    return (
        f'<!doctype html><html><head><meta charset="utf-8"><title>{title}</title></head>'
        f'<body style="{CSS["body"]}"><div style="{CSS["wrap"]}">'
        f'<h1 style="{CSS["h1"]}">{title}</h1>{body}</div></body></html>'
    )


def load_dotenv() -> None:
    """Lokaler Betrieb: Werte aus .env übernehmen, falls nicht schon gesetzt."""
    env_file = ROOT / ".env"
    if not env_file.exists():
        return
    for line in env_file.read_text().splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip())


def gmail_service():
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build

    load_dotenv()

    missing = [k for k in ("GMAIL_CLIENT_ID", "GMAIL_CLIENT_SECRET", "GMAIL_REFRESH_TOKEN") if not os.environ.get(k)]
    if missing:
        raise SystemExit(f"Fehlende Umgebungsvariablen: {', '.join(missing)}")
    creds = Credentials(
        token=None,
        refresh_token=os.environ["GMAIL_REFRESH_TOKEN"],
        client_id=os.environ["GMAIL_CLIENT_ID"],
        client_secret=os.environ["GMAIL_CLIENT_SECRET"],
        token_uri="https://oauth2.googleapis.com/token",
        scopes=["https://www.googleapis.com/auth/gmail.send"],
    )
    return build("gmail", "v1", credentials=creds, cache_discovery=False)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", default=str(ROOT / "out" / "briefing.md"))
    ap.add_argument("--preview", action="store_true")
    args = ap.parse_args()

    config = yaml.safe_load((ROOT / "config.yaml").read_text())
    mail = config["mail"]
    tz = ZoneInfo(config.get("timezone", "Europe/Berlin"))
    today = datetime.now(tz)
    weekdays = ["Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag"]
    title = f"{mail.get('subject_prefix', 'Daily Briefing')} · {weekdays[today.weekday()]}, {today:%d.%m.%Y}"

    md_text = Path(args.file).read_text()
    html = render(md_text, title)
    preview_path = ROOT / "out" / "briefing.html"
    preview_path.write_text(html)
    if args.preview:
        print(f"Vorschau: {preview_path}")
        return 0

    recipients = mail["recipients_test"] if mail.get("test_mode", True) else mail["recipients_live"]
    if any("TODO" in r for r in recipients):
        raise SystemExit("Empfänger in config.yaml noch nicht gesetzt.")

    load_dotenv()
    method = mail.get("method", "smtp")
    sender = os.environ.get("SMTP_USER") if method == "smtp" else None
    if mail.get("sender_email") and "TODO" not in mail["sender_email"]:
        sender = mail["sender_email"]

    msg = MIMEMultipart("alternative")
    msg["Subject"] = ("[TEST] " if mail.get("test_mode", True) else "") + title
    msg["To"] = ", ".join(recipients)
    if sender:
        msg["From"] = formataddr((mail.get("sender_name", "Daily Briefing"), sender))
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = make_msgid(domain=(sender or "localhost").split("@")[-1])
    msg.attach(MIMEText(md_text, "plain", "utf-8"))
    msg.attach(MIMEText(html, "html", "utf-8"))

    if method == "smtp":
        send_smtp(msg, recipients)
        print(f"Versendet per SMTP an {', '.join(recipients)}")
    else:
        raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
        sent = gmail_service().users().messages().send(userId="me", body={"raw": raw}).execute()
        print(f"Versendet per Gmail API an {', '.join(recipients)} (id {sent.get('id')})")
    return 0


def send_smtp(msg, recipients: list) -> None:
    """Versand per SMTP. Werte aus Umgebung/.env: SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASSWORD."""
    missing = [k for k in ("SMTP_HOST", "SMTP_USER", "SMTP_PASSWORD") if not os.environ.get(k)]
    if missing:
        raise SystemExit(f"Fehlende Umgebungsvariablen: {', '.join(missing)}")
    host = os.environ["SMTP_HOST"]
    port = int(os.environ.get("SMTP_PORT", "587"))
    context = ssl.create_default_context()
    if port == 465:
        server = smtplib.SMTP_SSL(host, port, context=context, timeout=30)
    else:
        server = smtplib.SMTP(host, port, timeout=30)
        server.starttls(context=context)
    try:
        server.login(os.environ["SMTP_USER"], os.environ["SMTP_PASSWORD"].replace(" ", ""))
        server.send_message(msg, to_addrs=recipients)
    finally:
        server.quit()


if __name__ == "__main__":
    sys.exit(main())
