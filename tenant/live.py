"""Run the existing agents from the tenant API. Off unless HOME_LIVE=1.

Fixture mode stays the default so tests and `python -m tenant.server` do not call
Claude. This module does not start the orchestrator. Agents are imported inside
the worker so importing tenant.server stays offline.
"""
import importlib.util
import os
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VIDEO_SUFFIX = {".mp4", ".mov", ".m4v", ".webm", ".avi"}
_AGENT_DIRS = ("property", "legal", "plan", "vision")
_COLLIDE = ("validate", "sources", "geocode", "research", "shell", "frames", "rules")
_SIDECARS = ("validate.py", "sources.py", "geocode.py", "contact.py", "shell.py", "frames.py", "rules.py")
_SIDECAR_ATTRS = ("validate", "CLAIM_FIELDS", "Sources", "SourceError", "geocode", "fill_company_contact")
_SITE = ROOT / "data" / ".venv" / "lib" / f"python{sys.version_info.major}.{sys.version_info.minor}" / "site-packages"
_pool = ThreadPoolExecutor(max_workers=4)
_lock = threading.Lock()
_modules = {}
_modules_lock = threading.RLock()


def _ensure_site():
    """Keep the project venv on sys.path so agent isolation cannot hide anthropic."""
    site = str(_SITE)
    if _SITE.is_dir() and site not in sys.path:
        sys.path.append(site)


def live_enabled():
    return os.environ.get("HOME_LIVE") == "1"


def save_enabled():
    return os.environ.get("HOME_LIVE_SAVE") == "1"


def load_env():
    env = ROOT / ".env"
    if not env.exists():
        return
    for line in env.read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip().removeprefix("export ").strip(), value.strip().strip('"').strip("'"))


def apply_key_fallbacks():
    """Same shared-key fallback the orchestrator uses for Legal, plus Property and Plan."""
    load_env()
    shared = os.environ.get("ANTHROPIC_API_KEY") or ""
    apify = os.environ.get("APIFY_TOKEN_Property") or os.environ.get("APIFY_TOKEN") or ""
    for suffix in ("Property", "Legal", "Plan"):
        if shared:
            os.environ.setdefault(f"ANTHROPIC_API_KEY_{suffix}", shared)
        if apify:
            os.environ.setdefault(f"APIFY_TOKEN_{suffix}", apify)


def empty_property(address):
    return {
        "address": address,
        "owner": {"name": "", "role": "owner", "mailing_address": "", "email": "", "phone": "", "website": ""},
        "manager": None,
        "records": [],
        "unresolved": [],
        "patterns": [],
        "disclaimer": "",
        "summary": "",
    }


def empty_runs():
    return {
        "hazard": "done",
        "property": "waiting",
        "plan": "waiting",
        "health": "waiting",
        "legal": "waiting",
    }


def _party(raw, default_role=""):
    raw = raw if isinstance(raw, dict) else {}
    return {
        "name": str(raw.get("name") or "").strip(),
        "role": str(raw.get("role") or default_role).strip(),
        "mailing_address": str(raw.get("mailing_address") or "").strip(),
        "email": str(raw.get("email") or "").strip(),
        "phone": str(raw.get("phone") or "").strip(),
        "website": str(raw.get("website") or "").strip(),
    }


def apply_property(case, result):
    report = (result or {}).get("report") or result or {}
    owner = _party(report.get("owner") if isinstance(report.get("owner"), dict) else {}, "owner")
    raw_manager = report.get("manager")
    manager = _party(raw_manager) if isinstance(raw_manager, dict) else raw_manager
    if not owner.get("name") and not (isinstance(manager, dict) and manager.get("name")):
        from tenant.history import named_from_summary
        lifted = named_from_summary(report.get("summary"))
        if lifted:
            owner["name"] = lifted
            owner["role"] = owner.get("role") or "company"
    from tenant.history import details_from_report
    details = details_from_report({**report, "owner": owner, "manager": manager if isinstance(manager, dict) else {}})
    for party in (owner, manager if isinstance(manager, dict) else None):
        if not party:
            continue
        for key in ("mailing_address", "email", "phone", "website"):
            if not party.get(key) and details.get(key):
                party[key] = details[key]
    with _lock:
        case["property"] = {
            "address": report.get("address") or case.get("address") or "",
            "owner": owner,
            "manager": manager,
            "records": list(report.get("records") or []),
            "unresolved": list(report.get("unresolved") or []),
            "patterns": list(report.get("patterns") or []),
            "disclaimer": str(report.get("disclaimer") or ""),
            "summary": str(report.get("summary") or ""),
        }


def apply_assessments(case, key, result):
    found = False
    with _lock:
        bucket = case.setdefault(key, {})
        for row in (result or {}).get("assessments") or []:
            tag = row.get("hazard")
            if tag:
                bucket[tag] = row
                found = True
    return found


def apply_vision(case, result):
    report = (result or {}).get("vision_report")
    if not report:
        return
    with _lock:
        case["vision"] = report
        case["confirmed"] = {}


def apply_plan(case, result):
    report = (result or {}).get("report")
    if not report:
        return
    with _lock:
        case["plan"] = report


def section3(case, hid=None):
    from tenant.layout import hazard_id

    hazards = []
    for item in (case.get("vision") or {}).get("hazards") or []:
        row_id = hazard_id(item)
        if hid and row_id != hid:
            continue
        hazards.append({
            "hazard": item.get("hazard"),
            "location": item.get("location") or "",
            "confidence": item.get("confidence") or 0,
            "confirmed_by_tenant": bool((case.get("confirmed") or {}).get(row_id)),
            "evidence_ids": list(item.get("evidence_ids") or []),
        })
    return {
        "case_id": case["case_id"],
        "address": case.get("address") or "",
        "jurisdiction": case.get("jurisdiction") or {"state": "CA", "city": "Oakland"},
        "hazards": hazards,
        "household": case.get("household") or {},
    }


def start_after_onboard(case):
    if not live_enabled():
        return
    apply_key_fallbacks()
    video = _video_path(case)
    for name in ("property", "plan", *(("vision",) if video else ())):
        try:
            _load(name)
        except Exception:
            pass
    _submit("property", case, _run_property)
    _submit("plan", case, _run_plan)
    if video:
        _submit("hazard", case, lambda c: _run_vision(c, video))


def analyzing(case):
    """True only while the walkthrough is still underway.

    Property and the floor plan keep running in the background so the home,
    landlord page, and nearby testing are usable as soon as hazards exist.
    """
    if case.get("fixture") or not live_enabled():
        return False
    return (case.get("runs") or {}).get("hazard") in {"running", "waiting"}


def start_after_confirm(case, hid):
    if not live_enabled():
        return
    apply_key_fallbacks()
    _submit("health", case, lambda c: _run_health(c, hid))
    _submit("legal", case, lambda c: _run_legal(c, hid))


def _submit(agent, case, fn):
    if (case.get("runs") or {}).get(agent) == "running" and agent in {"property", "plan", "hazard"}:
        return
    _set_run(case, agent, "running")
    _pool.submit(_guard, agent, case, fn)


def _guard(agent, case, fn):
    try:
        fn(case)
    except Exception as exc:
        _set_run(case, agent, "failed", f"{type(exc).__name__}: {exc}"[:400])


def _set_run(case, agent, status, error=""):
    with _lock:
        case.setdefault("runs", {})[agent] = status
        errors = case.setdefault("errors", {})
        if error:
            errors[agent] = error
        else:
            errors.pop(agent, None)


def _video_path(case):
    for item in reversed(case.get("media") or []):
        path = item.get("path") or ""
        if path and Path(path).suffix.lower() in VIDEO_SUFFIX and Path(path).is_file():
            return path
    return ""


def _claude(key_name):
    _ensure_site()
    from anthropic import Anthropic

    key = os.environ.get(key_name) or os.environ.get("ANTHROPIC_API_KEY")
    workspace = os.environ.get("ANTHROPIC_WORKSPACE_ID")
    return Anthropic(api_key=key, default_headers={"anthropic-workspace-id": workspace} if workspace else None)


def _isolate(name):
    """Agent folders all ship validate.py / sources.py. Keep only this agent's copies on the path."""
    keep = str(ROOT / "agents" / name)
    for other in _AGENT_DIRS:
        path = str(ROOT / "agents" / other)
        if path != keep:
            while path in sys.path:
                sys.path.remove(path)
    for key in _COLLIDE:
        mod = sys.modules.get(key)
        file = str(getattr(mod, "__file__", "") or "")
        if file and "/agents/" in file and f"/agents/{name}/" not in file:
            sys.modules.pop(key, None)


def _bind_local(name, mod, filename):
    """Pin agent.py to its own sidecar. Shared names like validate.py must not stay bound to another agent."""
    path = ROOT / "agents" / name / filename
    if not path.is_file():
        return
    spec = importlib.util.spec_from_file_location(f"home_live_{name}_{path.stem}", path)
    sidecar = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(sidecar)
    fn = getattr(sidecar, path.stem, None)
    if fn is not None:
        setattr(mod, path.stem, fn)
    for attr in _SIDECAR_ATTRS:
        if hasattr(sidecar, attr):
            setattr(mod, attr, getattr(sidecar, attr))


def _load(name):
    with _modules_lock:
        if name in _modules:
            return _modules[name]
        _ensure_site()
        _isolate(name)
        path = ROOT / "agents" / name / "agent.py"
        spec = importlib.util.spec_from_file_location(f"home_live_{name}", path)
        mod = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = mod
        spec.loader.exec_module(mod)
        for filename in _SIDECARS:
            _bind_local(name, mod, filename)
        _modules[name] = mod
        return mod


def _run_property(case):
    if not os.environ.get("ANTHROPIC_API_KEY_Property"):
        raise RuntimeError("ANTHROPIC_API_KEY_Property is not set")
    if not os.environ.get("APIFY_TOKEN_Property"):
        raise RuntimeError("APIFY_TOKEN_Property is not set")
    client = _claude("ANTHROPIC_API_KEY_Property")
    result = _load("property").assess(section3(case), save=save_enabled(), client=client)
    apply_property(case, result)
    _set_run(case, "property", "done")


def _run_plan(case):
    if not os.environ.get("ANTHROPIC_API_KEY_Plan"):
        raise RuntimeError("ANTHROPIC_API_KEY_Plan is not set")
    client = _claude("ANTHROPIC_API_KEY_Plan")
    result = _load("plan").assess({**section3(case), "vision": case.get("vision")}, save=save_enabled(), client=client)
    apply_plan(case, result)
    _set_run(case, "plan", "done")


def _run_health(case, hid):
    client = _claude("ANTHROPIC_API_KEY")
    result = _load("health").assess_case(section3(case, hid), save=save_enabled(), client=client)
    if apply_assessments(case, "health", result):
        _set_run(case, "health", "done")
        return
    reason = "; ".join(row.get("reason") or "" for row in result.get("not_assessed") or []) or "no assessment"
    _set_run(case, "health", "failed", reason)


def _run_legal(case, hid):
    if not os.environ.get("ANTHROPIC_API_KEY_Legal"):
        raise RuntimeError("ANTHROPIC_API_KEY_Legal is not set")
    client = _claude("ANTHROPIC_API_KEY_Legal")
    result = _load("legal").assess_case(section3(case, hid), save=save_enabled(), client=client)
    if apply_assessments(case, "legal", result):
        _set_run(case, "legal", "done")
        return
    reason = "; ".join(row.get("reason") or "" for row in result.get("not_assessed") or [])
    reason = reason or "; ".join(row.get("error") or "" for row in result.get("failed") or []) or "no assessment"
    _set_run(case, "legal", "failed", reason)


def _run_vision(case, path):
    segment = _video_segment(path)
    result = _load("vision").analyze(section3(case), segment, live=True, save=save_enabled())
    apply_vision(case, result)
    _set_run(case, "hazard", "done")


def _video_segment(path):
    try:
        raw = subprocess.check_output(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", path],
            text=True,
        )
        duration = max(1.0, float(raw.strip()))
    except (OSError, ValueError, subprocess.CalledProcessError) as exc:
        raise RuntimeError(f"could not read video duration: {exc}") from exc
    return [{"type": "video", "path": path, "start": 0, "end": duration}]
