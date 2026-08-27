#!/usr/bin/env bash
# Full gate. Every suite must pass before a vv release touches a real vault.
#   ./run_tests.sh            # standard
#   SEEDS="1 2 3" ./run_tests.sh   # extra fuzz seeds
set -uo pipefail
cd "$(dirname "$0")"

# No suite may write to the day-to-day usage log. Per-suite opt-in was the old
# design and it leaked: engine-parity, real-vault verification, and the oracle
# never set it, so a single gate run wrote ~300 ops into the shadow pilot's
# window (found 2026-08-26, hour one of the pilot). One export covers every
# child process, including any suite added later.
export VV_NO_METRICS=1

fail=0
run() {  # run <label> <cmd...>
  local label="$1"; shift
  local out
  out=$("$@" 2>&1); local rc=$?
  if [ $rc -eq 0 ]; then
    printf '  ok   %-28s %s\n' "$label" "$(printf '%s' "$out" | tail -1)"
  else
    fail=1
    printf '  FAIL %-28s\n' "$label"
    printf '%s\n' "$out" | grep -E '^FAIL|failures' | head -8 | sed 's/^/         /'
  fi
}

if [ -d vrust ]; then
  (cd vrust && cargo build --release >/dev/null 2>&1) && echo "  ok   rust engine built" \
    || echo "  --   rust engine unavailable (python fallback will be used)"
fi

echo "unit + integration:"
run "v1 commands"        python3 tests/test_vv.py
run "v1.5 commands"      python3 tests/test_vv15.py
# same suites forced onto the python fallback — the engine nobody exercises is
# the one that drifts (sqlx runs its scenarios per-backend for the same reason)
run "v1 (python engine)"   env VV_ENGINE=python python3 tests/test_vv.py
run "v1.5 (python engine)" env VV_ENGINE=python python3 tests/test_vv15.py
run "review regressions" python3 tests/test_panel_findings.py
run "oracle findings"    python3 tests/test_oracle_findings.py
run "round-2 review"     python3 tests/test_review_round2.py
run "engine parity"      python3 tests/test_engine_parity.py
run "native read path"   python3 tests/test_native_readpath.py
run "native graph"        python3 tests/test_graph_parity.py
run "native write"        python3 tests/test_write_parity.py
run "native query"        python3 tests/test_query_parity.py
run "full parity"         python3 tests/test_full_parity.py
run "phase2 cache+patch"  python3 tests/test_phase2.py
run "cache integrity"     python3 tests/test_cache_integrity.py
run "link needle filter"  python3 tests/test_link_needle.py
run "search entry points"  python3 tests/test_search_entry.py
run "sweep guards"        python3 tests/test_sweepguard.py

echo "property/fuzz:"
for s in ${SEEDS:-999 42 31337}; do
  STRESS_SEED=$s run "fuzz seed $s" python3 tests/test_stress.py
done

echo "real corpus (read-only):"
run "vault verification"  python3 tests/verify_real_vault.py --sample 25

[ $fail -eq 0 ] && echo "ALL SUITES PASS" || echo "SUITE FAILURES — do not ship"
exit $fail
