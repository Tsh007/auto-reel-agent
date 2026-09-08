# Auto-Reel Agent 🎬

Automated daily Instagram Reels generator powered by **Google Gemini**, **Pollinations.ai**, **edge-tts**, and **FFmpeg**. Runs fully on GitHub Actions — no servers, no paid APIs (beyond the Instagram Graph API, which is free with a developer account).

---

## Pipeline Overview

```
┌─────────────────────────────────┐
│  Stage 1 · Gemini API           │  → output/script.json
│  Trending topic + reel script   │
└────────────────┬────────────────┘
                 │ artifact
┌────────────────▼────────────────┐
│  Stage 2 · Pollinations.ai      │  → output/images/0..N.png
│  AI image generation            │
└────────────────┬────────────────┘
                 │ artifact
┌────────────────▼────────────────┐
│  Stage 3 · edge-tts + FFmpeg    │  → output/reel.mp4
│  TTS · Ken Burns video · upload │
└─────────────────────────────────┘
                 │ tmpfiles.org URL
         Instagram Graph API v26.0
```

---

## Prerequisites

| Requirement | Where to get it |
|---|---|
| **Google Gemini API key** | [Google AI Studio](https://aistudio.google.com/app/apikey) — free tier |
| **Instagram Business/Creator account** | [Meta for Developers](https://developers.facebook.com/) |
| **Instagram User ID** | In Meta Developer Dashboard → Your App → Instagram |
| **Instagram Access Token** | Generate a long-lived token via the Graph API Explorer |
| **FFmpeg** | Pre-installed in the workflow; `sudo apt install ffmpeg` locally |

---

## Repository Setup

### 1 · Fork / clone

```bash
git clone https://github.com/YOUR_USERNAME/auto-reel-agent.git
cd auto-reel-agent
```

### 2 · Add GitHub Actions Secrets

In your repo → **Settings → Secrets and variables → Actions → New repository secret**, add:

| Secret name | Value |
|---|---|
| `GEMINI_API_KEY` | Your Google AI Studio API key |
| `IG_USER_ID` | Your Instagram Business account numeric user ID |
| `IG_ACCESS_TOKEN` | Long-lived Instagram Graph API access token |

### 3 · Local development setup

```bash
python -m venv .venv
source .venv/bin/activate       # Windows: .venv\Scripts\activate
pip install -r requirements.txt

# Also install ffmpeg locally:
# macOS: brew install ffmpeg
# Ubuntu/Debian: sudo apt install ffmpeg
# Windows: https://ffmpeg.org/download.html
```

Create a `.env` file (never commit this!):

```dotenv
GEMINI_API_KEY=AIza...
IG_USER_ID=123456789
IG_ACCESS_TOKEN=EAABsb...
```

Then run each stage manually:

```bash
# Load .env (bash)
export $(cat .env | xargs)

python step1_trend_script.py   # Generates output/script.json
python step2_generate_images.py # Generates output/images/*.png
python step3_assemble_upload.py # Creates reel + uploads to Instagram
```

---

## Project Structure

```
auto-reel-agent/
├── config.py                   # Centralised config & env-var loading
├── step1_trend_script.py       # Stage 1: Gemini trend & script
├── step2_generate_images.py    # Stage 2: Pollinations.ai images
├── step3_assemble_upload.py    # Stage 3: TTS + FFmpeg + Instagram
├── requirements.txt
├── .github/
│   └── workflows/
│       └── daily-reel.yml      # Cron pipeline (00:00 UTC daily)
└── output/                     # Created at runtime (git-ignored)
    ├── script.json
    ├── images/
    ├── audio/
    ├── segments/
    └── reel.mp4
```

---

## Configuration Options

All values can be overridden via environment variables:

| Variable | Default | Description |
|---|---|---|
| `GEMINI_MODEL` | `gemini-1.5-flash` | Gemini model to use |
| `TTS_VOICE` | `en-US-AriaNeural` | edge-tts voice name |
| `OUTPUT_DIR` | `output` | Root output directory |

Find all available edge-tts voices: `edge-tts --list-voices`

---

## How Each Stage Works

### Stage 1 – Trend & Script (`step1_trend_script.py`)

1. Sends a structured prompt to **Gemini 1.5 Flash** requesting a trending topic and a 30-second video script.
2. The model returns strict JSON with `title`, `description`, and 5–7 `sentences`, each with an `image_prompt`.
3. Validates the JSON schema and retries with exponential back-off on failure.
4. Saves `output/script.json`.

### Stage 2 – Image Generation (`step2_generate_images.py`)

1. Reads `output/script.json`.
2. For each sentence, builds a Pollinations.ai URL with the `image_prompt`, a unique seed, and `720×1280` dimensions.
3. Downloads the image with retry logic and validates it with Pillow.
4. Saves `output/images/0.png`, `1.png`, …

### Stage 3 – Assembly & Upload (`step3_assemble_upload.py`)

1. **TTS**: Uses `edge-tts` (async) to synthesise each sentence into an MP3.
2. **Durations**: Measures each MP3's length via `ffprobe`.
3. **Segments**: For each sentence, FFmpeg:
   - Applies a **Ken Burns** zoom-in/out effect via the `zoompan` filter.
   - Overlays the sentence text at the bottom with a semi-transparent black box.
   - Muxes the MP3 audio into the segment.
   - Sets segment length = audio duration + 0.2 s overlap.
4. **Concatenation**: All segments are merged into `output/reel.mp4` (720×1280, H.264, AAC, web-optimised).
5. **Hosting**: Uploads `reel.mp4` to [tmpfiles.org](https://tmpfiles.org/) (free, no account needed) to get a public URL.
6. **Instagram**: Posts the Reel via the **Graph API v26.0**:
   - Creates a media container (`/media` endpoint).
   - Polls for `status_code == FINISHED`.
   - Publishes (`/media_publish` endpoint).

---

## Instagram Graph API Setup

Instagram requires your video to be publicly accessible via URL. The pipeline handles this automatically via `tmpfiles.org`.

### Getting a Long-Lived Access Token

1. Create a Meta App at [developers.facebook.com](https://developers.facebook.com/).
2. Add the **Instagram Graph API** product.
3. Connect your Instagram Business/Creator account.
4. Use the **Graph API Explorer** to get a short-lived token, then exchange it:

```bash
curl "https://graph.facebook.com/v26.0/oauth/access_token?\
grant_type=fb_exchange_token&\
client_id=YOUR_APP_ID&\
client_secret=YOUR_APP_SECRET&\
fb_exchange_token=YOUR_SHORT_LIVED_TOKEN"
```

The returned token is valid for ~60 days. Set up a cron job or GitHub Action to refresh it periodically.

---

## Customisation & Extension

### Replace Stage 2 with a different image/video API

`step2_generate_images.py` is fully self-contained. To swap providers:
1. Edit `generate_images()` — change the URL builder and download logic.
2. Keep the same output: `output/images/0.png … N.png` and annotate `sentence["image_path"]`.
3. No other file needs to change.

### Change TTS voice

Set the `TTS_VOICE` environment variable to any voice from `edge-tts --list-voices`.

### Change posting schedule

Edit the `cron` expression in [`.github/workflows/daily-reel.yml`](.github/workflows/daily-reel.yml):

```yaml
- cron: "0 9 * * *"   # 09:00 UTC daily
- cron: "0 18 * * 1"  # 18:00 UTC every Monday
```

---

## Troubleshooting

| Issue | Fix |
|---|---|
| `GEMINI_API_KEY not set` | Add the secret to GitHub Actions or export locally |
| Pollinations returns wrong size | Already handled — Pillow resizes automatically |
| `ffprobe: command not found` | Install FFmpeg (`brew install ffmpeg` or `apt install ffmpeg`) |
| Instagram `OAuthException` | Refresh your access token (they expire after ~60 days) |
| `status_code == ERROR` on Instagram | Video must be ≥3s, ≤60s, H.264, AAC, 720×1280 |
| `tmpfiles.org` upload fails | File too large (>512 MB limit) or service temporarily down — retry |

---

## .gitignore

```
output/
.env
__pycache__/
*.pyc
.venv/
```

---

## License

MIT — free to use, modify, and distribute.
