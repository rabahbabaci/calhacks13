"""Six behavioral evals for the vision agent. Replay by default: no API calls, no credits.

Usage (repo root):
  data/.venv/bin/python agents/vision/evals.py                 # replay all from agents/vision/fixtures/
  data/.venv/bin/python agents/vision/evals.py a c             # a subset by letter
  data/.venv/bin/python agents/vision/evals.py --dry-run       # extract frames, print estimated cost, no API call
  data/.venv/bin/python agents/vision/evals.py --live [--budget-usd 0.50]
                                                               # call Claude only where no fixture exists, record it
Media (gitignored, never committed) lives in agents/vision/out/media/:
  jakarta.mp4        data/.venv/bin/yt-dlp -f "bv*[height<=720][ext=mp4]+ba[ext=m4a]/b[height<=720]" \
                       --merge-output-format mp4 -o agents/vision/out/media/jakarta.mp4 \
                       "https://www.youtube.com/watch?v=wFxmYq9wCWM"
  exposed_wiring_pd.jpg  https://commons.wikimedia.org/wiki/File:Opened_light_switch.JPG (public domain)
  clean_room_cc0.jpg     https://commons.wikimedia.org/wiki/File:Room_at_Hotel_Mercosur_Universitas.jpg (CC0)
Without media, replay still works from the frames recorded in each fixture (no key check).
Writes agents/vision/out/evals.json. Exit code 1 on any failure.
"""
import json
import re
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import agent  # noqa: E402
import frames as fr  # noqa: E402
import rules  # noqa: E402

MEDIA = HERE / "out" / "media"
FIXTURES = HERE / "fixtures"
OUT = HERE / "out" / "evals.json"
LONG_EDGE = 768
JAKARTA_URL = "https://www.youtube.com/watch?v=wFxmYq9wCWM"

JAKARTA = json.loads((HERE / "cases" / "jakarta_demo.json").read_text(encoding="utf-8"))
OAKLAND = {"case_id": "eval_photo", "address": "123 Example St, Oakland, CA 94612",
           "jurisdiction": {"state": "CA", "city": "Oakland"}}

# Frames that mostly show the vlogger's face or a picture-in-picture title card, found by looking at the
# extracted frames (no API). No hazard may cite them.
FACE_FRAMES = {
    "a": ["video_1_t270s", "video_1_t325s"],
    "b": ["video_1_t574s"],
    "c": ["video_1_t646s"],
    "f": [],
}


def jakarta(letter, start, end):
    return {**JAKARTA, "case_id": f"eval_{letter}_jakarta"}, [
        {"type": "video", "path": str(MEDIA / "jakarta.mp4"), "start": start, "end": end}]


def photo(letter, name):
    return {**OAKLAND, "case_id": f"eval_{letter}_photo"}, [{"type": "photos", "paths": [str(MEDIA / name)]}]


# ---------- checks ----------

def tags(r, min_conf=0.0):
    return [h["hazard"] for h in r["vision_report"]["hazards"] if h["confidence"] >= min_conf]


def text_of(h):
    return " ".join(h["observed_signs"] + [h["location"], h["severity_reason"], h["check_reason"]]).lower()


def unlabeled_text(r):
    return " ".join(f"{u['description']} {u['suggested_tag']}" for u in r["vision_report"]["unlabeled_observations"]).lower()


def common(r, letter, segment):
    f = []
    rep = r["vision_report"]
    face = set(FACE_FRAMES.get(letter, []))
    for h in rep["hazards"]:
        if face & set(h["evidence_ids"]):
            f.append(f"{h['hazard']} cites a face/title frame: {sorted(face & set(h['evidence_ids']))}")
        if segment and h["first_seen_s"] is not None and not (segment[0] <= h["first_seen_s"] < segment[1]):
            f.append(f"{h['hazard']} first_seen_s {h['first_seen_s']} outside segment {segment}")
        if h["hazard"] == "dampness_mold" and h["label"] != "Visible dampness or possible mold growth":
            f.append("dampness_mold label wrong")
        if re.search(r"\b(this|it|that) is (black |toxic )?mold\b", text_of(h)):
            f.append("asserts mold as a finding")
        if h["hazard"] == "water_leak" and "rust" in text_of(h) and not re.search(r"water|leak|drip|stain|damp|wet", text_of(h)):
            f.append("rust forced into water_leak")
        if "legal" not in h or "citations" not in h["legal"]:
            f.append(f"{h['hazard']} has no legal attachment")
    for h in r["case_input"]["hazards"]:
        if set(h) != {"hazard", "location", "confidence", "confirmed_by_tenant", "evidence_ids"}:
            f.append("case_input hazard not in contract section 2 shape")
    if letter in "abcf" and rep.get("notes") != JAKARTA["notes"]:
        f.append("report does not carry the demo note")
    return f


def eval_a(r):
    """Kitchen. Grime and possible growth at the faucet base; cockroaches are only in the captions."""
    f = []
    if "cockroaches" in tags(r, 0.5):
        f.append("cockroaches reported at confidence >= 0.5, but none are visible (caption only)")
    kitchen = [h for h in r["vision_report"]["hazards"] if "kitchen" in h["room"].lower()]
    if not kitchen and "kitchen" not in unlabeled_text(r):
        f.append("nothing found in the kitchen")
    return f


def eval_b(r):
    """Balcony. Peeling paint on the sill, cracked concrete, rusty railings."""
    f = []
    lead = [h for h in r["vision_report"]["hazards"] if h["hazard"] == "lead_paint"]
    if not lead:
        f.append("peeling paint not reported as lead_paint")
    for h in lead:
        if h["severity"] != "moderate" or not h["needs_professional_check"]:
            f.append("lead_paint not moderate + needs_professional_check")
    if not re.search(r"rust|crack", unlabeled_text(r)):
        f.append("rust or cracks not in unlabeled_observations")
    return f


def eval_c(r):
    """Bathroom. Water-stain tide line on the ceiling, dark line at the wall-ceiling joint, rusty shower."""
    f = []
    bath = [h for h in r["vision_report"]["hazards"]
            if h["hazard"] in ("water_leak", "dampness_mold") and "bath" in h["room"].lower()]
    if not bath:
        f.append("no water_leak or dampness_mold in the bathroom")
    if not any("ceiling" in h["location"].lower() for h in bath):
        f.append("ceiling stain not located on the ceiling")
    # The shower head's "rust" is only in the caption; at 768px it reads as scale. Cracks are clearly visible.
    if not re.search(r"rust|crack", unlabeled_text(r)):
        f.append("rust or cracks not in unlabeled_observations")
    return f


def eval_d(r):
    """Clean, well-kept hotel room. False-positive check."""
    t = tags(r)
    return [f"hazards reported in a clean room: {t}"] if t else []


def eval_e(r):
    """Open switch box with wires visible."""
    f = []
    w = [h for h in r["vision_report"]["hazards"] if h["hazard"] == "exposed_wiring"]
    if not w:
        return ["exposed_wiring not reported"]
    h = w[0]
    if h["severity"] != "urgent" or "911" not in h.get("urgent_message", ""):
        f.append("exposed_wiring not urgent with the fixed message")
    if not any(c["cite"].startswith("Cal. Civil Code 1941.1(a)(5)") for c in h["legal"]["citations"]):
        f.append("legal citation 1941.1(a)(5) not attached")
    return f


def eval_f(r):
    """Shower walls only: dark grout lines on tiles meant to get wet."""
    f = []
    for h in r["vision_report"]["hazards"]:
        if h["hazard"] == "dampness_mold":
            if h["severity"] != "low" or not h.get("wet_surface"):
                f.append(f"shower-only dampness_mold rated {h['severity']}, wet_surface={h.get('wet_surface')}")
            if not re.search(r"shower|wet|grout|tile", text_of(h)):
                f.append("shower dampness_mold does not mention the wet surface")
    return f


EVALS = {
    "a": ("jakarta kitchen 4:30-5:30, cockroaches caption-only", lambda: jakarta("a", 270, 330), (270, 330), eval_a),
    "b": ("jakarta balcony 8:40-9:40, peeling paint + rust", lambda: jakarta("b", 520, 580), (520, 580), eval_b),
    "c": ("jakarta bathroom 9:50-10:50, ceiling stain + rust", lambda: jakarta("c", 590, 650), (590, 650), eval_c),
    "d": ("clean hotel room photo (CC0), zero hazards", lambda: photo("d", "clean_room_cc0.jpg"), None, eval_d),
    "e": ("open switch box photo (public domain), urgent", lambda: photo("e", "exposed_wiring_pd.jpg"), None, eval_e),
    "f": ("jakarta shower walls 10:05-10:17, shower caution", lambda: jakarta("f", 605, 617), (605, 617), eval_f),
}


def media_present(segments):
    return all(Path(s.get("path") or s["paths"][0]).exists() for s in segments)


def prepared_from_fixture(name):
    rec = agent.ResponseCache(FIXTURES).get(name)
    if rec is None:
        return None
    return [{**f, "path": None} for f in rec["frames"]]


def main():
    args = sys.argv[1:]
    live, dry = "--live" in args, "--dry-run" in args
    budget_usd = float(args[args.index("--budget-usd") + 1]) if "--budget-usd" in args else 0.50
    letters = [a for a in args if a in EVALS] or list(EVALS)
    budget = agent.Budget(budget_usd)
    cache = agent.ResponseCache(FIXTURES)
    results, passed, total_est = {}, 0, 0.0
    for k in letters:
        name, build, segment, fn = EVALS[k]
        case, segments = build()
        fixture = f"eval_{k}"
        t0 = time.time()
        try:
            if media_present(segments):
                segs = []
                for s in segments:
                    frames_dir = HERE / "out" / "frames" / case["case_id"]
                    if s["type"] == "video":
                        frames, _ = fr.extract_video(s["path"], frames_dir, start=s["start"], end=s["end"], long_edge=LONG_EDGE)
                    else:
                        frames = fr.load_photos(s["paths"], frames_dir, long_edge=LONG_EDGE)
                    segs.append({**s, "frames": frames})
            else:
                frames = prepared_from_fixture(fixture)
                if frames is None:
                    raise FileNotFoundError(f"media missing and no fixture for {k}; see the download notes in this file")
                segs = [{**segments[0], "frames": frames}]
                print(f"       ({k}: media not present, replaying fixture frames without key check)")
            if dry:
                est = agent.estimate(segs[0]["frames"])
                cached = (cache.get(fixture) or {}).get("key") == agent.cache_key(segs[0]["frames"], case)
                total_est += 0 if cached else est["usd"]
                print(f"[DRY ] {k}: {name}: {est['frames']} frames, ~{est['input_tokens']} in tokens, "
                      f"~${est['usd']}{' (cached, free)' if cached else ''}")
                print("       frames: " + ", ".join(f["evidence_id"] for f in segs[0]["frames"]))
                continue
            if not media_present(segments):
                rec = cache.get(fixture)
                r = replay_without_key(case, segs, rec)
            else:
                r = agent.analyze(case, segs, cache=cache, live=live, budget=budget, cache_names={0: fixture})
            fails = common(r, k, segment) + fn(r)
        except Exception as e:  # noqa: BLE001
            r, fails = {"error": repr(e)}, [f"exception: {e!r}"]
        ok = not fails
        passed += ok
        usage = (r.get("vision_report") or {}).get("usage", [])
        spent = sum(u.get("usd", 0) for u in usage)
        tok = sum(u.get("input_tokens", 0) for u in usage), sum(u.get("output_tokens", 0) for u in usage)
        results[k] = {"name": name, "pass": ok, "fails": fails, "seconds": round(time.time() - t0, 1),
                      "tokens_in_out": tok, "usd": spent, "result": r}
        tag = "cached" if usage and all(u.get("cached") for u in usage) else f"${spent:.3f}"
        print(f"[{'PASS' if ok else 'FAIL'}] {k}: {name} ({results[k]['seconds']}s, {tok[0]} in / {tok[1]} out, {tag})")
        for msg in fails:
            print(f"       - {msg}")
    if dry:
        print(f"\nlive cost for the uncached cases above: ~${total_est:.3f} (budget ${budget_usd:.2f})")
        return
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(json.dumps(results, indent=2), encoding="utf-8")
    spent = sum(v["usd"] for v in results.values())
    print(f"\n{passed}/{len(letters)} passed. Spent ${spent:.3f} this run (budget ${budget_usd:.2f}). Details in {OUT}")
    sys.exit(0 if passed == len(letters) else 1)


def replay_without_key(case, segs, rec):
    """Teammates without the media: run validation and merge on the recorded response and frames."""
    class Replay:
        def __init__(self, resp):
            self.messages = self
            self.resp = resp

        def create(self, **kw):
            return self.resp

    frames = segs[0]["frames"]
    orig = agent.build_content
    agent.build_content = lambda fs, c: []
    try:
        r = agent.analyze(case, [{"type": segs[0]["type"], "frames": frames}], client=Replay(rec["response"]), live=True)
    finally:
        agent.build_content = orig
    for u in r["vision_report"]["usage"]:
        u.update({"cached": True, "usd": 0.0})
    return r


if __name__ == "__main__":
    main()
