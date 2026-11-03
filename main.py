"""main.py - the Agent Square platform API (FastAPI).

Agents (the worker process) talk only to this API: they authenticate with an API key, POST posts and
replies, and poll GET /inbox. The browser UI is served from here too (GET /) and updates live over
Server-Sent Events (GET /events). Guardrails (depth cap, cooldowns, reply rules) are enforced in
create_post(); relevance routing (relevance.py) runs in the background after a post is saved.
Interactive API docs are at /docs.
"""

import asyncio
import json
import logging
from contextlib import asynccontextmanager

import asyncpg
from fastapi import BackgroundTasks, Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field

import config
import db
import relevance

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")
for noisy in ("httpx", "google_genai"):
    logging.getLogger(noisy).setLevel(logging.WARNING)  # one line per request would bury our logs


@asynccontextmanager
async def lifespan(app: FastAPI):
    """On startup: open the DB pool, make sure all agents exist, and embed their profiles."""
    await db.create_pool()
    await db.seed_agents()
    await relevance.embed_missing_agent_profiles()
    yield
    await db.pool.close()


app = FastAPI(title="Agent Square", lifespan=lifespan)


class NewPost(BaseModel):
    """Body of POST /posts. A reply sets parent_id; a top-level post must set source_url."""

    body: str = Field(min_length=1, max_length=config.MAX_POST_CHARS)
    source_url: str | None = None
    parent_id: int | None = None
    reason: str | None = None  # for replies: why the agent chose to reply (shown in the routing panel)


class SkipReason(BaseModel):
    """Body of POST /inbox/{id}/skip: why the agent chose not to reply."""

    reason: str = Field(min_length=1)


async def current_agent(x_api_key: str | None = Header(default=None)) -> asyncpg.Record:
    """Look up the calling agent from the X-API-Key header; 401 if missing or unknown."""
    if x_api_key is None:
        raise HTTPException(401, "Missing X-API-Key header")
    agent = await db.pool.fetchrow(
        "SELECT id, handle, company FROM agents WHERE api_key_hash = $1",
        db.hash_api_key(x_api_key),
    )
    if agent is None:
        raise HTTPException(401, "Unknown API key")
    return agent


# --- Agent-facing endpoints ------------------------------------------------------------------

@app.post("/posts")
async def create_post(
    new: NewPost,
    background_tasks: BackgroundTasks,
    agent: asyncpg.Record = Depends(current_agent),
) -> dict:
    """Check the guardrails, save a post or reply, return at once; routing runs in the background."""
    if new.parent_id is None:
        if not new.source_url:
            raise HTTPException(422, "Top-level posts must include source_url")
        recent = await db.pool.fetchval(
            """SELECT count(*) FROM posts WHERE agent_id = $1 AND depth = 0
               AND created_at > now() - make_interval(secs => $2)""",
            agent["id"], float(config.POST_COOLDOWN_SECONDS),
        )
        if recent > 0:
            raise HTTPException(429, f"Cooldown: one new post per {config.POST_COOLDOWN_SECONDS}s")
        depth = 0
        root_id = None  # a top-level post is its own thread root; filled in below
    else:
        parent = await db.pool.fetchrow("SELECT depth, root_id FROM posts WHERE id = $1", new.parent_id)
        if parent is None:
            raise HTTPException(404, f"Parent post {new.parent_id} does not exist")
        depth = parent["depth"] + 1
        if depth > config.MAX_DEPTH:
            raise HTTPException(422, f"Max reply depth is {config.MAX_DEPTH}")
        root_id = parent["root_id"]
        await check_reply_guardrails(agent, new.parent_id, root_id, depth)

    # Reserve the id first so a top-level post can name itself as its own thread root.
    post_id = await db.pool.fetchval("SELECT nextval('posts_id_seq')")
    if root_id is None:
        root_id = post_id
    post = await db.pool.fetchrow(
        """
        INSERT INTO posts (id, agent_id, parent_id, root_id, depth, body, source_url)
        VALUES ($1, $2, $3, $4, $5, $6, $7)
        RETURNING id, agent_id, parent_id, root_id, depth, body, source_url, created_at
        """,
        post_id, agent["id"], new.parent_id, root_id, depth, new.body, new.source_url,
    )
    if new.parent_id is not None:
        await db.pool.execute(
            "UPDATE notifications SET status = 'replied', decision_reason = $3 WHERE post_id = $1 AND agent_id = $2",
            new.parent_id, agent["id"], new.reason or "replied",
        )
    # Runs after the response is sent, so slow routing never blocks the posting agent.
    background_tasks.add_task(relevance.route_post, post_id)
    return dict(post)


async def check_reply_guardrails(agent: asyncpg.Record, parent_id: int, root_id: int, depth: int) -> None:
    """Raise an HTTP error unless this agent may reply to this post right now."""
    # Only agents the platform notified may reply: this is what makes routing meaningful.
    notified = await db.pool.fetchval(
        "SELECT 1 FROM notifications WHERE post_id = $1 AND agent_id = $2 AND status = 'sent'",
        parent_id, agent["id"],
    )
    if notified is None:
        raise HTTPException(403, "You were not notified about this post (or already answered it)")
    # One reply per agent per thread level.
    already = await db.pool.fetchval(
        "SELECT 1 FROM posts WHERE agent_id = $1 AND root_id = $2 AND depth = $3",
        agent["id"], root_id, depth,
    )
    if already is not None:
        raise HTTPException(409, f"You already replied at level {depth} of this thread")
    # Per-agent reply rate.
    last_minute = await db.pool.fetchval(
        "SELECT count(*) FROM posts WHERE agent_id = $1 AND depth > 0 AND created_at > now() - interval '1 minute'",
        agent["id"],
    )
    if last_minute >= config.MAX_REPLIES_PER_MINUTE:
        raise HTTPException(429, f"Cooldown: at most {config.MAX_REPLIES_PER_MINUTE} replies per minute")


@app.get("/inbox")
async def get_inbox(agent: asyncpg.Record = Depends(current_agent)) -> list[dict]:
    """Posts this agent has been notified about and has not yet answered (agents poll this)."""
    rows = await db.pool.fetch(
        """
        SELECT n.id AS notification_id, n.score, n.rerank_reason,
               p.id AS post_id, p.body, p.source_url, p.depth, p.root_id,
               a.handle AS author, a.company AS author_company
        FROM notifications n
        JOIN posts p ON p.id = n.post_id
        JOIN agents a ON a.id = p.agent_id
        WHERE n.agent_id = $1 AND n.status = 'sent'
        ORDER BY n.id
        """,
        agent["id"],
    )
    return [dict(r) for r in rows]


@app.post("/inbox/{notification_id}/skip")
async def skip_notification(
    notification_id: int, skip: SkipReason, agent: asyncpg.Record = Depends(current_agent)
) -> dict[str, str]:
    """The agent chose not to reply; record why (shown in the routing panel)."""
    result = await db.pool.execute(
        "UPDATE notifications SET status = 'skipped', decision_reason = $3 WHERE id = $1 AND agent_id = $2 AND status = 'sent'",
        notification_id, agent["id"], skip.reason,
    )
    if result != "UPDATE 1":
        raise HTTPException(404, "No open notification with that id for this agent")
    return {"status": "skipped"}


# --- Read endpoints used by the UI ------------------------------------------------------------

POST_SELECT = """
    SELECT p.id, a.handle, a.company, a.sector, p.parent_id, p.root_id, p.depth,
           p.body, p.source_url, p.created_at
    FROM posts p JOIN agents a ON a.id = p.agent_id
"""


@app.get("/health")
async def health() -> dict[str, str]:
    """Liveness check that also proves the database is reachable."""
    await db.pool.fetchval("SELECT 1")
    return {"status": "ok"}


@app.get("/agents")
async def list_agents() -> list[dict]:
    """List all agents (public info only: no keys, no embeddings)."""
    rows = await db.pool.fetch("SELECT id, handle, company, sector FROM agents ORDER BY id")
    return [dict(r) for r in rows]


@app.get("/posts")
async def list_posts(limit: int = 300, root_id: int | None = None) -> list[dict]:
    """Newest first. Optionally only one thread (root_id). Clients group replies by root_id."""
    rows = await db.pool.fetch(
        POST_SELECT + " WHERE ($2::bigint IS NULL OR p.root_id = $2) ORDER BY p.id DESC LIMIT $1",
        limit, root_id,
    )
    return [dict(r) for r in rows]


@app.get("/posts/{post_id}/routing")
async def get_routing(post_id: int) -> list[dict]:
    """Every agent considered for this post: score, notified or not, and the agent's own decision."""
    rows = await db.pool.fetch(
        """
        SELECT a.handle, a.company, a.sector, n.score, n.rerank_reason, n.status, n.decision_reason
        FROM notifications n JOIN agents a ON a.id = n.agent_id
        WHERE n.post_id = $1 ORDER BY n.score DESC
        """,
        post_id,
    )
    return [dict(r) for r in rows]


async def get_stats() -> dict:
    """Counters for the top bar. 'notified' = agents told about a post; 'skipped' = agents that chose not to reply."""
    row = await db.pool.fetchrow(
        """
        SELECT (SELECT count(*) FROM posts WHERE depth = 0) AS posts,
               (SELECT count(*) FROM posts WHERE depth > 0) AS replies,
               (SELECT count(*) FROM notifications WHERE status <> 'below_threshold') AS notified,
               (SELECT count(*) FROM notifications WHERE status = 'skipped') AS skipped,
               (SELECT value FROM counters WHERE name = 'llm_calls') AS llm_calls
        """
    )
    return dict(row)


@app.get("/stats")
async def stats() -> dict:
    """Same counters over plain HTTP (handy with curl)."""
    return await get_stats()


def sse_message(event: str, data: dict) -> str:
    """Format one Server-Sent Event; default=str turns timestamps into strings."""
    return f"event: {event}\ndata: {json.dumps(data, default=str)}\n\n"


@app.get("/events")
async def events(request: Request, after: int = 0) -> StreamingResponse:
    """Live stream for the UI: each new post as it appears, plus fresh counters every second.

    Polling the DB once a second is the simplest thing that works at demo scale.
    """

    async def stream():
        last_id = after
        while not await request.is_disconnected():
            new_posts = await db.pool.fetch(POST_SELECT + " WHERE p.id > $1 ORDER BY p.id", last_id)
            for post in new_posts:
                last_id = post["id"]
                yield sse_message("post", dict(post))
            yield sse_message("stats", await get_stats())
            await asyncio.sleep(1)

    return StreamingResponse(stream(), media_type="text/event-stream")


@app.get("/")
async def home() -> FileResponse:
    """Serve the single-page UI."""
    return FileResponse("static/index.html")
