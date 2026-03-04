from __future__ import annotations

from typing import Optional

import aiosqlite
import config

_db: aiosqlite.Connection | None = None


async def get_db() -> aiosqlite.Connection:
    global _db
    if _db is None:
        _db = await aiosqlite.connect(config.DB_PATH)
        _db.row_factory = aiosqlite.Row
        await _db.execute("PRAGMA journal_mode=WAL")
        await _db.execute("PRAGMA foreign_keys=ON")
        await _init_tables(_db)
    return _db


async def _init_tables(db: aiosqlite.Connection) -> None:
    await db.executescript("""
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            discord_message_id TEXT UNIQUE,
            channel_id TEXT NOT NULL,
            channel_name TEXT NOT NULL,
            author_id TEXT NOT NULL,
            author_name TEXT NOT NULL,
            content TEXT NOT NULL,
            sentiment TEXT DEFAULT NULL,
            score REAL DEFAULT NULL,
            keywords TEXT DEFAULT NULL,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            analyzed_at TEXT DEFAULT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_messages_channel ON messages(channel_id);
        CREATE INDEX IF NOT EXISTS idx_messages_author ON messages(author_id);
        CREATE INDEX IF NOT EXISTS idx_messages_created ON messages(created_at);
        CREATE INDEX IF NOT EXISTS idx_messages_sentiment ON messages(sentiment);

        CREATE TABLE IF NOT EXISTS sentiment_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            bucket TEXT NOT NULL,
            channel_id TEXT,
            positive_count INTEGER NOT NULL DEFAULT 0,
            negative_count INTEGER NOT NULL DEFAULT 0,
            neutral_count INTEGER NOT NULL DEFAULT 0,
            avg_score REAL NOT NULL DEFAULT 0.0,
            message_count INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );

        CREATE INDEX IF NOT EXISTS idx_snapshots_bucket ON sentiment_snapshots(bucket);

        CREATE TABLE IF NOT EXISTS key_moments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            label TEXT NOT NULL,
            description TEXT DEFAULT '',
            moment_type TEXT NOT NULL DEFAULT 'announcement',
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );

        CREATE INDEX IF NOT EXISTS idx_moments_timestamp ON key_moments(timestamp);
    """)
    await db.commit()


async def insert_message(
    discord_message_id: str,
    channel_id: str,
    channel_name: str,
    author_id: str,
    author_name: str,
    content: str,
    created_at: Optional[str] = None,
) -> int:
    db = await get_db()
    if created_at:
        cursor = await db.execute(
            """INSERT OR IGNORE INTO messages
               (discord_message_id, channel_id, channel_name, author_id, author_name, content, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (discord_message_id, channel_id, channel_name, author_id, author_name, content, created_at),
        )
    else:
        cursor = await db.execute(
            """INSERT OR IGNORE INTO messages
               (discord_message_id, channel_id, channel_name, author_id, author_name, content)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (discord_message_id, channel_id, channel_name, author_id, author_name, content),
        )
    await db.commit()
    return cursor.lastrowid or 0


async def update_sentiment(
    discord_message_id: str, sentiment: str, score: float, keywords: str
) -> None:
    db = await get_db()
    await db.execute(
        """UPDATE messages
           SET sentiment=?, score=?, keywords=?, analyzed_at=datetime('now')
           WHERE discord_message_id=?""",
        (sentiment, score, keywords, discord_message_id),
    )
    await db.commit()


async def get_unanalyzed(limit: int = 20) -> list[dict]:
    db = await get_db()
    cursor = await db.execute(
        """SELECT discord_message_id, channel_name, author_name, content
           FROM messages WHERE sentiment IS NULL
           ORDER BY created_at ASC LIMIT ?""",
        (limit,),
    )
    rows = await cursor.fetchall()
    return [dict(r) for r in rows]


async def insert_snapshot(
    bucket: str,
    channel_id: str | None,
    positive: int,
    negative: int,
    neutral: int,
    avg_score: float,
    message_count: int,
) -> None:
    db = await get_db()
    await db.execute(
        """INSERT INTO sentiment_snapshots
           (bucket, channel_id, positive_count, negative_count, neutral_count, avg_score, message_count)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (bucket, channel_id, positive, negative, neutral, avg_score, message_count),
    )
    await db.commit()


# ── Key Moments ──


async def insert_key_moment(
    timestamp: str, label: str, description: str = "", moment_type: str = "announcement"
) -> int:
    db = await get_db()
    cursor = await db.execute(
        """INSERT INTO key_moments (timestamp, label, description, moment_type)
           VALUES (?, ?, ?, ?)""",
        (timestamp, label, description, moment_type),
    )
    await db.commit()
    return cursor.lastrowid or 0


async def get_key_moments(hours: int = 720) -> list[dict]:
    db = await get_db()
    cursor = await db.execute(
        """SELECT id, timestamp, label, description, moment_type
           FROM key_moments
           WHERE timestamp >= datetime('now', ? || ' hours')
           ORDER BY timestamp ASC""",
        (f"-{hours}",),
    )
    rows = await cursor.fetchall()
    return [dict(r) for r in rows]


async def delete_key_moment(moment_id: int) -> None:
    db = await get_db()
    await db.execute("DELETE FROM key_moments WHERE id = ?", (moment_id,))
    await db.commit()


# ── Query helpers for the API ──


async def overview(sentiment_filter: Optional[str] = None) -> dict:
    db = await get_db()
    where = ""
    params = []
    if sentiment_filter:
        where = "WHERE sentiment = ?"
        params.append(sentiment_filter)

    cursor = await db.execute(
        f"""SELECT
             COUNT(*) as total,
             SUM(CASE WHEN sentiment='positive' THEN 1 ELSE 0 END) as positive,
             SUM(CASE WHEN sentiment='negative' THEN 1 ELSE 0 END) as negative,
             SUM(CASE WHEN sentiment='neutral' THEN 1 ELSE 0 END) as neutral,
             AVG(CASE WHEN score IS NOT NULL THEN score END) as avg_score
           FROM messages {where}""",
        params,
    )
    row = await cursor.fetchone()
    return dict(row) if row else {}


async def timeline(hours: int = 72) -> list[dict]:
    db = await get_db()
    cursor = await db.execute(
        """SELECT
             strftime('%Y-%m-%dT%H:00:00', created_at) as bucket,
             SUM(CASE WHEN sentiment='positive' THEN 1 ELSE 0 END) as positive,
             SUM(CASE WHEN sentiment='negative' THEN 1 ELSE 0 END) as negative,
             SUM(CASE WHEN sentiment='neutral' THEN 1 ELSE 0 END) as neutral,
             AVG(CASE WHEN score IS NOT NULL THEN score END) as avg_score,
             COUNT(*) as total
           FROM messages
           WHERE created_at >= datetime('now', ? || ' hours')
             AND sentiment IS NOT NULL
           GROUP BY bucket
           ORDER BY bucket ASC""",
        (f"-{hours}",),
    )
    rows = await cursor.fetchall()
    return [dict(r) for r in rows]


async def top_users(limit: int = 20, sentiment_filter: Optional[str] = None) -> list[dict]:
    db = await get_db()
    where = "WHERE sentiment IS NOT NULL"
    params = []
    if sentiment_filter:
        where += " AND sentiment = ?"
        params.append(sentiment_filter)
    params.append(limit)

    cursor = await db.execute(
        f"""SELECT
             author_name,
             COUNT(*) as message_count,
             AVG(score) as avg_score,
             SUM(CASE WHEN sentiment='positive' THEN 1 ELSE 0 END) as positive,
             SUM(CASE WHEN sentiment='negative' THEN 1 ELSE 0 END) as negative,
             SUM(CASE WHEN sentiment='neutral' THEN 1 ELSE 0 END) as neutral
           FROM messages
           {where}
           GROUP BY author_id
           ORDER BY message_count DESC
           LIMIT ?""",
        params,
    )
    rows = await cursor.fetchall()
    return [dict(r) for r in rows]


async def recent_messages(
    limit: int = 50,
    channel: Optional[str] = None,
    sentiment_filter: Optional[str] = None,
) -> list[dict]:
    db = await get_db()
    where_parts = []
    params = []

    if channel:
        where_parts.append("channel_name = ?")
        params.append(channel)
    if sentiment_filter:
        where_parts.append("sentiment = ?")
        params.append(sentiment_filter)

    where = ("WHERE " + " AND ".join(where_parts)) if where_parts else ""
    params.append(limit)

    cursor = await db.execute(
        f"""SELECT discord_message_id, channel_name, author_name, content,
                  sentiment, score, keywords, created_at
           FROM messages
           {where}
           ORDER BY created_at DESC LIMIT ?""",
        params,
    )
    rows = await cursor.fetchall()
    return [dict(r) for r in rows]


async def channel_list() -> list[dict]:
    db = await get_db()
    cursor = await db.execute(
        """SELECT channel_id, channel_name,
                  COUNT(*) as message_count,
                  AVG(score) as avg_score
           FROM messages
           WHERE sentiment IS NOT NULL
           GROUP BY channel_id
           ORDER BY message_count DESC"""
    )
    rows = await cursor.fetchall()
    return [dict(r) for r in rows]


async def close() -> None:
    global _db
    if _db is not None:
        await _db.close()
        _db = None
