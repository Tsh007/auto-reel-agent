"""
step1_trend_script.py
Stage 1 – Trend detection & script generation via Google Gemini.

Outputs: output/script.json
"""

from __future__ import annotations

import json
import sys
import textwrap
import time
from pathlib import Path
from typing import Any

import google.generativeai as genai

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
    You always respond with valid JSON and nothing else.
""").strip()

SCRIPT_PROMPT = textwrap.dedent("""
    Today's date: {date}.

    Task:
    1. Identify ONE genuinely trending and interesting topic from the last
       24 hours (technology, science, culture, or global news). Use your
       training knowledge up to your knowledge cutoff and reason about
       what types of topics trend cyclically or recurrently.
    2. Write a 30-second Instagram Reels script about that topic.
       – The script must have 5 to 7 sentences.
       – Each sentence must be 5–10 words long (punchy & fast-paced).
       – For every sentence, also write a detailed AI image-generation prompt
         that visually represents that sentence in a cinematic, vertical
         (9:16) portrait style.

    Return ONLY valid JSON in this exact schema — no markdown, no prose:
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


def configure_gemini() -> None:
    """Authenticate the Gemini client."""
    genai.configure(api_key=GEMINI_API_KEY)
    logger.info("Gemini API configured with model: %s", GEMINI_MODEL)


def build_model() -> genai.GenerativeModel:
    """Return a GenerativeModel configured for JSON output."""
    generation_config = genai.GenerationConfig(
        temperature=0.9,
        top_p=0.95,
        top_k=40,
        max_output_tokens=2048,
        response_mime_type="application/json",
    )
    safety_settings = [
        {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_MEDIUM_AND_ABOVE"},
        {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_MEDIUM_AND_ABOVE"},
        {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "BLOCK_MEDIUM_AND_ABOVE"},
        {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_MEDIUM_AND_ABOVE"},
    ]
    return genai.GenerativeModel(
        model_name=GEMINI_MODEL,
        generation_config=generation_config,
        safety_settings=safety_settings,
        system_instruction=SYSTEM_PROMPT,
    )


def generate_script(model: genai.GenerativeModel, retries: int = 3) -> dict[str, Any]:
    """Call Gemini and return parsed JSON script dict."""
    from datetime import datetime, timezone

    date_str = datetime.now(timezone.utc).strftime("%A, %d %B %Y")
    prompt = SCRIPT_PROMPT.format(date=date_str)

    for attempt in range(1, retries + 1):
        logger.info("Calling Gemini (attempt %d/%d)…", attempt, retries)
        try:
            response = model.generate_content(prompt)
            raw_text = response.text.strip()
            logger.debug("Raw Gemini response:\n%s", raw_text)

            # Strip optional markdown code fences if present
            if raw_text.startswith("```"):
                lines = raw_text.splitlines()
                # Remove first and last fence lines
                raw_text = "\n".join(lines[1:-1]) if lines[-1] == "```" else "\n".join(lines[1:])

            data = json.loads(raw_text)
            _validate_script(data)
            logger.info("Script generated: '%s' (%d sentences)", data["title"], len(data["sentences"]))
            return data

        except (json.JSONDecodeError, ValueError) as exc:
            logger.warning("Parse/validation error on attempt %d: %s", attempt, exc)
            if attempt < retries:
                time.sleep(2 ** attempt)  # exponential back-off
        except Exception as exc:  # pylint: disable=broad-except
            logger.error("Gemini API error: %s", exc)
            if attempt < retries:
                time.sleep(3)

    raise RuntimeError("Failed to generate a valid script after %d attempts." % retries)


def _validate_script(data: dict[str, Any]) -> None:
    """Raise ValueError if the script JSON doesn't match expected schema."""
    required_top = {"title", "description", "sentences"}
    missing = required_top - data.keys()
    if missing:
        raise ValueError(f"Missing top-level keys: {missing}")

    sentences = data["sentences"]
    if not isinstance(sentences, list) or not (5 <= len(sentences) <= 7):
        raise ValueError(
            f"'sentences' must be a list of 5–7 items; got {len(sentences) if isinstance(sentences, list) else type(sentences)}"
        )
    for i, s in enumerate(sentences):
        if "text" not in s or "image_prompt" not in s:
            raise ValueError(f"Sentence {i} missing 'text' or 'image_prompt' keys.")
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
    configure_gemini()
    model = build_model()
    script = generate_script(model)
    save_script(script)

    # Print a pretty summary to stdout so the GitHub Actions log is readable
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
