"""Tests for calclex.py (the Airtable formula lexer). Fabricated formulas only; no server, database or network."""
import ast, os, sys
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "src"))
import calclex as cl
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ok = True
def check(n, c, x=""):
    global ok; ok &= bool(c); print(("PASS " if c else "FAIL ") + n, "" if c else str(x)[:400])

def kv(text):
    return [(t.kind, t.value) for t in cl.lex(text)]

# ---- the commission example
check("a reference, an operator and a number", kv("{fldBase01}*0.15") == [("FIELD", "fldBase01"), ("OP", "*"), ("NUMBER", "0.15")], kv("{fldBase01}*0.15"))
t = cl.lex("IF({fldTot} < 200, {fldTot}*0.03, {fldTot}*0.07)")
check("IF with three arguments", [x.kind for x in t][:3] == ["FUNC", "LPAREN", "FIELD"] and [x.value for x in t if x.kind == "NUMBER"] == ["200", "0.03", "0.07"], t)
check("references are sorted and unique", cl.field_references(cl.lex("{fldB}+{fldA}+{fldB}")) == ["fldA", "fldB"])
check("a leading dot number", kv(".5+1") == [("NUMBER", ".5"), ("OP", "+"), ("NUMBER", "1")])

# ---- whitespace and line breaks
f = "IF(\n  {fldA} > 1,\r\n\t\"yes\",   \"no\"\n)"
check("line breaks and tabs are only layout", [x.kind for x in cl.lex(f)] == [x.kind for x in cl.lex("IF({fldA} > 1, \"yes\", \"no\")")])
check("normalize closes up layout", cl.normalize(f) == 'IF( {fldA} > 1, "yes", "no" )', cl.normalize(f))
check("normalize keeps spaces inside a string", cl.normalize('CONCATENATE( "a   b" ,  {fldA} )') == 'CONCATENATE( "a   b" , {fldA} )')
check("normalize keeps a brace reference whole", cl.normalize("{fld A}  +  1") == "{fld A} + 1")

# ---- strings and JSON escaping
check("single and double quotes", kv("'a'&\"b\"") == [("STRING", "a"), ("OP", "&"), ("STRING", "b")])
check("a quoted pipe separator", kv('{fldA} & "|" & {fldB}')[2] == ("STRING", "|"))
check("a backslash escapes a quote inside a string", kv('"say \\"hi\\""') == [("STRING", 'say "hi"')], kv('"say \\"hi\\""'))
check("a doubly encoded text (no bare quote) is unescaped first", kv('CONCATENATE({fldA}, \\"|\\", {fldB})')[4] == ("STRING", "|"), kv('CONCATENATE({fldA}, \\"|\\", {fldB})'))
check("unescape_json leaves a mixed text alone", cl.unescape_json('"a" & \\"b') == '"a" & \\"b')
check("unescape_json leaves a text without escapes alone", cl.unescape_json('"a"') == '"a"')
try:
    cl.lex('x & "abc'); e = None
except cl.LexError as ex:
    e = ex
check("unclosed string reports where", e is not None and e.pos == 4, e)

# ---- functions and names
check("a function name is upper-cased and followed by a bracket", kv("concatenate(1)")[0] == ("FUNC", "CONCATENATE"))
check("a function with a space before the bracket is still a function", kv("IF (1,2,3)")[0] == ("FUNC", "IF"))
check("a bare word is an identifier", kv("TRUE") == [("IDENT", "TRUE")])
check("function_names sorted unique", cl.function_names(cl.lex("IF(AND(1,1),ROUND(2,0),ROUND(3,0))")) == ["AND", "IF", "ROUND"])
check("TODAY() with no arguments", kv("TODAY()") == [("FUNC", "TODAY"), ("LPAREN", "("), ("RPAREN", ")")])

# ---- operators
check("two-character operators", [v for k, v in kv("1<=2 >= 3 != 4 <> 5 && 6 || 7")if k == "OP"] == ["<=", ">=", "!=", "<>", "&&", "||"])
check("single operators", [v for k, v in kv("1+2-3*4/5&6=7<8>9") if k == "OP"] == list("+-*/&=<>"))

# ---- references written as names
tk = cl.lex("{Base Price}*2+{fldAbc12}")
check("an ID reference and a name reference are told apart", cl.field_references(tk) == ["fldAbc12"] and cl.name_references(tk) == ["Base Price"], (cl.field_references(tk), cl.name_references(tk)))
for bad, why in (("{}", "empty reference"), ("{fldA", "unclosed reference"), ("1 # 2", "unknown character"), ("1 ; 2", "unknown character")):
    try:
        cl.lex(bad); raised = False
    except cl.LexError:
        raised = True
    check("error: " + why + " (%s)" % bad, raised)
try:
    cl.lex("secretword # x"); msg = ""
except cl.LexError as ex:
    msg = str(ex)
check("the error says the position, not the text", "secretword" not in msg and "character 11" in msg, msg)

# ---- inventory
inv = cl.inventory([("fldB", "ROUND({fldX},2)+LEN({fldY})"), ("fldA", "IF(1,ROUND(2,0),3)"), ("fldC", "this is # not valid")])
check("inventory counts calls, lists formulas, sorted, skips what does not lex", inv == [("IF", 1, ["fldA"]), ("LEN", 1, ["fldB"]), ("ROUND", 2, ["fldA", "fldB"])], inv)
check("same input, same output", cl.inventory([("a", "IF(1,2,3)")]) == cl.inventory([("a", "IF(1,2,3)")]))

# ---- no model, no network, no clock, no randomness: only these standard modules may be imported
src = open(os.path.join(ROOT, "src", "calclex.py"), encoding="utf-8").read()
imported = set()
for node in ast.walk(ast.parse(src)):
    if isinstance(node, ast.Import):
        imported |= {a.name.split(".")[0] for a in node.names}
    elif isinstance(node, ast.ImportFrom):
        imported.add((node.module or "").split(".")[0])
check("the lexer imports only re", imported <= {"re"}, imported)

print("OK" if ok else "FAILED")
sys.exit(0 if ok else 1)
