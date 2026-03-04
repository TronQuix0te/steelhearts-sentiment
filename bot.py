import asyncio
import json
import logging
import re
from collections import deque
from datetime import datetime, timezone

import discord

import config
import db
from analyzer import analyze_batch

log = logging.getLogger(__name__)

# Queue of messages awaiting analysis
_queue: deque[dict] = deque()

# Connected WebSocket clients (set by web.py)
ws_clients: set = set()

# Regex for emoji-only messages
_EMOJI_ONLY = re.compile(
    r"^[\s]*"
    r"(?:"
    r"<a?:\w+:\d+>"       # custom emoji
    r"|[\U00010000-\U0010ffff]"  # supplementary plane emoji
    r"|[\u2600-\u27bf]"   # misc symbols
    r"|[\ufe00-\ufe0f]"   # variation selectors
    r"|[\u200d]"           # ZWJ
    r"|[\u20e3]"           # combining enclosing keycap
    r"|[\ufe0f]"           # variation selector-16
    r"|\s"
    r")+$"
)


def _should_skip(message: discord.Message) -> bool:
    if message.author.bot:
        return True
    if config.IGNORE_USERS and message.author.display_name in config.IGNORE_USERS:
        return True
    if config.MONITOR_CHANNELS:
        channel_name = getattr(message.channel, "name", "")
        if not any(allowed in channel_name for allowed in config.MONITOR_CHANNELS):
            return True
    content = message.content.strip()
    if not content:
        return True
    if _EMOJI_ONLY.match(content):
        return True
    return False


intents = discord.Intents.default()
intents.message_content = True
intents.members = True

client = discord.Client(intents=intents)


HISTORY_LIMIT = int(config.BATCH_INTERVAL_SECONDS and 500)  # max messages per channel


@client.event
async def on_ready():
    log.info("Bot connected as %s (ID: %s)", client.user, client.user.id)
    log.info("Monitoring %d guild(s)", len(client.guilds))
    # Backfill history, queue unanalyzed, then start loops
    await backfill_history()
    await queue_unanalyzed()
    client.loop.create_task(batch_analysis_loop())
    client.loop.create_task(snapshot_loop())


async def queue_unanalyzed():
    """Queue any DB messages that haven't been analyzed yet."""
    rows = await db.get_unanalyzed(limit=5000)
    for r in rows:
        _queue.append(r)
    if rows:
        log.info("Queued %d unanalyzed messages from DB", len(rows))


async def backfill_history():
    """Scan all text channels and import recent message history."""
    total = 0
    for guild in client.guilds:
        for channel in guild.text_channels:
            try:
                if config.MONITOR_CHANNELS:
                    if not any(allowed in channel.name for allowed in config.MONITOR_CHANNELS):
                        continue

                perms = channel.permissions_for(guild.me)
                if not perms.read_messages or not perms.read_message_history:
                    continue

                count = 0
                async for message in channel.history(limit=5000, oldest_first=False):
                    if _should_skip(message):
                        continue

                    msg_time = message.created_at.strftime("%Y-%m-%d %H:%M:%S")
                    row_id = await db.insert_message(
                        discord_message_id=str(message.id),
                        channel_id=str(channel.id),
                        channel_name=channel.name,
                        author_id=str(message.author.id),
                        author_name=message.author.display_name,
                        content=message.content,
                        created_at=msg_time,
                    )
                    if row_id:
                        _queue.append({
                            "discord_message_id": str(message.id),
                            "channel_name": channel.name,
                            "author_name": message.author.display_name,
                            "content": message.content,
                        })
                        count += 1

                if count:
                    log.info("Backfilled %d messages from #%s", count, channel.name)
                    total += count

            except discord.Forbidden:
                log.debug("No access to #%s, skipping", channel.name)
            except Exception:
                log.exception("Error backfilling #%s", channel.name)

    log.info("History backfill complete: %d messages total", total)


@client.event
async def on_message(message: discord.Message):
    if _should_skip(message):
        return

    channel_name = getattr(message.channel, "name", "DM")
    channel_id = str(message.channel.id)
    author_id = str(message.author.id)
    author_name = message.author.display_name

    msg_time = message.created_at.strftime("%Y-%m-%d %H:%M:%S")
    row_id = await db.insert_message(
        discord_message_id=str(message.id),
        channel_id=channel_id,
        channel_name=channel_name,
        author_id=author_id,
        author_name=author_name,
        content=message.content,
        created_at=msg_time,
    )

    if row_id:
        _queue.append({
            "discord_message_id": str(message.id),
            "channel_name": channel_name,
            "author_name": author_name,
            "content": message.content,
        })
        log.debug("Queued message %s from %s in #%s", message.id, author_name, channel_name)


async def batch_analysis_loop():
    """Drain the queue every BATCH_INTERVAL or when BATCH_SIZE is reached."""
    await client.wait_until_ready()
    log.info("Batch analysis loop started (interval=%ds, batch=%d)",
             config.BATCH_INTERVAL_SECONDS, config.BATCH_SIZE)

    while not client.is_closed():
        # Process faster when there's a backlog, normal interval otherwise
        interval = 10 if len(_queue) > config.BATCH_SIZE else config.BATCH_INTERVAL_SECONDS
        await asyncio.sleep(interval)
        await _drain_queue()


async def _drain_queue():
    if not _queue:
        return

    batch = []
    while _queue and len(batch) < config.BATCH_SIZE:
        batch.append(_queue.popleft())

    if not batch:
        return

    # Build lookup for original message data
    batch_lookup = {m["discord_message_id"]: m for m in batch}

    log.info("Analyzing batch of %d messages", len(batch))
    results = await analyze_batch(batch)

    enriched = []
    for result in results:
        keywords_str = json.dumps(result["keywords"])
        await db.update_sentiment(
            discord_message_id=result["id"],
            sentiment=result["sentiment"],
            score=result["score"],
            keywords=keywords_str,
        )
        # Enrich with original message data for WebSocket
        orig = batch_lookup.get(result["id"], {})
        enriched.append({
            "discord_message_id": result["id"],
            "author_name": orig.get("author_name", ""),
            "channel_name": orig.get("channel_name", ""),
            "content": orig.get("content", ""),
            "sentiment": result["sentiment"],
            "score": result["score"],
            "keywords": keywords_str,
        })

    # Push to WebSocket clients
    await notify_websockets(enriched)


async def snapshot_loop():
    """Create hourly aggregate snapshots."""
    await client.wait_until_ready()
    log.info("Snapshot loop started (interval=%ds)", config.SNAPSHOT_INTERVAL_SECONDS)

    while not client.is_closed():
        await asyncio.sleep(config.SNAPSHOT_INTERVAL_SECONDS)
        try:
            now = datetime.now(timezone.utc)
            bucket = now.strftime("%Y-%m-%dT%H:00:00")

            database = await db.get_db()
            cursor = await database.execute(
                """SELECT
                     channel_id,
                     SUM(CASE WHEN sentiment='positive' THEN 1 ELSE 0 END) as pos,
                     SUM(CASE WHEN sentiment='negative' THEN 1 ELSE 0 END) as neg,
                     SUM(CASE WHEN sentiment='neutral' THEN 1 ELSE 0 END) as neu,
                     AVG(score) as avg_score,
                     COUNT(*) as cnt
                   FROM messages
                   WHERE analyzed_at >= datetime('now', '-1 hour')
                     AND sentiment IS NOT NULL
                   GROUP BY channel_id"""
            )
            rows = await cursor.fetchall()
            for row in rows:
                await db.insert_snapshot(
                    bucket=bucket,
                    channel_id=row["channel_id"],
                    positive=row["pos"],
                    negative=row["neg"],
                    neutral=row["neu"],
                    avg_score=row["avg_score"] or 0.0,
                    message_count=row["cnt"],
                )
            log.info("Created %d snapshot(s) for bucket %s", len(rows), bucket)
        except Exception:
            log.exception("Snapshot loop error")


async def notify_websockets(results: list[dict]):
    """Push analysis results to all connected WebSocket clients."""
    if not ws_clients:
        return

    payload = json.dumps({"type": "sentiment_update", "data": results})
    disconnected = set()

    for ws in ws_clients:
        try:
            await ws.send_text(payload)
        except Exception:
            disconnected.add(ws)

    for ws in disconnected:
        ws_clients.discard(ws)
