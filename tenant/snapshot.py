"""Crop a zoomed JPEG of the cited vision frame around a hazard."""
import re
import subprocess
from io import BytesIO
from pathlib import Path
from urllib.parse import quote

from PIL import Image

from tenant.layout import hazard_id

ROOT = Path(__file__).resolve().parents[1]
FRAMES = ROOT / "agents" / "vision" / "out" / "frames"
CROPS = ROOT / "data" / "media" / "frames"
VIDEO_SUFFIX = {".mp4", ".mov", ".m4v", ".webm", ".avi"}
PAD = 0.35
MIN_FRAC = 0.2
ASPECT = 16 / 9


def _evidence_id(h):
    return h.get("bbox_frame") or (h.get("evidence_ids") or [None])[0]


def _time_from_id(eid):
    match = re.search(r"_t(\d+(?:\.\d+)?)s$", eid or "")
    return float(match.group(1)) if match else None


def _video_path(case):
    for item in reversed(case.get("media") or []):
        path = Path(item.get("path") or "")
        if path.suffix.lower() in VIDEO_SUFFIX and path.is_file():
            return path
    return None


def _extract_cited(case, eid):
    video = _video_path(case)
    stamp = _time_from_id(eid)
    if video is None or stamp is None:
        return None
    CROPS.mkdir(parents=True, exist_ok=True)
    dest = CROPS / f"{case.get('case_id') or 'case'}_{eid}.jpg"
    if dest.is_file() and dest.stat().st_size > 0:
        return dest
    try:
        subprocess.run(
            ["ffmpeg", "-y", "-ss", str(stamp), "-i", str(video), "-frames:v", "1", "-q:v", "3", str(dest)],
            check=True,
            capture_output=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return None
    return dest if dest.is_file() and dest.stat().st_size > 0 else None


def frame_path(case, h):
    eid = _evidence_id(h)
    if not eid:
        return None
    for row in (case.get("vision") or {}).get("frames") or []:
        if row.get("evidence_id") != eid:
            continue
        path = Path(row.get("path") or "")
        if path.is_file():
            return path
    cid = case.get("case_id") or ""
    for folder in (FRAMES / cid, FRAMES, CROPS):
        for ext in (".jpg", ".jpeg", ".png"):
            path = folder / f"{eid}{ext}"
            if path.is_file():
                return path
            if cid:
                path = folder / f"{cid}_{eid}{ext}"
                if path.is_file():
                    return path
    if case.get("fixture"):
        return None
    return _extract_cited(case, eid)


def photo_url(case, h):
    if frame_path(case, h) is None:
        return ""
    return "/api/hazards/" + quote(hazard_id(h), safe="") + "/photo"


def _box(bbox):
    if not bbox or len(bbox) != 4:
        return 0.22, 0.22, 0.78, 0.78
    x0, y0, x1, y1 = [float(v) for v in bbox]
    if x1 <= x0 or y1 <= y0:
        return 0.22, 0.22, 0.78, 0.78
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    width = max((x1 - x0) * (1 + PAD), MIN_FRAC)
    height = max((y1 - y0) * (1 + PAD), MIN_FRAC / ASPECT)
    if width / height > ASPECT:
        height = width / ASPECT
    else:
        width = height * ASPECT
    left = min(max(cx - width / 2, 0), max(0, 1 - width))
    top = min(max(cy - height / 2, 0), max(0, 1 - height))
    return left, top, min(1, left + width), min(1, top + height)


def jpeg(case, hid):
    h = None
    for item in (case.get("vision") or {}).get("hazards") or []:
        if hazard_id(item) == hid:
            h = item
            break
    if h is None:
        return None, "Unknown hazard."
    path = frame_path(case, h)
    if path is None:
        return None, "No frame for this hazard."
    left, top, right, bottom = _box(h.get("bbox"))
    with Image.open(path) as im:
        img = im.convert("RGB")
        w, ht = img.size
        crop = img.crop((int(left * w), int(top * ht), int(right * w), int(bottom * ht)))
        if max(crop.size) > 900:
            crop.thumbnail((900, 900), Image.Resampling.LANCZOS)
        buf = BytesIO()
        crop.save(buf, "JPEG", quality=88)
        return buf.getvalue(), ""
