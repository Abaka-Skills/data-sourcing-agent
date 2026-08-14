"""Lark user OAuth — obtains a user_access_token, required for Lark Mail SEND.

Lark Mail's send API rejects the app/tenant token (99991663) — it must act as a
real user. This is the one-time browser authorization (mirrors Google auth.py),
using the manual copy-the-code flow so no local callback server is needed.

One-time console prerequisites (the app owner/admin does these once):
  1. Permissions → add the Mail **send-as-user** scope, then create a version and
     RELEASE it (scopes don't take effect until released).
  2. Security Settings → Redirect URLs → add EXACTLY:  http://localhost:8080/callback
     (change with --redirect / config `base.oauth_redirect` if you register another).

Flow:
  python3 tools/lark_auth.py authurl              # prints the URL to open in a browser
  # …authorize in the browser; it redirects to http://localhost:8080/callback?code=XXX
  # copy that whole redirected URL (or just the code) and:
  python3 tools/lark_auth.py exchange "<redirected URL or code>"
  python3 tools/lark_auth.py status               # show stored-token validity
Tokens are cached in ~/.abaka/lark_user.json (chmod 600) and auto-refreshed.
"""
from __future__ import annotations

import json
import os
import pathlib
import sys
import time
import urllib.parse

sys.path.insert(0, "tools")
import lark  # noqa: E402

USER_TOK_PATH = pathlib.Path.home() / ".abaka" / "lark_user.json"
DEFAULT_REDIRECT = "http://localhost:8080/callback"
# User scopes to request. Mail send scope id can vary by console edition; override
# with --scope if the consent screen doesn't list mail send.
DEFAULT_SCOPE = "mail:user_mailbox.message:send mail:user_mailbox.message:send_as"


def _redirect() -> str:
    return lark._c()[2].get("oauth_redirect", DEFAULT_REDIRECT)


def _base() -> str:
    return lark._c()[1]          # https://open.larksuite.com


def authorize_url(redirect: str | None = None, scope: str | None = None,
                  state: str = "abaka") -> str:
    app_id = lark._c()[0]["app_id"]
    q = {"app_id": app_id, "redirect_uri": redirect or _redirect(),
         "state": state, "response_type": "code"}
    if scope:
        q["scope"] = scope
    return f"{_base()}/open-apis/authen/v1/authorize?" + urllib.parse.urlencode(q)


def _code_from(arg: str) -> str:
    """Accept a raw code or a full redirected URL and pull the code out."""
    if "code=" in arg:
        return urllib.parse.parse_qs(urllib.parse.urlparse(arg).query).get("code", [""])[0]
    return arg.strip()


def exchange(code_or_url: str, redirect: str | None = None) -> dict:
    code = _code_from(code_or_url)
    if not code:
        sys.exit("exchange: no code found in the argument")
    r = lark.api("POST", "/open-apis/authen/v1/oidc/access_token",
                 {"grant_type": "authorization_code", "code": code})
    if r.get("code") not in (0, None):
        sys.exit(f"token exchange failed: {r.get('code')} {r.get('msg')}")
    d = r.get("data", {})
    _store(d)
    return d


def _store(d: dict) -> None:
    now = time.time()
    blob = {"access_token": d["access_token"], "refresh_token": d.get("refresh_token"),
            "expire_at": now + d.get("expires_in", 7200),
            "refresh_expire_at": now + d.get("refresh_expires_in", 30 * 86400)}
    USER_TOK_PATH.write_text(json.dumps(blob))
    os.chmod(USER_TOK_PATH, 0o600)


def _refresh(refresh_token: str) -> dict:
    r = lark.api("POST", "/open-apis/authen/v1/oidc/refresh_access_token",
                 {"grant_type": "refresh_token", "refresh_token": refresh_token})
    if r.get("code") not in (0, None):
        raise lark.NeedsUserAuth(f"refresh failed ({r.get('code')} {r.get('msg')}); re-run lark_auth.py authurl")
    d = r.get("data", {})
    _store(d)
    return d


def user_token() -> str:
    """Cached user_access_token; refresh 5 min before expiry. Raises NeedsUserAuth
    if never authorized or the refresh token has expired."""
    if not USER_TOK_PATH.exists():
        raise lark.NeedsUserAuth("no Lark user token — run: python3 tools/lark_auth.py authurl")
    t = json.loads(USER_TOK_PATH.read_text())
    if t.get("expire_at", 0) - 300 > time.time():
        return t["access_token"]
    if t.get("refresh_token") and t.get("refresh_expire_at", 0) > time.time():
        return _refresh(t["refresh_token"])["access_token"]
    raise lark.NeedsUserAuth("Lark user token expired — re-run: python3 tools/lark_auth.py authurl")


def _main(argv: list[str]) -> int:
    cmd = argv[0] if argv else "status"
    def opt(n, d=None):
        return argv[argv.index(n) + 1] if n in argv else d
    if cmd == "authurl":
        scope = opt("--scope", DEFAULT_SCOPE)
        url = authorize_url(redirect=opt("--redirect"), scope=(None if scope == "none" else scope))
        print("1) Open this URL in a browser and approve:\n")
        print(url)
        print(f"\n2) It redirects to {opt('--redirect', _redirect())}?code=…  — copy that URL, then run:")
        print('   python3 tools/lark_auth.py exchange "<pasted URL>"')
    elif cmd == "exchange":
        if len(argv) < 2:
            sys.exit('exchange needs the redirected URL or code, quoted')
        d = exchange(argv[1], redirect=opt("--redirect"))
        print("✓ stored Lark user token. scope:", d.get("scope", "(default)"),
              "| expires in", d.get("expires_in"), "s")
    elif cmd == "status":
        try:
            tok = user_token()
            t = json.loads(USER_TOK_PATH.read_text())
            print("✓ user token valid; expires", time.strftime("%Y-%m-%d %H:%M", time.localtime(t["expire_at"])),
                  "| token", tok[:10] + "…")
        except lark.NeedsUserAuth as e:
            print("✗", e)
    else:
        print(__doc__)
    return 0


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv[1:]))
