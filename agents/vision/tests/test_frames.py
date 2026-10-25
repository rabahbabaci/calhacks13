import random
import shutil
import subprocess

import pytest
from PIL import Image

import frames as fr

pytestmark = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg not installed")


def _encode(pattern_dir, out, fps=1):
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-framerate", str(fps), "-i", f"{pattern_dir}/p_%03d.png",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-r", "25", str(out)], check=True)


@pytest.fixture(scope="module")
def distinct_video(tmp_path_factory):
    """20 seconds, a different random image every second, so nothing is a near-duplicate."""
    d = tmp_path_factory.mktemp("distinct")
    rng = random.Random(7)
    for i in range(20):
        img = Image.new("RGB", (64, 36))
        img.putdata([tuple(rng.randrange(256) for _ in range(3)) for _ in range(64 * 36)])
        img.resize((1280, 720), Image.Resampling.NEAREST).save(d / f"p_{i:03d}.png")
    out = d / "distinct.mp4"
    _encode(d, out)
    return out


@pytest.fixture(scope="module")
def static_video(tmp_path_factory):
    d = tmp_path_factory.mktemp("static")
    out = d / "static.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc=size=640x360:rate=25:duration=10",
                    "-vf", "drawbox=x=0:y=0:w=iw:h=ih:color=gray:t=fill", "-pix_fmt", "yuv420p", str(out)], check=True)
    return out


def test_cap_and_even_spacing(distinct_video, tmp_path):
    frames, stats = fr.extract_video(distinct_video, tmp_path, max_frames=12)
    assert stats["sampled"] == 20 and stats["after_dedupe"] == 20
    assert len(frames) == 12
    times = [f["t_s"] for f in frames]
    assert times[0] == 0 and times[-1] == 19
    assert times == sorted(times)


def test_dedupe_static_video(static_video, tmp_path):
    frames, stats = fr.extract_video(static_video, tmp_path)
    assert stats["sampled"] >= 9
    assert stats["after_dedupe"] == 1
    assert len(frames) == 1


def test_segment_offsets_in_evidence_ids(distinct_video, tmp_path):
    frames, _ = fr.extract_video(distinct_video, tmp_path, start=5, end=10)
    ids = [f["evidence_id"] for f in frames]
    assert ids == ["video_1_t005s", "video_1_t006s", "video_1_t007s", "video_1_t008s", "video_1_t009s"]
    assert all(5 <= f["t_s"] < 10 for f in frames)


def test_resize_long_edge_and_files_written(distinct_video, tmp_path):
    frames, _ = fr.extract_video(distinct_video, tmp_path, start=0, end=3, long_edge=768)
    for f in frames:
        assert max(f["width"], f["height"]) == 768
        with Image.open(f["path"]) as im:
            assert im.format == "JPEG" and im.size == (f["width"], f["height"])


def test_photos_get_photo_ids(tmp_path):
    srcs = []
    for i in range(2):
        p = tmp_path / f"src{i}.png"
        Image.new("RGB", (2000, 1000), (i * 100, 0, 0)).save(p)
        srcs.append(p)
    frames = fr.load_photos(srcs, tmp_path / "out")
    assert [f["evidence_id"] for f in frames] == ["photo_1", "photo_2"]
    assert frames[0]["width"] == 768 and frames[0]["t_s"] is None


def test_bad_segment_rejected(distinct_video, tmp_path):
    with pytest.raises(ValueError):
        fr.extract_video(distinct_video, tmp_path, start=10, end=5)


def test_pick_evenly():
    assert fr.pick_evenly(list(range(5)), 12) == [0, 1, 2, 3, 4]
    assert fr.pick_evenly(list(range(23)), 12)[0] == 0
    assert fr.pick_evenly(list(range(23)), 12)[-1] == 22
    assert len(fr.pick_evenly(list(range(23)), 12)) == 12
