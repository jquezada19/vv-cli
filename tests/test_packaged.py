#!/usr/bin/env python3
"""P6a pins: the packaged (archive) layout actually works outside a checkout.

The v1.0 binary resolved python at <repo>/src/vv.py and most of the surface
died in any other layout — reproduced during the roadmap review, 4/4 seats.
Contract now:
  * resolution order for the python entry: VV_PY_ENTRY, then src/vv.py BESIDE
    the executable (archive layout), then the repo layout — first that exists.
  * VV_PYTHON overrides the interpreter; a missing interpreter is a grep-stable
    `engine: ... — next: ...` error, not a raw exec trace.
  * engine-skew handshake: when the VERSION beside the resolved python entry
    differs from the binary's baked version, ONE warning line on stderr; the
    command still runs (a warning, not a wall).
"""
import os, shutil, subprocess, sys, tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VR = os.path.join(REPO, "vrust/target/release/vrust")

fails = []
def check(name, cond, detail=""):
    print(("PASS " if cond else "FAIL ") + name + (f"  [{detail}]" if detail and not cond else ""))
    if not cond: fails.append(name)

pkg = tempfile.mkdtemp(prefix="vv-pkg-")     # the "archive": vv + src/ + VERSION
tv = tempfile.mkdtemp(prefix="vv-pkg-vault-")
try:
    shutil.copy2(VR, os.path.join(pkg, "vv"))
    os.makedirs(os.path.join(pkg, "src"))
    for f in ("vv.py", "vv_impl.py"):
        shutil.copy2(os.path.join(REPO, "src", f), os.path.join(pkg, "src", f))
    shutil.copy2(os.path.join(REPO, "VERSION"), os.path.join(pkg, "VERSION"))
    open(os.path.join(tv, "Note.md"), "w").write("---\nstatus: open\n---\n# N\nbody\n")
    VVBIN = os.path.join(pkg, "vv")
    base_env = {k: v for k, v in os.environ.items() if not k.startswith("VV_")}
    base_env.update(VV_NO_METRICS="1", VV_VAULT=tv)

    def run(*args, **env_over):
        env = dict(base_env); env.update(env_over)
        return subprocess.run([VVBIN, *args], capture_output=True, text=True, env=env, cwd=pkg)

    ver = open(os.path.join(REPO, "VERSION")).read().strip()
    r = run("--version")
    check("K1 packaged --version", r.returncode == 0 and r.stdout == f"vv {ver}\n", r.stdout + r.stderr[:60])
    r = run("outline", "Note.md")
    check("K2 packaged native read", r.returncode == 0 and "H1" in r.stdout, r.stdout[:60] + r.stderr[:60])
    r = run("lint", "--quick")            # python-only verb: exercises sibling resolution
    check("K3 packaged python fallback", r.returncode == 0 and "findings" in r.stdout,
          r.stdout[:60] + r.stderr[:120])
    r = run("--help")
    check("K4 packaged help (python-authored)", r.returncode == 0 and "Read:" in r.stdout, r.stderr[:100])
    check("K5 no skew warning when versions match", "skew" not in run("lint", "--quick").stderr, "")
    # skew: bundled VERSION differs from the binary's baked one -> one warning, still works
    open(os.path.join(pkg, "VERSION"), "w").write("9.9.9-other\n")
    r = run("lint", "--quick")
    check("K6 skew warns once on stderr, still runs",
          r.returncode == 0 and r.stderr.count("skew") == 1, r.stderr[:120])
    open(os.path.join(pkg, "VERSION"), "w").write(ver + "\n")
    # missing interpreter: grep-stable engine error
    r = run("lint", "--quick", VV_PYTHON="/nonexistent/python3")
    check("K7 missing interpreter is a grep-stable error",
          r.returncode == 1 and r.stderr.startswith("engine:") and "next:" in r.stderr, r.stderr[:120])
    # VV_PY_ENTRY still wins over the sibling layout
    r = run("lint", "--quick", VV_PY_ENTRY=os.path.join(REPO, "src", "vv.py"))
    check("K8 VV_PY_ENTRY override", r.returncode == 0, r.stderr[:80])
finally:
    shutil.rmtree(pkg, ignore_errors=True); shutil.rmtree(tv, ignore_errors=True)

print(f"\n{len(fails)} failures: {fails}" if fails else "\nALL PASS (packaged: 8)")
sys.exit(1 if fails else 0)
