"""
step1_trend_script.py
Stage 1 – Trend detection & script generation via Google Gemini.

Uses the new `google-genai` SDK (google.genai), which replaces the
deprecated `google-generativeai` (google.generativeai) package.

Outputs: output/script.json
"""

from __future__ import annotations

import json
import sys
import textwrap
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from google import genai
from google.genai import types

from config import (
    GEMINI_API_KEY,
    GEMINI_MODEL,
    SCRIPT_JSON,
    ensure_dirs,
    logger,
)

# ---------------------------------------------------------------------------
# Prompt templates
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = textwrap.dedent("""
    You are a professional social-media content strategist who creates
    highly engaging short-form video scripts for Instagram Reels.
    You always respond with valid JSON and nothing else — no markdown fences,
    no prose, no commentary. Only the raw JSON object.
""").strip()

SCRIPT_PROMPT = textwrap.dedent("""
    Today's date: {date}.

    Task:
    1. Identify ONE genuinely trending and interesting topic from the last
       24 hours (technology, science, culture, or global news). Use your
       training knowledge and reason about what types of topics trend
       cyclically or recurrently.
    2. Write a 30-second Instagram Reels script about that topic.
       – The script must have 5 to 7 sentences.
       – Each sentence must be 5–10 words long (punchy & fast-paced).
       – For every sentence, also write a detailed AI image-generation prompt
         that visually represents that sentence in a cinematic, vertical
         (9:16) portrait style.

    Return ONLY valid JSON in this exact schema (no markdown, no prose):
    {{
      "title": "<Short catchy title (max 8 words)>",
      "description": "<60-word description ending with 5-10 relevant hashtags>",
      "sentences": [
        {{
          "text": "<sentence text>",
          "image_prompt": "<detailed image generation prompt, cinematic, 9:16 portrait>"
        }}
      ]
    }}

    Image prompt style tips:
    - Photorealistic or hyper-detailed digital art
    - Specific lighting (golden hour, neon, studio)
    - Camera angle (close-up, wide shot, aerial)
    - Mood keywords (epic, mysterious, vibrant)
    - NO text or letters in images
""").strip()

# ---------------------------------------------------------------------------
# Core functions
# ---------------------------------------------------------------------------


def build_client() -> genai.Client:
    """Create and return an authenticated Gemini client."""
    client = genai.Client(api_key=GEMINI_API_KEY)
    logger.info("Gemini client initialised (model: %s)", GEMINI_MODEL)
    return client


def generate_script(client: genai.Client, retries: int = 3) -> dict[str, Any]:
    """Call Gemini and return the parsed JSON script dict."""
    date_str = datetime.now(timezone.utc).strftime("%A, %d %B %Y")
    full_prompt = f"{SYSTEM_PROMPT}\n\n{SCRIPT_PROMPT.format(date=date_str)}"

    config = types.GenerateContentConfig(
        temperature=0.9,
        top_p=0.95,
        top_k=40,
        max_output_tokens=2048,
        response_mime_type="application/json",
    )

    for attempt in range(1, retries + 1):
        logger.info("Calling Gemini (attempt %d/%d)…", attempt, retries)
        try:
            response = client.models.generate_content(
                model=GEMINI_MODEL,
                contents=full_prompt,
                config=config,
            )
            raw_text = response.text.strip()
            logger.debug("Raw Gemini response:\n%s", raw_text)

            # Strip optional markdown code fences if the model adds them anyway
            if raw_text.startswith("```"):
                lines = raw_text.splitlines()
                end = -1 if lines[-1].strip() == "```" else len(lines)
                raw_text = "\n".join(lines[1:end])

            data = json.loads(raw_text)
            _validate_script(data)
            logger.info(
                "Script generated: '%s' (%d sentences)",
                data["title"], len(data["sentences"]),
            )
            return data

        except (json.JSONDecodeError, ValueError) as exc:
            logger.warning("Parse/validation error on attempt %d: %s", attempt, exc)
            if attempt < retries:
                time.sleep(2 ** attempt)
        except Exception as exc:  # pylint: disable=broad-except
            logger.error("Gemini API error: %s", exc)
            if attempt < retries:
                time.sleep(3)

    raise RuntimeError(f"Failed to generate a valid script after {retries} attempts.")


def _validate_script(data: dict[str, Any]) -> None:
    """Raise ValueError if the script JSON doesn't match the expected schema."""
    required_top = {"title", "description", "sentences"}
    missing = required_top - data.keys()
    if missing:
        raise ValueError(f"Missing top-level keys: {missing}")

    sentences = data["sentences"]
    if not isinstance(sentences, list) or not (5 <= len(sentences) <= 7):
        raise ValueError(
            f"'sentences' must be a list of 5–7 items; got "
            f"{len(sentences) if isinstance(sentences, list) else type(sentences)}"
        )
    for i, s in enumerate(sentences):
        if "text" not in s or "image_prompt" not in s:
            raise ValueError(f"Sentence {i} missing 'text' or 'image_prompt' key.")
        if not s["text"].strip():
            raise ValueError(f"Sentence {i} has empty 'text'.")
        if not s["image_prompt"].strip():
            raise ValueError(f"Sentence {i} has empty 'image_prompt'.")


def save_script(data: dict[str, Any]) -> Path:
    """Persist script to SCRIPT_JSON and return the path."""
    out_path = Path(SCRIPT_JSON)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    logger.info("Script saved to: %s", out_path)
    return out_path


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    ensure_dirs()
    client = build_client()
    script = generate_script(client)
    save_script(script)

    print("\n" + "=" * 60)
    print(f"  TITLE       : {script['title']}")
    print(f"  DESCRIPTION : {script['description'][:80]}…")
    print(f"  SENTENCES   : {len(script['sentences'])}")
    print("=" * 60 + "\n")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # pylint: disable=broad-except
        logger.critical("Step 1 failed: %s", exc, exc_info=True)
        sys.exit(1)
