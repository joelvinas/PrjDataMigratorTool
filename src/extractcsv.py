"""Turn one saved Airtable table extract (the JSON file the extract step writes) into a CSV a person can open and check.

Layout of the CSV (one row per line):
  1. the extract's own facts, label in column A and value in column B: format, base_id, table_id, table_name, captured_at, record_count, field_count
  2. two blank rows
  3. field_id row, data_type row, then the header row (record_id, createdTime, then one field name per column)
  4. one row per record, one column per field

An array or an object stays in ONE cell as JSON text, so nothing is lost and nothing is ambiguous. Long text keeps its line breaks inside its cell. The csv module quotes every
comma, quote and line break, so a value can hold any of them. Numbers keep the digits Airtable sent. The file starts with a byte order mark so Excel reads accents correctly.
Standard library only; the same input always gives the same output. Record values are handled in memory only: nothing is stored or logged, and the caller gets counts back."""
import base64, csv, decimal, io, json, re, unicodedata

BOM = b"\xef\xbb\xbf"
MAX_NAME_LEN = 80
OUT_OF_LIST = "(not in the fields list)"


class ExtractError(Exception):
    """A plain-language reason the file cannot be converted. Never contains a record value."""


def parse(raw):
    """Bytes of an extract file -> the parsed document. Numbers stay exact (Decimal)."""
    if isinstance(raw, str):
        raw = raw.encode("utf-8")
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise ExtractError("The file is not UTF-8 text. Choose the .json file the extract step saved.")
    if not text.strip():
        raise ExtractError("The file is empty.")
    try:
        doc = json.loads(text, parse_float=decimal.Decimal)
    except ValueError as e:
        raise ExtractError("The file is not valid JSON (%s)." % str(e)[:120])
    if not isinstance(doc, dict):
        raise ExtractError("The file is JSON but not an extract: the top level must be an object with fields and records.")
    if not isinstance(doc.get("fields"), list) or not isinstance(doc.get("records"), list):
        raise ExtractError('The file is JSON but not an extract: it needs a "fields" list and a "records" list.')
    for i, f in enumerate(doc["fields"]):
        if not isinstance(f, dict) or not isinstance(f.get("field_id"), str):
            raise ExtractError('Entry %d of "fields" has no field_id.' % (i + 1))
    for i, r in enumerate(doc["records"]):
        if not isinstance(r, dict):
            raise ExtractError('Entry %d of "records" is not an object.' % (i + 1))
    return doc


def cell(value):
    """One record value as the text of one CSV cell."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, str):
        return value
    if isinstance(value, (int, decimal.Decimal)):
        return str(value)
    return json.dumps(value, ensure_ascii=False, default=str, separators=(", ", ": "))  # arrays and objects: one cell of JSON text


def _text(v):
    return "" if v is None else cell(v)


def columns(doc):
    """The columns in file order: [(field_id, field_name, data_type)]. A field a record holds but the fields list does not is added at the end, in sorted order."""
    cols, seen = [], set()
    for f in doc["fields"]:
        fid = f["field_id"]
        if fid in seen:
            continue
        seen.add(fid)
        cols.append((fid, _text(f.get("field_name")) or fid, _text(f.get("data_type"))))
    extra = set()
    for r in doc["records"]:
        vals = r.get("fields")
        if isinstance(vals, dict):
            extra.update(k for k in vals if k not in seen)
    return cols + [(k, k, OUT_OF_LIST) for k in sorted(extra)]


def file_name(doc):
    """A Windows-safe name: table_id, the table name, the capture date. Letters, digits, underscore and hyphen only (so it is also safe in a download header)."""
    def clean(s, n):
        s = unicodedata.normalize("NFKD", _text(s)).encode("ascii", "ignore").decode("ascii")
        return re.sub(r"[^A-Za-z0-9]+", "_", s).strip("_")[:n]
    tid, tname = clean(doc.get("table_id"), 30), clean(doc.get("table_name"), MAX_NAME_LEN)
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})", _text(doc.get("captured_at")))
    date = "".join(m.groups()) if m else ""
    stem = "_".join(p for p in (tid, tname, date) if p) or "airtable_extract"
    return stem + ".csv"


def convert(doc):
    """The parsed extract -> (csv bytes, summary). The summary holds counts and warnings only, never a value."""
    cols = columns(doc)
    records = doc["records"]
    warnings = []
    stated = doc.get("record_count")
    if isinstance(stated, int) and not isinstance(stated, bool) and stated != len(records):
        warnings.append("The file says record_count is %d but it holds %d records. The CSV shows what the file says in its facts rows, and one row for each record actually present." % (stated, len(records)))
    n_extra = sum(1 for c in cols if c[2] == OUT_OF_LIST)
    if n_extra:
        warnings.append("%d field(s) appear in records but not in the fields list; they are at the end, headed by their field ID." % n_extra)
    if len({c[1] for c in cols}) != len(cols):
        warnings.append("Two or more fields share a name. Each keeps its own column; the field_id row above the headings tells them apart.")
    out = io.StringIO(newline="")
    w = csv.writer(out, lineterminator="\r\n", quoting=csv.QUOTE_MINIMAL)
    for label, key in (("format", "format"), ("base_id", "base_id"), ("table_id", "table_id"), ("table_name", "table_name"), ("captured_at", "captured_at"), ("record_count", "record_count")):
        w.writerow([label, _text(doc.get(key))])
    w.writerow(["field_count", len(cols)])
    w.writerow([])
    w.writerow([])
    w.writerow(["field_id", ""] + [c[0] for c in cols])
    w.writerow(["data_type", ""] + [c[2] for c in cols])
    w.writerow(["record_id", "createdTime"] + [c[1] for c in cols])
    for r in records:
        vals = r.get("fields") if isinstance(r.get("fields"), dict) else {}
        w.writerow([_text(r.get("id")), _text(r.get("createdTime"))] + [cell(vals.get(c[0])) for c in cols])
    data = BOM + out.getvalue().encode("utf-8")
    return data, {"file_name": file_name(doc), "record_count": len(records), "field_count": len(cols), "warnings": warnings}


def convert_b64(json_base64):
    """The HTTP form: the file's bytes as base64 -> (csv bytes, summary)."""
    try:
        raw = base64.b64decode(json_base64 or "", validate=True)
    except (ValueError, TypeError):
        raise ExtractError("The upload could not be read. Choose the file again.")
    return convert(parse(raw))
