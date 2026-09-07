#!/usr/bin/env python3
"""Pin: _ondisk chooses a component by IDENTITY via os.stat(entry.path), never
via DirEntry.stat().

On Windows, DirEntry.stat() always reports st_ino == st_dev == 0 (CPython docs,
os.DirEntry.stat), so os.path.samestat(entry.stat(), os.stat(path)) is False for
every entry. With the pre-2026-09-07 code the identity pass then found no hit and
every scoped `board`/`props`/`orphans` died `not-found:` on a folder that exists —
the lexical fallback removed in v2.0.0 had masked it. There is no Windows CI, so
this suite simulates the failure mode: os.scandir is wrapped so each entry's
.stat() returns zeroed st_ino/st_dev while .path stays real. The fixed code
resolves the folder; the old code raises SystemExit. Found by review 2026-09-06.
"""
import os, sys, tempfile, atexit, shutil, io, contextlib

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_VAULT = tempfile.mkdtemp(prefix="vv-ondisk-vault-")
atexit.register(shutil.rmtree, _VAULT, True)
os.makedirs(os.path.join(_VAULT, "Sub", "Deep"))
os.environ["VV_VAULT"] = _VAULT
os.environ.setdefault("VV_NO_METRICS", "1")
sys.path.insert(0, os.path.join(REPO, "src"))
import vv_impl

fails = []
def check(name, cond, detail=""):
    print(("PASS " if cond else "FAIL ") + name + (f"  [{str(detail)[:160]}]" if detail and not cond else ""))
    if not cond: fails.append(name)

class _ZeroInodeStat:
    """A stat_result view with st_ino/st_dev forced to 0 — what Windows reports
    from DirEntry.stat()."""
    def __init__(self, real): self._r = real
    st_ino = 0
    st_dev = 0
    def __getattr__(self, k): return getattr(self._r, k)

class _WinEntry:
    def __init__(self, e): self._e = e
    def stat(self, *a, **k): return _ZeroInodeStat(self._e.stat(*a, **k))
    def __getattr__(self, k): return getattr(self._e, k)

_real_scandir = os.scandir
class _WinScandir:
    def __init__(self, path): self._it = _real_scandir(path)
    def __enter__(self): self._it.__enter__(); return self
    def __exit__(self, *a): return self._it.__exit__(*a)
    def __iter__(self): return (_WinEntry(e) for e in self._it)

def _ondisk_under(scandir):
    os.scandir = scandir
    try:
        with contextlib.redirect_stderr(io.StringIO()) as err:
            try:
                return vv_impl._ondisk(os.path.join("Sub", "Deep"), "Sub/Deep"), err.getvalue()
            except SystemExit as e:
                return e, err.getvalue()
    finally:
        os.scandir = _real_scandir

def main():
    out, err = _ondisk_under(_real_scandir)
    check("OI1 control: _ondisk resolves Sub/Deep with real DirEntry.stat()",
          out == os.path.join("Sub", "Deep"), (out, err))
    out, err = _ondisk_under(_WinScandir)
    check("OI2 _ondisk resolves Sub/Deep when DirEntry.stat() reports st_ino=st_dev=0 (Windows) — identity must come from os.stat(entry.path)",
          out == os.path.join("Sub", "Deep"), (out, err))
    # a search-permission failure on an ANCESTOR is reported against that
    # directory, not against the caller's folder argument (which was never listed)
    sub = os.path.join(_VAULT, "Sub")
    if os.geteuid() == 0:
        print("SKIP OI3: running as root, permission bits are not enforced")
    else:
        os.chmod(sub, 0)
        try:
            out, err = _ondisk_under(_real_scandir)
        finally:
            os.chmod(sub, 0o755)
        check("OI3 a permission failure names the unreadable ancestor (Sub) while resolving Sub/Deep, and refuses (exit 1)",
              isinstance(out, SystemExit) and out.code == 1
              and err.startswith("refused: cannot read Sub while resolving Sub/Deep"), (out, err))
    print(f"\n{len(fails)} failure(s)" if fails else "\nall passed")
    return 1 if fails else 0

if __name__ == "__main__":
    sys.exit(main())
