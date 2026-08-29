#!/usr/bin/env python3
"""Pin: vv rows say whether they came from a benchmark, and the report uses it.

The pilot's keep/kill question is "did a HUMAN-driven session use this", but a
benchmark loop writes rows indistinguishable from an agent session. On
2026-08-27 that made the report claim `adoption: 100%` over 118,726 ops of which
99% were our own benchmark traffic — a triumphant number measuring the
instrument.

So invocations now declare themselves (VV_METRICS_SRC -> the row's `src` field)
and the report separates by provenance FIRST, falling back to an arrival-rate
heuristic only for unmarked traffic — and reporting how much of that it saw, so
a forgotten mark is visible rather than silent.

The label reaches a record that the native engine builds by STRING FORMATTING,
so it is sanitised to [A-Za-z0-9_-]; an unescaped quote would corrupt every
subsequent row in the log.

The report must also keep its two input cohorts distinct. A 2026-08-28 pilot
read-out rebound the loaded legacy-route rows to the pre-provenance rate-burst
cohort, turning 250 real legacy operations into 1,552 synthetic `backlinks`
operations and changing adoption from 80% to 39%.
"""
import json, os, shutil, subprocess, sys, tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VR = os.path.join(REPO, "vrust/target/release/vrust")
VV = os.path.join(REPO, "src/vv.py")
sys.path.insert(0, os.path.join(REPO, "bench"))


def main():
    if not os.path.exists(VR):
        print("SKIP: binary not built"); return 0
    fails = []
    checks_run = 0
    def check(lbl, ok, info=""):
        nonlocal checks_run
        checks_run += 1
        print(("PASS " if ok else "FAIL ") + lbl + ("" if ok else f"  [{str(info)[:180]}]"))
        if not ok: fails.append(lbl)

    vault = tempfile.mkdtemp(prefix="vv-prov-v-")
    home = tempfile.mkdtemp(prefix="vv-prov-h-")
    os.makedirs(os.path.join(home, ".claude/metrics"))
    open(f"{vault}/A.md", "w").write("# A\nbody\n")
    log = os.path.join(home, ".claude/metrics/vv.jsonl")

    def run(argv, src=None, native=True, extra=None):
        env = dict(os.environ, HOME=home, VV_VAULT=vault)
        env.pop("VV_NO_METRICS", None); env.pop("VV_JOURNAL_ROOT", None)
        env.pop("VV_METRICS_SRC", None)
        if src is not None: env["VV_METRICS_SRC"] = src
        env.update(extra or {})
        cmd = [VR] + argv if native else [sys.executable, VV] + argv
        return subprocess.run(cmd, capture_output=True, env=env, cwd=vault)

    def rows():
        if not os.path.exists(log): return []
        return [json.loads(l) for l in open(log) if l.strip()]

    # --- unmarked usage carries no label ------------------------------------
    run(["outline", "A.md"]); run(["read", "A.md"], native=False)
    r = rows()
    check("both engines log unmarked ordinary usage", len(r) == 2, r)
    check("ordinary usage has no src field", all("src" not in x for x in r), r)

    # --- marked traffic is labelled, by BOTH engines -------------------------
    run(["outline", "A.md"], src="bench")
    run(["read", "A.md"], src="bench", native=False)
    r = rows()[2:]
    check("native engine writes src", any(x.get("src") == "bench" and
          x.get("engine") == "native" for x in r), r)
    check("python engine writes src", any(x.get("src") == "bench" and
          x.get("engine") != "native" for x in r), r)

    # --- the label cannot corrupt the log -----------------------------------
    run(["outline", "A.md"], src='ev"il, "op": "pwned')
    run(["read", "A.md"], src='ev"il" x', native=False)
    raw = [l for l in open(log) if l.strip()]
    bad = 0
    for l in raw:
        try: json.loads(l)
        except Exception: bad += 1
    check("a quote in the label does not corrupt any row", bad == 0, f"{bad} unparseable")
    srcs = [x.get("src") for x in rows() if x.get("src")]
    check("label is sanitised to [A-Za-z0-9_-]",
          all(all(c.isalnum() or c in "-_" for c in s) for s in srcs), srcs)
    check("no injected key survives", all("pwned" not in x.get("op", "") for x in rows()))

    # --- suppression still wins over marking --------------------------------
    before = len(rows())
    run(["outline", "A.md"], src="bench", extra={"VV_NO_METRICS": "1"})
    check("VV_NO_METRICS still suppresses entirely", len(rows()) == before, rows()[-1:])

    # --- mark_bench sets the env children inherit ---------------------------
    import sweepguard as sg
    keep = os.environ.get("VV_METRICS_SRC")
    try:
        sg.mark_bench("unit-probe")
        check("mark_bench sets VV_METRICS_SRC",
              os.environ.get("VV_METRICS_SRC") == "unit-probe")
        env = dict(os.environ, HOME=home, VV_VAULT=vault)
        env.pop("VV_NO_METRICS", None); env.pop("VV_JOURNAL_ROOT", None)
        subprocess.run([VR, "outline", "A.md"], capture_output=True, env=env, cwd=vault)
        check("a child process inherits the mark", rows()[-1].get("src") == "unit-probe",
              rows()[-1])
    finally:
        if keep is None: os.environ.pop("VV_METRICS_SRC", None)
        else: os.environ["VV_METRICS_SRC"] = keep

    # --- the report prefers provenance over the rate heuristic --------------
    import pilot_report as pr
    marked_rows = [{"ts": "2026-08-27T10:00:00", "op": "read", "src": "bench"}] * 5
    slow_human = [{"ts": f"2026-08-27T10:0{i}:00", "op": "read"} for i in range(1, 6)]
    burst = [{"ts": "2026-08-27T11:00:00", "op": "read"}] * (pr.MACHINE_OPS_PER_MIN + 1)
    human, marked, unmarked, hot, labels = pr.classify_traffic(
        marked_rows + slow_human + burst)
    check("declared-synthetic rows are excluded by LABEL, not rate",
          len(marked) == 5 and labels["bench"] == 5, (len(marked), dict(labels)))
    check("unmarked burst is still caught by the rate backstop",
          len(unmarked) == pr.MACHINE_OPS_PER_MIN + 1, len(unmarked))
    check("slow unmarked traffic is treated as usage", len(human) == 5, len(human))
    check("a marked BURST is not double-counted as unmarked",
          all(not r.get("src") for r in unmarked))

    # rows written before stamping existed must not read as an outstanding
    # action -- an alarm that fires forever on something nobody can fix is one
    # people learn to scroll past
    check("provenance epoch is defined", bool(pr.PROVENANCE_SINCE), pr.PROVENANCE_SINCE)
    old = [{"ts": "2026-08-26T15:00:00", "op": "read"}] * (pr.MACHINE_OPS_PER_MIN + 1)
    new = [{"ts": "2026-09-05T15:00:00", "op": "read"}] * (pr.MACHINE_OPS_PER_MIN + 1)
    _, _, unm_old, _, _ = pr.classify_traffic(old)
    _, _, unm_new, _, _ = pr.classify_traffic(new)
    check("pre-epoch burst is still excluded by rate",
          len(unm_old) == pr.MACHINE_OPS_PER_MIN + 1, len(unm_old))
    check("post-epoch burst is also excluded by rate",
          len(unm_new) == pr.MACHINE_OPS_PER_MIN + 1, len(unm_new))
    check("epoch splits old from new for reporting",
          all(r["ts"] < pr.PROVENANCE_SINCE for r in unm_old) and
          all(r["ts"] >= pr.PROVENANCE_SINCE for r in unm_new))

    # --- report integration: diagnostics must not replace the denominator ---
    report_home = tempfile.mkdtemp(prefix="vv-prov-report-")
    report_metrics = os.path.join(report_home, ".claude/metrics")
    os.makedirs(report_metrics)
    burst_n = pr.MACHINE_OPS_PER_MIN + 1
    vv_rows = [
        {"ts": "2026-08-27T11:00:00", "op": "backlinks", "exit": 0,
         "ms": 1, "out_bytes": 1}
        for _ in range(burst_n)
    ] + [
        {"ts": "2026-08-27T12:00:00", "op": "read", "exit": 0,
         "ms": 2, "out_bytes": 10}
    ]
    legacy_rows = [
        {"ts": "2026-08-27T12:00:01", "op": "read", "note_bytes": 100},
        {"ts": "2026-08-27T12:00:02", "op": "edit", "note_bytes": 200},
    ]
    for name, records in (("vv.jsonl", vv_rows), ("vv-legacy.jsonl", legacy_rows)):
        with open(os.path.join(report_metrics, name), "w") as f:
            for record in records:
                f.write(json.dumps(record) + "\n")
    report = subprocess.run(
        [sys.executable, os.path.join(REPO, "bench/pilot_report.py"),
         "--since", "2026-08-27T10:00", "--until", "2026-08-27T13:00"],
        capture_output=True, text=True, env=dict(os.environ, HOME=report_home))
    check("pilot report preserves the real legacy adoption cohort",
          report.returncode == 0 and
          "adoption: vv handled 1 of 3 logged vault ops (33%) · legacy 2 — read:1, edit:1"
          in report.stdout, report.stderr + report.stdout)
    check("pilot report keeps the pre-provenance burst diagnostic-only",
          f"{burst_n} predate provenance stamping" in report.stdout and
          f"legacy_in_window={len(legacy_rows)}" in report.stdout,
          report.stdout)
    shutil.rmtree(report_home, ignore_errors=True)

    shutil.rmtree(vault, ignore_errors=True); shutil.rmtree(home, ignore_errors=True)
    print(("ALL PASS (metrics provenance: %d)" % checks_run) if not fails
          else "FAILURES: " + ", ".join(fails))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
