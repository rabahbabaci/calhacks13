"""Vision agent: finds visible housing hazards in a video walkthrough or photos, places them in the home,
grades severity, and attaches legal definitions from the jurisdiction pack.

Usage (repo root):
  data/.venv/bin/python agents/vision/agent.py CASE.json --video V.mp4 [--start S --end E | --segment S-E ...]
                                                   [--photo P.jpg ...] [--dry-run] [--live] [--save]
  --dry-run  extract frames and print the estimated cost, no API call
  --live     allow a Claude call when there is no cached response (out/cache/); without it only cached
             responses are used, so a rerun never spends credits by accident
  --save     not wired to the live hazard tables yet (contracts section 6); stops with "reducer missing"

Input: case stub {case_id, address, jurisdiction, household, notes?, source_url?}.
Output: {"case_input": contract section 3 object, "vision_report": docs/contracts.md section 8 object}
"""
import argparse
import hashlib
import json
import os
import re
import sys
import threading
import time
import urllib.error
from base64 import b64encode
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT / "data"))
sys.path.insert(0, str(ROOT / "agents" / "health"))
sys.path.insert(0, str(HERE))

import frames as fr  # noqa: E402
import rules  # noqa: E402
from stdb import StdbError, call_reducer  # noqa: E402
from urgent import URGENT  # noqa: E402

MODEL = "claude-sonnet-5-5"
MAX_TOKENS = 2000
PRICE_IN_PER_M = 3.0  # Sonnet list pricing, USD; check the Console usage page for exact figures
PRICE_OUT_PER_M = 15.0
EST_OUTPUT_TOKENS = 1000
MAX_PARALLEL = 6
OUT = HERE / "out"
HOUSEHOLD = {"children": False, "older_adults": False, "pregnancy": False, "breathing_condition": False}


def load_env():
    env = ROOT / ".env"
    if env.exists():
        for line in env.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                k, v = line.split("=", 1)
                if v.strip():
                    os.environ.setdefault(k.strip(), v.strip())


class DataLayerDown(RuntimeError):
    pass


class ReducerMissing(RuntimeError):
    pass


class NoCachedResponse(RuntimeError):
    pass


class StaleFixture(RuntimeError):
    pass


class BudgetExceeded(RuntimeError):
    pass


def _tag_lines(d):
    return "\n".join(f"- {t}: {d[t]}" for t in rules.TAGS)


SYSTEM = f"""You review frames from a video walkthrough or photos of a rented home and report visible housing hazards.

Tags. Use only these nine. Definitions:
{_tag_lines(rules.DEFINITIONS)}

Anything that looks like a problem but fits no tag (rust, structural cracks, broken windows or doors, broken fixtures, trash, missing smoke alarm) goes in unlabeled_observations with a short snake_case suggested_tag. Never force it into the closest tag. Rust is not water_leak unless water damage or a leak is also visible.

Evidence:
- Each frame is preceded by its evidence_id. Cite evidence_ids exactly as given, and only frames where the hazard itself is visible.
- Never cite a frame that mostly shows a person's face or a title card. Ignore captions, on-screen text, narration and video titles; judge only what is visible.
- One entry per tag per room. If the same hazard shows in several frames, list all those frames in one entry.
- If nothing is visible, return an empty hazards list. Do not report a hazard to be safe.

Location:
- room: infer from what is visible (shower or toilet = bathroom, stove or sink with counters = kitchen, bed = bedroom). If unclear, write "unknown room". Never guess.
- location: room plus surface, e.g. "bathroom ceiling, above shower".
- bbox: [x0, y0, x1, y1] in 0-1 coordinates of the first cited frame, coarse. Omit it if unsure.

Severity rubric (low, moderate, high, urgent):
{_tag_lines(rules.RUBRIC)}
severity_reason must point to the observed_signs.

Wording:
- observed_signs are plain descriptions of what is visible, written for a tenant.
- Never state that something is mold. Write "possible mold growth" or "dark spotting".
- Never state that paint contains lead.
- No legal statements, no advice about rent, no claims about the landlord. Legal text is added by code.
- confidence is your honest 0-1 estimate that the hazard is really there.
- needs_professional_check is true when an inspection or test is needed to confirm; say why in check_reason.

Be brief. At most 2 observed_signs per hazard, each under 15 words. severity_reason and check_reason are one short sentence each. Unlabeled descriptions are one short sentence. Only list what you see; never list tags or problems that are absent.

Call report_hazards exactly once."""

TOOL = {
    "name": "report_hazards",
    "description": "Report every visible hazard and every observation that fits no tag.",
    "input_schema": {
        "type": "object",
        "properties": {
            "hazards": {"type": "array", "items": {"type": "object", "properties": {
                "hazard": {"type": "string", "enum": rules.TAGS},
                "location": {"type": "string"},
                "room": {"type": "string"},
                "bbox": {"type": "array", "items": {"type": "number"}, "minItems": 4, "maxItems": 4},
                "evidence_ids": {"type": "array", "items": {"type": "string"}},
                "observed_signs": {"type": "array", "items": {"type": "string"}, "maxItems": 2},
                "severity": {"type": "string", "enum": rules.SEVERITIES},
                "severity_reason": {"type": "string"},
                "extent": {"type": "string", "enum": rules.EXTENTS},
                "confidence": {"type": "number"},
                "needs_professional_check": {"type": "boolean"},
                "check_reason": {"type": "string"},
            }, "required": ["hazard", "location", "room", "evidence_ids", "observed_signs", "severity",
                            "severity_reason", "extent", "confidence", "needs_professional_check", "check_reason"]}},
            "unlabeled_observations": {"type": "array", "items": {"type": "object", "properties": {
                "description": {"type": "string"},
                "evidence_ids": {"type": "array", "items": {"type": "string"}},
                "suggested_tag": {"type": "string"},
            }, "required": ["description", "evidence_ids", "suggested_tag"]}},
        },
        "required": ["hazards", "unlabeled_observations"],
    },
}


# ---------- prompt, cost, cache ----------

def user_text(case):
    return (
        f"Case {case['case_id']}. Tenant notes: {case.get('notes') or 'none'}\n"
        "Tenant notes are context only; a hazard still needs a visible sign in a cited frame, except "
        "inadequate_heat, which may come from notes with evidence_ids of the frames of the affected room.\n"
        "Review every frame above, then call report_hazards."
    )


def build_content(frames, case):
    blocks = []
    for f in frames:
        when = f" (t={f['t_s']}s)" if f.get("t_s") is not None else ""
        blocks.append({"type": "text", "text": f"evidence_id: {f['evidence_id']}{when}"})
        data = b64encode(Path(f["path"]).read_bytes()).decode("ascii")
        blocks.append({"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": data}})
    blocks.append({"type": "text", "text": user_text(case)})
    return blocks


def cache_key(frames, case):
    blob = json.dumps({
        "model": MODEL, "system": SYSTEM, "tool": TOOL, "text": user_text(case),
        "frames": [[f["evidence_id"], f["sha256"]] for f in frames],
    }, sort_keys=True)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def estimate(frames):
    image_tokens = sum(int(f["width"] * f["height"] / 750) for f in frames)
    text_tokens = (len(SYSTEM) + len(json.dumps(TOOL)) + 200 + 30 * len(frames)) // 4
    tokens_in = image_tokens + text_tokens
    usd = (tokens_in * PRICE_IN_PER_M + EST_OUTPUT_TOKENS * PRICE_OUT_PER_M) / 1e6
    return {"frames": len(frames), "input_tokens": tokens_in, "output_tokens": EST_OUTPUT_TOKENS, "usd": round(usd, 4)}


def usage_usd(u):
    tokens_in = u.get("input_tokens", 0) + u.get("cache_creation_input_tokens", 0) * 1.25 + u.get("cache_read_input_tokens", 0) * 0.1
    return round((tokens_in * PRICE_IN_PER_M + u.get("output_tokens", 0) * PRICE_OUT_PER_M) / 1e6, 4)


class Budget:
    """Thread-safe. reserve() holds the estimate before a call so parallel calls cannot overshoot together."""

    def __init__(self, limit_usd):
        self.limit = limit_usd
        self.spent = 0.0
        self.reserved = 0.0
        self._lock = threading.Lock()

    def reserve(self, est_usd):
        with self._lock:
            if self.spent + self.reserved + est_usd > self.limit:
                raise BudgetExceeded(f"next call ~${est_usd:.3f} would pass budget ${self.limit:.2f} "
                                     f"(spent ${self.spent:.3f}, in flight ${self.reserved:.3f})")
            self.reserved += est_usd

    def settle(self, est_usd, actual_usd):
        with self._lock:
            self.reserved -= est_usd
            self.spent += actual_usd


def make_client():
    load_env()
    from anthropic import Anthropic
    workspace = os.environ.get("ANTHROPIC_WORKSPACE_ID")
    return Anthropic(default_headers={"anthropic-workspace-id": workspace} if workspace else None)


class ResponseCache:
    """One JSON file per response. name defaults to the cache key, evals pass the eval case name."""

    def __init__(self, directory):
        self.dir = Path(directory)

    def path(self, name):
        return self.dir / f"{name}.json"

    def get(self, name):
        p = self.path(name)
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None

    def put(self, name, record):
        self.dir.mkdir(parents=True, exist_ok=True)
        self.path(name).write_text(json.dumps(record, indent=2), encoding="utf-8")


def _to_dict(resp):
    if isinstance(resp, dict):
        return resp
    d = resp.model_dump()
    return {"content": [b for b in d["content"] if b.get("type") == "tool_use"], "usage": d.get("usage") or {},
            "stop_reason": d.get("stop_reason")}


def call_model(client, frames, case, cache=None, cache_name=None, live=False, budget=None):
    """One forced report_hazards call for a set of frames. Returns (tool_input, usage, cached)."""
    key = cache_key(frames, case)
    name = cache_name or key[:16]
    rec = cache.get(name) if cache else None
    if rec is not None and rec["key"] == key:
        resp = rec["response"]
        cached = True
    elif rec is not None and not live:
        raise StaleFixture(f"{name}: prompt, schema or frames changed since recording; rerun with --live")
    elif not live:
        est = estimate(frames)
        raise NoCachedResponse(f"{name}: no cached response. A live call would send {est['frames']} frames, "
                               f"~{est['input_tokens']} input tokens, ~${est['usd']}. Rerun with --live.")
    else:
        est = estimate(frames)
        if budget:
            budget.reserve(est["usd"])
        try:
            if client is None:
                client = make_client()
            raw = client.messages.create(
                model=MODEL, max_tokens=MAX_TOKENS,
                system=[{"type": "text", "text": SYSTEM, "cache_control": {"type": "ephemeral"}}],
                tools=[TOOL],
                messages=[{"role": "user", "content": build_content(frames, case)}],
            )
        except Exception:
            if budget:
                budget.settle(est["usd"], 0.0)
            raise
        resp = _to_dict(raw)
        if budget:
            budget.settle(est["usd"], usage_usd(dict(resp.get("usage") or {})))
        cached = False
        if cache:
            cache.put(name, {
                "key": key, "model": MODEL, "recorded_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                "frames": [{k: f[k] for k in ("evidence_id", "t_s", "sha256", "width", "height")} for f in frames],
                "response": resp,
            })
    blocks = [b for b in resp["content"] if b.get("type") == "tool_use" and b.get("name") == "report_hazards"]
    if not blocks:
        raise RuntimeError(f"{name}: model did not call report_hazards (stop_reason {resp.get('stop_reason')})")
    return blocks[0]["input"], dict(resp.get("usage") or {}), cached


# ---------- validation and merge ----------

def _clean_ids(ids):
    return [str(i) for i in ids or []]


def _bbox_ok(b):
    if not isinstance(b, (list, tuple)) or len(b) != 4:
        return False
    try:
        x0, y0, x1, y1 = (float(v) for v in b)
    except (TypeError, ValueError):
        return False
    return 0 <= x0 < x1 <= 1 and 0 <= y0 < y1 <= 1


def _confidence(v):
    try:
        return min(1.0, max(0.0, float(v)))
    except (TypeError, ValueError):
        return 0.0


def validate(raw, frames):
    """Enforce evidence, tags and wording in code. Returns (hazards, unlabeled, dropped)."""
    by_id = {f["evidence_id"]: f for f in frames}
    hazards, unlabeled, dropped = [], [], []
    for h in raw.get("hazards") or []:
        tag = str(h.get("hazard", ""))
        ids = _clean_ids(h.get("evidence_ids"))
        signs = [str(s).strip() for s in h.get("observed_signs") or [] if str(s).strip()]
        reason = None
        if tag not in rules.TAGS:
            reason = f"unknown tag: {tag}"
        elif not ids:
            reason = "no evidence_ids"
        elif not set(ids) <= set(by_id):
            reason = f"evidence_id not in frames sent: {sorted(set(ids) - set(by_id))}"
        elif not signs:
            reason = "empty observed_signs"
        elif "bbox" in h and h["bbox"] is not None and not _bbox_ok(h["bbox"]):
            reason = f"bbox outside 0-1: {h['bbox']}"
        else:
            reason = rules.never_say_hit(tag, signs + [str(h.get(k, "")) for k in rules.TEXT_FIELDS])
        if reason:
            dropped.append({"kind": "hazard", "hazard": tag, "item": h, "reason": reason})
            continue
        times = [by_id[i]["t_s"] for i in ids if by_id[i]["t_s"] is not None]
        out = {
            "hazard": tag,
            "label": rules.LABELS[tag],
            "location": str(h.get("location") or "unknown room").strip(),
            "room": str(h.get("room") or "unknown room").strip() or "unknown room",
            "evidence_ids": sorted(set(ids), key=lambda i: (by_id[i]["t_s"] is None, by_id[i]["t_s"] or 0, i)),
            "first_seen_s": min(times) if times else None,
            "observed_signs": signs,
            "severity": h.get("severity") if h.get("severity") in rules.SEVERITIES else "low",
            "severity_reason": str(h.get("severity_reason", "")).strip(),
            "extent": h.get("extent") if h.get("extent") in rules.EXTENTS else "spot",
            "confidence": _confidence(h.get("confidence")),
            "needs_professional_check": bool(h.get("needs_professional_check")),
            "check_reason": str(h.get("check_reason", "")).strip(),
            "confirmed_by_tenant": False,
        }
        if h.get("bbox") is not None:
            out["bbox"] = [round(float(v), 3) for v in h["bbox"]]
            out["bbox_frame"] = out["evidence_ids"][0]
        hazards.append(rules.apply_rubric(out))
    for o in raw.get("unlabeled_observations") or []:
        ids = [i for i in _clean_ids(o.get("evidence_ids")) if i in by_id]
        desc = str(o.get("description", "")).strip()
        if not ids or not desc:
            dropped.append({"kind": "unlabeled", "item": o, "reason": "no valid evidence_ids or empty description"})
            continue
        unlabeled.append({"description": desc, "evidence_ids": ids, "suggested_tag": str(o.get("suggested_tag", "")).strip()})
    return hazards, unlabeled, dropped


def rooms_of(hazards):
    rooms = {}
    for h in hazards:
        r = rooms.setdefault(_room_key(h["room"]), {"room": h["room"], "evidence_ids": []})
        r["evidence_ids"] = list(dict.fromkeys(r["evidence_ids"] + h["evidence_ids"]))
    return list(rooms.values())


def not_visible(hazards):
    seen = {h["hazard"] for h in hazards}
    return [f"{t}: {rules.NOT_SEEN.get(t, 'no visible signs in the frames reviewed')}" for t in rules.TAGS if t not in seen]


def _room_key(room):
    return " ".join(re.sub(r"\(.*?\)", " ", room.lower()).split())


def merge(hazards):
    """Same tag in the same room becomes one hazard, across frames and segments."""
    merged = {}
    for h in hazards:
        k = (h["hazard"], _room_key(h["room"]))
        if k not in merged:
            merged[k] = dict(h)
            continue
        m = merged[k]
        best = h if h["confidence"] > m["confidence"] else m
        ids = list(dict.fromkeys(m["evidence_ids"] + h["evidence_ids"]))
        times = [t for t in (m["first_seen_s"], h["first_seen_s"]) if t is not None]
        out = dict(best)
        out.update({
            "evidence_ids": ids,
            "first_seen_s": min(times) if times else None,
            "observed_signs": list(dict.fromkeys(m["observed_signs"] + h["observed_signs"])),
            "severity": rules.max_severity(m["severity"], h["severity"]),
            "confidence": max(m["confidence"], h["confidence"]),
            "extent": max(m["extent"], h["extent"], key=rules.EXTENTS.index),
            "needs_professional_check": m["needs_professional_check"] or h["needs_professional_check"],
            "check_reason": best["check_reason"] or m["check_reason"] or h["check_reason"],
        })
        merged[k] = rules.apply_rubric(out)
    return list(merged.values())


# ---------- outputs ----------

def to_case_input(case, hazards):
    """Contract section 3 case input, hazards in the exact section 2 shape."""
    out = {
        "case_id": case["case_id"],
        "address": case.get("address", ""),
        "jurisdiction": case.get("jurisdiction", {}),
        "hazards": [{"hazard": h["hazard"], "location": h["location"], "confidence": h["confidence"],
                     "confirmed_by_tenant": h["confirmed_by_tenant"], "evidence_ids": h["evidence_ids"]}
                    for h in hazards],
        "household": {**HOUSEHOLD, **(case.get("household") or {})},
    }
    if case.get("notes"):
        out["notes"] = case["notes"]
    return out


def _segment_frames(seg, case_id, video_n, long_edge, max_frames):
    frames_dir = OUT / "frames" / case_id
    if "frames" in seg:
        return seg["frames"], {"prepared": len(seg["frames"])}
    if seg["type"] == "video":
        return fr.extract_video(seg["path"], frames_dir, video_n=video_n, start=seg.get("start"), end=seg.get("end"),
                                max_frames=max_frames, long_edge=long_edge)
    photos = fr.load_photos(seg["paths"], frames_dir, long_edge=long_edge, first_n=seg.get("first_n", 1))
    return photos, {"photos": len(photos)}


def analyze(case, segments, client=None, cache=None, live=False, save=False, budget=None,
            long_edge=fr.LONG_EDGE, max_frames=fr.MAX_FRAMES, cache_names=None):
    """segments: [{"type": "video", "path", "start", "end", "video_n"?} | {"type": "photos", "paths"}
                  | {"type": ..., "frames": prepared frames}]. One Claude call per segment."""
    t_start = time.time()
    pack = rules.load_pack(case.get("jurisdiction"))
    if live and client is None:
        client = make_client()

    def run(i, seg):
        t0 = time.time()
        frames, stats = _segment_frames(seg, case["case_id"], seg.get("video_n", 1), long_edge, max_frames)
        t1 = time.time()
        raw, u, cached = call_model(client, frames, case, cache=cache,
                                    cache_name=(cache_names or {}).get(i), live=live, budget=budget)
        h, un, dr = validate(raw, frames)
        usage = {**u, "segment": i, "cached": cached, "usd": 0.0 if cached else usage_usd(u),
                 "extract_s": round(t1 - t0, 2), "call_s": round(time.time() - t1, 2),
                 "seconds": round(time.time() - t0, 2)}
        log = {"type": seg.get("type"), "start": seg.get("start"), "end": seg.get("end"),
               "frames": [f["evidence_id"] for f in frames], **stats}
        return frames, h, un, dr, usage, log

    workers = max(1, min(MAX_PARALLEL, len(segments)))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        results = list(pool.map(lambda a: run(*a), enumerate(segments)))
    all_frames, hazards, unlabeled, dropped, usage, seg_log = [], [], [], [], [], []
    for frames, h, un, dr, u, log in results:
        all_frames += [{k: f.get(k) for k in ("evidence_id", "t_s", "path")} for f in frames]
        hazards += h
        unlabeled += un
        dropped += dr
        usage.append(u)
        seg_log.append(log)
    final = merge(hazards)
    for h in final:
        h["legal"] = rules.legal_for(h["hazard"], pack)
        if h["hazard"] in URGENT:
            h["urgent_message"] = URGENT[h["hazard"]]
    final.sort(key=lambda h: (-rules.SEVERITIES.index(h["severity"]), h["first_seen_s"] or 0))
    report = {
        "case_id": case["case_id"],
        "notes": case.get("notes"),
        "source": {"type": segments[0].get("type") if segments else None,
                   "url_or_path": case.get("source_url") or (segments[0].get("path") if segments else None),
                   "segments": seg_log},
        "frames": all_frames,
        "hazards": final,
        "unlabeled_observations": unlabeled,
        "rooms": rooms_of(final),
        "not_visible_from_video": not_visible(final),
        "dropped": dropped,
        "usage": usage,
        "wall_seconds": round(time.time() - t_start, 2),
    }
    result = {"case_input": to_case_input(case, final), "vision_report": report}
    if save:
        save_detections(report)
    return result


def save_detections(report):
    """One save_detection call per hazard. Raises ReducerMissing until Rabah publishes the reducer."""
    for h in report["hazards"]:
        try:
            call_reducer("save_detection", [report["case_id"], h["hazard"], h["severity"], json.dumps(h)])
        except urllib.error.URLError as e:
            raise DataLayerDown(f"SpacetimeDB unreachable: {e.reason}") from None
        except StdbError as e:
            if e.status == 404 or "no such procedure" in e.body.lower() or "no such reducer" in e.body.lower():
                raise ReducerMissing("save_detection is not published on this database yet. Ask Rabah to publish "
                                     "spacetimedb/src/index.ts (no --delete-data), or run without --save.") from None
            raise RuntimeError(f"save_detection rejected for {h['hazard']}: {e}") from None


def _parse_segment(s):
    a, b = s.split("-", 1)
    return float(a), float(b)


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("case")
    p.add_argument("--video")
    p.add_argument("--start", type=float)
    p.add_argument("--end", type=float)
    p.add_argument("--segment", action="append", default=[], help="START-END in seconds, repeatable")
    p.add_argument("--photo", action="append", default=[])
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--live", action="store_true")
    p.add_argument("--save", action="store_true")
    a = p.parse_args(argv)
    case = json.loads(Path(a.case).read_text(encoding="utf-8"))
    segments = []
    if a.video:
        spans = [_parse_segment(s) for s in a.segment] or [(a.start, a.end)]
        segments += [{"type": "video", "path": a.video, "start": s, "end": e} for s, e in spans]
    if a.photo:
        segments.append({"type": "photos", "paths": a.photo})
    if not segments:
        sys.exit("give --video and/or --photo")
    if a.dry_run:
        total = 0.0
        for i, seg in enumerate(segments):
            frames, stats = _segment_frames(seg, case["case_id"], 1, fr.LONG_EDGE, fr.MAX_FRAMES)
            est = estimate(frames)
            total += est["usd"]
            print(json.dumps({"segment": i, **stats, "estimate": est}))
        print(f"total estimate: {len(segments)} calls, ~${total:.3f}")
        return
    try:
        out = analyze(case, segments, cache=ResponseCache(OUT / "cache"), live=a.live, save=a.save)
    except (NoCachedResponse, StaleFixture, ReducerMissing, DataLayerDown, fr.FfmpegMissing) as e:
        sys.exit(str(e))
    print(json.dumps(out, indent=2))


if __name__ == "__main__":
    main()
