"""Workfront credentials for the loader, read from config.json. Standard library only.

config.json may hold a "workfront" section. Core has environments (sandbox, production); Workfront Planning has none, only Workspaces, so its block is
not per environment:

    "workfront": {
      "core": {"sandbox":    {"base_url": "https://yourcompany.sb01.workfront.com", "api_key": "PASTE_CORE_SANDBOX_KEY_HERE"},
               "production": {"base_url": "https://...",                          "api_key": "..."}},
      "planning": {"base_url": "https://...", "bearer_token": "PASTE_PLANNING_BEARER_TOKEN_HERE", "workspace_id": "PASTE_DEV_WORKSPACE_ID_HERE"}
    }

A system takes a bearer_token or an api_key (use whichever Workfront gave you). The values are secrets: they are never written to the database,
a log, an error message, a report or the browser. server.py ignores this section (it reads only the keys it knows), so nothing here can reach
the page. Nothing in the loader uses these yet: creating items in Workfront is not implemented (docs/LOADER_PLAN.md).
"""
import json, os

SYSTEMS = ("core", "planning")
ENVIRONMENTS = ("sandbox", "production")      # Core only; Planning uses Workspaces
SECRET_KEYS = ("bearer_token", "api_key")
PLACEHOLDERS = ("PASTE", "REPLACE", "XXXX", "YOURCOMPANY")


class ConfigError(Exception):
    """A plain-language problem with the configuration. The message never contains a secret."""


def _real(value):
    v = str(value or "").strip()
    return bool(v) and not any(p in v.upper() for p in PLACEHOLDERS)


class SystemCredentials(object):
    """The address and one secret for one system in one environment. The secret is kept out of repr(), str() and as_status()."""
    __slots__ = ("environment", "system", "base_url", "_secret", "kind", "workspace_id")

    def __init__(self, environment, system, base_url, secret, kind, workspace_id=None):
        self.environment, self.system, self.base_url, self._secret, self.kind, self.workspace_id = environment, system, base_url, secret, kind, workspace_id

    def secret(self):
        return self._secret

    def authorization_header(self):
        """The header the transport sends. Bearer for a bearer token; an API key travels in its own header, which the Core transport decides."""
        return ("Authorization", "Bearer " + self._secret) if self.kind == "bearer_token" else ("apiKey", self._secret)

    def __repr__(self):
        return "<%s %s credentials: %s%s, secret hidden>" % (self.environment or "workspace", self.system, self.base_url, " workspace " + self.workspace_id if self.workspace_id else "")

    __str__ = __repr__


def read_config(path):
    """The parsed config.json, or ConfigError with a plain message (never the file's content)."""
    if not os.path.isfile(path):
        raise ConfigError("config.json was not found at %s." % path)
    try:
        with open(path, encoding="utf-8-sig") as f:
            data = json.load(f)
    except (OSError, ValueError):
        raise ConfigError("config.json could not be read; it must be valid JSON.")
    if not isinstance(data, dict):
        raise ConfigError("config.json must be a JSON object.")
    return data


def credentials(data, system, environment=None):
    """SystemCredentials, or None when the block is missing, incomplete or still holds a placeholder. Core needs an environment (sandbox or
    production); Planning has none (it uses a Workspace), so its block is read as it is and carries the workspace_id."""
    if system not in SYSTEMS or (system == "core" and environment not in ENVIRONMENTS):
        raise ConfigError("Unknown system or environment.")
    wf = data.get("workfront") or {}
    block = ((wf.get("core") or {}).get(environment) if system == "core" else wf.get("planning"))
    if not isinstance(block, dict):
        return None
    url = str(block.get("base_url") or "").strip().rstrip("/")
    if not url.lower().startswith("https://") or any(p in url.upper() for p in PLACEHOLDERS):
        return None
    ws = str(block.get("workspace_id") or "").strip() if system == "planning" else ""
    ws = None if (not ws or any(p in ws.upper() for p in PLACEHOLDERS)) else ws
    for kind in SECRET_KEYS:
        if _real(block.get(kind)):
            return SystemCredentials(environment, system, url, str(block[kind]).strip(), kind, ws)
    return None


def status(data):
    """What is configured, as yes/no only. Safe to print or log: {"core": {"sandbox": bool, "production": bool}, "planning": bool,
    "planning_workspace": bool}."""
    p = credentials(data, "planning")
    return {"core": {e: credentials(data, "core", e) is not None for e in ENVIRONMENTS}, "planning": p is not None, "planning_workspace": bool(p and p.workspace_id)}


def secret_values(data):
    """Every secret in the configuration (the Airtable token included), so a message can be cleaned of them with redact()."""
    out = set()
    if _real(data.get("token") or data.get("airtable_pat")):
        out.add(str(data.get("token") or data.get("airtable_pat")).strip())
    for c in [credentials(data, "core", e) for e in ENVIRONMENTS] + [credentials(data, "planning")]:
        if c:
            out.add(c.secret())
    return out


def redact(text, secrets):
    """The text with every configured secret value replaced, longest first, as a second line of defence after loadrun.scrub()."""
    text = str(text)
    for v in sorted(secrets, key=len, reverse=True):
        text = text.replace(v, "[removed]")
    return text
