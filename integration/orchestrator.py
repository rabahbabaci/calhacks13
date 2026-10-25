"""Orchestrator: watches SpacetimeDB for confirmed hazards and runs the agents.

Usage (repo root):
    data/.venv/bin/python integration/orchestrator.py                       # watch all cases, forever
    data/.venv/bin/python integration/orchestrator.py --case-id case_demo_002
    data/.venv/bin/python integration/orchestrator.py --case-id case_demo_002 --once   # exit when idle, print wall time

How it works:
- Polls SQL every POLL_S seconds: confirmed hazards and agent_run rows.
- For each (case, agent, hazard) it launches a run when there is no agent_run row,
  the row is failed, or the row has been `running` longer than STALE_S (a dead process).
  In-flight runs are tracked in memory so one confirm never launches twice.
- Runs go to a bounded ThreadPoolExecutor, parallel across agents and hazards.
- The orchestrator calls start_run before dispatching, so the UI shows "running" at once
  and fail_run always has a running row to fail. Each agent then records its own
  finish_run or fail_run. On timeout the orchestrator marks the run failed and moves on;
  the thread keeps going in the background and its late finish_run is rejected by the module.
- One orchestrator process only. SpacetimeDB is the only channel to the UI.

Adding an agent: one line in AGENTS (per confirmed hazard) or CASE_AGENTS (once per
case, hazard ""). Property is case-level: it launches when the case has an address
and has no property run. Health and legal still wait for confirm_hazard.

Env: STDB_URL etc. from .env (see docs/stack-findings.md). The legal and property agents
want ANTHROPIC_API_KEY_Legal / _Property and APIFY_TOKEN_Legal / _Property; when unset they
fall back to ANTHROPIC_API_KEY and APIFY_TOKEN. Property is off by default (--agents).
"""
import argparse
import importlib.util
import json
import logging
import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "data"))


def load_env():
    env = ROOT / ".env"
    if env.exists():
        for line in env.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip().removeprefix("export ").strip(), v.strip().strip('"').strip("'"))
    for key in ("ANTHROPIC_API_KEY", "APIFY_TOKEN", "COURTLISTENER_TOKEN"):
        for suffix in ("_Legal", "_Property"):
            if os.environ.get(key) and not os.environ.get(key + suffix):
                os.environ[key + suffix] = os.environ[key]


load_env()
from stdb import BASE_URL, StdbError, call_reducer, sql  # noqa: E402

POLL_S = 1.5
RUN_TIMEOUT_S = 180  # legal measured at 135-140s on Sat 6:56 PM; tighten after the speed pass
STALE_S = 240  # a `running` row older than this belongs to a dead process
MAX_RETRIES = 2  # relaunches of a failed (case, agent, hazard); after that it stays failed
WORKERS = 6  # 3 hazards x 2 agents all in flight; urgent health runs never queue
DEFAULT_JURISDICTION = {"state": "CA", "city": "Oakland"}

log = logging.getLogger("orchestrator")


# ---------- agent registry ----------

_modules = {}
_modules_lock = threading.Lock()


def _load(name, path):
    """Import agents/<name>/agent.py under a unique module name. Each agent.py inserts
    its own dirs into sys.path at import, so the two agent.py files don't collide."""
    with _modules_lock:
        if name not in _modules:
            spec = importlib.util.spec_from_file_location(f"agent_{name}", path)
            mod = importlib.util.module_from_spec(spec)
            sys.modules[spec.name] = mod
            spec.loader.exec_module(mod)
            _modules[name] = mod
        return _modules[name]


def run_health(case):
    return _load("health", ROOT / "agents" / "health" / "agent.py").assess_case(case, save=True)


def run_legal(case):
    return _load("legal", ROOT / "agents" / "legal" / "agent.py").assess_case(case, save=True)


def run_property(case):
    return _load("property", ROOT / "agents" / "property" / "agent.py").assess(case, save=True)


# Per confirmed hazard. Property is not here: it reads the address, not one hazard.
AGENTS = {
    "health": run_health,
    "legal": run_legal,
}

CASE_AGENTS = {
    "property": run_property,
}


def split_agents(spec):
    names = [a.strip() for a in spec.split(",") if a.strip()]
    unknown = [a for a in names if a not in AGENTS and a not in CASE_AGENTS]
    if unknown:
        raise SystemExit(f"unknown agent: {unknown[0]}")
    return (
        {a: AGENTS[a] for a in names if a in AGENTS},
        {a: CASE_AGENTS[a] for a in names if a in CASE_AGENTS},
    )


def case_level_keys(case_rows, runs, enabled, now):
    """Cases with an address and no finished case-level run. enabled is the agent names to consider."""
    keys = []
    for row in case_rows:
        if not str(row.get("address") or "").strip():
            continue
        for agent in enabled:
            if agent not in CASE_AGENTS:
                continue
            key = (row["case_id"], agent, "")
            if should_launch(runs.get(key), now):
                keys.append(key)
    return keys


# ---------- db helpers ----------

def q(s):
    return s.replace("'", "''")


def ts_seconds(v):
    """Timestamps come back as [micros] from SQL over HTTP."""
    if isinstance(v, list) and v:
        v = v[0]
    try:
        return int(v) / 1_000_000
    except (TypeError, ValueError):
        return 0.0


def fetch_state(case_id=None):
    where = f" AND case_id = '{q(case_id)}'" if case_id else ""
    hazards = sql(f"SELECT * FROM hazard WHERE confirmed_by_tenant = true{where}")
    runs = sql(f"SELECT case_id, agent, hazard, status, error, updated_at FROM agent_run WHERE agent <> ''{where}")
    return hazards, {(r["case_id"], r["agent"], r["hazard"]): r for r in runs}


def fetch_cases(case_id=None):
    where = f" WHERE case_id = '{q(case_id)}'" if case_id else ""
    return sql(f"SELECT case_id, address FROM case_file{where}")


_case_cache = {}


def case_payload(case_id):
    if case_id not in _case_cache:
        rows = sql(f"SELECT address, payload_json FROM case_file WHERE case_id = '{q(case_id)}'")
        if not rows:
            raise RuntimeError(f"case {case_id} not in case_file")
        try:
            payload = json.loads(rows[0]["payload_json"] or "{}")
        except json.JSONDecodeError:
            payload = {}
        payload.setdefault("case_id", case_id)
        payload.setdefault("address", rows[0]["address"])
        if not payload.get("jurisdiction"):
            log.warning("%s has no jurisdiction in payload_json, defaulting to %s", case_id, DEFAULT_JURISDICTION)
            payload["jurisdiction"] = DEFAULT_JURISDICTION
        payload.setdefault("household", {"children": False, "older_adults": False, "pregnancy": False, "breathing_condition": False})
        _case_cache[case_id] = payload
    return _case_cache[case_id]


def one_hazard_case(hazard_row):
    """Section 3 case input containing only this hazard row. Works the same for seeded
    rows and for rows written by the hazard vision agent."""
    base = case_payload(hazard_row["case_id"])
    try:
        evidence = json.loads(hazard_row.get("evidence_ids_json") or "[]")
    except json.JSONDecodeError:
        evidence = []
    case = {k: v for k, v in base.items() if k != "hazards"}
    case["hazards"] = [{
        "hazard": hazard_row["hazard"],
        "location": hazard_row.get("location", ""),
        "confidence": float(hazard_row.get("confidence", 0)),
        "confirmed_by_tenant": True,
        "evidence_ids": evidence,
    }]
    return case


# ---------- run bookkeeping ----------

class Run:
    def __init__(self, key, future, t0):
        self.key = key
        self.future = future
        self.t0 = t0
        self.timed_out = False


def worker(key, fn, case):
    case_id, agent, hazard = key
    try:
        fn(case)
    except BaseException as e:  # SystemExit from an agent's env check counts too
        err = f"{type(e).__name__}: {e}"[:500]
        log.warning("%s/%s/%s raised %s", case_id, agent, hazard, err)
        try:
            call_reducer("fail_run", [case_id, agent, hazard, err])
        except StdbError:
            pass  # the agent already marked it failed (or done)
        raise


def run_status(key):
    case_id, agent, hazard = key
    rows = sql(
        f"SELECT status, error FROM agent_run WHERE case_id = '{q(case_id)}' "
        f"AND agent = '{q(agent)}' AND hazard = '{q(hazard)}'"
    )
    return (rows[0]["status"], rows[0]["error"]) if rows else ("missing", "")


_retries = {}  # (case_id, agent, hazard) -> relaunches so far


def should_launch(row, now):
    if row is None:
        return "no row"
    if row["status"] == "failed":
        key = (row.get("case_id"), row.get("agent"), row.get("hazard"))
        n = _retries.get(key, 0)
        if n >= MAX_RETRIES:
            if n == MAX_RETRIES:  # log once, then stay quiet
                _retries[key] = n + 1
                log.error("%s/%s/%s still failed after %d retries, giving up (%s)", *key, MAX_RETRIES, str(row.get("error", ""))[:120])
            return None
        _retries[key] = n + 1
        return f"retry failed {n + 1}/{MAX_RETRIES}"
    if row["status"] == "running" and now - ts_seconds(row["updated_at"]) > STALE_S:
        return "stale running"
    return None


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--case-id", default=None, help="only watch this case")
    ap.add_argument("--once", action="store_true", help="exit when nothing is running or launchable")
    ap.add_argument("--workers", type=int, default=WORKERS)
    ap.add_argument("--timeout", type=float, default=RUN_TIMEOUT_S)
    ap.add_argument("--agents", default="health,legal", help="comma-separated subset of agents (add property to run it)")
    ap.add_argument("--wait", type=float, default=120, help="--once: seconds to wait for the first confirm before exiting")
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")
    logging.getLogger("httpx").setLevel(logging.WARNING)  # Anthropic SDK request lines
    agents, case_agents = split_agents(args.agents)
    log.info("db=%s agents=%s case_agents=%s workers=%d timeout=%ss case=%s",
             BASE_URL, list(agents), list(case_agents), args.workers, args.timeout, args.case_id or "all")

    pool = ThreadPoolExecutor(max_workers=args.workers, thread_name_prefix="agent")
    inflight = {}  # key -> Run
    timings = []  # (key, seconds, status)
    t_start = None
    idle_polls = 0

    while True:
        now = time.time()
        try:
            hazards, runs = fetch_state(args.case_id)
            cases = fetch_cases(args.case_id) if case_agents else []
        except Exception as e:
            log.error("poll failed: %s", e)
            time.sleep(POLL_S)
            continue

        # launch
        for h in hazards:
            for agent, fn in agents.items():
                key = (h["case_id"], agent, h["hazard"])
                if key in inflight:
                    continue
                reason = should_launch(runs.get(key), now)
                if not reason:
                    continue
                try:
                    case = one_hazard_case(h)
                    call_reducer("start_run", list(key))
                except Exception as e:
                    log.error("launch %s/%s/%s skipped: %s", *key, e)
                    continue
                if t_start is None:
                    t_start = time.time()
                log.info("launch %s/%s/%s (%s)", *key, reason)
                inflight[key] = Run(key, pool.submit(worker, key, fn, case), time.time())

        for key in case_level_keys(cases, runs, list(case_agents), now):
            if key in inflight:
                continue
            try:
                case = case_payload(key[0])
                call_reducer("start_run", list(key))
            except Exception as e:
                log.error("launch %s/%s/%s skipped: %s", *key, e)
                continue
            if t_start is None:
                t_start = time.time()
            log.info("launch %s/%s/%s (case)", *key)
            inflight[key] = Run(key, pool.submit(worker, key, case_agents[key[1]], case), time.time())

        # reap
        for key, run in list(inflight.items()):
            elapsed = time.time() - run.t0
            if run.future.done():
                status, err = run_status(key)
                timings.append((key, elapsed, status))
                log.info("%s %s/%s/%s in %.1fs%s", status.upper(), *key, elapsed, f" ({err[:120]})" if err else "")
                del inflight[key]
            elif elapsed > args.timeout and not run.timed_out:
                run.timed_out = True
                try:
                    call_reducer("fail_run", [*key, f"timeout after {args.timeout:.0f}s"])
                except StdbError as e:
                    log.warning("timeout fail_run rejected for %s/%s/%s: %s", *key, e)
                timings.append((key, elapsed, "failed"))
                log.error("TIMEOUT %s/%s/%s after %.0fs, marked failed", *key, elapsed)

        if args.once:
            busy = any(not r.timed_out for r in inflight.values())
            idle_polls = 0 if busy else idle_polls + 1
            if t_start is not None and not busy and idle_polls >= 2:
                total = time.time() - t_start
                log.info("all runs settled: %d runs, %.1fs wall", len(timings), total)
                for key, secs, status in sorted(timings, key=lambda x: -x[1]):
                    log.info("  %-7s %5.1fs  %s/%s/%s", status, secs, *key)
                pool.shutdown(wait=False, cancel_futures=True)
                sys.exit(0 if all(s == "done" for _, _, s in timings) else 1)
            if t_start is None and idle_polls * POLL_S >= args.wait:
                log.info("nothing to launch for %s in %.0fs, exiting", args.case_id or "any case", args.wait)
                pool.shutdown(wait=False)
                sys.exit(0)

        time.sleep(POLL_S)


if __name__ == "__main__":
    main()
