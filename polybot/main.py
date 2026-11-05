"""POLYBOT application entry point.

Runs the trading orchestrator and the local web dashboard together.
LIVE execution is locked until the user explicitly activates it via the
dashboard (strict LIVE gate, section 36).
"""

from __future__ import annotations

import asyncio
import logging
import sys

import uvicorn

from polybot.app import App
from polybot.config import load_settings
from polybot.database import Database
from polybot.dashboard.server import create_app

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("polybot.main")


async def run() -> None:
    settings = load_settings()
    db = Database("data/polybot.db")
    app = App(settings=settings, db=db)

    dashboard = create_app(app)

    async def _run_dashboard():
        config = uvicorn.Config(
            dashboard,
            host=settings.dashboard.host,
            port=settings.dashboard.port,
            log_level="warning",
        )
        server = uvicorn.Server(config)
        await server.serve()

    await app.start()
    if not app.ready:
        logger.error("POLYBOT failed to reach READY state")
    await _run_dashboard()


def main() -> int:
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(run())
    except KeyboardInterrupt:
        logger.info("Interrupted by user")
    finally:
        try:
            loop.run_until_complete(Database("data/polybot.db").close())
        except Exception:
            pass
        loop.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
