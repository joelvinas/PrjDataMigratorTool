"""Tests for calcparse.py (formula parser) and calcxlate.py (formula translator). Fabricated formulas and names only; no server, database or network."""
import ast, os, sys
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "src"))
import calcparse as cp, calcxlate as cx
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ok = True
def check(n, c, x=""):
    global ok; ok &= bool(c); print(("PASS " if c else "FAIL ") + n, "" if c else str(x)[:500])

NAMES = {"fldBase": "base_price", "fldTot": "total", "fldA": "FieldA", "fldB": "FieldB", "fldFlag": "flag", "fldSp": "Has Space"}
def R(fid): return NAMES.get(fid)
def T(text): return cx.translate(text, R)

# ---- parser
check("precedence: * binds tighter than +", cp.parse("1+2*3") == ("bin", "+", ("num", "1"), ("bin", "*", ("num", "2"), ("num", "3"))))
check("left to right: 1-2-3", cp.parse("1-2-3") == ("bin", "-", ("bin", "-", ("num", "1"), ("num", "2")), ("num", "3")))
check("brackets group", cp.parse("(1+2)*3") == ("bin", "*", ("bin", "+", ("num", "1"), ("num", "2")), ("num", "3")))
check("a call with arguments", cp.parse("IF({fldA}, 1, 2)") == ("call", "IF", [("ref", "fldA", "id"), ("num", "1"), ("num", "2")]))
check("a call with no arguments", cp.parse("TODAY()") == ("call", "TODAY", []))
check("unary minus", cp.parse("-{fldA}") == ("neg", ("ref", "fldA", "id")))
check("walk lists every node", len(list(cp.walk(cp.parse("IF(1,2+3,4)")))) == 6)
for bad, why in (("", "empty"), ("1 +", "ends early"), ("(1", "unclosed bracket"), ("IF(1,", "unclosed call"), ("1 2", "two values"), ("1 )", "stray bracket"), ("IF(,1)", "empty argument"), ("1 # 2", "unknown character")):
    try:
        cp.parse(bad); e = None
    except cp.ParseError as ex:
        e = ex
    check("parse error: " + why, e is not None and isinstance(e.pos, int), bad)
try:
    cp.parse("secretword # 1"); msg = ""
except cp.ParseError as ex:
    msg = str(ex)
check("an error gives a position, not the formula", "secretword" not in msg and "character" in msg, msg)
try:
    cp.parse("(" * 100 + "1" + ")" * 100); deep = False
except cp.ParseError:
    deep = True
check("very deep nesting is refused, not a crash", deep)

# ---- the owner's examples
r = T("{fldBase}*0.15")
check("Special Order commission: {DE:base_price}*0.15", r.status == "TRANSLATABLE" and r.expression == "{DE:base_price}*0.15", (r.status, r.expression, r.reason))
r = T("IF({fldTot} < 200, {fldTot}*0.03, {fldTot}*0.07)")
check("In-store commission with IF", r.expression == "IF({DE:total}<200,{DE:total}*0.03,{DE:total}*0.07)", r.expression)
check("references and functions are recorded", r.references == ["fldTot"] and r.functions == ["IF"], (r.references, r.functions))

# ---- precedence is kept with the fewest brackets
check("brackets kept where needed", T("({fldA}+{fldB})*2").expression == "({DE:FieldA}+{DE:FieldB})*2", T("({fldA}+{fldB})*2").expression)
check("brackets dropped where not needed", T("({fldA}*{fldB})+2").expression == "{DE:FieldA}*{DE:FieldB}+2")
check("right side of a minus keeps its brackets", T("{fldA}-({fldB}-1)").expression == "{DE:FieldA}-({DE:FieldB}-1)", T("{fldA}-({fldB}-1)").expression)
check("a left chain loses them", T("({fldA}-{fldB})-1").expression == "{DE:FieldA}-{DE:FieldB}-1")
check("unary minus on a sum", T("-({fldA}+1)").expression == "-({DE:FieldA}+1)")
check("a division by a sum", T("{fldA}/({fldB}+1)").expression == "{DE:FieldA}/({DE:FieldB}+1)")

# ---- the direct set
check("CONCATENATE becomes CONCAT", T('CONCATENATE({fldA}, "-", {fldB})').expression == 'CONCAT({DE:FieldA},"-",{DE:FieldB})', T('CONCATENATE({fldA}, "-", {fldB})').expression)
check("& chain becomes one CONCAT", T('{fldA} & "|" & {fldB}').expression == 'CONCAT({DE:FieldA},"|",{DE:FieldB})', T('{fldA} & "|" & {fldB}').expression)
check("a quoted pipe separator survives", '"|"' in T('CONCATENATE({fldA}, "|", {fldB})').expression)
check("& after a function call", T('UPPER({fldA}) & "-6"').expression == 'CONCAT(UPPER({DE:FieldA}),"-6")', T('UPPER({fldA}) & "-6"').expression)
check("SUM AND OR TODAY", T("SUM({fldA},{fldB},1)").expression == "SUM({DE:FieldA},{DE:FieldB},1)" and T("AND(1,0)").expression == "AND(1,0)" and T("OR(1,0)").expression == "OR(1,0)" and T("TODAY()").expression == "TODAY()")
check("&& and || become AND and OR", T("{fldA} && {fldB}").expression == "AND({DE:FieldA},{DE:FieldB})" and T("{fldA} || {fldB} || 1").expression == "OR({DE:FieldA},{DE:FieldB},1)")
check("LEN, ROUND, UPPER", T("LEN({fldA})+5").expression == "LEN({DE:FieldA})+5" and T("ROUND({fldA}*1.5+4, 2)").expression == "ROUND({DE:FieldA}*1.5+4,2)" and T("UPPER({fldA})").expression == "UPPER({DE:FieldA})")
check("comparison operators; <> becomes !=", T("IF({fldA}<>1,1,0)").expression == "IF({DE:FieldA}!=1,1,0)" and T("IF({fldA}>=1,1,0)").expression == "IF({DE:FieldA}>=1,1,0)")
check("X = BLANK() becomes ISBLANK(X), either way round", T("IF({fldA}=BLANK(),1,0)").expression == "IF(ISBLANK({DE:FieldA}),1,0)" and T("IF(BLANK()={fldA},1,0)").expression == "IF(ISBLANK({DE:FieldA}),1,0)")
check("a warning when CONCAT takes more than two parts", cx.CONCAT_NOTE in T('CONCATENATE({fldA}, "-", {fldB})').warnings and cx.CONCAT_NOTE not in T("{fldA} & {fldB}").warnings)

# ---- blocked: unsupported
for text, word in (("DATETIME_DIFF({fldA},{fldB},'days')", "DATETIME_DIFF"), ("DATETIME_FORMAT({fldA},'LL')", "format"), ("WORKDAY_DIFF({fldA},{fldB})", "WORKDAY_DIFF"),
                   ("WEEKNUM({fldA})", "WEEKNUM"), ("REGEX_MATCH({fldA},'x')", "REGEX_MATCH"), ("IF({fldA}, 1)", "IF with 2 arguments"), ("IF({fldA}!=BLANK(),1,0)", "not-blank"),
                   ("IF({fldA}=0,BLANK(),1)", "BLANK()"), ("TRUE", "TRUE"), ("1 + $", "could not be read")):
    r = T(text)
    check("unsupported: %s" % text, r.status == "BLOCKED_UNSUPPORTED_FUNCTION" and r.expression is None and word in r.reason, (r.status, r.reason))
check("a manual-review function gives its reason", "unit argument" in T("DATETIME_DIFF({fldA},{fldB},'days')").reason)
check("functions are listed even when blocked", T("IF({fldA}, DATETIME_DIFF(1,2,'d'), 0)").functions == ["DATETIME_DIFF", "IF"])
check("a string with a quote is not guessed", T("'say \"hi\"'").status == "BLOCKED_UNSUPPORTED_FUNCTION")

# ---- blocked: missing reference
r = T("{fldBase}*{fldNope}")
check("missing target field", r.status == "BLOCKED_MISSING_REFERENCE" and r.missing == ["fldNope"] and r.expression is None and "fldNope" in r.reason and r.references == ["fldBase", "fldNope"], (r.status, r.reason, r.references))
r = T("{Base Price}*2")
check("a reference by name is a missing reference", r.status == "BLOCKED_MISSING_REFERENCE" and r.missing == ["Base Price"])
r = T("{fldNope}+DATETIME_DIFF(1,2,'d')")
check("unsupported wins, and the reason also names the missing target", r.status == "BLOCKED_UNSUPPORTED_FUNCTION" and "fldNope" in r.reason, r.reason)
check("a name with a space still translates, with a warning", T("{fldSp}+1").expression == "{DE:Has Space}+1" and any("Has Space" in w for w in T("{fldSp}+1").warnings))

# ---- deterministic
check("same input, same output", T("IF({fldA}>1,{fldB},0)").expression == T("IF({fldA}>1,{fldB},0)").expression)
check("layout does not change the result", T("IF(\n {fldA} > 1 ,\r\n {fldB}, 0)").expression == T("IF({fldA}>1,{fldB},0)").expression)

# ---- dependency order
order, cyc = cx.order_formulas({"c": {"a", "b"}, "b": {"a", "plain"}, "a": set()})
check("a formula comes after what it reads; plain fields are ignored", order == ["a", "b", "c"] and cyc == [], (order, cyc))
order, cyc = cx.order_formulas({"z": set(), "y": set(), "x": {"y"}})
check("independent formulas go first in sorted order, then what reads them", order == ["y", "z", "x"], order)
order, cyc = cx.order_formulas({"p": {"q"}, "q": {"p"}, "r": {"p"}, "s": set()})
check("a loop and what reads it are reported, the rest is ordered", order == ["s"] and cyc == ["p", "q", "r"], (order, cyc))
check("a formula that reads itself is a loop", cx.order_formulas({"a": {"a"}})[1] == ["a"])
check("the order is repeatable", cx.order_formulas({"b": {"a"}, "a": set(), "c": set()}) == cx.order_formulas({"c": set(), "a": set(), "b": {"a"}}))

# ---- shapes seen in a sample schema translate, or block for a stated reason
real = [("ROUND(({fldA}-{fldB})/{fldA},3)", "TRANSLATABLE"), ("IF({fldA}=0,BLANK(),ROUND(({fldA}-{fldB})/{fldA},3))", "BLOCKED_UNSUPPORTED_FUNCTION"),
        ("{fldA} * {fldB}", "TRANSLATABLE"), ('CONCATENATE({fldA}, "-", {fldB}, "-2")', "TRANSLATABLE"), ('IF({fldFlag}, "yes-3", "no-3")', "TRANSLATABLE")]
check("sample shapes", all(T(t).status == s for t, s in real), [(t, T(t).status) for t, s in real if T(t).status != s])
check("ROUND with a nested division keeps its brackets", T("ROUND(({fldA}-{fldB})/{fldA},3)").expression == "ROUND(({DE:FieldA}-{DE:FieldB})/{DE:FieldA},3)", T("ROUND(({fldA}-{fldB})/{fldA},3)").expression)

# ---- the modules import nothing that could reach a network or a model, tell the time or draw a random number
for mod, allowed in (("calcparse.py", {"calclex"}), ("calcxlate.py", {"calcparse"})):
    imported = set()
    for node in ast.walk(ast.parse(open(os.path.join(ROOT, "src", mod), encoding="utf-8").read())):
        if isinstance(node, ast.Import):
            imported |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom):
            imported.add((node.module or "").split(".")[0])
    check("%s imports only %s" % (mod, sorted(allowed)), imported <= allowed, imported)

print("OK" if ok else "FAILED")
sys.exit(0 if ok else 1)
