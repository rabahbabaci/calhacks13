"""Frame extraction. No LLM. ffmpeg samples, Pillow hashes, dedupes and resizes.

Every frame gets an evidence_id: "video_<n>_t<seconds>s" (seconds from the start of the source
video, not the segment) or "photo_<n>".
"""
import hashlib
import json
import shutil
import subprocess
import tempfile
from pathlib import Path

from PIL import Image

MAX_FRAMES = 12
LONG_EDGE = 768
JPEG_QUALITY = 80
DEDUPE_BITS = 6  # dhash Hamming distance at or below this counts as a near-duplicate


class FfmpegMissing(RuntimeError):
    pass


def _ffmpeg(name):
    path = shutil.which(name)
    if not path:
        raise FfmpegMissing(f"{name} not found. Install with: brew install ffmpeg")
    return path


def duration_s(video):
    out = subprocess.run(
        [_ffmpeg("ffprobe"), "-v", "error", "-show_entries", "format=duration", "-of", "json", str(video)],
        capture_output=True, text=True, check=True,
    ).stdout
    return float(json.loads(out)["format"]["duration"])


def dhash(img, size=8):
    g = img.convert("L").resize((size + 1, size), Image.Resampling.LANCZOS)
    px = g.tobytes()
    bits = 0
    for row in range(size):
        for col in range(size):
            left = px[row * (size + 1) + col]
            right = px[row * (size + 1) + col + 1]
            bits = (bits << 1) | (left > right)
    return bits


def hamming(a, b):
    return bin(a ^ b).count("1")


def pick_evenly(items, cap):
    if len(items) <= cap:
        return list(items)
    if cap == 1:
        return [items[0]]
    step = (len(items) - 1) / (cap - 1)
    return [items[round(i * step)] for i in range(cap)]


def resize(img, long_edge):
    img = img.convert("RGB")
    if max(img.size) > long_edge:
        img.thumbnail((long_edge, long_edge), Image.Resampling.LANCZOS)
    return img


def video_id(n, t):
    return f"video_{n}_t{int(t):03d}s"


def _save(img, path):
    path.parent.mkdir(parents=True, exist_ok=True)
    img.save(path, "JPEG", quality=JPEG_QUALITY)
    return hashlib.sha256(path.read_bytes()).hexdigest()


def extract_video(video, out_dir, video_n=1, start=None, end=None, fps=1.0,
                  max_frames=MAX_FRAMES, long_edge=LONG_EDGE):
    """Sample a segment at `fps`, drop near-duplicates, cap evenly, resize, save.

    Returns frames: [{evidence_id, t_s, path, sha256, width, height}], and stats for the log.
    """
    video = Path(video)
    start = float(start or 0)
    end = float(end) if end is not None else duration_s(video)
    if end <= start:
        raise ValueError(f"segment end {end} must be after start {start}")
    out_dir = Path(out_dir)
    with tempfile.TemporaryDirectory() as tmp:
        scale = (f"scale='if(gt(iw,ih),min({long_edge},iw),-2)':'if(gt(iw,ih),-2,min({long_edge},ih))'"
                 ":flags=bilinear")
        subprocess.run(
            [_ffmpeg("ffmpeg"), "-v", "error", "-ss", f"{start}", "-to", f"{end}", "-i", str(video),
             "-an", "-sn", "-vf", f"fps={fps},{scale}", "-q:v", "3", f"{tmp}/f_%05d.jpg"],
            check=True,
        )
        raw = sorted(Path(tmp).glob("f_*.jpg"))
        kept, last = [], None
        for i, p in enumerate(raw):
            t = start + i / fps
            if t >= end:
                break
            with Image.open(p) as im:
                h = dhash(im)
            if last is not None and hamming(h, last) <= DEDUPE_BITS:
                continue
            kept.append((t, p))
            last = h
        chosen = pick_evenly(kept, max_frames)
        frames = []
        for t, p in chosen:
            eid = video_id(video_n, t)
            with Image.open(p) as im:
                img = resize(im, long_edge)
            dest = out_dir / f"{eid}.jpg"
            sha = _save(img, dest)
            frames.append({"evidence_id": eid, "t_s": round(t, 1), "path": str(dest), "sha256": sha,
                           "width": img.width, "height": img.height})
    stats = {"video": str(video), "start": start, "end": end, "sampled": len(raw), "after_dedupe": len(kept),
             "sent": len(frames)}
    return frames, stats


def load_photos(paths, out_dir, long_edge=LONG_EDGE, first_n=1):
    frames = []
    for i, src in enumerate(paths, start=first_n):
        eid = f"photo_{i}"
        with Image.open(src) as im:
            img = resize(im, long_edge)
        dest = Path(out_dir) / f"{eid}.jpg"
        sha = _save(img, dest)
        frames.append({"evidence_id": eid, "t_s": None, "path": str(dest), "sha256": sha,
                       "width": img.width, "height": img.height})
    return frames


def contact_sheet(video, out_dir, every_s=10, cols=4, rows=5, thumb_w=360):
    """Tiled overviews of the whole video with timestamps, for picking segments by eye. No LLM."""
    from PIL import ImageDraw

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    sheets = []
    with tempfile.TemporaryDirectory() as tmp:
        subprocess.run(
            [_ffmpeg("ffmpeg"), "-v", "error", "-i", str(video), "-vf", f"fps=1/{every_s},scale={thumb_w}:-2",
             f"{tmp}/s_%05d.jpg"],
            check=True,
        )
        thumbs = sorted(Path(tmp).glob("s_*.jpg"))
        per = cols * rows
        for s in range(0, len(thumbs), per):
            batch = thumbs[s:s + per]
            with Image.open(batch[0]) as first:
                tw, th = first.size
            sheet = Image.new("RGB", (cols * tw, rows * th), "black")
            draw = ImageDraw.Draw(sheet)
            for j, p in enumerate(batch):
                x, y = (j % cols) * tw, (j // cols) * th
                with Image.open(p) as im:
                    sheet.paste(im, (x, y))
                t = (s + j) * every_s
                draw.rectangle([x, y, x + 70, y + 18], fill="black")
                draw.text((x + 4, y + 3), f"{t // 60}:{t % 60:02d}", fill="yellow")
            dest = out_dir / f"sheet_{s // per + 1:02d}.jpg"
            sheet.save(dest, "JPEG", quality=80)
            sheets.append(dest)
    return sheets
