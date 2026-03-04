import asyncio
import logging
import signal

import uvicorn

import config
import db
from bot import client
from web import app

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("steelhearts")


async def run_web_server():
    server_config = uvicorn.Config(
        app,
        host=config.WEB_HOST,
        port=config.WEB_PORT,
        log_level="info",
        access_log=False,
    )
    server = uvicorn.Server(server_config)
    await server.serve()


async def main():
    # Initialize database
    await db.get_db()
    log.info("Database initialized at %s", config.DB_PATH)

    log.info("Starting SteelHearts Sentiment Dashboard")
    log.info("Dashboard will be available at http://localhost:%d", config.WEB_PORT)

    try:
        await asyncio.gather(
            client.start(config.DISCORD_TOKEN),
            run_web_server(),
        )
    except asyncio.CancelledError:
        pass
    finally:
        log.info("Shutting down...")
        await client.close()
        await db.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log.info("Interrupted by user")
