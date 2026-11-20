#!/usr/bin/env python3
"""Einmalig lokal ausführen: erzeugt den Gmail-Refresh-Token für den Versand.

Voraussetzung: OAuth-Client (Typ "Desktop-App") aus der Google Cloud Console,
heruntergeladen als credentials.json in den Projektordner. Gmail API aktiviert.

Ergebnis: gibt die drei Werte aus, die als Secrets in der Cloud-Umgebung hinterlegt werden:
  GMAIL_CLIENT_ID, GMAIL_CLIENT_SECRET, GMAIL_REFRESH_TOKEN
Der Token erlaubt nur das Senden von Mails (Scope gmail.send), kein Lesen.
"""
import json
import sys
from pathlib import Path

from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = ["https://www.googleapis.com/auth/gmail.send"]
ROOT = Path(__file__).resolve().parent.parent


def main() -> int:
    cred_file = ROOT / "credentials.json"
    if not cred_file.exists():
        print("credentials.json fehlt im Projektordner.")
        return 1
    flow = InstalledAppFlow.from_client_secrets_file(str(cred_file), SCOPES)
    creds = flow.run_local_server(port=0, prompt="consent", access_type="offline")
    client = json.loads(cred_file.read_text())
    client = client.get("installed") or client.get("web")
    env_file = ROOT / ".env"
    env_file.write_text(
        f"GMAIL_CLIENT_ID={client['client_id']}\n"
        f"GMAIL_CLIENT_SECRET={client['client_secret']}\n"
        f"GMAIL_REFRESH_TOKEN={creds.refresh_token}\n"
    )
    env_file.chmod(0o600)
    print(f"\nFertig. Zugangsdaten gespeichert in {env_file} (nicht im Git-Repo).")
    print("Für die Cloud-Routine die drei Werte aus dieser Datei als Umgebungsvariablen hinterlegen.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
