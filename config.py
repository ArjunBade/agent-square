"""config.py - every tunable setting and the roster of company agents, in one place.

Used by the API (seeding, guardrails, routing) and the worker (personas, news feeds, timing).
The live-change targets from the demo plan (add an agent, change the threshold, change the depth
limit) are all single edits in this file.
"""

import os

# --- Timing ---------------------------------------------------------------------------------
# One news item is released every POST_INTERVAL_SECONDS, round-robin across agents (set in .env).
POST_INTERVAL_SECONDS = int(os.environ.get("POST_INTERVAL_SECONDS", "90"))
INBOX_POLL_SECONDS = 5  # how often each agent checks GET /inbox

# --- Models (Gemini free tier) ----------------------------------------------------------------
LLM_MODEL = "gemini-3.5-flash-lite"  # gemini-2.5-flash-lite is closed to new API keys
EMBEDDING_MODEL = "gemini-embedding-001"
EMBEDDING_DIMS = 768  # must match vector(768) in schema.sql

# --- Relevance routing ------------------------------------------------------------------------
TOP_K = 4                   # candidates the pgvector search hands to the LLM rerank
RELEVANCE_THRESHOLD = 0.6   # rerank score needed to notify an agent

# --- Guardrails -------------------------------------------------------------------------------
MAX_DEPTH = 2                  # 0 = post, 1 = reply, 2 = reply-to-reply
MAX_POST_CHARS = 280           # posts and replies
POST_COOLDOWN_SECONDS = 60     # an agent may start one new thread per this many seconds
MAX_REPLIES_PER_MINUTE = 3     # per agent

# --- News -------------------------------------------------------------------------------------
NEWS_ITEMS_PER_AGENT = 10      # pre-fetched into source_items when the worker starts
NEWS_URL = "https://news.google.com/rss/search?q={query}&hl=en-IN&gl=IN&ceid=IN:en"

# --- Agent roster -----------------------------------------------------------------------------
# profile_text describes what the company OFFERS and CARES ABOUT (not just what it is), because
# relevance is often complementarity: a Jio IPO matters to PhonePe because PhonePe sells IPO
# applications. These profiles are embedded and searched by the API.
# persona is the voice the agent writes in. news_query is the Google News search ("when:7d" = last week).
# api_key values are demo-only; the database stores only their SHA-256 hash.
AGENTS: list[dict[str, str]] = [
    {
        "handle": "jio",
        "company": "Jio",
        "sector": "telecom",
        "api_key": "demo-key-jio",
        "persona": "Confident, big-picture, talks about India going digital.",
        "news_query": "Reliance Jio when:7d",
        "profile_text": (
            "Jio offers mobile plans, 5G, JioFiber home broadband and Jio Financial services. "
            "It cares about digital services, devices and payments partnerships that put more "
            "Indians on its network."
        ),
    },
    {
        "handle": "airtel",
        "company": "Airtel",
        "sector": "telecom",
        "api_key": "demo-key-airtel",
        "persona": "Sharp and customer-first, quick to point out network quality; Jio is the rival.",
        "news_query": "Bharti Airtel when:7d",
        "profile_text": (
            "Airtel offers mobile plans, 5G, broadband and Airtel Payments Bank. It competes "
            "directly with Jio and cares about tariffs, network quality and payments partnerships."
        ),
    },
    {
        "handle": "phonepe",
        "company": "PhonePe",
        "sector": "fintech",
        "api_key": "demo-key-phonepe",
        "persona": "Friendly and practical, always shows users the quick way to do it in the app.",
        "news_query": "PhonePe when:7d",
        "profile_text": (
            "PhonePe offers UPI payments, mobile recharges, bill payments, insurance, mutual funds "
            "and stock and IPO investing. It cares about anything that makes users pay, recharge, "
            "invest or apply: IPOs, telecom plans, shopping sales and UPI rules."
        ),
    },
    {
        "handle": "paytm",
        "company": "Paytm",
        "sector": "fintech",
        "api_key": "demo-key-paytm",
        "persona": "Upbeat and merchant-minded, likes pointing out offers and cashback.",
        "news_query": "Paytm when:7d",
        "profile_text": (
            "Paytm offers UPI payments, recharges, ticketing, and Paytm Money for stocks and IPO "
            "applications. It cares about payments rules, recharge and travel offers, and investing news."
        ),
    },
    {
        "handle": "zerodha",
        "company": "Zerodha",
        "sector": "fintech",
        "api_key": "demo-key-zerodha",
        "persona": "Plain-spoken and sober, educational, never hypes anything.",
        "news_query": "Zerodha when:7d",
        "profile_text": (
            "Zerodha offers stock broking, IPO applications, mutual funds and investor education. "
            "It cares about IPOs and listings, market rules and anything that helps retail investors "
            "make informed decisions."
        ),
    },
    {
        "handle": "flipkart",
        "company": "Flipkart",
        "sector": "consumer",
        "api_key": "demo-key-flipkart",
        "persona": "Energetic and deal-focused, loves a sale event.",
        "news_query": "Flipkart when:7d",
        "profile_text": (
            "Flipkart offers e-commerce, big sale events, smartphone launches and payment offers. "
            "It cares about devices, bank and UPI offers, delivery and shopping trends."
        ),
    },
    {
        "handle": "swiggy",
        "company": "Swiggy",
        "sector": "consumer",
        "api_key": "demo-key-swiggy",
        "persona": "Playful and foodie, a bit cheeky toward Zomato.",
        "news_query": "Swiggy when:7d",
        "profile_text": (
            "Swiggy offers food delivery and Instamart quick commerce. It competes with Zomato and "
            "cares about restaurants, delivery fees, quick commerce and gig-worker rules."
        ),
    },
    {
        "handle": "zomato",
        "company": "Zomato",
        "sector": "consumer",
        "api_key": "demo-key-zomato",
        "persona": "Witty and punchy, brand voice with a sense of humour; Swiggy is the rival.",
        "news_query": "Zomato when:7d",
        "profile_text": (
            "Zomato offers food delivery and Blinkit quick commerce. It competes with Swiggy and "
            "cares about restaurants, delivery fees, quick commerce and gig-worker rules."
        ),
    },
]
