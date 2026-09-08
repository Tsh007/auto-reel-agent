"""
step3_assemble_upload.py
Stage 3 – TTS audio generation, FFmpeg video assembly, file hosting & Instagram upload.

Reads : output/script.json  (with image_path fields from Step 2)
Writes: output/audio/*.mp3, output/segments/*.mp4, output/reel.mp4
Posts : Instagram Reel via Graph API v26.0
"""

from __future__ import annotations

import asyncio
import json
import math
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import requests

from config import (
    AUDIO_DIR,
    AUDIO_OVERLAP_S,
    FINAL_VIDEO,
    IG_ACCESS_TOKEN,
    IG_API_BASE,
    IG_USER_ID,
    KENBURNS_ZOOM_RATIO,
    OUTPUT_DIR,
    SCRIPT_JSON,
    SEGMENTS_DIR,
    TTS_VOICE,
    VIDEO_FPS,
    VIDEO_HEIGHT,
    VIDEO_WIDTH,
    ensure_dirs,
    logger,
)

# ---------------------------------------------------------------------------
# Helper – run subprocess safely
# ---------------------------------------------------------------------------


def _run(cmd: list[str], label: str = "") -> None:
    """Run a shell command; raise on non-zero exit."""
    logger.debug("  CMD%s: %s", f" [{label}]" if label else "", " ".join(cmd))
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        logger.error("STDERR: %s", result.stderr[-2000:])
        raise RuntimeError(
            f"Command failed (exit {result.returncode}){' – ' + label if label else ''}"
        )


# ---------------------------------------------------------------------------
# Stage 3a – TTS via edge-tts
# ---------------------------------------------------------------------------


async def _synthesize_one(text: str, dest: Path, voice: str) -> None:
    """Async helper: synthesise one sentence to MP3."""
    import edge_tts  # imported lazily so tests without edge-tts still import

    communicate = edge_tts.Communicate(text, voice)
    await communicate.save(str(dest))


def generate_audio_files(sentences: list[dict[str, Any]], audio_dir: str = AUDIO_DIR) -> list[Path]:
    """
    Generate one MP3 per sentence using edge-tts.
    Returns ordered list of Paths.
    """
    out = Path(audio_dir)
    out.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []

    for idx, sentence in enumerate(sentences):
        text = sentence["text"]
        dest = out / f"{idx}.mp3"
        logger.info("TTS %d: '%s' → %s", idx, text, dest)
        asyncio.run(_synthesize_one(text, dest, TTS_VOICE))
        sentence["audio_path"] = str(dest)
        paths.append(dest)

    logger.info("Generated %d audio files.", len(paths))
    return paths


# ---------------------------------------------------------------------------
# Stage 3b – Measure audio durations
# ---------------------------------------------------------------------------


def get_audio_duration(path: Path) -> float:
    """Return duration of an audio file in seconds via ffprobe."""
    cmd = [
        "ffprobe", "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        str(path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"ffprobe failed on {path}: {result.stderr}")
    return float(result.stdout.strip())


def annotate_durations(sentences: list[dict[str, Any]]) -> list[float]:
    """
    Add 'duration' key to each sentence dict.
    Returns list of durations in sentence order.
    """
    durations: list[float] = []
    for s in sentences:
        dur = get_audio_duration(Path(s["audio_path"]))
        s["duration"] = dur
        durations.append(dur)
        logger.debug("  Duration of '%s…': %.2fs", s["text"][:30], dur)
    total = sum(durations)
    logger.info("Total audio duration: %.2fs", total)
    return durations


# ---------------------------------------------------------------------------
# Stage 3c – Build per-sentence video segments with FFmpeg
# ---------------------------------------------------------------------------


def _escape_drawtext(text: str) -> str:
    """
    Escape a string for FFmpeg's drawtext filter.
    Characters that must be escaped: ' : \\ [ ]
    """
    # Order matters: escape backslash first
    text = text.replace("\\", "\\\\")
    text = text.replace("'", "\\'")
    text = text.replace(":", "\\:")
    text = text.replace("[", "\\[")
    text = text.replace("]", "\\]")
    return text


def build_segment(
    image_path: Path,
    audio_path: Path,
    text: str,
    duration: float,
    dest: Path,
    zoom_direction: str = "in",   # "in" | "out"
) -> None:
    """
    Build a single video segment:
      - Ken Burns zoom+pan on the image
      - Text overlay at the bottom
      - Exactly <duration + AUDIO_OVERLAP_S> seconds long
      - Audio from the MP3 file
    """
    total_dur = duration + AUDIO_OVERLAP_S
    total_frames = math.ceil(total_dur * VIDEO_FPS)
    w, h = VIDEO_WIDTH, VIDEO_HEIGHT

    # Ken Burns: zoompan filter
    # zoom_in: start at 1.0, end at ~1.15
    # zoom_out: start at 1.15, end at 1.0
    zoom_speed = KENBURNS_ZOOM_RATIO
    if zoom_direction == "in":
        zoom_expr = f"min(1.0+{zoom_speed}*on,1.15)"
        x_expr = "iw/2-(iw/zoom/2)"
        y_expr = "ih/2-(ih/zoom/2)"
    else:
        zoom_expr = f"max(1.15-{zoom_speed}*on,1.0)"
        x_expr = "iw/2-(iw/zoom/2)"
        y_expr = "ih/2-(ih/zoom/2)"

    zoompan = (
        f"zoompan="
        f"z='{zoom_expr}':"
        f"x='{x_expr}':"
        f"y='{y_expr}':"
        f"d={total_frames}:"
        f"s={w}x{h}:"
        f"fps={VIDEO_FPS}"
    )

    # Text styling
    font_size = max(36, int(h * 0.045))   # ~4.5% of height
    box_h = font_size + 32
    safe_text = _escape_drawtext(text)

    drawtext = (
        f"drawtext="
        f"text='{safe_text}':"
        f"fontcolor=white:"
        f"fontsize={font_size}:"
        f"font='DejaVu Sans Bold':"
        f"x=(w-text_w)/2:"                      # horizontally centred
        f"y=h-{box_h}:"                         # near bottom
        f"box=1:"
        f"boxcolor=black@0.55:"
        f"boxborderw=12:"
        f"line_spacing=4"
    )

    # Build the FFmpeg filter chain
    vf = f"{zoompan},{drawtext}"

    cmd = [
        "ffmpeg", "-y",
        "-loop", "1",
        "-i", str(image_path),
        "-i", str(audio_path),
        "-filter_complex", vf,
        "-c:v", "libx264",
        "-preset", "fast",
        "-crf", "23",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac",
        "-b:a", "128k",
        "-shortest",                             # trim to the shorter of video/audio
        "-t", str(total_dur),
        "-r", str(VIDEO_FPS),
        str(dest),
    ]
    _run(cmd, label=f"segment {dest.name}")
    logger.info("  Segment written: %s", dest)


def build_all_segments(sentences: list[dict[str, Any]], segments_dir: str = SEGMENTS_DIR) -> list[Path]:
    """Build all per-sentence video segments and return their paths."""
    out = Path(segments_dir)
    out.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []

    zoom_dirs = ["in", "out"]  # alternate directions for visual variety
    for idx, s in enumerate(sentences):
        dest = out / f"segment_{idx:02d}.mp4"
        logger.info("Building segment %d/%d…", idx + 1, len(sentences))
        build_segment(
            image_path=Path(s["image_path"]),
            audio_path=Path(s["audio_path"]),
            text=s["text"],
            duration=s["duration"],
            dest=dest,
            zoom_direction=zoom_dirs[idx % 2],
        )
        paths.append(dest)

    return paths


# ---------------------------------------------------------------------------
# Stage 3d – Concatenate segments → final MP4
# ---------------------------------------------------------------------------


def concatenate_segments(segment_paths: list[Path], output: str = FINAL_VIDEO) -> Path:
    """
    Write a concat list file and merge all segments into a single MP4
    using FFmpeg's concat demuxer (lossless stream copy).
    """
    concat_list = Path(OUTPUT_DIR) / "concat_list.txt"
    with concat_list.open("w", encoding="utf-8") as fh:
        for p in segment_paths:
            # Use absolute paths so FFmpeg doesn't resolve them relative
            # to the concat list's own directory (which would double the prefix).
            fh.write(f"file '{p.resolve().as_posix()}'\n")

    out_path = Path(output)
    cmd = [
        "ffmpeg", "-y",
        "-f", "concat",
        "-safe", "0",
        "-i", str(concat_list),
        "-c:v", "libx264",
        "-preset", "fast",
        "-crf", "23",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac",
        "-b:a", "128k",
        "-movflags", "+faststart",   # web-optimised
        str(out_path),
    ]
    _run(cmd, label="concat")
    logger.info("Final video: %s (%.1f MB)", out_path, out_path.stat().st_size / 1e6)
    return out_path


# ---------------------------------------------------------------------------
# Stage 3e – Upload video to tmpfiles.org for a public URL
# ---------------------------------------------------------------------------


def upload_to_tmpfiles(video_path: Path) -> str:
    """
    Upload the video to tmpfiles.org and return a publicly accessible URL.
    API: POST https://tmpfiles.org/api/v1/upload  (multipart/form-data, field 'file')
    Response: {"status":"success","data":{"url":"https://tmpfiles.org/XXXXX/reel.mp4"}}
    """
    upload_url = "https://tmpfiles.org/api/v1/upload"
    logger.info("Uploading video to tmpfiles.org (%s)…", video_path.name)

    with video_path.open("rb") as fh:
        resp = requests.post(
            upload_url,
            files={"file": (video_path.name, fh, "video/mp4")},
            timeout=300,
        )

    resp.raise_for_status()
    payload = resp.json()

    if payload.get("status") != "success":
        raise RuntimeError(f"tmpfiles.org upload failed: {payload}")

    page_url: str = payload["data"]["url"]
    # tmpfiles.org returns a page URL like https://tmpfiles.org/12345/reel.mp4
    # The raw/direct download URL is https://tmpfiles.org/dl/12345/reel.mp4
    direct_url = page_url.replace("tmpfiles.org/", "tmpfiles.org/dl/")
    logger.info("Public video URL: %s", direct_url)
    return direct_url


# ---------------------------------------------------------------------------
# Stage 3f – Instagram Graph API upload
# ---------------------------------------------------------------------------


def _ig_post(endpoint: str, payload: dict[str, Any]) -> dict[str, Any]:
    """POST to the Instagram API; raise on error."""
    url = f"{IG_API_BASE}/{endpoint}"
    payload["access_token"] = IG_ACCESS_TOKEN
    resp = requests.post(url, data=payload, timeout=120)

    try:
        data = resp.json()
    except ValueError:
        raise RuntimeError(f"Non-JSON response from Instagram API: {resp.text[:500]}")

    if "error" in data:
        raise RuntimeError(f"Instagram API error: {data['error']}")

    return data


def _ig_get(endpoint: str, params: dict[str, Any]) -> dict[str, Any]:
    """GET from the Instagram Graph API; raise on error."""
    url = f"{IG_API_BASE}/{endpoint}"
    params["access_token"] = IG_ACCESS_TOKEN
    resp = requests.get(url, params=params, timeout=60)
    data = resp.json()
    if "error" in data:
        raise RuntimeError(f"Instagram API error: {data['error']}")
    return data


def resolve_target_user_id() -> str:
    """
    Auto-detect the target Instagram User ID.
    1. If IG_USER_ID is set to a valid numeric ID (not "me" or placeholders), try using it.
    2. Otherwise, query GET /me?fields=id using access_token to get the true App-Scoped ID.
    3. Fall back to "me" if query is unsupported.
    """
    if IG_USER_ID and IG_USER_ID not in ("me", "placeholder") and IG_USER_ID.isdigit():
        return IG_USER_ID

    try:
        data = _ig_get("me", {"fields": "id,username"})
        user_id = data.get("id")
        username = data.get("username", "")
        if user_id:
            logger.info("Auto-resolved Instagram User ID: %s (username: @%s)", user_id, username)
            return user_id
    except Exception as exc:
        logger.warning("Could not auto-resolve ID from GET /me: %s. Falling back to 'me'.", exc)

    return "me"


def upload_reel_to_instagram(
    video_url: str,
    caption: str,
    *,
    poll_interval_s: float = 15.0,
    max_wait_s: float = 300.0,
) -> str:
    """
    Two-phase Instagram Reel upload:
      1. Create a media container (async processing).
      2. Poll until status == FINISHED, then publish.

    Returns the published media ID.
    """
    target_id = resolve_target_user_id()
    logger.info("Creating Instagram Reel media container (target: %s)…", target_id)

    container_id: str | None = None
    for attempt in range(1, 4):
        try:
            container = _ig_post(
                f"{target_id}/media",
                {
                    "media_type": "REELS",
                    "video_url": video_url,
                    "caption": caption,
                    "share_to_feed": "true",
                },
            )
            container_id = container["id"]
            break
        except Exception as exc:
            logger.warning("Container creation attempt %d/3 failed: %s", attempt, exc)
            if ("33" in str(exc) or "100" in str(exc)) and target_id != "me":
                logger.info("Retrying container creation using endpoint target 'me'…")
                target_id = "me"
            if attempt == 3:
                raise
            time.sleep(3)

    assert container_id is not None
    logger.info("Container created: %s", container_id)

    # Poll for processing completion
    logger.info("Polling for processing status (max %.0fs)…", max_wait_s)
    deadline = time.monotonic() + max_wait_s
    while time.monotonic() < deadline:
        status_resp = _ig_get(
            container_id,
            {"fields": "status,status_code"},
        )
        status_code = status_resp.get("status_code", "")
        logger.info("  Status: %s", status_code)

        if status_code == "FINISHED":
            break
        if status_code == "ERROR":
            raise RuntimeError(f"Instagram media processing error: {status_resp}")
        if status_code not in ("IN_PROGRESS", "PUBLISHED", ""):
            logger.warning("Unexpected status code: %s — will keep polling.", status_code)

        time.sleep(poll_interval_s)
    else:
        raise TimeoutError(
            f"Instagram media container {container_id} did not finish processing "
            f"within {max_wait_s}s."
        )

    # Publish
    logger.info("Publishing Reel…")
    publish_resp = _ig_post(
        f"{target_id}/media_publish",
        {"creation_id": container_id},
    )
    media_id: str = publish_resp["id"]
    logger.info("Reel published! Media ID: %s", media_id)
    return media_id


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def load_script(path: str = SCRIPT_JSON) -> dict[str, Any]:
    script_path = Path(path)
    if not script_path.exists():
        raise FileNotFoundError(
            f"Script not found: {script_path}. Run step1 and step2 first."
        )
    with script_path.open(encoding="utf-8") as fh:
        return json.load(fh)


def save_script(script: dict[str, Any], path: str = SCRIPT_JSON) -> None:
    Path(path).write_text(json.dumps(script, indent=2, ensure_ascii=False), encoding="utf-8")


def main() -> None:
    ensure_dirs()

    # ── Load script ───────────────────────────────────────────────────────
    script = load_script()
    sentences = script["sentences"]

    # ── Stage 3a: TTS ────────────────────────────────────────────────────
    logger.info("=== Stage 3a: Generating TTS audio ===")
    generate_audio_files(sentences)

    # ── Stage 3b: Measure durations ───────────────────────────────────────
    logger.info("=== Stage 3b: Measuring audio durations ===")
    annotate_durations(sentences)

    # Save updated script (now has audio_path + duration)
    save_script(script)

    # ── Stage 3c: Build segments ──────────────────────────────────────────
    logger.info("=== Stage 3c: Building video segments ===")
    segment_paths = build_all_segments(sentences)

    # ── Stage 3d: Concatenate ─────────────────────────────────────────────
    logger.info("=== Stage 3d: Concatenating segments ===")
    final_video = concatenate_segments(segment_paths)

    # ── Stage 3e: Upload for public URL ───────────────────────────────────
    logger.info("=== Stage 3e: Uploading to tmpfiles.org ===")
    public_url = upload_to_tmpfiles(final_video)

    # ── Stage 3f: Instagram upload ────────────────────────────────────────
    logger.info("=== Stage 3f: Uploading to Instagram ===")
    media_id = upload_reel_to_instagram(
        video_url=public_url,
        caption=script["description"],
    )

    print("\n" + "=" * 60)
    print(f"  ✅  Reel published successfully!")
    print(f"  Title     : {script['title']}")
    print(f"  Media ID  : {media_id}")
    print(f"  Video URL : {public_url}")
    print("=" * 60 + "\n")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # pylint: disable=broad-except
        logger.critical("Step 3 failed: %s", exc, exc_info=True)
        sys.exit(1)
