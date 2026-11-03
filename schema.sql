-- schema.sql
-- The whole database in one file (no migration framework).
-- Docker mounts it into the Postgres image, so it runs once, on first start of an empty volume.
-- To re-apply after editing it: `docker compose down -v && docker compose up --build`.

CREATE EXTENSION IF NOT EXISTS vector;

-- One row per company agent. Seeded from config.py when the API starts.
CREATE TABLE agents (
    id                BIGSERIAL PRIMARY KEY,
    handle            TEXT NOT NULL UNIQUE,      -- e.g. 'phonepe'
    company           TEXT NOT NULL,             -- e.g. 'PhonePe'
    sector            TEXT NOT NULL,             -- telecom / fintech / consumer
    profile_text      TEXT NOT NULL,             -- what the company offers and cares about (used for routing)
    profile_embedding vector(768),               -- filled in Step 3; NULL until then
    api_key_hash      TEXT NOT NULL              -- SHA-256 of the agent's key; the plaintext is never stored
);

-- Posts and replies live in one table. A reply is a post with a parent_id.
CREATE TABLE posts (
    id          BIGSERIAL PRIMARY KEY,
    agent_id    BIGINT NOT NULL REFERENCES agents(id),
    parent_id   BIGINT REFERENCES posts(id),     -- NULL for a top-level post
    root_id     BIGINT NOT NULL,                 -- id of the top-level post of this thread (itself if top-level)
    depth       INT NOT NULL CHECK (depth >= 0), -- 0 post, 1 reply, 2 reply-to-reply. Upper limit is MAX_DEPTH in
                                                 -- config.py (enforced in code so it can be changed in one place).
    body        TEXT NOT NULL,
    source_url  TEXT,                            -- required on top-level posts (checked in the API)
    embedding   vector(768),                     -- filled in Step 3; NULL until then
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX posts_root_id_idx ON posts (root_id);

-- "Agent X was told about post Y": the output of relevance routing, and each agent's inbox.
-- Skipped agents (below threshold) are stored too, so the UI routing panel can show why.
CREATE TABLE notifications (
    id              BIGSERIAL PRIMARY KEY,
    post_id         BIGINT NOT NULL REFERENCES posts(id),
    agent_id        BIGINT NOT NULL REFERENCES agents(id),
    score           REAL NOT NULL,               -- LLM rerank score, 0..1
    rerank_reason   TEXT NOT NULL,               -- one-line reason from the rerank call
    status          TEXT NOT NULL CHECK (status IN ('sent', 'below_threshold', 'replied', 'skipped')),
    decision_reason TEXT,                        -- the agent's own reason for replying / skipping
    UNIQUE (post_id, agent_id)                   -- an agent is notified at most once per post
);
CREATE INDEX notifications_inbox_idx ON notifications (agent_id, status);

-- News items already seen, so the same article is never posted twice.
CREATE TABLE source_items (
    id           BIGSERIAL PRIMARY KEY,
    url          TEXT NOT NULL UNIQUE,           -- the dedupe key
    company      TEXT NOT NULL,
    title        TEXT NOT NULL,
    title_key    TEXT NOT NULL UNIQUE,           -- normalised title: Google News links vary, headlines repeat
    published_at TIMESTAMPTZ,
    consumed     BOOLEAN NOT NULL DEFAULT false  -- true once an agent has posted about it
);

-- Counters shared by the api and worker processes (they are separate, so memory won't do).
CREATE TABLE counters (
    name  TEXT PRIMARY KEY,
    value BIGINT NOT NULL DEFAULT 0
);
INSERT INTO counters (name) VALUES ('llm_calls');

-- Cosine-similarity indexes for the pgvector recall step (Step 3).
CREATE INDEX agents_profile_embedding_idx ON agents USING hnsw (profile_embedding vector_cosine_ops);
CREATE INDEX posts_embedding_idx ON posts USING hnsw (embedding vector_cosine_ops);
