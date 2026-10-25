"""Seed a demo case with 3 unconfirmed hazards on the shared SpacetimeDB.

Usage (repo root):
    data/.venv/bin/python integration/seed_demo.py                # case_demo_002
    data/.venv/bin/python integration/seed_demo.py --case-id case_e2e_123

Safe to rerun: create_case upserts, and a hazard is only added if the case
has no row with the same tag yet. Prints the resulting hazard rows.
Uses STDB_URL from .env (maincloud) or the env var; see docs/stack-findings.md.
"""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "data"))
from stdb import BASE_URL, StdbError, call_reducer, sql  # noqa: E402

DEMO_CASE_ID = "case_demo_002"
DEMO_ADDRESS = "1456 7th Ave, Oakland, CA 94606"

DEMO_HAZARDS = [
    # (hazard tag, location, confidence, evidence ids)
    ("dampness_mold", "bathroom ceiling", 0.86, ["photo_1"]),
    ("water_leak", "under kitchen sink", 0.78, ["photo_2"]),
    ("exposed_wiring", "hallway outlet", 0.71, ["photo_3"]),
]


def case_payload(case_id, address, hazards):
    """Section 3 case input. Hazards start unconfirmed."""
    return {
        "case_id": case_id,
        "address": address,
        "jurisdiction": {"state": "CA", "city": "Oakland"},
        "hazards": [
            {
                "hazard": tag,
                "location": loc,
                "confidence": conf,
                "confirmed_by_tenant": False,
                "evidence_ids": ids,
            }
            for tag, loc, conf, ids in hazards
        ],
        "household": {
            "children": False,
            "older_adults": False,
            "pregnancy": False,
            "breathing_condition": False,
        },
        "notes": "",
    }


def q(s):
    return s.replace("'", "''")


def hazards_for(case_id):
    return sql(
        f"SELECT id, hazard, location, confidence, confirmed_by_tenant "
        f"FROM hazard WHERE case_id = '{q(case_id)}'"
    )


def seed(case_id=DEMO_CASE_ID, address=DEMO_ADDRESS, hazards=DEMO_HAZARDS):
    payload = case_payload(case_id, address, hazards)
    call_reducer("create_case", [case_id, address, json.dumps(payload)])
    existing = {row["hazard"] for row in hazards_for(case_id)}
    added = []
    for tag, loc, conf, ids in hazards:
        if tag in existing:
            continue
        call_reducer("add_hazard", [case_id, tag, loc, conf, json.dumps(ids)])
        added.append(tag)
    return added, hazards_for(case_id)


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--case-id", default=DEMO_CASE_ID)
    ap.add_argument("--address", default=DEMO_ADDRESS)
    args = ap.parse_args()

    print(f"db: {BASE_URL}")
    try:
        added, rows = seed(args.case_id, args.address)
    except StdbError as e:
        print(f"ERROR {e}")
        sys.exit(1)
    print(f"case {args.case_id}: added {added or 'nothing (already seeded)'}")
    for r in sorted(rows, key=lambda r: r["id"]):
        print(
            f"  id={r['id']} {r['hazard']:<16} {r['location']:<22} "
            f"conf={float(r['confidence']):.2f} confirmed={r['confirmed_by_tenant']}"
        )
    if len(rows) != len(DEMO_HAZARDS):
        print(f"WARNING expected {len(DEMO_HAZARDS)} hazards, found {len(rows)}")
        sys.exit(1)


if __name__ == "__main__":
    main()
