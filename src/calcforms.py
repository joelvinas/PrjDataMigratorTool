#!/usr/bin/env python3
"""The list of valid custom forms (CTGY) for the calculated fields page. Standard library only.

A person uploads a CSV with two columns, spelled exactly: category_id, name. The tool only ever reads or writes a form whose ID is on this list (the page's form picker, the
read-only form check and the push are all gated by it), so a mistyped or unintended form ID can never be touched. Plain functions, tested without a server:

    template_csv()           the header and one example row, ready to fill in
    parse_upload(b64|bytes)  -> (forms, problems)   forms: [{"category_id", "name"}] in file order; any problem refuses the whole file, with a row number for each

No record value, no credential: form IDs and names only.
"""
import base64
import binascii
import csv
import io
import re

HEADER = ["category_id", "name"]
ID_RE = re.compile(r"^[A-Za-z0-9]{1,64}$")
MAX_ROWS = 2000
MAX_NAME = 120


def template_csv():
    """The header and one dummy row (replace or delete it), CRLF."""
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\r\n")
    w.writerow(HEADER)
    w.writerow(["0123456789abcdef0123456789abcdef", "Example form name"])
    return buf.getvalue()


def parse_upload(b64_or_bytes):
    """(forms, problems). `problems` is a list of {"row", "column", "message"} (row 0 = the file itself). A file with any problem loads nothing."""
    if isinstance(b64_or_bytes, str):
        try:
            raw = base64.b64decode(b64_or_bytes, validate=True)
        except (binascii.Error, ValueError):
            return [], [{"row": 0, "column": "", "message": "The upload was not a readable file."}]
    else:
        raw = b64_or_bytes or b""
    if not raw.strip():
        return [], [{"row": 0, "column": "", "message": "The file is empty."}]
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        return [], [{"row": 0, "column": "", "message": "The file is not UTF-8 text. In Excel, save it as CSV UTF-8."}]
    try:
        table = list(csv.reader(io.StringIO(text, newline="")))
    except csv.Error as e:
        return [], [{"row": 0, "column": "", "message": "The file is not a readable CSV: %s." % e}]
    table = [r for r in table if any(c.strip() for c in r)]
    if not table or [c.strip() for c in table[0]] != HEADER:
        return [], [{"row": 0, "column": "", "message": "The first line must be exactly: %s (lower case, in this order)." % ",".join(HEADER)}]
    body = table[1:]
    if not body:
        return [], [{"row": 0, "column": "", "message": "The file has a header but no rows."}]
    if len(body) > MAX_ROWS:
        return [], [{"row": 0, "column": "", "message": "The file has %d rows; the most allowed is %d." % (len(body), MAX_ROWS)}]
    forms, problems, seen = [], [], {}
    for i, r in enumerate(body):
        n = i + 2                                    # the row number as the person sees it in a spreadsheet (the header is row 1)
        if len(r) != 2:
            problems.append({"row": n, "column": "", "message": "Expected 2 columns, found %d." % len(r)})
            continue
        cid, name = r[0].strip(), r[1].strip()
        if not ID_RE.match(cid):
            problems.append({"row": n, "column": "category_id", "message": "A form ID is letters and digits only (up to 64)."})
        elif cid in seen:
            problems.append({"row": n, "column": "category_id", "message": "The same ID as row %d." % seen[cid]})
        else:
            seen[cid] = n
        if not name or len(name) > MAX_NAME or any(ord(c) < 32 for c in name):
            problems.append({"row": n, "column": "name", "message": "The name must be plain text of 1 to %d characters." % MAX_NAME})
        forms.append({"category_id": cid, "name": name})
    return ([], problems) if problems else (forms, [])
