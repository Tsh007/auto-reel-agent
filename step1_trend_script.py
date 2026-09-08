"""
step1_trend_script.py
Stage 1 – Trend detection & script generation via Google Gemini.

Retry strategy
──────────────
• 503 UNAVAILABLE  → persistent back-off loop (30 s → 5 min cap).
                     Gives up only after MAX_503_WAIT_S seconds (1.5 h).
• JSON parse / validation errors → up to MAX_PARSE_RETRIES fresh API calls.
• Any other error (4xx, etc.) → logged and counted as a parse-retry attempt.

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
# Retry knobs
# ---------------------------------------------------------------------------

MAX_503_WAIT_S: float = 5_400.0      # 1.5 hours max wait on 503s
INITIAL_503_BACKOFF_S: float = 30.0  # first sleep after a 503
MAX_503_BACKOFF_S: float = 300.0     # cap individual sleeps at 5 min

MAX_PARSE_RETRIES: int = 5           # retries for bad / truncated JSON

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
       – The script must have exactly 6 sentences (no fewer, no more).
       – Each sentence must be 5–10 words long (punchy & fast-paced).
       – For every sentence, also write a detailed AI image-generation prompt
         that visually represents that sentence in a cinematic, vertical
         (9:16) portrait style.

    Return ONLY valid JSON in this exact schema — no markdown, no prose,
    no trailing commas, all strings properly closed:
    {{
      "title": "<Short catchy title (max 8 words)>",
      "description": "<60-word description ending with 5-10 relevant hashtags>",
      "sentences": [
        {{
          "text": "<sentence text>",
          "image_prompt": "<detailed image generation prompt, cinematic, 9:16 portrait>"
        }},
        {{
          "text": "<sentence text>",
          "image_prompt": "<detailed image generation prompt, cinematic, 9:16 portrait>"
        }},
        {{
          "text": "<sentence text>",
          "image_prompt": "<detailed image generation prompt, cinematic, 9:16 portrait>"
        }},
        {{
          "text": "<sentence text>",
          "image_prompt": "<detailed image generation prompt, cinematic, 9:16 portrait>"
        }},
        {{
          "text": "<sentence text>",
          "image_prompt": "<detailed image generation prompt, cinematic, 9:16 portrait>"
        }},
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
    - Keep image prompts concise (under 40 words) to avoid truncation
""").strip()

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _is_503(exc: Exception) -> bool:
    """Return True when *exc* represents a 503 / UNAVAILABLE error."""
    msg = str(exc)
    return "503" in msg or "UNAVAILABLE" in msg or "high demand" in msg.lower()


def _strip_fences(raw: str) -> str:
    """Remove optional markdown code fences the model sometimes adds."""
    raw = raw.strip()
    if raw.startswith("```"):
        lines = raw.splitlines()
        end = -1 if lines[-1].strip() == "```" else len(lines)
        raw = "\n".join(lines[1:end]).strip()
    return raw


def _extract_json_object(raw: str) -> str:
    """
    Best-effort extraction of the first {...} block from *raw*.
    Useful when the model prefixes or suffixes the JSON with stray text.
    """
    start = raw.find("{")
    end = raw.rfind("}")
    if start != -1 and end != -1 and end > start:
        return raw[start : end + 1]
    return raw


# ---------------------------------------------------------------------------
# Gemini client
# ---------------------------------------------------------------------------


def build_client() -> genai.Client:
    """Create and return an authenticated Gemini client."""
    client = genai.Client(api_key=GEMINI_API_KEY)
    logger.info("Gemini client initialised (model: %s)", GEMINI_MODEL)
    return client


# ---------------------------------------------------------------------------
# Core API call with 503-aware retry
# ---------------------------------------------------------------------------


def _call_with_503_retry(
    client: genai.Client,
    prompt: str,
    config: types.GenerateContentConfig,
) -> str:
    """
    Call the Gemini API and return response.text.

    Retries indefinitely on 503 UNAVAILABLE with exponential back-off
    (30 s → 5 min cap) until MAX_503_WAIT_S seconds have elapsed,
    then raises TimeoutError.

    All other exceptions are re-raised immediately.
    """
    start_time = time.monotonic()
    backoff = INITIAL_503_BACKOFF_S

    while True:
        try:
            response = client.models.generate_content(
                model=GEMINI_MODEL,
                contents=prompt,
                config=config,
            )
            return response.text

        except Exception as exc:  # pylint: disable=broad-except
            if _is_503(exc):
                elapsed = time.monotonic() - start_time
                remaining = MAX_503_WAIT_S - elapsed

                if remaining <= 0:
                    raise TimeoutError(
                        f"Gemini returned 503 for >{MAX_503_WAIT_S/60:.0f} minutes. "
                        "Giving up."
                    ) from exc

                sleep_s = min(backoff, remaining)
                logger.warning(
                    "503 UNAVAILABLE — model busy. Retrying in %.0fs "
                    "(%.0f min elapsed / %.0f min max).",
                    sleep_s, elapsed / 60, MAX_503_WAIT_S / 60,
                )
                time.sleep(sleep_s)
                backoff = min(backoff * 1.5, MAX_503_BACKOFF_S)
            else:
                raise


# ---------------------------------------------------------------------------
# Script generation with parse-retry loop
# ---------------------------------------------------------------------------


def generate_script(client: genai.Client) -> dict[str, Any]:
    """
    Generate and return the validated script dict.

    Outer loop: retries on JSON parse / validation failure.
    Inner loop (_call_with_503_retry): retries on 503 only.
    """
    date_str = datetime.now(timezone.utc).strftime("%A, %d %B %Y")
    full_prompt = f"{SYSTEM_PROMPT}\n\n{SCRIPT_PROMPT.format(date=date_str)}"

    config = types.GenerateContentConfig(
        temperature=0.85,
        top_p=0.95,
        top_k=40,
        max_output_tokens=8192,          # bumped to prevent truncation
        response_mime_type="application/json",
        automatic_function_calling=types.AutomaticFunctionCallingConfig(
            disable=True
        ),
    )

    last_exc: Exception | None = None

    for attempt in range(1, MAX_PARSE_RETRIES + 1):
        logger.info("Calling Gemini (parse attempt %d/%d)…", attempt, MAX_PARSE_RETRIES)
        try:
            raw_text = _call_with_503_retry(client, full_prompt, config)
            logger.debug("Raw response (first 500 chars):\n%s", raw_text[:500])

            cleaned = _extract_json_object(_strip_fences(raw_text))
            data = json.loads(cleaned)
            _validate_script(data)

            logger.info(
                "✓ Script generated: '%s' (%d sentences)",
                data["title"], len(data["sentences"]),
            )
            return data

        except TimeoutError:
            # 503 exhausted — propagate immediately, no point retrying parse
            raise

        except json.JSONDecodeError as exc:
            last_exc = exc
            # Check if it looks like a truncation (unterminated string/array)
            truncated = any(
                kw in str(exc).lower()
                for kw in ("unterminated", "end of file", "unexpected end")
            )
            if truncated:
                logger.warning(
                    "Response appears truncated (attempt %d/%d): %s — retrying.",
                    attempt, MAX_PARSE_RETRIES, exc,
                )
            else:
                logger.warning(
                    "JSON parse error (attempt %d/%d): %s — retrying.",
                    attempt, MAX_PARSE_RETRIES, exc,
                )

        except ValueError as exc:
            last_exc = exc
            logger.warning(
                "Validation error (attempt %d/%d): %s — retrying.",
                attempt, MAX_PARSE_RETRIES, exc,
            )

        except Exception as exc:  # pylint: disable=broad-except
            last_exc = exc
            logger.error(
                "API / unexpected error (attempt %d/%d): %s — retrying.",
                attempt, MAX_PARSE_RETRIES, exc,
            )

        # Brief pause before next parse-retry so we don't hammer the API
        if attempt < MAX_PARSE_RETRIES:
            time.sleep(5)

    raise RuntimeError(
        f"Failed to generate a valid script after {MAX_PARSE_RETRIES} attempts. "
        f"Last error: {last_exc}"
    )


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


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
            raise ValueError(
                f"Sentence {i} missing 'text' or 'image_prompt' key."
            )
        if not s["text"].strip():
            raise ValueError(f"Sentence {i} has empty 'text'.")
        if not s["image_prompt"].strip():
            raise ValueError(f"Sentence {i} has empty 'image_prompt'.")


# ---------------------------------------------------------------------------
# Persist
# ---------------------------------------------------------------------------


def save_script(data: dict[str, Any]) -> Path:
    """Persist script to SCRIPT_JSON and return the path."""
    out_path = Path(SCRIPT_JSON)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8"
    )
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
