#!/usr/bin/env bash
# Full gate. Every suite must pass before a vv release touches a real vault.
#   ./run_tests.sh            # standard
#   SEEDS="1 2 3" ./run_tests.sh   # extra fuzz seeds
set -uo pipefail
cd "$(dirname "$0")"

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
run "review regressions" python3 tests/test_panel_findings.py
run "oracle findings"    python3 tests/test_oracle_findings.py
run "engine parity"      python3 tests/test_engine_parity.py

echo "property/fuzz:"
for s in ${SEEDS:-999 42 31337}; do
  STRESS_SEED=$s run "fuzz seed $s" python3 tests/test_stress.py
done

echo "real corpus (read-only):"
run "vault verification"  python3 tests/verify_real_vault.py --sample 25

[ $fail -eq 0 ] && echo "ALL SUITES PASS" || echo "SUITE FAILURES — do not ship"
exit $fail
