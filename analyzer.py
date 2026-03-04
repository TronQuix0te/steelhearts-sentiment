from __future__ import annotations

import json
import logging
import re

import anthropic

import config

log = logging.getLogger(__name__)

_client: anthropic.AsyncAnthropic | None = None

SYSTEM_PROMPT = """\
You are a sentiment analysis engine for the SteelHearts NFT Discord community.
They had a failed mint and are planning a new 10,000 NFT mint in 30 days.

Classify each message as **positive**, **negative**, or **neutral** and assign a
score from -1.0 (very negative) to +1.0 (very positive). Extract up to 3 keywords.

NFT/crypto slang guide:
- Bullish terms (positive): LFG, WAGMI, moon, diamond hands, based, fren, ser,
  bullish, degen (when enthusiastic), wen mint, hyped, gm, gn, lets go
- Bearish terms (negative): rug, rugged, scam, dead project, paper hands, dump,
  rekt, ngmi, exit scam, waste, refund, wen refund
- Neutral: gm/gn (greetings alone), floor price inquiries, simple questions

Return ONLY a JSON array with one object per message:
[
  {"id": "<message_id>", "sentiment": "positive|negative|neutral", "score": 0.0, "keywords": ["kw1","kw2"]}
]

No markdown fences, no extra text — just the JSON array."""


def _clean(text: str) -> str:
    """Strip Discord custom emojis, mentions, and truncate."""
    text = re.sub(r"<a?:\w+:\d+>", "", text)  # custom emojis
    text = re.sub(r"<@!?\d+>", "@user", text)  # user mentions
    text = re.sub(r"<#\d+>", "#channel", text)  # channel mentions
    text = re.sub(r"<@&\d+>", "@role", text)  # role mentions
    text = text.strip()
    if len(text) > 500:
        text = text[:500] + "..."
    return text


def _get_client() -> anthropic.AsyncAnthropic:
    global _client
    if _client is None:
        _client = anthropic.AsyncAnthropic(api_key=config.ANTHROPIC_API_KEY)
    return _client


async def analyze_batch(messages: list[dict]) -> list[dict]:
    """Analyze a batch of messages via Claude API.

    Each message dict must have 'discord_message_id' and 'content' keys.
    Returns a list of dicts with 'id', 'sentiment', 'score', 'keywords'.
    """
    if not messages:
        return []

    if not config.ANTHROPIC_API_KEY or config.ANTHROPIC_API_KEY.startswith("your-"):
        log.warning("No Anthropic API key configured — skipping analysis")
        return []

    user_content = "\n".join(
        f'[{m["discord_message_id"]}] {_clean(m["content"])}'
        for m in messages
    )

    client = _get_client()
    try:
        response = await client.messages.create(
            model=config.CLAUDE_MODEL,
            max_tokens=2048,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_content}],
        )

        raw = response.content[0].text.strip()
        # Strip markdown fences if Claude adds them despite instructions
        if raw.startswith("```"):
            raw = re.sub(r"^```(?:json)?\s*", "", raw)
            raw = re.sub(r"\s*```$", "", raw)

        results = json.loads(raw)
        if not isinstance(results, list):
            raise ValueError("Expected JSON array")

        # Validate each result
        validated = []
        for r in results:
            sentiment = r.get("sentiment", "neutral")
            if sentiment not in ("positive", "negative", "neutral"):
                sentiment = "neutral"
            score = float(r.get("score", 0.0))
            score = max(-1.0, min(1.0, score))
            keywords = r.get("keywords", [])
            if not isinstance(keywords, list):
                keywords = []
            validated.append({
                "id": str(r.get("id", "")),
                "sentiment": sentiment,
                "score": round(score, 2),
                "keywords": keywords[:3],
            })

        return validated

    except (json.JSONDecodeError, KeyError, ValueError) as exc:
        log.warning("Failed to parse Claude response: %s", exc)
        return [
            {
                "id": m["discord_message_id"],
                "sentiment": "neutral",
                "score": 0.0,
                "keywords": [],
            }
            for m in messages
        ]
    except anthropic.APIError as exc:
        log.error("Claude API error: %s", exc)
        return []


INSIGHTS_PROMPT = """\
You are a community intelligence analyst for the SteelHearts NFT project on Discord.
They had a failed mint (only 20 sold) and are planning a new 10,000 NFT mint in 30 days.

Analyze the following community messages and produce a JSON report with these sections:

1. **action_items**: Concrete things the team should act on right now. Each item has:
   - "priority": "high" | "medium" | "low"
   - "title": short action (under 10 words)
   - "detail": 1-2 sentence explanation with evidence from messages

2. **summary**: A 3-5 sentence narrative summary of the overall community mood,
   key themes, and how sentiment is trending.

3. **risks**: Emerging concerns or negative patterns the team should watch.
   Each has "title" and "detail".

4. **opportunities**: Positive signals or community suggestions worth amplifying.
   Each has "title" and "detail".

Return ONLY valid JSON:
{
  "action_items": [...],
  "summary": "...",
  "risks": [...],
  "opportunities": [...]
}

No markdown fences, no extra text."""


# Simple in-memory cache for insights
_insights_cache: dict = {"data": None, "timestamp": 0}
INSIGHTS_CACHE_TTL = 300  # 5 minutes


async def generate_insights(messages: list[dict]) -> dict:
    """Generate actionable insights from recent community messages."""
    import time

    now = time.time()
    if _insights_cache["data"] and (now - _insights_cache["timestamp"]) < INSIGHTS_CACHE_TTL:
        return _insights_cache["data"]

    if not messages:
        return {"action_items": [], "summary": "No messages to analyze.", "risks": [], "opportunities": []}

    if not config.ANTHROPIC_API_KEY or config.ANTHROPIC_API_KEY.startswith("your-"):
        return {"action_items": [], "summary": "No API key configured.", "risks": [], "opportunities": []}

    # Build message context with sentiment labels
    lines = []
    for m in messages[:200]:  # cap at 200 messages
        sent = f" [{m.get('sentiment', '?')}]" if m.get("sentiment") else ""
        lines.append(f"[{m.get('author_name', '?')}]{sent}: {_clean(m.get('content', ''))}")

    user_content = "\n".join(lines)

    client = _get_client()
    try:
        response = await client.messages.create(
            model=config.CLAUDE_MODEL,
            max_tokens=2048,
            system=INSIGHTS_PROMPT,
            messages=[{"role": "user", "content": user_content}],
        )

        raw = response.content[0].text.strip()
        if raw.startswith("```"):
            raw = re.sub(r"^```(?:json)?\s*", "", raw)
            raw = re.sub(r"\s*```$", "", raw)

        result = json.loads(raw)
        _insights_cache["data"] = result
        _insights_cache["timestamp"] = now
        return result

    except (json.JSONDecodeError, KeyError, ValueError) as exc:
        log.warning("Failed to parse insights response: %s", exc)
        return {"action_items": [], "summary": "Failed to generate insights.", "risks": [], "opportunities": []}
    except anthropic.APIError as exc:
        log.error("Claude API error (insights): %s", exc)
        return {"action_items": [], "summary": "API error generating insights.", "risks": [], "opportunities": []}
