"""Workfront credentials for the loader, read from config.json. Standard library only.

config.json may hold a "workfront" section. Core has four environments (sandbox1, sandbox2, preview, production); Workfront Planning has none, only Workspaces, so its block is
not per environment:

    "workfront": {
      "core": {"sandbox1":   {"base_url": "https://yourcompany.sb01.workfront.com", "api_key": "PASTE_CORE_SANDBOX_KEY_HERE"},
               "production": {"base_url": "https://...",                          "api_key": "..."}},
      "planning": {"dev":  {"base_url": "https://...", "bearer_token": "PASTE_PLANNING_BEARER_TOKEN_HERE", "workspace_id": "PASTE_DEV_WORKSPACE_ID_HERE"},
                   "test": {...}, "prod": {...}}
    }

Planning has one block per STAGE (a Workspace). The stage names are dev, uat (also written test) and production (also written prod); test and prod are stored as uat and
production, the names the database allows. Defining both names of one stage (uat and test, or production and prod) is ambiguous: that stage is left out and
planning_problems() says why.

A system takes a bearer_token or an api_key (use whichever Workfront gave you). Planning uses a Bearer token. The values are secrets: they are never written to the
database, a log, an error message, a report or the browser. server.py ignores this section (it reads only the keys it knows), so nothing here can reach the page.

Other settings live here too, never in code:
    api_path   the path in front of the API routes. Planning defaults to /maestro/api (the documented pattern); Core has no default because it holds the API version, for
               example "/attask/api/v21.0", so a Core block without one is refused with a plain message.
    default_currency, currency_by_symbol   Planning: the ISO currency code to give a currency field (Airtable stores a symbol such as $, which is not a code).
    share_with, section_name   Planning, per stage: who every record type the tool creates is shared with (a list of {"subjectId", "subjectType", "role"}; copy the role
               string from a workspace manager's entry in GET /v2/permissions/workspaces/{workspaceId}/members), and the name of the sidebar section (recordTypeSection) the
               new record types are placed in. See planning_sharing(). Without them a record type made by the API account is invisible in the Planning UI.
    The loader's `run` command reads these; the web pages never do.
"""
import json, os

SYSTEMS = ("core", "planning")
ENVIRONMENTS = ("sandbox1", "sandbox2", "preview", "production")   # Core only (SB01, SB02, Preview, my); Planning uses Workspaces
PLANNING_STAGES = ("dev", "uat", "production")   # the stage names Planning rows can be stored under (the database allows these); config.json decides which exist
PLANNING_STAGE_ALIASES = {"test": "uat", "prod": "production"}   # other names config.json may use for a stage; rows are stored under the stage name
SECRET_KEYS = ("bearer_token", "api_key")
PLACEHOLDERS = ("PASTE", "REPLACE", "XXXX", "YOURCOMPANY")
PLANNING_DEFAULT_API_PATH = "/maestro/api"      # the documented base pattern https://{customer-domain}/maestro/api
LOCAL_HOSTS = ("http://127.0.0.1", "http://localhost")      # only a local test server may use plain http


class ConfigError(Exception):
    """A plain-language problem with the configuration. The message never contains a secret."""


def _real(value):
    v = str(value or "").strip()
    return bool(v) and not any(p in v.upper() for p in PLACEHOLDERS)


class SystemCredentials(object):
    """The address and one secret for one system in one environment. The secret is kept out of repr(), str() and as_status()."""
    __slots__ = ("environment", "system", "base_url", "_secret", "kind", "workspace_id", "api_path", "extra")

    def __init__(self, environment, system, base_url, secret, kind, workspace_id=None, api_path=None, extra=None):
        self.environment, self.system, self.base_url, self._secret, self.kind, self.workspace_id = environment, system, base_url, secret, kind, workspace_id
        self.api_path, self.extra = api_path, extra or {}

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


def planning_stage(name):
    """The stage name a Planning stage is stored under: "Test" -> "uat", "PROD" -> "production", "dev" -> "dev". Any other name comes back lower case and unchanged (callers
    check it against the defined stages)."""
    n = str(name or "").strip().lower()
    return PLANNING_STAGE_ALIASES.get(n, n)


def _planning_keys(data):
    """{stage: [config keys that name it, as written]} for the planning blocks that are objects and name a known stage. Keys sorted, so the result is the same every time."""
    p = (data.get("workfront") or {}).get("planning")
    out = {}
    if isinstance(p, dict) and "base_url" not in p:
        for k in sorted(p, key=str):
            if isinstance(p[k], dict) and planning_stage(k) in PLANNING_STAGES:
                out.setdefault(planning_stage(k), []).append(str(k))
    return out


def planning_blocks(data):
    """{stage: settings block} for Workfront Planning, stages in the order dev, uat, production. The user defines them in config.json under workfront.planning, one block per
    stage ("DEV", "dev" and "Dev" are the same; "test" is uat and "prod" is production), each with its own base_url, bearer_token and workspace_id. A stage named twice (uat and
    test) is left out (see planning_problems). An older config whose planning block holds base_url directly is one block that serves dev and uat."""
    p = (data.get("workfront") or {}).get("planning")
    if not isinstance(p, dict):
        return {}
    if "base_url" in p:
        return {"dev": p, "uat": p}
    keys = _planning_keys(data)
    return {s: p[keys[s][0]] for s in PLANNING_STAGES if len(keys.get(s, ())) == 1}


def planning_labels(data):
    """{stage: the name config.json uses for it}, for the stage picker ("uat" may be written "test"). Only the usable stages of planning_blocks."""
    keys = _planning_keys(data)
    return {s: (keys[s][0].strip().lower() if s in keys else s) for s in planning_blocks(data)}


def planning_problems(data):
    """Plain messages about the planning section that leave a stage out (a stage named twice). Never contains a value from the blocks."""
    return ["config.json names the Planning stage %s twice (workfront.planning: %s). Keep one of them; until then the %s stage cannot be used." % (s, " and ".join(ks), s)
            for s, ks in sorted(_planning_keys(data).items(), key=lambda kv: PLANNING_STAGES.index(kv[0])) if len(ks) > 1]


def credentials(data, system, environment=None):
    """SystemCredentials, or None when the block is missing, incomplete or still holds a placeholder. Core needs an environment (sandbox1, sandbox2,
    preview or production). Planning takes its STAGE (dev, uat or production) as `environment`; with none given the first stage defined in config.json is used. A Planning
    block carries its own base_url, bearer_token and workspace_id."""
    if system not in SYSTEMS or (system == "core" and environment not in ENVIRONMENTS):
        raise ConfigError("Unknown system or environment.")
    wf = data.get("workfront") or {}
    if system == "planning":
        blocks = planning_blocks(data)
        stage = planning_stage(environment) if environment else (next(iter(blocks), None))
        block = blocks.get(stage)
        environment = stage
    else:
        block = (wf.get("core") or {}).get(environment)
    if not isinstance(block, dict):
        return None
    url = str(block.get("base_url") or "").strip().rstrip("/")
    if not (url.lower().startswith("https://") or url.lower().startswith(LOCAL_HOSTS)) or any(p in url.upper() for p in PLACEHOLDERS):
        return None
    ws = str(block.get("workspace_id") or "").strip() if system == "planning" else ""
    ws = None if (not ws or any(p in ws.upper() for p in PLACEHOLDERS)) else ws
    path = str(block.get("api_path") or "").strip()
    path = ("/" + path.strip("/")) if path else (PLANNING_DEFAULT_API_PATH if system == "planning" else None)
    extra = {k: v for k, v in block.items() if k not in SECRET_KEYS + ("base_url", "api_path", "workspace_id") and isinstance(v, (str, int, dict)) and not any(p in str(v).upper() for p in PLACEHOLDERS)}
    for kind in SECRET_KEYS:
        if _real(block.get(kind)):
            return SystemCredentials(environment, system, url, str(block[kind]).strip(), kind, ws, path, extra)
    return None


def planning_sharing(data, stage):
    """The sharing settings of a Planning stage: {"share_with": [{"subjectId", "subjectType", "role"}, ...], "section_name": "..."}, or None when the stage block has neither key
    (nothing is configured). Raises ConfigError with a plain message when only one of them is given, or either is malformed or still a placeholder. Never repeats a value that
    could be a secret (IDs and role names are not secrets, but the bearer token is never read here)."""
    block = planning_blocks(data).get(planning_stage(stage))
    if not isinstance(block, dict) or ("share_with" not in block and "section_name" not in block):
        return None
    where = "workfront.planning.%s" % planning_labels(data).get(planning_stage(stage), planning_stage(stage))
    if "share_with" not in block or "section_name" not in block:
        raise ConfigError("config.json (%s) needs both share_with and section_name, or neither: %s is missing." % (where, "share_with" if "share_with" not in block else "section_name"))
    sec = block.get("section_name")
    if not isinstance(sec, str) or not _real(sec):
        raise ConfigError("config.json (%s): section_name must be the name of a section in the Planning sidebar (not blank or a placeholder)." % where)
    people = block.get("share_with")
    if not isinstance(people, list) or not people:
        raise ConfigError("config.json (%s): share_with must be a list with at least one {\"subjectId\", \"subjectType\", \"role\"}." % where)
    out = []
    for n, p in enumerate(people, 1):
        if not isinstance(p, dict):
            raise ConfigError("config.json (%s): share_with item %d must be an object with subjectId, subjectType and role." % (where, n))
        bad = [k for k in ("subjectId", "subjectType", "role") if not isinstance(p.get(k), str) or not _real(p.get(k))]
        if bad:
            raise ConfigError("config.json (%s): share_with item %d needs a real %s (blank or placeholder values are refused)." % (where, n, ", ".join(bad)))
        extra = sorted(set(p) - {"subjectId", "subjectType", "role"})
        if extra:
            raise ConfigError("config.json (%s): share_with item %d has keys Planning ignores (%s); only subjectId, subjectType and role are sent." % (where, n, ", ".join(extra)))
        out.append({"subjectId": p["subjectId"].strip(), "subjectType": p["subjectType"].strip(), "role": p["role"].strip()})
    return {"share_with": out, "section_name": sec.strip()}


def status(data):
    """What is configured, as yes/no only. Safe to print or log: {"core": {"<environment>": bool, ...}, "planning": bool,
    "planning_workspace": bool}."""
    ps = {s: credentials(data, "planning", s) for s in planning_blocks(data)}
    return {"core": {e: credentials(data, "core", e) is not None for e in ENVIRONMENTS}, "planning": any(c is not None for c in ps.values()), "planning_workspace": any(c is not None and bool(c.workspace_id) for c in ps.values()),
            "planning_stages": {s: (c is not None and bool(c.workspace_id)) for s, c in ps.items()}}


def secret_values(data):
    """Every secret in the configuration (the Airtable token included), so a message can be cleaned of them with redact()."""
    out = set()
    if _real(data.get("token") or data.get("airtable_pat")):
        out.add(str(data.get("token") or data.get("airtable_pat")).strip())
    for c in [credentials(data, "core", e) for e in ENVIRONMENTS] + [credentials(data, "planning", s) for s in planning_blocks(data)]:
        if c:
            out.add(c.secret())
    p = (data.get("workfront") or {}).get("planning")        # a stage left out as ambiguous still has a token to keep out of messages
    for b in ([p] if isinstance(p, dict) and "base_url" in p else list(p.values()) if isinstance(p, dict) else []):
        for k in SECRET_KEYS:
            if isinstance(b, dict) and _real(b.get(k)):
                out.add(str(b[k]).strip())
    return out


def redact(text, secrets):
    """The text with every configured secret value replaced, longest first, as a second line of defence after loadrun.scrub()."""
    text = str(text)
    for v in sorted(secrets, key=len, reverse=True):
        text = text.replace(v, "[removed]")
    return text
