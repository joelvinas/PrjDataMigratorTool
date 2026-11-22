"""Tests for calcforms.py (the list of valid custom forms: template and upload). Fabricated form IDs and names; no server or database."""
import base64, os, sys
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "src"))
import calcforms as cf
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass
ok = True
def check(n, c, x=""):
    global ok; ok &= bool(c); print(("PASS " if c else "FAIL ") + n, "" if c else str(x)[:400])
def up(text): return cf.parse_upload(base64.b64encode(text.encode("utf-8")).decode())
H = "category_id,name\r\n"

forms, probs = up(H + "AAA111,Special Order Sales\r\nBBB222,In Store Sales\r\n")
check("a good file loads in file order", probs == [] and forms == [{"category_id": "AAA111", "name": "Special Order Sales"}, {"category_id": "BBB222", "name": "In Store Sales"}], (forms, probs))
check("the template is the header plus one example row, and loads", cf.template_csv().splitlines()[0] == "category_id,name" and len(cf.template_csv().splitlines()) == 2 and up(cf.template_csv())[1] == [])
forms, probs = up("﻿" + H + "AAA111,Form A\r\n")
check("an Excel UTF-8 file with a byte order mark loads", probs == [] and len(forms) == 1, probs)
forms, probs = up(H + "AAA111,Form A\r\n\r\n,\r\nBBB222,Form B\r\n")
check("blank lines are ignored", probs == [] and len(forms) == 2, probs)
forms, probs = up(H + " AAA111 , Form A \r\n")
check("spaces around a cell are trimmed", forms == [{"category_id": "AAA111", "name": "Form A"}])
forms, probs = up(H + 'AAA111,"Sales, Special"\r\n')
check("a comma inside quotes is part of the name", forms[0]["name"] == "Sales, Special")
forms, probs = up(H + "AAA 111,Form A\r\nB-B,Form B\r\nCCC,\r\nDDD,Ok\r\nDDD,Again\r\nEEE,a;b,extra\r\n")
check("every problem is listed with its row and column, and nothing loads", forms == [] and sorted((p["row"], p["column"]) for p in probs) == [(2, "category_id"), (3, "category_id"), (4, "name"), (6, "category_id"), (7, "")], probs)
check("a repeated ID names the row it repeats", "row 5" in [p for p in probs if p["row"] == 6][0]["message"])
for text, word in (("", "empty"), (H, "no rows"), ("id,name\r\nA,B\r\n", "first line"), ("name,category_id\r\nA,B\r\n", "first line"), ("Category_Id,name\r\nA,B\r\n", "first line")):
    f, p = up(text)
    check("refused: " + word, f == [] and len(p) == 1 and p[0]["row"] == 0 and word in p[0]["message"].lower(), p)
check("a file that is not base64 is refused", cf.parse_upload("%%%")[1][0]["message"].startswith("The upload was not"))
check("a file that is not UTF-8 is refused in plain words", "UTF-8" in cf.parse_upload(b"\xff\xfe\x00bad")[1][0]["message"])
check("too many rows are refused", "most allowed" in up(H + "".join("A%d,N\r\n" % i for i in range(cf.MAX_ROWS + 1)))[1][0]["message"])
check("a name with a control character is refused", up(H + "AAA,bad\tname\r\n")[1][0]["column"] == "name")
print("OK" if ok else "FAILED")
sys.exit(0 if ok else 1)
