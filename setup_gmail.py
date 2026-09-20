"""One-time Gmail OAuth setup. Run once — after this Curie handles auth automatically.

    python setup_gmail.py

Opens a browser for Google sign-in, saves token.json locally, then tests by
reading your 3 most recent emails to confirm everything works.
"""
import sys
from pathlib import Path

BASE_DIR   = Path(__file__).parent
CREDS_PATH = BASE_DIR / "data" / "gmail_credentials.json"
TOKEN_PATH = BASE_DIR / "data" / "gmail_token.json"

if not CREDS_PATH.exists():
    print(f"\n❌  credentials file not found at:\n    {CREDS_PATH}\n")
    print("Steps:")
    print("  1. Go to console.cloud.google.com")
    print("  2. APIs & Services → Credentials → Create OAuth client ID (Desktop app)")
    print("  3. Download JSON → rename to gmail_credentials.json")
    print(f"  4. Move it to:  {CREDS_PATH}\n")
    sys.exit(1)

print("Opening browser for Google sign-in...")

from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build

_SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/gmail.modify",
]

flow  = InstalledAppFlow.from_client_secrets_file(str(CREDS_PATH), _SCOPES)
creds = flow.run_local_server(port=0)
TOKEN_PATH.write_text(creds.to_json())
print(f"✅  Token saved to {TOKEN_PATH}")

# Quick smoke test
print("\nTesting connection — reading 3 most recent emails...\n")
svc      = build("gmail", "v1", credentials=creds)
results  = svc.users().messages().list(userId="me", labelIds=["INBOX"], maxResults=3).execute()
messages = results.get("messages", [])

if not messages:
    print("Inbox appears empty — but auth worked fine.")
else:
    for m in messages:
        full    = svc.users().messages().get(userId="me", id=m["id"], format="full").execute()
        headers = full.get("payload", {}).get("headers", [])
        sender  = next((h["value"] for h in headers if h["name"] == "From"), "Unknown")
        subject = next((h["value"] for h in headers if h["name"] == "Subject"), "(no subject)")
        print(f"  From: {sender}")
        print(f"  Subject: {subject}")
        print()

print("✅  Gmail is connected. Curie can now read and send your emails.")
print("    You never need to run this script again.\n")
