#!/usr/bin/env bash
# Smoke test for the live core reducers (contracts.md section 6) over plain HTTP, no token.
# Usage: bash data/test_core.sh [base_url] [db]
#   bash data/test_core.sh http://127.0.0.1:3000 home-dev
#   bash data/test_core.sh https://maincloud.spacetimedb.com home
# Writes rows for case_id case_core_test. There is no delete reducer, so they stay.
set -u
BASE="${1:-http://127.0.0.1:3000}"
DB="${2:-home}"
H="$BASE/v1/database/$DB"
pass=0
fail=0

call() { # reducer, json args, expected http code, label
  local out code body r
  out=$(curl -s -w '\n%{http_code}' -X POST "$H/call/$1" -H 'Content-Type: application/json' --data "$2")
  code=$(printf '%s' "$out" | tail -n1)
  body=$(printf '%s' "$out" | sed '$d')
  if [ "$code" = "$3" ]; then pass=$((pass + 1)); r=PASS; else fail=$((fail + 1)); r=FAIL; fi
  printf '%s  %-44s HTTP %s %s\n' "$r" "$4" "$code" "$body"
}

sql() { curl -s -X POST "$H/sql" -H 'Content-Type: text/plain' --data "$1"; }

rows() {
  python3 -c 'import json, sys
d = json.load(sys.stdin)[0]
names = [(e.get("name") or {}).get("some") for e in d["schema"]["elements"]]
for r in d["rows"]:
    print("   ", json.dumps(dict(zip(names, r))))'
}

echo "target: $H"
echo "--- happy path"
call create_case '["case_core_test","123 Example St, Oakland, CA 94612","{\"case_id\":\"case_core_test\",\"notes\":\"core smoke test\"}"]' 200 "create_case"
call add_hazard '["case_core_test","dampness_mold","bathroom ceiling",0.82,"[\"photo_3\"]"]' 200 "add_hazard"
HID=$(sql "SELECT id FROM hazard WHERE case_id = 'case_core_test'" | python3 -c 'import json, sys; print(json.load(sys.stdin)[0]["rows"][-1][0])')
call confirm_hazard "[$HID,true]" 200 "confirm_hazard (id $HID)"
call start_run '["case_core_test","health","dampness_mold"]' 200 "start_run"
call finish_run '["case_core_test","health","dampness_mold","{\"hazard\":\"dampness_mold\",\"urgent\":false}"]' 200 "finish_run"
echo "    agent_run after finish (expect status done, payload set):"
sql "SELECT status, payload_json, error FROM agent_run WHERE case_id = 'case_core_test' AND agent = 'health' AND hazard = 'dampness_mold'" | rows

echo "--- lifecycle"
call start_run '["case_core_test","health","dampness_mold"]' 200 "start_run again (reset to running)"
echo "    agent_run after reset (expect status running, payload and error empty):"
sql "SELECT status, payload_json, error FROM agent_run WHERE case_id = 'case_core_test' AND agent = 'health' AND hazard = 'dampness_mold'" | rows
call fail_run '["case_core_test","health","dampness_mold","test failure"]' 200 "fail_run"

echo "--- rejections (must fail with 530)"
call start_run '["case_core_test","robot","dampness_mold"]' 530 "reject: bad agent name"
call start_run '["case_nope","health","dampness_mold"]' 530 "reject: unknown case"
call finish_run '["case_core_test","health","dampness_mold","{}"]' 530 "reject: finish a run that is not running"
call add_hazard '["case_core_test","mold","x",0.5,"[]"]' 530 "reject: bad hazard tag"

echo "--- final state"
echo "    hazard rows:"
sql "SELECT id, hazard, location, confidence, confirmed_by_tenant FROM hazard WHERE case_id = 'case_core_test'" | rows
echo "    agent_run rows (expect exactly 1, status failed):"
sql "SELECT agent, hazard, status, error FROM agent_run WHERE case_id = 'case_core_test'" | rows

echo
echo "$pass passed, $fail failed"
[ "$fail" -eq 0 ]
