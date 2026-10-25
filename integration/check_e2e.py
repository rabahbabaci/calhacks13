"""End-to-end check: seed a fresh case, confirm its hazards, run the orchestrator, assert every run is done.

Run before every push (repo root):
    data/.venv/bin/python integration/check_e2e.py                 # all agents
    data/.venv/bin/python integration/check_e2e.py --agents health  # faster, health only

Exit 0 when every (hazard, agent) run reached `done` with a payload that matches the
contract (section 5 for health, section 7 for legal); exit 1 otherwise, with the reasons.
Uses whatever STDB_URL .env points at. Each run leaves a permanent case_e2e_<time> case
in the database (no delete reducer), which is fine while tables hold demo data only.
"""
import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "data"))
sys.path.insert(0, str(ROOT / "integration"))
sys.path.insert(0, str(ROOT / "agents" / "health"))
from seed_demo import DEMO_HAZARDS, seed  # noqa: E402
from stdb import BASE_URL, call_reducer, sql  # noqa: E402
from urgent import URGENT  # noqa: E402  (the shared urgent list, read only)

URGENT_TAGS = set(URGENT)

REQUIRED = {
    "health": ["hazard", "urgent", "urgent_message", "exposure_pathways", "health_risks", "vulnerable_groups",
               "precautions", "see_a_doctor_if", "sources", "disclaimer"],
    "legal": ["hazard", "urgent", "urgent_message", "jurisdiction", "landlord_duty", "tenant_options", "deadlines",
              "ask_legal_aid", "similar_cases", "cautions", "sources", "disclaimer"],
}
CLAIM_FIELDS = {
    "health": ["precautions", "health_risks"],
    "legal": ["landlord_duty", "tenant_options"],
}


def check_payload(agent, hazard, payload):
    """Returns a list of problems, empty when the payload is fine."""
    problems = []
    if payload.get("not_assessed"):
        return [f"not_assessed: {payload.get('reason')}"]
    for key in REQUIRED.get(agent, []):
        if key not in payload:
            problems.append(f"missing {key}")
    if payload.get("hazard") != hazard:
        problems.append(f"hazard is {payload.get('hazard')!r}")
    if not payload.get("disclaimer"):
        problems.append("empty disclaimer")
    if hazard in URGENT_TAGS:
        if not payload.get("urgent") or not payload.get("urgent_message"):
            problems.append("urgent hazard without urgent flag or message")
    elif agent in CLAIM_FIELDS:
        if not any(payload.get(f) for f in CLAIM_FIELDS[agent]):
            problems.append(f"no claims in {CLAIM_FIELDS[agent]}")
        if not payload.get("sources"):
            problems.append("no sources")
    return problems


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--agents", default="health,legal")
    ap.add_argument("--timeout", type=float, default=180)
    ap.add_argument("--case-id", default=f"case_e2e_{int(time.time())}")
    args = ap.parse_args()
    agents = [a for a in args.agents.split(",") if a]
    cid = args.case_id
    t0 = time.time()
    print(f"db: {BASE_URL}\ncase: {cid}")

    added, rows = seed(cid)
    print(f"seeded {len(rows)} hazards")
    for r in rows:
        call_reducer("confirm_hazard", [int(r["id"]), True])
    print("confirmed all hazards")

    cmd = [sys.executable, str(ROOT / "integration" / "orchestrator.py"), "--case-id", cid, "--once",
           "--wait", "30", "--timeout", str(args.timeout), "--agents", ",".join(agents)]
    print("running orchestrator:", " ".join(cmd[1:]))
    proc = subprocess.run(cmd, cwd=ROOT)
    print(f"orchestrator exit {proc.returncode}")

    runs = {(r["agent"], r["hazard"]): r for r in sql(
        f"SELECT agent, hazard, status, payload_json, error FROM agent_run WHERE case_id = '{cid}'")}
    failures = []
    if proc.returncode != 0:
        failures.append(f"orchestrator exited {proc.returncode}: a run failed or timed out before a retry (see its log above)")
    for tag, _loc, _conf, _ids in DEMO_HAZARDS:
        for agent in agents:
            run = runs.get((agent, tag))
            if run is None:
                failures.append(f"{agent}/{tag}: no agent_run row")
                continue
            if run["status"] != "done":
                failures.append(f"{agent}/{tag}: status {run['status']} {run['error'][:200]}")
                continue
            try:
                payload = json.loads(run["payload_json"])
            except json.JSONDecodeError as e:
                failures.append(f"{agent}/{tag}: payload is not JSON ({e})")
                continue
            for p in check_payload(agent, tag, payload):
                failures.append(f"{agent}/{tag}: {p}")

    total = time.time() - t0
    n = len(DEMO_HAZARDS) * len(agents)
    if failures:
        print(f"\nFAIL {len(failures)} problem(s) in {total:.0f}s:")
        for f in failures:
            print("  -", f)
        sys.exit(1)
    print(f"\nPASS {n}/{n} runs done with valid payloads in {total:.0f}s (case {cid})")


if __name__ == "__main__":
    main()
