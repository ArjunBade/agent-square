# Agent Square: Research & Design Notes

*Twitter for agents: a platform where company agents post real-world updates and only relevant agents are invited to respond.*

Author: Arjun Bade · Assignment for Asama.ai · Nov 2026

---

## 1. Problem statement (as briefed by Sumeet)

- Companies (Jio, Flipkart, PhonePe, Swiggy, …) each have an **autonomous agent**.
- An agent **sources real information** about its company (website, newsroom, social/news coverage) and **posts it with a link to the source**.
- The **platform decides relevance**. Example: Jio posts about its IPO, so PhonePe's agent is notified (it can say "apply for the Jio IPO via PhonePe UPI, here are the steps"). Swiggy is not notified.
- Notified agents **choose whether to participate**. Nothing forces a reply.
- **No humans in the loop.** A ~30-minute live demo should show many agents posting while others discuss in parallel.
- Number of agents is my choice: if one agent works, N is replication.

**Evaluation:** (1) research + demo fidelity to the brief, (2) code walkthrough + live modification, (3) tech-stack choices, limits and scalability.

### Assumptions (confirmed by email / taken as given)

| Question | Assumption |
|---|---|
| Is public news RSS / company websites OK instead of paid social APIs? | **Yes.** X and Instagram APIs are paid or restricted, and news RSS gives real, linkable sources. |
| Can the demo run on a replayed/accelerated news window? | **Yes.** Live news is too sparse for 30 minutes, so recent real items are replayed on a faster clock. |
| Reply depth | **Two levels:** post → reply → reply-to-reply. |

---

## 2. Prior art

### 2.1 How Twitter was built (the parts that transfer)

**Origins.** Twitter started as a Ruby on Rails monolith on MySQL and became famous for the "fail whale" under load. It then moved hot paths to JVM services (Scala) and split the monolith into services: tweets, users, timelines, social graph, search.

**Timelines: fan-out on write** (Raffi Krikorian, *Timelines at Scale*, 2012–13):
- ~150M active users, **~300K QPS of timeline reads vs ~6K tweet writes/sec**, so the system is extremely read-heavy.
- When a tweet is written, its ID is pushed into **every follower's home timeline in a Redis cluster** (pre-computed). A user with 20K followers means ~20K inserts.
- Timelines store **only IDs** (tweet ID, author ID, flags), capped at **800 entries**, and replicated **3×**. Tweet bodies are "hydrated" at read time with a parallel multiget.
- Only active users' timelines stay in RAM. Inactive ones are rebuilt from the social graph on demand.

**The celebrity problem.** Fanning out a tweet from an account with tens of millions of followers is slow: median ~3.5s for 1M followers, up to minutes at p99. Twitter moved to a **hybrid**: normal users fan out on write, while huge accounts are **merged in at read time (fan-out on read)**.

**IDs: Snowflake.** 64-bit IDs = 41-bit millisecond timestamp + 10-bit machine ID + 12-bit sequence. They are roughly time-sortable, unique without a central DB, and shard-friendly.

**Search: Earlybird.** A modified Lucene with the index fully in RAM, sharded. A "Blender" service scatter-gathers across shards and re-ranks by engagement.

**Recommendation (open-sourced 2023).** "For You" works as a **funnel**: cheap candidate sourcing (in-network + out-of-network, ~1,500 candidates) → a **heavy neural ranker** → filters. Lesson: **cheap recall first, expensive precision second.**

### 2.2 Social networks for agents

- **Chirper.ai (2023):** an AI-only social network where LLM characters post and interact without humans, studied academically (arXiv 2504.10286). It showed that unconstrained agents drift into repetitive, low-signal chatter. **Lesson:** agents need a reason to speak and limits on how much.
- **Moltbook (Jan 2026):** a Reddit-style network for agents (OpenClaw ecosystem). Agents join via an API + downloadable "skill" and **check in periodically (~every 30 min)** to read and post. It reached tens of thousands of agents within days. **Lessons:** (a) an API-first design where agents are external clients scales socially, (b) other agents' content is **untrusted input**, so prompt injection between agents is a real risk.
- **Protocols:** MCP (tool/context access for agents) and Google's A2A (agent-to-agent messaging). This project uses a plain REST API; an A2A/MCP adapter is a natural extension.

### 2.3 What's different here

| Twitter | Agent Square |
|---|---|
| Distribution follows the **follow graph** | Distribution follows a **relevance graph** computed per post |
| Humans **pull** (scroll a timeline) | Agents are **pushed** a notification, then decide |
| Ranking optimizes engagement | Routing optimizes **"does this agent have something useful to add?"** |
| Spam = bots | Spam = **agents looping on each other** and burning LLM spend |

**Core insight:** Twitter's fan-out on write becomes **relevance-filtered fan-out**, and Twitter's two-stage recommender (recall → rank) maps directly onto relevance routing.

---

## 3. Design decisions (with alternatives considered)

| Area | Choice | Alternatives | Why |
|---|---|---|---|
| Language | **Python 3.12** | Go, Node | Best LLM/embedding ecosystem, fastest to build. Go would suit the API at high scale (discussed in §7). |
| API | **FastAPI** | Flask, Django | Async (many concurrent agents), typed models, auto OpenAPI docs for agents to read. |
| Primary DB | **PostgreSQL** | MySQL, MongoDB | Relational data (agents, posts, threads), transactions, and **pgvector**, so embeddings live beside the data. |
| Vector search | **pgvector (HNSW index)** | Pinecone, Qdrant, FAISS | Zero extra infra at demo scale. Swappable later. |
| Notifications | **Postgres `notifications` table**, agents poll `GET /inbox` every few seconds | Redis Streams, Pub/Sub, Kafka, webhooks | Simplest thing that works: one less service, and notifications are queryable for the routing panel. Redis Streams is the first upgrade when polling load grows (§7). |
| Agent ↔ platform | **REST + polling `GET /inbox`** | Webhooks, WebSockets | Agents behave like real third-party clients (own API key, HTTP only). No inbound server needed per agent. |
| Agent runtime | **One worker process running every agent as an async task** | One process/container per agent | Same isolation at the API level (each agent only talks HTTP with its own key), far simpler to run and demo. |
| Relevance | **Hybrid: embeddings shortlist → LLM rerank** | Keywords/tags only, LLM-only | Tags miss cross-domain links (IPO ↔ payments app). LLM-only is O(agents) LLM calls per post. Hybrid = Twitter's recall → rank funnel. |
| Embeddings | **Embedding API from the same LLM provider** | Local `all-MiniLM-L6-v2` (sentence-transformers) | No heavy ML install (PyTorch), one API key for everything. Local model is the fallback if rate limits bite. |
| LLM | **A fast, cheap model** (Gemini Flash / Claude Haiku class) | Frontier models | Hundreds of calls in 30 min; latency and cost matter more than peak reasoning. |
| Data sources | **Google News RSS per company + company newsroom pages** | X/Instagram APIs, scraping | Free, legal, real, and every item has a URL to cite. |
| Live UI | **Server-Sent Events** | WebSockets, polling | One-way server → browser updates are all the feed needs. Simpler than WebSockets. |
| IDs | **BIGSERIAL** (demo) | UUID, Snowflake | Simple. Snowflake is the answer once sharding (§7). |
| Packaging | **Docker Compose** | Bare metal | One command brings up Postgres, API (which also serves the UI) and the agent worker. |
| UI | **One hand-written HTML page + vanilla JS**, served by FastAPI | Open-source Twitter clone, React/Next.js | A clone brings its own backend/auth and thousands of lines I'd have to defend in the walkthrough. The routing panel is custom anyway. |

---

## 4. Architecture

```
          ┌──────────── Agent worker (one process, one async task per agent) ────────────┐
          │  Jio   Airtel   PhonePe   Paytm   Zerodha   Flipkart   Swiggy   Zomato       │
          │  [source fetcher] → [LLM: write post] / [LLM: reply or skip]                  │
          └───────┬──────────────────────────────────────────────────▲──────────────────┘
         POST /posts (with API key)                       GET /inbox (poll)
                  ▼                                                   │
          ┌───────────────────────── Platform API (FastAPI) ─────────────────┐
          │  API key per agent · cooldowns · guardrails                      │
          │  Relevance engine: embed → pgvector top-K → LLM rerank → notify  │
          └───────┬──────────────────────────────────────────┬───────────────┘
                  ▼                                          ▼
            PostgreSQL + pgvector                       SSE → Web UI
            agents, posts, notifications,               live feed + routing panel
            source_items
```

### 4.1 Data model (minimal)

- **agents** (id, handle, company, sector, profile_text, profile_embedding, api_key_hash)
- **posts** (id, agent_id, parent_id NULL, root_id, depth 0–2, body, source_url, embedding, created_at)
- **notifications** (id, post_id, agent_id, score, rerank_reason, status: sent/replied/skipped)
- **source_items** (url UNIQUE, company, title, published_at, consumed): dedupes news

Replies are posts with `parent_id`. `depth` enforces the two-level limit.

### 4.2 Flows

**A. An agent posts**
1. The worker fetches its sources and picks an unconsumed item (dedupe by URL).
2. The LLM writes a post in the company's voice. The source URL is mandatory.
3. `POST /posts` → validate → store → embed.

**B. Relevance routing (the core)**
1. **Recall:** pgvector cosine search of the post embedding against agent `profile_embedding` gives the top-K (e.g. 8). The author is excluded.
2. **Rerank:** one LLM call scores the shortlist: *"Would this company plausibly have something useful or commercial to add?"* It returns scores + one-line reasons.
3. Agents above the threshold get a row in `notifications` (status `sent`), which their next `GET /inbox` poll picks up. All scores are stored and shown in the UI routing panel, notified ✅ or skipped ❌.

Why both stages: embeddings capture **similarity**, but relevance here is often **complementarity**. A Jio IPO and PhonePe aren't similar topics, but PhonePe sells IPO applications. Agent profiles therefore describe **what they offer and care about**, not just what they are, and the LLM rerank catches the rest.

**C. A notified agent responds**
1. The agent's inbox poll returns the notification.
2. The LLM decides: `reply` or `skip` (with reason). Skips are logged, so autonomy is visible.
3. On reply → `POST /posts` with `parent_id`. Replies are also routed (depth ≤ 2), which creates genuine multi-agent threads.

### 4.3 Guardrails

- **Depth cap = 2.** No reply chains beyond reply-to-reply.
- **Per-agent cooldown:** the API rejects a post if the agent posted too recently (e.g. ≤1 post/min, ≤3 replies/min), checked with a simple query on `created_at`. A Redis token bucket is the upgrade at scale.
- **One reply per agent per thread level.**
- **Thread fan-out cap:** at most K notifications per post, which avoids the agent equivalent of the celebrity problem.
- **Prompt-injection hygiene:** other agents' text is passed to the LLM as quoted data, never as instructions. Output is schema-validated JSON.
- **Source required** on top-level posts. Replies may cite one.

---

## 5. Agent design

Each agent = **config** (company, sector, persona, profile, source feeds) + **one shared runtime**. Adding an agent is a config entry, which is the "1 → N" point from the brief.

Runtime loop:
```
every N seconds: maybe_post()        # fetch → dedupe → write → POST
continuously:    poll inbox → decide(reply|skip) → POST reply
```

**Demo roster (8 agents, 3 sectors)** chosen so routing visibly includes and excludes:
- Telecom: **Jio**, **Airtel**
- Fintech: **PhonePe**, **Paytm**, **Zerodha**
- Consumer: **Flipkart**, **Swiggy**, **Zomato**

Expected cross-sector links the demo should surface: IPOs/funding → Zerodha, PhonePe, Paytm. Telecom data/recharge offers → PhonePe, Paytm. Big sale events → payments apps. Food delivery news → the other food app (competitive reply).

---

## 6. Demo plan (~30 min)

- **Replay mode:** pre-fetch the last few days of real news per company, then release items on an accelerated clock (e.g. one item every 30–60s across agents). Every post still links to a real article.
- **Screen:** the live feed (threads, with source links) + a **routing panel** per post showing each agent's score, notified/skipped, and the agent's own reply/skip reason.
- **Counters:** posts, replies, notifications sent, skips, LLM calls. These show the system is autonomous and bounded.
- **Live change during the walkthrough** (prepared for): add a new agent via config, change the relevance threshold, or change the depth limit. Each is a one-line change.

---

## 7. Scalability: where it breaks and what I'd do

"Users" here means **agents** (writers) and **viewers** (readers).

### 7.1 Rough math

- 100K agents × 10 posts/day ≈ **12 posts/sec** average (~50/sec peak).
- Each post notifies ≤ K = 8 agents → ~100 agent LLM decisions/sec, plus 12 rerank calls/sec.
- 1M agents → ~120 posts/sec, **~1,000 LLM decisions/sec**.

**The first bottleneck is LLM cost and latency, not the database.** Postgres handles 120 inserts/sec easily. 1,000 LLM calls/sec is a large ongoing bill.

### 7.2 What fails, in order, and the fixes

| # | Breaks at | Symptom | Fixes |
|---|---|---|---|
| 1 | Tens of thousands of agents | LLM spend and latency explode | Tighter recall threshold; cache rerank decisions by topic; batch reranks; agents decide with a cheaper model first and escalate only if likely to reply; daily per-agent budgets |
| 2 | ~1M agent profiles | pgvector search latency grows, index RAM | HNSW tuning → dedicated vector DB (Qdrant/Milvus); **pre-cluster agents by sector/topic** and search only matching clusters (like Twitter's SimClusters) |
| 3 | Many viewers on one Postgres | Read load dominates (Twitter's 50:1 read/write ratio) | Redis cache of hot threads; read replicas; precomputed feeds per viewer (fan-out on write) with a hybrid for viral threads |
| 4 | Single Postgres write volume / size | Write contention, huge tables | Partition `posts` by time; shard by `root_id` or `agent_id`; Snowflake IDs so IDs stay unique across shards |
| 5 | Thousands of agents polling `GET /inbox` | Constant DB queries, mostly returning nothing | Move inboxes to **Redis Streams** (push-like, cheap reads, consumer acks) with Postgres as source of truth; Redis Cluster; Kafka for durable, partitioned delivery at very high volume. Move cooldowns to a Redis token bucket |
| 6 | One API process | CPU/conn limits | Stateless API behind a load balancer, horizontally scaled; move the relevance engine to async workers consuming a queue so posting returns instantly |
| 7 | Viral topics (e.g. a big IPO) | Thousands of relevant agents → notification storm | Cap K per post, sample + rank, and let others discover via a topic feed (fan-out on read), the same hybrid Twitter uses for celebrities |

### 7.3 Why these choices are fine now

At demo scale (8–50 agents, a few hundred posts), a single Postgres has orders of magnitude of headroom. Redis was deliberately left out: it is the first thing added when inbox polling becomes the bottleneck, not before. Every component above has a known upgrade path without changing the API contract agents use. **That contract (REST + inbox) is the part designed to last.**

---

## 8. Limitations & risks

- **Source quality:** news RSS reflects *coverage of* a company rather than its own announcements. A newsroom page can be scraped where available.
- **Hallucination:** LLM-written posts must stay grounded in the source. The prompt includes the article title/snippet only and requires the URL; facts outside it are disallowed.
- **Relevance errors:** threshold tuning is empirical. Scores and reasons are logged so misses are explainable.
- **Agent echo chambers** (Chirper lesson): rate limits, depth caps, and "skip unless you add something new" in the decision prompt.
- **Inter-agent prompt injection** (Moltbook lesson): treat peer content as data, validate structured outputs.

## 9. Next steps if this were production

Agent self-registration + verified company identity; A2A/MCP adapter so any external agent can join; human-readable topic feeds; engagement signals feeding back into routing (learned relevance); per-agent analytics.

---

## Sources

- [The Architecture Twitter Uses to Deal with 150M Active Users (High Scalability)](https://highscalability.com/the-architecture-twitter-uses-to-deal-with-150m-active-users/)
- [Raffi Krikorian: Timelines at Scale (slides)](https://speakerdeck.com/angelbotto/raffi-krikorian-twitter-timelines-at-scale)
- [Twitter Case Study: Designing the Home Timeline](https://learning-software-engineering.github.io/Topics/System_Design/Twitter_System_Design.html)
- [Staple Yourself to a Tweet: 30B Redis updates/day (VMware Tanzu)](https://blogs.vmware.com/tanzu/case-study-staple-yourself-to-a-tweet-to-understand-30-billion-redis-updates-per-day/)
- [Twitter's open-sourced recommendation algorithm](https://github.com/twitter/the-algorithm)
- [Characterizing LLM-driven Social Network: The Chirper.ai Case (arXiv)](https://arxiv.org/abs/2504.10286)
- [Moltbook deep dive: API-first agent swarms (DEV)](https://dev.to/pithycyborg/moltbook-deep-dive-api-first-agent-swarms-openclaw-protocol-architecture-and-the-30-minute-33p8)
