"""
config.py
Central configuration module: reads all secrets from environment variables
and defines shared constants used across all pipeline stages.
"""

import os
import logging

# ---------------------------------------------------------------------------
# Logging setup (imported by every other module)
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s – %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
logger = logging.getLogger("auto-reel")


# ---------------------------------------------------------------------------
# Secrets – all read from environment variables, never hard-coded
# ---------------------------------------------------------------------------
def _require(name: str) -> str:
    """Return the value of an environment variable or raise a clear error."""
    value = os.environ.get(name, "").strip()
    if not value:
        raise EnvironmentError(
            f"Required environment variable '{name}' is not set or is empty. "
            "Add it to your .env file (local) or GitHub Actions secrets."
        )
    return value


def _optional(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


# Google Gemini
GEMINI_API_KEY: str = _require("GEMINI_API_KEY")

# Instagram Graph API
IG_USER_ID: str = _require("IG_USER_ID")
IG_ACCESS_TOKEN: str = _require("IG_ACCESS_TOKEN")

# ---------------------------------------------------------------------------
# Paths & filenames
# ---------------------------------------------------------------------------
OUTPUT_DIR: str = _optional("OUTPUT_DIR", "output")
IMAGES_DIR: str = os.path.join(OUTPUT_DIR, "images")
AUDIO_DIR: str = os.path.join(OUTPUT_DIR, "audio")
SCRIPT_JSON: str = os.path.join(OUTPUT_DIR, "script.json")
FINAL_VIDEO: str = os.path.join(OUTPUT_DIR, "reel.mp4")
SEGMENTS_DIR: str = os.path.join(OUTPUT_DIR, "segments")

# ---------------------------------------------------------------------------
# Video / image settings
# ---------------------------------------------------------------------------
VIDEO_WIDTH: int = 720
VIDEO_HEIGHT: int = 1280
VIDEO_FPS: int = 30
AUDIO_OVERLAP_S: float = 0.2          # seconds of image hold after audio ends
KENBURNS_ZOOM_RATIO: float = 0.0005   # zoom speed for Ken Burns effect

# ---------------------------------------------------------------------------
# Pollinations.ai image generation
# ---------------------------------------------------------------------------
POLLINATIONS_BASE_URL: str = "https://image.pollinations.ai/prompt/{prompt}"
POLLINATIONS_PARAMS: dict = {
    "width": VIDEO_WIDTH,
    "height": VIDEO_HEIGHT,
    "nologo": "true",
    "enhance": "true",
}

# ---------------------------------------------------------------------------
# edge-tts voice
# ---------------------------------------------------------------------------
TTS_VOICE: str = _optional("TTS_VOICE", "en-US-AriaNeural")

# ---------------------------------------------------------------------------
# Instagram Graph API
# ---------------------------------------------------------------------------
IG_API_BASE: str = f"https://graph.instagram.com/{IG_API_VERSION}"

# ---------------------------------------------------------------------------
# Gemini model
# ---------------------------------------------------------------------------
GEMINI_MODEL: str = _optional("GEMINI_MODEL", "gemini-3.6-flash")

# ---------------------------------------------------------------------------
# Utility: ensure all output directories exist
# ---------------------------------------------------------------------------
def ensure_dirs() -> None:
    for d in (OUTPUT_DIR, IMAGES_DIR, AUDIO_DIR, SEGMENTS_DIR):
        os.makedirs(d, exist_ok=True)
    logger.debug("Output directories ensured.")
