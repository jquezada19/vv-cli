#!/usr/bin/env python3
"""vv launcher stub. The implementation lives in vv_impl.py and is IMPORTED so
CPython caches its bytecode in __pycache__ — running an 87 KB main script
directly recompiles it on every invocation (~13 ms, measured 2026-08-27,
Python 3.14). The sys.modules alias makes `import vv` yield the real module
(every name, underscored included), so test-suite imports and monkeypatching
are unchanged. Argv contract, env vars, and exit codes are unchanged; callers
keep invoking src/vv.py."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import vv_impl
sys.modules[__name__] = vv_impl
if __name__ == "__main__":
    vv_impl.main()
