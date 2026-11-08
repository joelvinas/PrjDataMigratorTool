#!/usr/bin/env python3
"""Check this app's load-order logic against an answer key (optional; stdlib only).

    python test/answer_key_check.py PATH_TO/out/answer_key [scratch.db]

Expects expected_profile.csv, expected_link_treatment.csv, expected_load_order.csv (Resolve mode) and
expected_load_order_literal.csv (Literal mode) in that folder. It rebuilds an Airtable-metadata-shaped schema from
the key (table, field and link definitions only), loads it into a scratch database, and compares load number, depth
and second-pass treatment in both modes. It does not call Airtable and writes no record data."""
import collections, csv, os, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import server  # noqa: E402


def main():
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    key_dir = sys.argv[1]
    db = os.path.abspath(sys.argv[2]) if len(sys.argv) > 2 else os.path.join(ROOT, "answer_key_scratch.db")
    rd = lambda n: list(csv.DictReader(open(os.path.join(key_dir, n), encoding="utf-8-sig")))
    prof, links = rd("expected_profile.csv"), rd("expected_link_treatment.csv")
    bases = {b: "app%s%06d" % ("KEY", i) for i, b in enumerate(sorted({p["base"] for p in prof}), 1)}
    fid = {(p["base"], p["table"], p["field"]): p["local_field_id"] for p in prof}
    tid = {(p["base"], p["table"]): p["local_table_id"] for p in prof}
    lk = {(l["base"], l["table"], l["field"]): l for l in links}
    schemas = {b: collections.OrderedDict() for b in bases}
    for p in prof:
        if p["field_exists"] == "no":
            continue
        t = schemas[p["base"]].setdefault(p["table"], {"id": p["local_table_id"], "name": p["table"], "fields": []})
        f = {"id": p["local_field_id"], "name": p["field"], "type": p["field_type"]}
        if p["field_type"] == "multipleRecordLinks":
            l = lk[(p["base"], p["table"], p["field"])]
            f["options"] = {"linkedTableId": tid[(p["base"], l["links_to_table"])],
                            "inverseLinkFieldId": fid[(p["base"], l["links_to_table"], l["inverse_field"])],
                            "prefersSingleRecordLink": l["prefers_single_record"] == "yes"}
        t["fields"].append(f)
    if os.path.exists(db):
        os.remove(db)
    server.api_db_create({"path": db})
    server.api_db_connect({"path": db})
    for b, s in schemas.items():
        server.upsert_schema(bases[b], server.parse_schema({"tables": list(s.values())}), "json")
    inv = {v: k for k, v in bases.items()}
    bad = checked = 0
    for mode, order_file, pass_col in (("collapse", "expected_load_order.csv", "second_pass_resolve_mode"),
                                       ("literal", "expected_load_order_literal.csv", "second_pass_literal_mode")):
        with server.db() as c:
            g = server.compute_graph(c, mode)
        exp = {(r["base"], r["table"]): r for r in rd(order_file)}
        seq = collections.Counter()
        for m in g["matrix"]:
            b = inv[m["base_id"]]
            seq[b] += 1
            e = exp[(b, m["table_name"])]
            checked += 2
            if int(e["load_no_within_base"]) != seq[b]:
                bad += 1; print("FAIL %s %s.%s load_no key %s mine %s" % (mode, b, m["table_name"], e["load_no_within_base"], seq[b]))
            if int(e["depth"]) != m["dependency_depth"]:
                bad += 1; print("FAIL %s %s.%s depth key %s mine %s" % (mode, b, m["table_name"], e["depth"], m["dependency_depth"]))
        mine = {(inv[x["base_id"]], x["table_name"], x["field_name"]): x for x in g["link_fields"]}
        for l in links:
            m = mine.get((l["base"], l["table"], l["field"]))
            if m is None:
                continue
            checked += 1
            if (l[pass_col] == "Yes") != m["second_pass"]:
                bad += 1; print("FAIL %s %s.%s.%s second pass key %s mine %s" % (mode, l["base"], l["table"], l["field"], l[pass_col], m["second_pass"]))
    os.remove(db)
    print("checked=%d fail=%d" % (checked, bad))
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
