"""db.py - Postgres connection pool and agent seeding.

Imported by main.py (and by the worker from Step 2). The tables themselves are created by
schema.sql when the database container first starts, not here.
"""

import hashlib
import os

import asyncpg
from pgvector.asyncpg import register_vector

import config

pool: asyncpg.Pool  # set by create_pool(); other modules use it as `db.pool`


def hash_api_key(api_key: str) -> str:
    """Return the SHA-256 hex digest of an API key, which is the only form we store."""
    return hashlib.sha256(api_key.encode()).hexdigest()


async def create_pool() -> None:
    """Open the connection pool. Fails loudly if DATABASE_URL is missing."""
    global pool
    url = os.environ["DATABASE_URL"]  # KeyError with the var name if unset: intended
    # `init` runs on every new connection so vector columns map to Python lists/numpy arrays.
    pool = await asyncpg.create_pool(url, init=register_vector)


async def seed_agents() -> None:
    """Insert or update one row per agent in config.AGENTS (safe to run on every startup).

    An empty table gets all 8 agents; later runs pick up edits (e.g. a new agent added to config.py).
    """
    for agent in config.AGENTS:
        await pool.execute(
            """
            INSERT INTO agents (handle, company, sector, profile_text, api_key_hash)
            VALUES ($1, $2, $3, $4, $5)
            ON CONFLICT (handle) DO UPDATE
            SET company = $2, sector = $3, api_key_hash = $5,
                -- Keep the embedding unless the profile text changed (then it must be re-embedded).
                profile_embedding = CASE WHEN agents.profile_text = $4
                                         THEN agents.profile_embedding ELSE NULL END,
                profile_text = $4
            """,
            agent["handle"],
            agent["company"],
            agent["sector"],
            agent["profile_text"],
            hash_api_key(agent["api_key"]),
        )
