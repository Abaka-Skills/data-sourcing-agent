"""Configuration + identity store for the sourcing workflow.

All non-sheet state lives in ~/.abaka/ (outside the repo, so credentials never
land in version control):

    ~/.abaka/config.json       - sheet id + workflow settings
    ~/.abaka/profile.json      - the user's outreach identity
    ~/.abaka/credentials.json  - Google OAuth "desktop app" client secret (user-provided)
    ~/.abaka/token.json        - cached OAuth token (auto-created on first login)

A fresh machine with sheet access + config.json + profile.json + a valid
token.json resumes the whole workflow. The Google Sheet holds all workflow
state; these files hold only identity + settings.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ABAKA_DIR = Path.home() / ".abaka"
CONFIG_PATH = ABAKA_DIR / "config.json"
PROFILE_PATH = ABAKA_DIR / "profile.json"
CREDENTIALS_PATH = ABAKA_DIR / "credentials.json"
TOKEN_PATH = ABAKA_DIR / "token.json"

# Settings with sensible defaults. Anything in config.json overrides these.
# One tracker == one Google Sheet, tied to one domain. The active tracker is
# chosen by the ABAKA_TRACKER env var, else default_tracker.
CONFIG_DEFAULTS = {
    "trackers": {},          # {name: {sheet_id, sheet_url, domain}}
    "default_tracker": "medical",
    "followup_days": 3,      # suggest a follow-up after this many days of silence
    "followup_max": 3,       # soft cap; the skill warns but never hard-blocks
}

_INT_KEYS = {"followup_days", "followup_max"}


def ensure_dir() -> None:
    ABAKA_DIR.mkdir(parents=True, exist_ok=True)


def _read_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text())
    except json.JSONDecodeError as exc:  # pragma: no cover - surfaced to user
        raise SystemExit(f"error: {path} is not valid JSON ({exc})")


def _write_json(path: Path, data: dict) -> None:
    ensure_dir()
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")


def _coerce(data: dict) -> dict:
    for key in _INT_KEYS:
        if key in data and data[key] != "":
            try:
                data[key] = int(data[key])
            except (TypeError, ValueError):
                pass
    return data


def _migrate_trackers(cfg: dict) -> None:
    """Fold a legacy single-sheet config (top-level sheet_id) into trackers['medical']."""
    cfg.setdefault("trackers", {})
    cfg.setdefault("default_tracker", "medical")
    legacy = cfg.get("sheet_id")
    if legacy and "medical" not in cfg["trackers"]:
        cfg["trackers"]["medical"] = {
            "sheet_id": legacy, "sheet_url": cfg.get("sheet_url", ""), "domain": "medical"}


def load_config() -> dict:
    cfg = dict(CONFIG_DEFAULTS)
    cfg.update(_read_json(CONFIG_PATH))
    cfg = _coerce(cfg)
    _migrate_trackers(cfg)
    return cfg


def active_tracker_name() -> str:
    return os.environ.get("ABAKA_TRACKER") or load_config().get("default_tracker") or "medical"


def get_tracker(name: str | None = None) -> dict:
    cfg = load_config()
    name = name or active_tracker_name()
    t = cfg.get("trackers", {}).get(name)
    if not t or not t.get("sheet_id"):
        known = ", ".join(cfg.get("trackers", {})) or "(none)"
        raise SystemExit(
            f"error: no tracker named {name!r} is configured (known: {known}). "
            f'Create it with: python3 scripts/setup_sheet.py --create --tracker {name} "<Title>"'
        )
    return t


def get_domain(name: str | None = None) -> str:
    return get_tracker(name).get("domain", "medical")


def set_tracker(name: str, sheet_id: str, sheet_url: str, domain: str) -> dict:
    cfg = _read_json(CONFIG_PATH)
    _migrate_trackers(cfg)
    cfg.pop("sheet_id", None)   # drop legacy top-level keys now that trackers own them
    cfg.pop("sheet_url", None)
    cfg.setdefault("trackers", {})[name] = {
        "sheet_id": sheet_id, "sheet_url": sheet_url, "domain": domain}
    cfg.setdefault("default_tracker", "medical")
    _write_json(CONFIG_PATH, _coerce(cfg))
    return load_config()


def save_config(updates: dict) -> dict:
    cfg = _read_json(CONFIG_PATH)
    cfg.update(updates)
    _write_json(CONFIG_PATH, _coerce(cfg))
    return load_config()


def load_profile() -> dict:
    return _read_json(PROFILE_PATH)


def save_profile(updates: dict) -> dict:
    prof = _read_json(PROFILE_PATH)
    prof.update(updates)
    _write_json(PROFILE_PATH, prof)
    return load_profile()


def get_sheet_id(name: str | None = None) -> str:
    return get_tracker(name)["sheet_id"]


def require_profile() -> dict:
    prof = load_profile()
    missing = [k for k in ("name", "role", "company", "email") if not prof.get(k)]
    if missing:
        raise SystemExit(
            "error: your outreach identity is incomplete (missing: "
            f"{', '.join(missing)}). Run /setup or "
            'python3 tools/config.py set-profile name="..." role="..." '
            'company="..." email="..."'
        )
    return prof


# --------------------------------------------------------------------------- #
# CLI:  python3 tools/config.py show
#       python3 tools/config.py set-profile name="Jane" role="..." email="..."
#       python3 tools/config.py set-config sheet_id="..." followup_days=3
# --------------------------------------------------------------------------- #
def _parse_kv(pairs: list[str]) -> dict:
    out: dict = {}
    for pair in pairs:
        if "=" not in pair:
            raise SystemExit(f'error: expected key=value, got "{pair}"')
        key, value = pair.split("=", 1)
        out[key.strip()] = value
    return out


def _main(argv: list[str]) -> int:
    if not argv or argv[0] in {"-h", "--help"}:
        print(__doc__)
        print("commands: show | trackers | set-profile k=v... | set-config k=v... | "
              "set-tracker name= sheet_id= domain= [sheet_url=]")
        return 0
    cmd, rest = argv[0], argv[1:]
    if cmd == "show":
        print(json.dumps({"config": load_config(), "profile": load_profile()}, indent=2))
    elif cmd == "trackers":
        cfg = load_config()
        print(json.dumps({"active": active_tracker_name(),
                          "default": cfg.get("default_tracker"),
                          "trackers": cfg.get("trackers", {})}, indent=2))
    elif cmd == "set-profile":
        print(json.dumps(save_profile(_parse_kv(rest)), indent=2))
    elif cmd == "set-config":
        print(json.dumps(save_config(_parse_kv(rest)), indent=2))
    elif cmd == "set-tracker":
        kv = _parse_kv(rest)
        missing = [k for k in ("name", "sheet_id", "domain") if not kv.get(k)]
        if missing:
            raise SystemExit(f"error: set-tracker needs name=, sheet_id=, domain= (missing: {missing})")
        url = kv.get("sheet_url") or f"https://docs.google.com/spreadsheets/d/{kv['sheet_id']}"
        set_tracker(kv["name"], kv["sheet_id"], url, kv["domain"])
        print(json.dumps(load_config().get("trackers", {}), indent=2))
    else:
        raise SystemExit(f"error: unknown command {cmd!r}")
    return 0


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))
