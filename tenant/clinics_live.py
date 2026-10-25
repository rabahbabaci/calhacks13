"""Cited nearby testing pages. One bounded Apify run. No Claude, no SpacetimeDB.

  data/.venv/bin/python -m tenant.clinics_live --zip 94612
"""
import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tenant.clinics import (  # noqa: E402
    DEFECT_TESTS, apify_token, hrsa_directory, lookup_services, tests_for_defects, write_cited,
)


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--zip", required=True)
    args = parser.parse_args(argv)
    directory = hrsa_directory(args.zip)
    if not directory["clinics"]:
        directory = hrsa_directory(args.zip)
    token = apify_token()
    if not directory["clinics"]:
        print(directory["note"])
        return 0
    if token:
        needed = tests_for_defects(DEFECT_TESTS)
        lookup_services(directory["clinics"], token, limit=8, needed=needed)
        directory["note"] = (
            "These are the sites the HRSA ZIP search lists within 5 miles. "
            "Tests are copied from the center's website only when that page states "
            + ", ".join(needed) + "."
        )
    path = write_cited(args.zip, directory)
    matched = [row for row in directory["clinics"] if row.get("tests")]
    print(path)
    print(f"{len(matched)} of {len(directory['clinics'])} state a matching test")
    for row in matched:
        print(f"{row['name']} | {row['tests']} | {row['insurance'] or '-'} | {row['source_url']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
