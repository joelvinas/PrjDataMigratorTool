#!/usr/bin/env python3
"""The attachment mover: takes the files Stage 1 already downloaded and makes each one a Workfront Core document on the Core object its record became. Standard library only.

Two steps, both restartable:
    plan     reads each table's attachment index (<table>.attachments.json, written by Stage 1) and records one attachment_map row per file: Airtable record, field and
             attachment IDs, the size Airtable reported, and (when the file is on disk and its size matches) its downloaded size and SHA-256. No file name and no link is stored.
    upload   for every downloaded attachment whose record already has a Core object (target_mappings, system core): check the file on disk still matches its SHA-256, upload
             it, create the document on the object, and record the new Core document ID. An attachment is never uploaded twice. When every attachment of a record is done the
             record gets the `attachments` pass marker.

Rules kept here (CLAUDE.md):
  * A file name is read from the local index in memory and sent to Workfront as the document name; it is never written to the database, a report, a log or an error message.
    A report names an attachment by its Airtable IDs only.
  * An Airtable link is never used here: the files are already on disk. Nothing is downloaded.
  * A file that changed after it was downloaded (its hash no longer matches) is not uploaded.
  * One failure never stops the run: the attachment is marked failed with a short scrubbed reason and, if it was downloaded, is tried again next run.
  * Known limit: a stop between Core's document answer and writing it uploads that file a second time on the next run.

UNVERIFIED (docs/LOADER_PLAN.md): the upload route and field name, the document properties, and how Core treats very large files.
"""
import hashlib
import json
import os

import coretargets as tg
import loadrun as lr
import recordmap as rm

INDEX_FORMAT = "airtable-attachment-index/1"


def read_index(extract_dir, base_id, table_id):
    """The table's attachment index as a dict, or None when there is none (or it is not readable or not in the expected format)."""
    path = os.path.join(extract_dir, base_id, table_id + ".attachments.json")
    try:
        with open(path, encoding="utf-8") as f:
            doc = json.load(f)
    except (OSError, ValueError):
        return None
    return doc if isinstance(doc, dict) and doc.get("format") == INDEX_FORMAT and isinstance(doc.get("attachments"), list) else None


def sha256_file(path, chunk=1 << 20):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for part in iter(lambda: f.read(chunk), b""):
            h.update(part)
    return h.hexdigest()


def local_path(extract_dir, base_id, entry):
    """The saved file for an index entry, only if it lies inside the base's extract folder (the index is data, so its path is never trusted)."""
    rel = entry.get("file")
    if not isinstance(rel, str) or not rel:
        return None
    root = os.path.realpath(os.path.join(extract_dir, base_id))
    path = os.path.realpath(os.path.join(root, rel))
    return path if path.startswith(root + os.sep) and os.path.isfile(path) else None


# ------------------------------------------------------------------ step 1: plan
def plan_table(conn, base_id, table_id, env, extract_dir):
    """Record the table's attachments from its index. Returns counts: planned, downloaded, failed, unregistered, missing_file, skipped."""
    out = {"planned": 0, "downloaded": 0, "failed": 0, "unregistered": 0, "missing_file": 0, "skipped": 0, "indexed": 0}
    doc = read_index(extract_dir, base_id, table_id)
    if doc is None:
        return out
    seen = set()
    for e in doc["attachments"]:
        if not isinstance(e, dict):
            out["skipped"] += 1
            continue
        key = (e.get("record_id"), e.get("field_id"), e.get("attachment_id"))
        if key in seen:
            continue
        seen.add(key)
        out["indexed"] += 1
        uid = rm.lookup(conn, base_id, table_id, e.get("record_id")) if rm.valid_record_id(e.get("record_id")) else None
        if uid is None:
            out["unregistered"] += 1
            continue
        size = e.get("size") if isinstance(e.get("size"), int) and e.get("size") >= 0 else None
        try:
            row = rm.plan_attachment(conn, uid, e.get("field_id"), e.get("attachment_id"), env, size)
        except rm.RecordMapError:
            out["skipped"] += 1
            continue
        out["planned"] += 1
        if conn.execute("SELECT status FROM attachment_map WHERE attachment_row_id=?", (row,)).fetchone()[0] != "pending":
            continue                                               # planned and handled on an earlier run
        if e.get("status") == "failed":
            rm.attachment_failed(conn, row, "download failed in Stage 1: " + str(e.get("error") or "no reason recorded")[:150])
            out["failed"] += 1
        elif local_path(extract_dir, base_id, e) is None or not isinstance(e.get("bytes"), int) or not isinstance(e.get("sha256"), str):
            rm.attachment_failed(conn, row, "the downloaded file is missing or its size and check value were not recorded")
            out["missing_file"] += 1
        else:
            ok = rm.attachment_downloaded(conn, row, e["bytes"], e["sha256"])
            out["downloaded" if ok else "failed"] += 1
    return out


# ------------------------------------------------------------------ step 2: upload
def _core_object(conn, uid, env):
    """(object code, Core object ID) of the record's Core object, or None when it has none yet."""
    for kind, tid in rm.targets_of(conn, uid, "core", env):
        if kind in tg.OBJECT_CODE:
            return tg.OBJECT_CODE[kind], tid
    return None


def upload_all(conn, base_id, env, tr, extract_dir, dry_run=True):
    """Upload every downloaded attachment whose record has a Core object. Returns {uploaded, failed, waiting, no_file, would_upload, retried, failures}."""
    res = {"uploaded": 0, "failed": 0, "waiting": 0, "no_file": 0, "would_upload": 0, "retried": 0, "failures": []}
    rows = [dict(r) for r in conn.execute(
        "SELECT a.attachment_row_id AS row_id, a.source_record_uid AS uid, s.table_id, s.record_id, a.airtable_attachment_id AS attachment_id, a.sha256, a.status, a.api_size_bytes, a.downloaded_bytes "
        "FROM attachment_map a JOIN source_records s ON s.source_record_uid=a.source_record_uid WHERE s.base_id=? AND a.environment=? AND a.status IN ('downloaded', 'failed') "
        "ORDER BY a.attachment_row_id", (base_id, env))]
    index = {}
    touched = set()
    for r in rows:
        if r["status"] == "failed" and (r["sha256"] is None or r["downloaded_bytes"] is None or (r["api_size_bytes"] is not None and r["api_size_bytes"] != r["downloaded_bytes"])):
            res["no_file"] += 1                                      # failed in Stage 1, or the size check failed: there is no trustworthy file to send
            continue
        obj = _core_object(conn, r["uid"], env)
        if obj is None:
            res["waiting"] += 1                                      # its record has no Core object yet
            continue
        if dry_run:
            res["would_upload"] += 1
            continue
        if r["status"] == "failed":
            rm.attachment_retry(conn, r["row_id"])
            res["retried"] += 1
        touched.add(r["uid"])
        if r["table_id"] not in index:
            doc = read_index(extract_dir, base_id, r["table_id"])
            index[r["table_id"]] = {e.get("attachment_id"): e for e in (doc or {}).get("attachments", []) if isinstance(e, dict)}
        entry = index[r["table_id"]].get(r["attachment_id"])
        try:
            path = local_path(extract_dir, base_id, entry or {})
            if path is None:
                raise ValueError("the downloaded file is no longer on disk")
            if sha256_file(path) != r["sha256"]:
                raise ValueError("the file on disk no longer matches its recorded check value, so it was not uploaded")
            name = str(entry.get("filename") or os.path.basename(path))
            handle = tr.upload_file(path, name, str(entry.get("type") or "application/octet-stream"))
            doc_id = tr.create_document(name, handle, obj[0], obj[1], name)
            rm.attachment_uploaded(conn, r["row_id"], doc_id)
            res["uploaded"] += 1
        except Exception as ex:
            rm.attachment_failed(conn, r["row_id"], str(ex))
            res["failed"] += 1
            res["failures"].append((r["table_id"], r["record_id"], r["attachment_id"], lr.scrub(ex)))
    if not dry_run:
        for uid in sorted(touched):
            left = conn.execute("SELECT COUNT(*) FROM attachment_map WHERE source_record_uid=? AND environment=? AND status!='uploaded'", (uid, env)).fetchone()[0]
            if left == 0:
                try:
                    rm.mark_pass(conn, [uid], "attachments", env, "core", "")
                except rm.RecordMapError:
                    pass                                             # the record's records pass is not marked for this environment yet
    return res
