"""Google OAuth + API client factory.

One OAuth "desktop app" credential authorizes BOTH Sheets and Gmail. Scopes:

    spreadsheets   - read/write the tracker
    gmail.compose  - create + send drafts (initial outreach and follow-ups)
    gmail.modify   - read threads (reply detection) + apply status labels

First run opens a browser once to consent; the resulting token is cached in
~/.abaka/token.json and refreshed automatically thereafter, so later runs are
unattended. For the abaka.ai Workspace, set the OAuth consent screen to
"Internal" to avoid the 7-day refresh-token expiry that applies to external
apps in "Testing" status.
"""
from __future__ import annotations

import sys

import config

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/gmail.compose",
    "https://www.googleapis.com/auth/gmail.modify",
]


def _load_libs():
    try:
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        from google_auth_oauthlib.flow import InstalledAppFlow
        from googleapiclient.discovery import build
    except ImportError as exc:  # pragma: no cover - surfaced to user
        raise SystemExit(
            "error: missing Google client libraries. Install them with:\n"
            "  python3 -m pip install -r requirements.txt\n"
            f"(import failed: {exc})"
        )
    return Request, Credentials, InstalledAppFlow, build


def get_credentials(*, interactive: bool = True):
    """Return valid OAuth credentials, refreshing or running the flow as needed.

    interactive=False raises instead of opening a browser (used by any headless
    entry point) so a missing/expired token fails loudly rather than hanging.
    """
    Request, Credentials, InstalledAppFlow, _ = _load_libs()
    config.ensure_dir()
    creds = None
    if config.TOKEN_PATH.exists():
        creds = Credentials.from_authorized_user_file(str(config.TOKEN_PATH), SCOPES)

    if creds and creds.valid:
        return creds

    if creds and creds.expired and creds.refresh_token:
        creds.refresh(Request())
        config.TOKEN_PATH.write_text(creds.to_json())
        return creds

    if not interactive:
        raise SystemExit(
            "error: no valid cached token and interactive login disabled. "
            "Run  python3 tools/auth.py --login  first."
        )

    if not config.CREDENTIALS_PATH.exists():
        raise SystemExit(
            f"error: {config.CREDENTIALS_PATH} not found.\n"
            "Download an OAuth 'Desktop app' client secret from Google Cloud "
            "Console (APIs & Services > Credentials) and save it there. "
            "See the README / run /setup for the full walkthrough."
        )

    flow = InstalledAppFlow.from_client_secrets_file(str(config.CREDENTIALS_PATH), SCOPES)
    creds = flow.run_local_server(port=0)
    config.TOKEN_PATH.write_text(creds.to_json())
    return creds


def gspread_client(*, interactive: bool = True):
    """Authorized gspread client (row-level Sheets read/write)."""
    import gspread  # imported lazily so `--help` works without deps

    return gspread.authorize(get_credentials(interactive=interactive))


def sheets_service(*, interactive: bool = True):
    """Sheets API v4 service (used for tab creation + formatting/validation)."""
    _, _, _, build = _load_libs()
    return build("sheets", "v4", credentials=get_credentials(interactive=interactive))


def gmail_service(*, interactive: bool = True):
    """Gmail API v1 service (drafts, send, threads)."""
    _, _, _, build = _load_libs()
    return build("gmail", "v1", credentials=get_credentials(interactive=interactive))


# --------------------------------------------------------------------------- #
# CLI:  python3 tools/auth.py --login   (force the consent flow)
#       python3 tools/auth.py --check   (verify token + report the account)
# --------------------------------------------------------------------------- #
def _main(argv: list[str]) -> int:
    if "--login" in argv:
        get_credentials(interactive=True)
        print(f"ok: token cached at {config.TOKEN_PATH}")
        return 0
    # default / --check
    creds = get_credentials(interactive=True)
    svc = gmail_service()
    profile = svc.users().getProfile(userId="me").execute()
    print(f"ok: authenticated as {profile.get('emailAddress')}")
    print(f"    scopes: {', '.join(getattr(creds, 'scopes', SCOPES) or SCOPES)}")
    return 0


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))
