"""
step2_generate_images.py
Stage 2 – AI image generation via Pollinations.ai.

Reads: output/script.json
Writes: output/images/0.png, 1.png, … (one per sentence)
"""

from __future__ import annotations

import json
import random
import sys
import time
import urllib.parse
from pathlib import Path
from typing import Any

import requests
from PIL import Image

from config import (
    IMAGES_DIR,
    POLLINATIONS_BASE_URL,
    POLLINATIONS_PARAMS,
    SCRIPT_JSON,
    VIDEO_HEIGHT,
    VIDEO_WIDTH,
    ensure_dirs,
    logger,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

REQUEST_TIMEOUT_S: int = 120          # Pollinations can be slow on first request
MAX_RETRIES: int = 4
RETRY_BACKOFF_BASE: float = 3.0      # seconds

# ---------------------------------------------------------------------------
# Pollinations helpers
# ---------------------------------------------------------------------------


def build_pollinations_url(prompt: str, seed: int) -> str:
    """Return the fully encoded Pollinations image URL for a given prompt."""
    # URL-encode the prompt (spaces → %20, special chars escaped)
    encoded_prompt = urllib.parse.quote(prompt, safe="")
    base = POLLINATIONS_BASE_URL.format(prompt=encoded_prompt)

    params = {**POLLINATIONS_PARAMS, "seed": seed}
    query = "&".join(f"{k}={v}" for k, v in params.items())
    return f"{base}?{query}"


def download_image(url: str, dest: Path, retries: int = MAX_RETRIES) -> None:
    """Download an image from *url* and save it as a verified PNG at *dest*."""
    for attempt in range(1, retries + 1):
        logger.info("  [%d/%d] Downloading: %s", attempt, retries, url)
        try:
            resp = requests.get(url, timeout=REQUEST_TIMEOUT_S, stream=True)
            resp.raise_for_status()

            dest.write_bytes(resp.content)

            # Verify the file is a valid image and matches expected dimensions
            with Image.open(dest) as img:
                if img.width != VIDEO_WIDTH or img.height != VIDEO_HEIGHT:
                    logger.warning(
                        "    Image size mismatch (%dx%d vs expected %dx%d). "
                        "Resizing…",
                        img.width, img.height, VIDEO_WIDTH, VIDEO_HEIGHT,
                    )
                    resized = img.resize((VIDEO_WIDTH, VIDEO_HEIGHT), Image.LANCZOS)
                    resized.save(str(dest), format="PNG")

            logger.info("    Saved to: %s", dest)
            return

        except requests.exceptions.RequestException as exc:
            logger.warning("    Request error: %s", exc)
        except Exception as exc:  # pylint: disable=broad-except
            logger.warning("    Unexpected error: %s", exc)
            # Remove potentially corrupt file
            if dest.exists():
                dest.unlink()

        if attempt < retries:
            sleep_s = RETRY_BACKOFF_BASE * (2 ** (attempt - 1))
            logger.info("    Retrying in %.1fs…", sleep_s)
            time.sleep(sleep_s)

    raise RuntimeError(f"Failed to download image after {retries} attempts: {url}")


# ---------------------------------------------------------------------------
# Main generation logic
# ---------------------------------------------------------------------------


def load_script(path: str = SCRIPT_JSON) -> dict[str, Any]:
    """Load and return the script JSON produced by Step 1."""
    script_path = Path(path)
    if not script_path.exists():
        raise FileNotFoundError(
            f"Script file not found: {script_path}. "
            "Run step1_trend_script.py first."
        )
    with script_path.open(encoding="utf-8") as fh:
        data = json.load(fh)
    logger.info("Loaded script with %d sentences.", len(data["sentences"]))
    return data


def generate_images(script: dict[str, Any], images_dir: str = IMAGES_DIR) -> list[Path]:
    """
    Generate one image per sentence.

    Returns a list of Paths to the downloaded PNG files in order.
    The list is also written back into *script* for convenience
    (script['sentences'][i]['image_path']).
    """
    out_dir = Path(images_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    sentences = script["sentences"]
    saved_paths: list[Path] = []

    # Generate a reproducible but varied set of seeds (based on the title)
    rng = random.Random(hash(script.get("title", "auto-reel")) & 0xFFFF_FFFF)

    for idx, sentence in enumerate(sentences):
        prompt = sentence["image_prompt"]
        seed = rng.randint(1, 99_999)
        dest = out_dir / f"{idx}.png"

        logger.info(
            "Image %d/%d | seed=%d | prompt: %.80s…",
            idx + 1, len(sentences), seed, prompt,
        )

        url = build_pollinations_url(prompt, seed)
        download_image(url, dest)

        # Annotate the script entry with the local path (used by Step 3)
        sentence["image_path"] = str(dest)
        saved_paths.append(dest)

        # Be polite to the free API — short pause between requests
        if idx < len(sentences) - 1:
            time.sleep(1.5)

    return saved_paths


def save_updated_script(script: dict[str, Any], path: str = SCRIPT_JSON) -> None:
    """Persist the script (now annotated with image_path) back to disk."""
    script_path = Path(path)
    script_path.write_text(json.dumps(script, indent=2, ensure_ascii=False), encoding="utf-8")
    logger.info("Updated script (with image paths) saved to: %s", script_path)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    ensure_dirs()
    script = load_script()
    paths = generate_images(script)
    save_updated_script(script)

    print("\n" + "=" * 60)
    print(f"  Generated {len(paths)} images in: {IMAGES_DIR}")
    for p in paths:
        print(f"    {p}")
    print("=" * 60 + "\n")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # pylint: disable=broad-except
        logger.critical("Step 2 failed: %s", exc, exc_info=True)
        sys.exit(1)
