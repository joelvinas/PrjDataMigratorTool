#!/usr/bin/env python3
"""The runner's state kept in the database table load_id_map (migrations 010 and 016). Standard library only.

It has the same methods as loadrun.MemoryState, so the runner does not care which one it is given:

    get(temp_id, env)               -> the Workfront ID once the item is created, else None
    begin(item, env)                -> writes a PENDING row BEFORE the create call and returns how many times this item has been tried
    set(temp_id, env, workfront_id) -> the create call returned: status created
    adopt(temp_id, env, workfront_id) -> the item turned out to exist already (an earlier, interrupted try created it): status created
    fail(temp_id, env, message)     -> status failed, with a short message that has any credential removed

Why a pending row first: if the program stops between the create call and writing the answer, the database still says an attempt was made. On
the next run that item is tried again; if Workfront refuses it because the name already exists, the runner may ADOPT the existing item, but only
when an earlier attempt was recorded here. A name clash with something this tool never tried to create is reported, never adopted.

The key is (temp_id, environment, workspace_key). A Planning item belongs to a Workspace, so writing one needs a workspace ID; Core rows use ''.
Holds IDs, names, statuses and short scrubbed messages only. No credential and no record value.
"""
import loadrun as lr

KINDS = ("record_type", "form", "field", "option")


def available(conn):
    """True when load_id_map has the workspace_key column (migration 016). An older database falls back to in-memory state."""
    return "workspace_key" in {r[1] for r in conn.execute("PRAGMA table_info(load_id_map)")}


class StateError(Exception):
    pass


def system_of(temp_id):
    """'planning' or 'core', read from the temporary ID prefix (P-T-, P-F-, P-O-, S-P- are Planning; C-T-, C-F-, C-O-, S-C- are Core)."""
    t = str(temp_id)
    return "planning" if t.startswith("P-") or t.startswith("S-P-") else "core"


class DbState(object):
    def __init__(self, conn, base_id, workspace_id=None):
        self.conn, self.base_id, self.workspace_id = conn, base_id, workspace_id or None

    def _ws(self, temp_id, writing=False):
        """(workspace_id, workspace_key) for the item's system. A Planning write without a Workspace is refused: its IDs would collide across Workspaces."""
        if system_of(temp_id) == "core":
            return None, ""
        if writing and not self.workspace_id:
            raise StateError("A Planning item needs the Planning Workspace ID before its state can be saved.")
        return self.workspace_id, self.workspace_id or ""

    def _row(self, temp_id, env):
        _ws, key = self._ws(temp_id)
        return self.conn.execute("SELECT * FROM load_id_map WHERE temp_id=? AND environment=? AND workspace_key=?", (temp_id, env, key)).fetchone()

    def get(self, temp_id, env):
        r = self._row(temp_id, env)
        return r["workfront_id"] if r is not None and r["status"] == "created" and r["workfront_id"] else None

    def status(self, temp_id, env):
        r = self._row(temp_id, env)
        return None if r is None else r["status"]

    def begin(self, item, env):
        ws, key = self._ws(item["temp_id"], writing=True)
        kind = item["kind"]
        if kind not in KINDS:
            raise StateError("Unknown item kind %r." % kind)
        with self.conn:
            self.conn.execute(
                "INSERT INTO load_id_map(temp_id, environment, workspace_id, workspace_key, system, kind, base_id, table_id, airtable_field_id, parent_temp_id, position, name, status, attempts) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?, 'pending', 1) "
                "ON CONFLICT(temp_id, environment, workspace_key) DO UPDATE SET status='pending', attempts=attempts+1, name=excluded.name, last_error=NULL, updated_at=CURRENT_TIMESTAMP",
                (item["temp_id"], env, ws, key, item["system"], kind, self.base_id, item.get("table_id"), item.get("field_id"), item.get("parent"), item.get("position"), item["name"]))
        return self._row(item["temp_id"], env)["attempts"]

    def _finish(self, temp_id, env, status, workfront_id=None, error=None):
        _ws, key = self._ws(temp_id, writing=True)
        with self.conn:
            n = self.conn.execute("UPDATE load_id_map SET status=?, workfront_id=COALESCE(?, workfront_id), last_error=?, updated_at=CURRENT_TIMESTAMP "
                                  "WHERE temp_id=? AND environment=? AND workspace_key=?", (status, workfront_id, error, temp_id, env, key)).rowcount
        if n != 1:
            raise StateError("No pending row for %s in %s: begin() was not called first." % (temp_id, env))

    def set(self, temp_id, env, workfront_id):
        self._finish(temp_id, env, "created", str(workfront_id))

    adopt = set

    def fail(self, temp_id, env, message):
        self._finish(temp_id, env, "failed", None, lr.scrub(message))

    def pending(self, env):
        """Items an earlier run began but never finished (the program stopped, or the answer was lost): sorted temp IDs. A person should know before a rerun."""
        keys = ["", self.workspace_id or ""]
        return [r[0] for r in self.conn.execute("SELECT temp_id FROM load_id_map WHERE environment=? AND status='pending' AND base_id=? AND workspace_key IN (?,?) ORDER BY temp_id",
                                                (env, self.base_id, keys[0], keys[1]))]
