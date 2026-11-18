#!/usr/bin/env python3
"""Runs a load plan against Workfront (or only describes what it would do). Standard library only.

The runner never knows how to talk to Workfront. It is given one TRANSPORT per system ('planning', 'core') with two methods:

    describe(item, parent_id) -> dict      what would be sent, for a dry run; makes no call
    create(item, parent_id)   -> str       creates the item and returns its Workfront ID

The real Planning and Core transports (each with its own authentication) are added when the request shapes are verified against Adobe's current
documentation and a sandbox; until then only a DryRunTransport exists, so the runner can be exercised end to end without credentials.

Rules this module enforces:
  * Environment defaults to SANDBOX (Sandbox 1). Production needs a deliberate extra step: the exact confirmation phrase, and only for a real run (a dry
    run changes nothing, so it needs none).
  * Idempotent: every item has a temporary ID. Its Workfront ID is written back to the STATE the moment create() returns, and any item that
    already has an ID is skipped, so a restart (or a second run) never creates a duplicate. With the database state (loadstate.DbState) a PENDING row
    is written before each create call; if the program stopped mid-call, the next run tries the item again and, when Workfront says the name already
    exists AND an earlier attempt is on record, adopts the one matching item through the transport's optional find_existing(item, parent_id).
  * One failure never stops the run: the item is marked failed with a short, scrubbed message and the run continues. An item whose parent
    has no Workfront ID is skipped, not guessed.
  * Dry run is the default.
"""
import re

SANDBOX, PRODUCTION = "sandbox1", "production"
CORE_ENVIRONMENTS = ("sandbox1", "sandbox2", "preview", "production")   # Core: Sandbox 1 (SB01), Sandbox 2 (SB02), Preview, Production (my)
PLANNING_STAGES = ("dev", "uat", "production")                                          # Planning has no environments, only Workspaces: Dev is wiped and recreated as UAT, or promoted
ENVIRONMENTS = CORE_ENVIRONMENTS + tuple(s for s in PLANNING_STAGES if s not in CORE_ENVIRONMENTS)   # "production" is both a Core environment and a Planning stage; rows also carry their system and Workspace
PRODUCTION_PHRASE = "CREATE IN PRODUCTION"
ENV_COLUMN = {"sandbox1": "DEV", "sandbox2": "DEV", "preview": "DEV", "production": "PROD", "dev": "DEV", "uat": "UAT"}        # the environment names used by workfront_fields and field_crosswalk
_SECRET = re.compile(r"(?i)(authorization\s*[=:]\s*(?:bearer\s+|basic\s+)?\S+|bearer\s+\S+|eyJ[\w-]{10,}\.[\w-]{10,}\.[\w-]{5,}"
                     r"|(?:api[_-]?key|token|secret|password)\s*[=:]\s*\S+)")


class Environment(object):
    def __init__(self, name, confirmed, workspace_id=None):
        self.name, self.confirmed, self.workspace_id = name, confirmed, workspace_id

    def __repr__(self):
        return "Environment(%s)" % self.name


class NameConflict(Exception):
    """A transport raises this when Workfront refuses a create because the name already exists (for example FIELD_DISPLAY_NAME_NOT_UNIQUE)."""


def resolve_environment(name=None, confirm=None, dry_run=True, workspace_id=None):
    """The environment to run in. Sandbox unless production is asked for; a real production run needs the exact confirmation phrase."""
    name = (name or SANDBOX).strip().lower()
    if name not in ENVIRONMENTS:
        raise ValueError("The environment must be one of: %s." % ", ".join(ENVIRONMENTS))
    confirmed = confirm == PRODUCTION_PHRASE
    if name == PRODUCTION and not dry_run and not confirmed:
        raise ValueError("Production needs a deliberate extra step: pass the confirmation phrase %r exactly." % PRODUCTION_PHRASE)
    return Environment(name, confirmed, workspace_id or None)


def scrub(text, limit=300):
    """A short message with anything that looks like a credential removed (the token never reaches a log or the database)."""
    return _SECRET.sub("[removed]", str(text))[:limit]


class MemoryState(object):
    """Temporary ID -> Workfront ID, per environment, plus a status. Same methods as loadstate.DbState (which keeps this in the database); used by
    dry runs and tests."""
    def __init__(self):
        self.ids, self.status, self.attempts = {}, {}, {}

    def begin(self, item, env):
        key = (item["temp_id"], env)
        self.attempts[key] = self.attempts.get(key, 0) + 1
        self.status[key] = "pending"
        return self.attempts[key]

    def adopt(self, temp_id, env, workfront_id):
        self.set(temp_id, env, workfront_id)

    def get(self, temp_id, env):
        return self.ids.get((temp_id, env))

    def set(self, temp_id, env, workfront_id):
        self.ids[(temp_id, env)] = workfront_id
        self.status[(temp_id, env)] = "created"

    def fail(self, temp_id, env, message):
        self.status[(temp_id, env)] = "failed: " + message


class DryRunTransport(object):
    """Describes what would be created; talks to nothing. It reports the INTENT (name, type, parent), not a request body: the real request shape
    for each system is added when it is verified."""
    def __init__(self, system):
        self.system = system

    def describe(self, item, parent_id):
        return {"system": self.system, "action": "create " + item["kind"], "name": item["name"], "label": item["label"], "data_type": item["data_type"], "display_type": item.get("display_type"),
                "position": item["position"], "parent": parent_id, "temp_id": item["temp_id"], "note": "intent only: the request shape is not implemented yet"}

    def create(self, item, parent_id):
        raise RuntimeError("The dry-run transport cannot create anything.")


def is_name_conflict(e):
    return isinstance(e, NameConflict) or "FIELD_DISPLAY_NAME_NOT_UNIQUE" in str(e)


def _find_one(tr, args):
    """The ID of the one existing item that matches, else None (none found, more than one, or the lookup failed: never guess)."""
    try:
        found = tr.find_existing(*args)
    except Exception:
        return None
    if isinstance(found, (list, tuple, set)):
        found = list(found)[0] if len(found) == 1 else None
    return str(found) if found else None


def run_plan(items, state, transports, env, dry_run=True, log=None, should_stop=None):
    """Walk the plan in order. Returns {results: [{temp_id, outcome, detail}], counts}. outcomes: would_create, created, already_created, not_ready,
    parent_missing, no_transport, adopted, failed. log(temp_id, outcome, detail) is called for each item (the caller decides where it goes).
    should_stop() is asked BEFORE each item (never in the middle of one), so a stop leaves no item half done; the result then says cancelled=True."""
    if not isinstance(env, Environment):
        raise ValueError("Give the runner an Environment from resolve_environment().")
    if not dry_run and env.name == PRODUCTION and not env.confirmed:
        raise ValueError("Production needs the confirmation phrase.")
    log = log or (lambda *a: None)
    results, counts = [], {}

    def done(item, outcome, detail=None):
        results.append({"temp_id": item["temp_id"], "system": item["system"], "kind": item["kind"], "name": item["name"], "outcome": outcome, "detail": detail})
        counts[outcome] = counts.get(outcome, 0) + 1
        log(item["temp_id"], outcome, detail)

    placeholder = {}                                        # dry run: a parent that would be created stands in for its ID
    cancelled = False
    for item in items:
        if should_stop and should_stop():
            cancelled = True
            break
        tid = item["temp_id"]
        if item["status"] != "ready":
            done(item, "not_ready", "; ".join(item["reasons"]) or item["status"])
            continue
        existing = state.get(tid, env.name)
        if existing:
            done(item, "already_created", existing)
            continue
        parent_id = None
        if item["parent"]:
            parent_id = state.get(item["parent"], env.name) or (placeholder.get(item["parent"]) if dry_run else None)
            if not parent_id:
                done(item, "parent_missing", item["parent"])
                continue
        tr = transports.get(item["system"])
        if tr is None:
            done(item, "no_transport", "no %s transport is configured" % item["system"])
            continue
        refs, lost = {}, None
        for need in item.get("requires") or []:                   # a second-pass item (a link) needs OTHER items to exist first
            got = state.get(need, env.name) or (placeholder.get(need) if dry_run else None)
            if not got:
                lost = need
                break
            refs[need] = got
        if lost:
            done(item, "parent_missing", lost)
            continue
        args = (item, parent_id, refs) if refs else (item, parent_id)
        if dry_run:
            placeholder[tid] = "<new %s>" % tid
            done(item, "would_create", tr.describe(*args))
            continue
        begin = getattr(state, "begin", None)
        tries = begin(item, env.name) if begin else 1       # a PENDING row is written before the call, so an interrupted run leaves a trace
        try:
            wid = str(tr.create(*args))
        except Exception as e:                              # one failure never stops the run
            if is_name_conflict(e) and tries > 1 and hasattr(tr, "find_existing"):
                found = _find_one(tr, args)                 # an earlier, interrupted try may have created it: adopt it, but only if exactly one item matches
                if found:
                    state.adopt(tid, env.name, found)
                    done(item, "adopted", found)
                    continue
            why = scrub(e) + ("; an item with this name exists in Workfront and this tool has no earlier attempt for it, so it was not adopted" if is_name_conflict(e) and tries <= 1 else "")
            state.fail(tid, env.name, why)
            done(item, "failed", why)
            continue
        state.set(tid, env.name, wid)                       # written back at once, so a restart skips it
        done(item, "created", wid)
    return {"results": results, "counts": counts, "environment": env.name, "dry_run": dry_run, "cancelled": cancelled}
