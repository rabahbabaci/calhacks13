"""Minimal SpacetimeDB HTTP client for the "home" database.

call_reducer(name, args) -> POST /v1/database/<db>/call/<name>, body = JSON array of positional args
sql(query)               -> POST /v1/database/<db>/sql, returns list of row dicts

Config: STDB_URL (default http://127.0.0.1:3000), STDB_DB (default home), STDB_TOKEN (optional, sent as Bearer).
Read from real environment variables first, then from STDB_* lines in the repo-root .env. Only STDB_* keys are read.
Shared team db: STDB_URL=https://maincloud.spacetimedb.com (no token needed for reads or reducer calls).
"""
import json
import os
import urllib.error
import urllib.request
from pathlib import Path


def _load_dotenv():
    env = Path(__file__).resolve().parent.parent / ".env"
    if not env.exists():
        return
    for line in env.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip().removeprefix("export ").strip()
        if key.startswith("STDB_") and key not in os.environ:
            os.environ[key] = value.strip().strip('"').strip("'")


_load_dotenv()

BASE_URL = os.environ.get("STDB_URL", "http://127.0.0.1:3000").rstrip("/")
DATABASE = os.environ.get("STDB_DB", "home")
TIMEOUT = 30


class StdbError(RuntimeError):
    def __init__(self, status, body, url):
        super().__init__(f"HTTP {status} from {url}: {body.strip()[:500]}")
        self.status = status
        self.body = body
        self.url = url


def _headers(content_type):
    headers = {"Content-Type": content_type}
    token = os.environ.get("STDB_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _post(path, data, content_type):
    url = f"{BASE_URL}/v1/database/{DATABASE}/{path}"
    req = urllib.request.Request(url, data=data, method="POST", headers=_headers(content_type))
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            return resp.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        raise StdbError(e.code, e.read().decode("utf-8", "replace"), url) from None


def call_reducer(name, args):
    """Call a reducer with positional args. Raises StdbError on failure (reducer errors are HTTP 530)."""
    _post(f"call/{name}", json.dumps(list(args)).encode("utf-8"), "application/json")


def _column_name(element, index):
    name = element.get("name")
    if isinstance(name, dict):
        name = name.get("some")
    return name if isinstance(name, str) else f"col{index}"


def sql(query):
    """Run SQL and return rows as dicts. Multiple statements are flattened into one list."""
    body = _post("sql", query.encode("utf-8"), "text/plain")
    rows = []
    for statement in json.loads(body):
        elements = statement.get("schema", {}).get("elements", [])
        names = [_column_name(e, i) for i, e in enumerate(elements)]
        for row in statement.get("rows", []):
            rows.append(dict(zip(names, row)))
    return rows


if __name__ == "__main__":
    import sys

    query = " ".join(sys.argv[1:]) or "SELECT * FROM health_source"
    print(json.dumps(sql(query), indent=2))
