"""Gmail integration — read, send, and search emails via Gmail API.

First-time setup: run  python setup_gmail.py  from the project root.
After that, token.json is saved locally and auth is automatic forever.
"""
from __future__ import annotations
import base64
import email.mime.multipart
import email.mime.text
import json
from datetime import datetime

import settings

_CREDS_PATH    = settings.BASE_DIR / "data" / "gmail_credentials.json"
_TOKEN_PATH    = settings.BASE_DIR / "data" / "gmail_token.json"
_CONTACTS_PATH = settings.BASE_DIR / "data" / "email_contacts.json"

_SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/gmail.modify",
]


class GmailAuthError(Exception):
    """Auth needs user action — carries a speakable message."""


_NET_TIMEOUT_S = 15   # hard cap on any Gmail network call — voice loop must never hang


def _refresh_with_timeout(creds) -> None:
    """Refresh OAuth token in a worker thread with a hard timeout.

    google-auth's refresh uses `requests` with NO timeout — on a bad network it
    blocks forever, which froze the whole voice loop. A daemon thread + join
    guarantees we give up after _NET_TIMEOUT_S no matter what.
    """
    import threading
    from google.auth.transport.requests import Request

    errors: list[Exception] = []

    def _do():
        try:
            creds.refresh(Request())
        except Exception as e:
            errors.append(e)

    t = threading.Thread(target=_do, daemon=True)
    t.start()
    t.join(_NET_TIMEOUT_S)
    if t.is_alive():
        raise TimeoutError(f"token refresh timed out after {_NET_TIMEOUT_S}s")
    if errors:
        raise errors[0]


def _get_service():
    """Return authenticated Gmail service. Refreshes token automatically.

    IMPORTANT: this runs inside the voice loop, so it must NEVER block.
    The interactive OAuth browser flow (run_local_server) lives only in
    setup_gmail.py — here we refresh silently or fail fast with a clear message.
    """
    import httplib2
    import google_auth_httplib2
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build

    if not _CREDS_PATH.exists():
        raise GmailAuthError(
            "Gmail isn't set up yet — the credentials file is missing. "
            "Run setup underscore gmail dot py once from the project folder."
        )
    if not _TOKEN_PATH.exists():
        raise GmailAuthError(
            "Gmail isn't connected yet. Run setup underscore gmail dot py once "
            "to log in, then ask me again."
        )

    creds = Credentials.from_authorized_user_file(str(_TOKEN_PATH), _SCOPES)
    if not creds.valid:
        if creds.expired and creds.refresh_token:
            try:
                _refresh_with_timeout(creds)
                _TOKEN_PATH.write_text(creds.to_json())
            except Exception as e:
                print(f"[gmail] token refresh failed: {e}")
                raise GmailAuthError(
                    "My Gmail login has expired and I couldn't renew it right now. "
                    "Check the internet connection, or run setup underscore gmail "
                    "dot py once to log back in."
                )
        else:
            raise GmailAuthError(
                "My Gmail login has expired. Run setup underscore gmail dot py "
                "once to log back in."
            )

    # Explicit-timeout transport — every API call fails fast instead of hanging
    authed_http = google_auth_httplib2.AuthorizedHttp(
        creds, http=httplib2.Http(timeout=_NET_TIMEOUT_S))
    return build("gmail", "v1", http=authed_http, cache_discovery=False)


def _header(headers: list[dict], name: str) -> str:
    for h in headers:
        if h["name"].lower() == name.lower():
            return h["value"]
    return ""


def _sender_name(raw: str) -> str:
    """Extract display name from 'Name <email>' format."""
    if "<" in raw:
        return raw.split("<")[0].strip().strip('"\'')
    return raw.strip()


def _plain_body(msg_data: dict) -> str:
    """Extract plain-text body from a Gmail message payload."""
    payload = msg_data.get("payload", {})

    def _decode(data: str) -> str:
        return base64.urlsafe_b64decode(data + "==").decode("utf-8", errors="ignore")

    # Single-part plain text
    if payload.get("mimeType") == "text/plain":
        data = payload.get("body", {}).get("data", "")
        if data:
            return _decode(data)

    # Walk parts recursively
    def _walk(parts):
        for part in parts:
            if part.get("mimeType") == "text/plain":
                data = part.get("body", {}).get("data", "")
                if data:
                    return _decode(data)
            sub = part.get("parts", [])
            if sub:
                found = _walk(sub)
                if found:
                    return found
        return ""

    return _walk(payload.get("parts", []))


# ── contact tracking — remember who Ashish emails with and what about ─────────

def _load_contacts() -> dict:
    try:
        return json.loads(_CONTACTS_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _sender_email(raw: str) -> str:
    if "<" in raw and ">" in raw:
        return raw.split("<")[1].split(">")[0].strip().lower()
    return raw.strip().lower()


def _track_contact(raw_from: str, subject: str, snippet: str) -> None:
    """Record who this email is from and what it's about (local file only)."""
    try:
        name  = _sender_name(raw_from)
        addr  = _sender_email(raw_from)
        if not addr:
            return
        contacts = _load_contacts()
        entry = contacts.get(addr, {"name": name, "email": addr, "count": 0, "threads": []})
        entry["name"]  = name or entry.get("name", "")
        entry["count"] = int(entry.get("count", 0)) + 1
        entry["last_seen"] = datetime.now().strftime("%Y-%m-%d")
        threads = entry.get("threads", [])
        topic = f"{subject}: {snippet[:80]}".strip(": ")
        if topic and topic not in threads:
            threads.append(topic)
        entry["threads"] = threads[-5:]   # keep the 5 most recent topics per person
        contacts[addr] = entry
        # Keep the file bounded to the 40 most recently seen contacts
        if len(contacts) > 40:
            ordered = sorted(contacts.items(),
                             key=lambda kv: kv[1].get("last_seen", ""), reverse=True)
            contacts = dict(ordered[:40])
        _CONTACTS_PATH.write_text(
            json.dumps(contacts, indent=2, ensure_ascii=False), encoding="utf-8")
    except Exception as e:
        print(f"[gmail] contact tracking failed: {e}")


def _fetch_summaries(svc, messages: list[dict]) -> list[str]:
    """Fetch sender/subject/snippet lines for messages and track contacts."""
    lines = []
    for m in messages:
        full     = svc.users().messages().get(userId="me", id=m["id"], format="full").execute()
        headers  = full.get("payload", {}).get("headers", [])
        raw_from = _header(headers, "From")
        sender   = _sender_name(raw_from)
        subject  = _header(headers, "Subject") or "(no subject)"
        snippet  = full.get("snippet", "")[:160]
        _track_contact(raw_from, subject, snippet)
        lines.append(f"From {sender} — {subject}. {snippet}")
    return lines


# ── public action functions ────────────────────────────────────────────────────

def gmail_read(parameters=None, **_) -> str:
    """Read N most recent emails from inbox (default 5, max 10)."""
    params = parameters or {}
    count  = min(int(params.get("count", 5)), 10)

    try:
        svc = _get_service()
        res = svc.users().messages().list(
            userId="me", labelIds=["INBOX"], maxResults=count
        ).execute()

        messages = res.get("messages", [])
        if not messages:
            return "Your inbox is empty."

        lines = _fetch_summaries(svc, messages)
        return f"You have {len(lines)} recent email(s). " + " || ".join(lines)

    except (FileNotFoundError, GmailAuthError) as e:
        return str(e)
    except Exception as e:
        return f"Could not read emails: {e}"


def gmail_send(parameters=None, **_) -> str:
    """Send an email. Params: to, subject, body."""
    params  = parameters or {}
    to      = params.get("to", "").strip()
    subject = params.get("subject", "").strip() or "(no subject)"
    body    = params.get("body", "").strip()

    if not to:
        return "Who should I send the email to? I need an email address."
    if not body:
        return "What should the email say? I need a message body."

    try:
        svc = _get_service()
        msg = email.mime.multipart.MIMEMultipart()
        msg["To"]      = to
        msg["Subject"] = subject
        msg.attach(email.mime.text.MIMEText(body, "plain"))

        raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
        svc.users().messages().send(userId="me", body={"raw": raw}).execute()
        return f"Email sent to {to}."

    except (FileNotFoundError, GmailAuthError) as e:
        return str(e)
    except Exception as e:
        return f"Could not send email: {e}"


def gmail_search(parameters=None, **_) -> str:
    """Search emails by keyword, sender, or subject. Params: query, count."""
    params = parameters or {}
    query  = params.get("query", "").strip()
    count  = min(int(params.get("count", 5)), 10)

    if not query:
        return "What should I search for in your emails?"

    try:
        svc = _get_service()
        res = svc.users().messages().list(
            userId="me", q=query, maxResults=count
        ).execute()

        messages = res.get("messages", [])
        if not messages:
            return f"No emails found matching: {query}"

        lines = _fetch_summaries(svc, messages)
        return f"Found {len(lines)} email(s) for '{query}'. " + " || ".join(lines)

    except (FileNotFoundError, GmailAuthError) as e:
        return str(e)
    except Exception as e:
        return f"Could not search emails: {e}"


def gmail_contacts(parameters=None, **_) -> str:
    """Who Ashish has been emailing with recently, and what about."""
    contacts = _load_contacts()
    if not contacts:
        return ("I haven't tracked any email contacts yet — "
                "ask me to read your inbox first and I'll start keeping track.")
    ordered = sorted(contacts.values(),
                     key=lambda c: c.get("last_seen", ""), reverse=True)[:10]
    lines = []
    for c in ordered:
        topics = "; ".join(c.get("threads", [])[-2:])
        lines.append(f"{c.get('name') or c.get('email')} (last {c.get('last_seen','?')})"
                     + (f" — about: {topics}" if topics else ""))
    return "People you've emailed with recently. " + " || ".join(lines)
