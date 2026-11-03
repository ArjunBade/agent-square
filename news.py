"""news.py - fetches real news from Google News RSS and hands it out one item at a time.

Used by the worker. At startup it pre-fetches recent items for every agent into the source_items
table (this is "replay mode": the real articles are released later on the worker's slower clock
instead of waiting for live news). Duplicates are dropped by URL and by normalised title.
"""

import logging
import re
import xml.etree.ElementTree as ET
from datetime import datetime
from email.utils import parsedate_to_datetime
from urllib.parse import quote_plus

import httpx

import config
import db

log = logging.getLogger("news")


def normalise_title(title: str) -> str:
    """Lowercase headline without the ' - Publisher' suffix or punctuation, to catch repeats."""
    headline = title.rsplit(" - ", 1)[0]
    return re.sub(r"[^a-z0-9]+", " ", headline.lower()).strip()


async def fetch_feed_items(client: httpx.AsyncClient, query: str) -> list[dict]:
    """Download one Google News RSS search and return its items (newest first, as served)."""
    response = await client.get(config.NEWS_URL.format(query=quote_plus(query)))
    response.raise_for_status()
    items = []
    for item in ET.fromstring(response.text).iterfind("./channel/item"):
        title, link, pub_date = item.findtext("title"), item.findtext("link"), item.findtext("pubDate")
        if not title or not link:
            continue  # an item without a headline or link can't be posted
        published: datetime | None = parsedate_to_datetime(pub_date) if pub_date else None
        items.append({"title": title, "url": link, "published_at": published})
    return items


async def prefetch_all(agents: list[dict]) -> None:
    """Store up to NEWS_ITEMS_PER_AGENT recent items per agent; one failing feed doesn't stop the rest."""
    async with httpx.AsyncClient(timeout=20, follow_redirects=True) as client:
        for agent in agents:
            try:
                items = await fetch_feed_items(client, agent["news_query"])
            except (httpx.HTTPError, ET.ParseError) as e:
                log.error("[%s] could not fetch news: %s", agent["company"], e)
                continue
            added = 0
            for item in items[: config.NEWS_ITEMS_PER_AGENT]:
                # ON CONFLICT DO NOTHING covers both UNIQUE columns (url and title_key).
                result = await db.pool.execute(
                    """
                    INSERT INTO source_items (url, company, title, title_key, published_at)
                    VALUES ($1, $2, $3, $4, $5) ON CONFLICT DO NOTHING
                    """,
                    item["url"], agent["company"], item["title"],
                    normalise_title(item["title"]), item["published_at"],
                )
                added += result == "INSERT 0 1"
            log.info("[%s] pre-fetched %d new news items (%d in feed)", agent["company"], added, len(items))


async def claim_next_item(company: str) -> dict | None:
    """Take the oldest unconsumed item for a company (so replay runs in time order), or None."""
    # Marked consumed up front: if the LLM then fails we skip the item rather than retry it forever.
    row = await db.pool.fetchrow(
        """
        UPDATE source_items SET consumed = true
        WHERE id = (SELECT id FROM source_items WHERE company = $1 AND NOT consumed
                    ORDER BY published_at ASC NULLS LAST, id LIMIT 1)
        RETURNING url, title
        """,
        company,
    )
    return dict(row) if row else None
