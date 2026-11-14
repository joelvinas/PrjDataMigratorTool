#!/usr/bin/env python3
"""Run every regression suite against the mock Airtable, each in a throwaway copy of the project.

    python test/run_regression.py            # all suites
    python test/run_regression.py e2e2       # one suite (e2e2 also runs report_test, which needs its database)

Needs only the standard library (ui_test also needs Playwright and is skipped without it). Ports 8765 (app) and 9100
(mock) must be free. Nothing in your own project folder, config.json or databases is touched. Prints one line per
suite and exits 1 if any check failed."""
import os, shutil, signal, socket, subprocess, sys, tempfile, time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TESTS = os.path.join(ROOT, "test", "regression")
TOKEN = "patMOCK.0000000000000000000000"
# (name, scripts run in order against ONE server, MOCK_FILES)
SUITES = [("e2e2", ["e2e2", "report_test"], "0"), ("views", ["views_test"], "0"), ("forms", ["forms_test"], "0"), ("automations", ["automations_test"], "0"), ("extract", ["extract_test"], "0"),
          ("attach", ["attach_test"], "1"), ("attstats", ["attstats_test"], "1"), ("ui", ["ui_test"], "1"), ("formsui", ["forms_ui_test"], "0"), ("autoui", ["automations_ui_test"], "0"), ("names", ["fieldnames_test", "fieldnames_io_test", "fieldnames_store_test", "fielddesc_test", "emoji_test"], "0"), ("namesapi", ["names_api_test"], "0")]


def wait_port(port, secs=10):
    end = time.time() + secs
    while time.time() < end:
        with socket.socket() as s:
            s.settimeout(0.3)
            if s.connect_ex(("127.0.0.1", port)) == 0:
                return True
        time.sleep(0.2)
    return False


def run_suite(scripts, mock_files):
    work = tempfile.mkdtemp(prefix="regress_")
    proj = os.path.join(work, "p")
    shutil.copytree(ROOT, proj, ignore=shutil.ignore_patterns("__pycache__", "*.db", "config.json", "data_extract", ".git", "db"))
    with open(os.path.join(proj, "config.json"), "w") as f:
        f.write('{"token":"%s","scopes":["data.records:read","schema.bases:read"],"bases":[{"name":"Sample Shop","baseId":"appSampleBase001"}],"db_path":"db/migration.db","requests_per_second":5}' % TOKEN)
    env = dict(os.environ, MOCK_FILES=mock_files, MOCK_PORT="9100", AIRTABLE_API_BASE="http://127.0.0.1:9100", AT_BACKOFF_BASE="0.2")
    procs = [subprocess.Popen([sys.executable, "test/mock_airtable.py"], cwd=proj, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL),
             subprocess.Popen([sys.executable, "server.py"], cwd=proj, env=env, stdout=open(os.path.join(proj, "server.log"), "w"), stderr=subprocess.STDOUT)]
    results = []
    try:
        if not (wait_port(9100) and wait_port(8765)):
            return [("startup", 0, 1, "server or mock did not start (are ports 8765 and 9100 free?)")]
        for name in scripts:
            p = subprocess.run([sys.executable, os.path.join(TESTS, name + ".py")], cwd=proj, env=env, capture_output=True, text=True, timeout=300)
            out = p.stdout + p.stderr
            lines = out.splitlines()
            skipped = any(l.startswith("SKIP") for l in lines)
            fails = [l for l in lines if l.startswith("FAIL")]
            passes = sum(1 for l in lines if l.startswith("PASS"))
            bad = len(fails) + (1 if (p.returncode != 0 and not skipped and not fails) else 0)
            results.append((name, passes, bad, "SKIPPED: " + [l for l in lines if l.startswith("SKIP")][0][5:] if skipped else "\n".join(fails[:6]) or (lines[-1] if bad else "")))
    finally:
        for pr in procs:
            pr.terminate()
        for pr in procs:
            try:
                pr.wait(5)
            except subprocess.TimeoutExpired:
                pr.kill()
        shutil.rmtree(work, ignore_errors=True)
    return results


def main():
    want = set(sys.argv[1:])
    total_bad = 0
    for key, scripts, mf in SUITES:
        if want and key not in want and not (want & set(scripts)):
            continue
        for name, ok, bad, note in run_suite(scripts, mf):
            total_bad += bad
            print("%-14s pass=%-3d fail=%d %s" % (name, ok, bad, note))
    print("ALL PASS" if not total_bad else "FAILURES: %d" % total_bad)
    sys.exit(1 if total_bad else 0)


if __name__ == "__main__":
    main()
