"""Health agent: cited public-health information per confirmed hazard.

Usage (repo root):
  data/.venv/bin/python agents/health/agent.py agents/health/cases/mold_basic.json            # assess, save, print
  data/.venv/bin/python agents/health/agent.py agents/health/cases/mold_basic.json --no-save

With save (default): creates the case_file row if missing, records one agent_run row per assessed hazard
(agent "health": start_run, then finish_run with the assessment or fail_run with the error), and keeps save_assessment.

Input: case object per docs/contracts.md section 3 (optional "notes" string).
Output: {"case_id", "assessments": [section 5 objects], "not_assessed": [{"hazard", "reason"}],
         "dropped": [{"hazard", "field", "text", "reason"}], "usage": [...]}
"""
import json
import os
import sys
import time
import urllib.error
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "data"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from stdb import StdbError, call_reducer, sql  # noqa: E402
from urgent import DISCLAIMER, is_urgent, urgent_assessment  # noqa: E402

MODEL = "claude-sonnet-5-5"
MIN_CONFIDENCE = 0.5
MAX_TURNS = 6
CLAIM_FIELDS = ["exposure_pathways", "health_risks", "vulnerable_groups", "precautions", "see_a_doctor_if"]
BANNED_TERMS = {"rodents": ["hantavirus"]}


def load_env():
    env = ROOT / ".env"
    if env.exists():
        for line in env.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())


class DataLayerDown(RuntimeError):
    pass


def _sql(query):
    try:
        return sql(query)
    except urllib.error.URLError as e:
        raise DataLayerDown(f"SpacetimeDB unreachable: {e.reason}") from None


def fetch_corpus(hazard):
    chunks = _sql(f"SELECT * FROM health_chunk WHERE hazard = '{hazard}'")
    sources = _sql(f"SELECT * FROM health_source WHERE hazard = '{hazard}'")
    chunks.sort(key=lambda c: (c["source_url"], int(c["ordinal"])))
    return chunks, sources


SYSTEM = """You write general public-health information for a renter about a housing hazard.

Hard rules:
- Use only the text returned by search_sources. Every claim must cite the chunk ids it comes from. A claim with no supporting chunk is dropped by the validator, so do not write it.
- Never diagnose anyone. Never say or imply that any symptom a tenant, child, or household member has is caused by this hazard. If the case notes ask about a symptom, do not answer that question. The disclaimer and see_a_doctor_if cover it.
- Never name a specific disease as something the household was exposed to or has. Health risks are stated as general risks the sources describe.
- Do not give medical advice, treatment, dosing, or testing recommendations beyond what a source says about when to see a health professional.
- Keep each claim to one or two plain sentences, written for a tenant, not a clinician.
- One point per claim. Never combine two different ideas, diseases, or recommendations in one claim; split them. The validator drops a whole claim at once, so a good sentence paired with a bad one is lost.
- Household flags only change emphasis: if children, older adults, pregnancy, or a breathing condition are present, lead vulnerable_groups with the matching group when the sources cover it. Do not add groups the sources do not mention.
- Never mention the tenant, their household, their children, or the case notes inside a claim. Claims describe the hazard and groups of people in general. Do not write a refusal or a disclaimer as a claim; the fixed disclaimer field handles that.
- Scale output to the evidence. If search_sources returns fewer than 5 chunks, write at most 6 claims in total across all fields and merge related points into one claim.
- If the sources say little on a field, write little. Empty lists are fine.

Process: call search_sources for the hazard once, read the chunks, then call save_assessment exactly once with the full object. Do not write prose outside the tools."""

CLAIM_SCHEMA = {
    "type": "array",
    "items": {
        "type": "object",
        "properties": {
            "text": {"type": "string"},
            "chunk_ids": {"type": "array", "items": {"type": "integer"}},
        },
        "required": ["text", "chunk_ids"],
    },
}

TOOLS = [
    {
        "name": "search_sources",
        "description": "Return every corpus chunk for a hazard tag, with chunk ids and source metadata.",
        "input_schema": {
            "type": "object",
            "properties": {"hazard": {"type": "string"}},
            "required": ["hazard"],
        },
    },
    {
        "name": "save_assessment",
        "description": "Save the finished assessment. Each claim is {text, chunk_ids}.",
        "input_schema": {
            "type": "object",
            "properties": {"hazard": {"type": "string"}, **{f: CLAIM_SCHEMA for f in CLAIM_FIELDS}},
            "required": ["hazard"] + CLAIM_FIELDS,
        },
    },
]


def validate(hazard, draft, chunks, sources):
    """Enforce citations in code. Returns (assessment, dropped)."""
    valid_ids = {int(c["id"]) for c in chunks}
    banned = [t.lower() for t in BANNED_TERMS.get(hazard, [])]
    dropped = []
    out = {"hazard": hazard, "urgent": False, "urgent_message": None}
    cited = set()
    for field in CLAIM_FIELDS:
        kept = []
        for claim in draft.get(field) or []:
            text = str(claim.get("text", "")).strip()
            ids = sorted({int(i) for i in claim.get("chunk_ids") or [] if str(i).lstrip("-").isdigit()})
            reason = None
            if not text:
                reason = "empty text"
            elif not ids:
                reason = "no chunk_ids"
            elif not set(ids) <= valid_ids:
                reason = f"chunk_ids not retrieved: {sorted(set(ids) - valid_ids)}"
            elif any(b in text.lower() for b in banned):
                reason = "banned term for this hazard"
            if reason:
                dropped.append({"hazard": hazard, "field": field, "text": text, "reason": reason})
                continue
            kept.append({"text": text, "chunk_ids": ids})
            cited.update(ids)
        out[field] = kept
    cited_urls = {c["source_url"] for c in chunks if int(c["id"]) in cited}
    out["sources"] = [
        {"url": s["url"], "title": s["title"], "publisher": s["publisher"]}
        for s in sources
        if s["url"] in cited_urls
    ]
    out["disclaimer"] = DISCLAIMER
    return out, dropped


def run_llm(client, case, hazard_obj, chunks, sources):
    hazard = hazard_obj["hazard"]
    household = case.get("household", {})
    flags = [k for k, v in household.items() if v] or ["none"]
    user = (
        f"Hazard: {hazard}\nLocation in home: {hazard_obj.get('location', 'unknown')}\n"
        f"Household flags: {', '.join(flags)}\n"
        f"Case notes from tenant: {case.get('notes') or 'none'}\n\n"
        "Call search_sources, then save_assessment."
    )
    messages = [{"role": "user", "content": user}]
    usage = {"input_tokens": 0, "output_tokens": 0, "calls": 0}
    draft = None
    for _ in range(MAX_TURNS):
        resp = client.messages.create(model=MODEL, max_tokens=3000, system=SYSTEM, tools=TOOLS, messages=messages)
        usage["input_tokens"] += resp.usage.input_tokens
        usage["output_tokens"] += resp.usage.output_tokens
        usage["calls"] += 1
        messages.append({"role": "assistant", "content": resp.content})
        if resp.stop_reason != "tool_use":
            break
        results = []
        for block in resp.content:
            if block.type != "tool_use":
                continue
            if block.name == "search_sources":
                payload = {
                    "chunks": [{"id": int(c["id"]), "source_url": c["source_url"], "text": c["text"]} for c in chunks],
                    "sources": [{"url": s["url"], "title": s["title"], "publisher": s["publisher"]} for s in sources],
                }
                results.append({"type": "tool_result", "tool_use_id": block.id, "content": json.dumps(payload)})
            elif block.name == "save_assessment":
                draft = block.input
                results.append({"type": "tool_result", "tool_use_id": block.id, "content": "saved"})
        messages.append({"role": "user", "content": results})
        if draft is not None:
            break
    if draft is None:
        raise RuntimeError(f"{hazard}: model never called save_assessment")
    return draft, usage


def _db(reducer, args):
    try:
        call_reducer(reducer, args)
    except urllib.error.URLError as e:
        raise DataLayerDown(f"SpacetimeDB unreachable: {e.reason}") from None
    except StdbError as e:
        raise RuntimeError(f"{reducer} rejected: {e}") from None


def ensure_case(case):
    cid = case["case_id"].replace("'", "''")
    try:
        exists = sql(f"SELECT case_id FROM case_file WHERE case_id = '{cid}'")
    except urllib.error.URLError as e:
        raise DataLayerDown(f"SpacetimeDB unreachable: {e.reason}") from None
    except StdbError as e:
        raise RuntimeError(f"case_file lookup failed: {e}") from None
    if not exists:
        _db("create_case", [case["case_id"], case.get("address", ""), json.dumps(case)])


def assess_case(case, save=True, client=None):
    load_env()
    cid = case["case_id"]
    result = {"case_id": cid, "assessments": [], "not_assessed": [], "dropped": [], "usage": []}
    if save:
        ensure_case(case)
    for h in case.get("hazards", []):
        hazard = h["hazard"]
        t0 = time.time()
        if not is_urgent(hazard) and float(h.get("confidence", 0)) < MIN_CONFIDENCE and not h.get("confirmed_by_tenant"):
            result["not_assessed"].append({"hazard": hazard, "reason": "low confidence and not confirmed by tenant"})
            continue
        if save:
            _db("start_run", [cid, "health", hazard])
        try:
            if is_urgent(hazard):
                assessment = urgent_assessment(hazard)
                result["usage"].append({"hazard": hazard, "llm": False, "seconds": round(time.time() - t0, 2)})
            else:
                chunks, sources = fetch_corpus(hazard)
                if not chunks:
                    reason = "no source in corpus"
                    result["not_assessed"].append({"hazard": hazard, "reason": reason})
                    if save:
                        _db("finish_run", [cid, "health", hazard, json.dumps({"hazard": hazard, "not_assessed": True, "reason": reason})])
                    continue
                if client is None:
                    from anthropic import Anthropic
                    client = Anthropic()
                draft, usage = run_llm(client, case, h, chunks, sources)
                assessment, dropped = validate(hazard, draft, chunks, sources)
                result["dropped"].extend(dropped)
                usage.update({"hazard": hazard, "llm": True, "seconds": round(time.time() - t0, 2)})
                result["usage"].append(usage)
            if save:
                _db("save_assessment", [cid, hazard, json.dumps(assessment)])
                _db("finish_run", [cid, "health", hazard, json.dumps(assessment)])
        except DataLayerDown:
            raise
        except Exception as e:
            if save:
                try:
                    _db("fail_run", [cid, "health", hazard, f"{type(e).__name__}: {e}"[:500]])
                except Exception:
                    pass
            raise
        result["assessments"].append(assessment)
    return result

if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if not args:
        sys.exit(__doc__)
    case = json.loads(Path(args[0]).read_text(encoding="utf-8"))
    out = assess_case(case, save="--no-save" not in sys.argv)
    print(json.dumps(out, indent=2))
