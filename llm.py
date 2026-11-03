"""llm.py - the only place that talks to Gemini (text generation and embeddings).

Used by relevance.py (API process) and worker.py (worker process). Each process has its own
rate limiter, because they are separate processes: GEMINI_RPM_API and GEMINI_RPM_WORKER in .env
must add up to the free-tier limit. Every request is counted in the DB (counters.llm_calls) so the
UI shows one combined total. Any failure raises LLMError, which callers catch, log and skip.
"""

import asyncio
import json
import logging
import os
import time
from typing import Any

from google import genai
from google.genai import errors, types

import config
import db

log = logging.getLogger("llm")

MAX_RETRIES = 3        # attempts per call when Gemini answers 429 (rate limited) or 503 (overloaded)
RETRY_WAIT_SECONDS = 20  # first wait; doubles each retry


class LLMError(Exception):
    """Raised when a Gemini call fails or returns something unusable."""


api_key = os.environ.get("GEMINI_API_KEY")
if not api_key:
    raise RuntimeError("GEMINI_API_KEY is empty. Put your key in .env")
client = genai.Client(api_key=api_key)

# --- Rate limiter: at most N calls per minute for this process ----------------------------------
# PROCESS_NAME is "api" or "worker" (set in docker-compose.yml).
rpm_var = f"GEMINI_RPM_{os.environ['PROCESS_NAME'].upper()}"
seconds_between_calls = 60 / float(os.environ[rpm_var])
limiter_lock = asyncio.Lock()
next_allowed_time = 0.0


async def wait_for_rate_limit() -> None:
    """Sleep until this process may make its next Gemini call (calls are spaced evenly)."""
    global next_allowed_time
    async with limiter_lock:  # the lock makes concurrent callers queue up one by one
        wait = next_allowed_time - time.monotonic()
        if wait > 0:
            await asyncio.sleep(wait)
        next_allowed_time = time.monotonic() + seconds_between_calls


async def call_gemini(make_request) -> Any:
    """Run `make_request()` under the rate limiter; wait and retry when Gemini says 429 or 503."""
    for attempt in range(1, MAX_RETRIES + 1):
        await wait_for_rate_limit()
        await db.pool.execute("UPDATE counters SET value = value + 1 WHERE name = 'llm_calls'")
        try:
            return await make_request()
        except errors.APIError as e:
            if e.code not in (429, 503):
                raise LLMError(f"Gemini error {e.code}: {e.message}") from e
            wait = RETRY_WAIT_SECONDS * 2 ** (attempt - 1)
            log.warning("Gemini busy (%d), retry %d/%d in %ds", e.code, attempt, MAX_RETRIES, wait)
            await asyncio.sleep(wait)
    raise LLMError(f"Gemini still busy (429/503) after {MAX_RETRIES} attempts")


async def generate_json(prompt: str) -> Any:
    """Ask Gemini for a JSON answer and return it parsed. Callers must validate the shape."""
    response = await call_gemini(
        lambda: client.aio.models.generate_content(
            model=config.LLM_MODEL,
            contents=prompt,
            config=types.GenerateContentConfig(response_mime_type="application/json"),
        )
    )
    try:
        return json.loads(response.text)
    except (TypeError, json.JSONDecodeError) as e:
        raise LLMError(f"Gemini returned invalid JSON: {response.text!r}") from e


async def embed(texts: list[str]) -> list[list[float]]:
    """Embed several texts in ONE request (768 dims each, same order as the input)."""
    response = await call_gemini(
        lambda: client.aio.models.embed_content(
            model=config.EMBEDDING_MODEL,
            contents=texts,
            config=types.EmbedContentConfig(
                output_dimensionality=config.EMBEDDING_DIMS,
                task_type="SEMANTIC_SIMILARITY",
            ),
        )
    )
    if response.embeddings is None or len(response.embeddings) != len(texts):
        raise LLMError("Gemini returned the wrong number of embeddings")
    return [e.values for e in response.embeddings]
