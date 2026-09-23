"""Railway entry point: one Discord connection and one admin web server."""
import asyncio
import os

import uvicorn

from portal.config import Settings
from portal.web import create_app


async def supervise(server, bot_main, bot):
    web_task = asyncio.create_task(server.serve(), name="web")
    bot_task = asyncio.create_task(bot_main(), name="discord")
    try:
        done, _ = await asyncio.wait({web_task, bot_task}, return_when=asyncio.FIRST_COMPLETED)
        for task in done:
            task.result()
        if bot_task in done and not server.should_exit:
            raise RuntimeError("Discord stopped unexpectedly")
    finally:
        server.should_exit = True
        await bot.close()
        bot_task.cancel()
        try:
            await asyncio.wait_for(asyncio.gather(web_task, bot_task, return_exceptions=True), timeout=15)
        except TimeoutError:
            web_task.cancel()
            await asyncio.gather(web_task, bot_task, return_exceptions=True)


async def run():
    os.environ.setdefault("VERIFICATION_MODE", "classes")
    if os.environ["VERIFICATION_MODE"] not in {"classes", "roster"}:
        raise ValueError("serve.py requires VERIFICATION_MODE=classes or roster")
    settings = Settings()
    settings.validate()
    if not os.getenv("DISCORD_TOKEN") or os.getenv("DISCORD_TOKEN") == "YOUR_BOT_TOKEN_HERE":
        raise ValueError("Set DISCORD_TOKEN in Railway Variables")
    from main import bot, main as bot_main

    app = create_app(settings)
    # Initialize before either service starts; the web and bot share these tables.
    await asyncio.to_thread(app.state.registry.initialize)
    app.state.discord_bot = bot
    server = uvicorn.Server(uvicorn.Config(app, host="0.0.0.0", port=int(os.getenv("PORT", "8000")),
        access_log=False, timeout_graceful_shutdown=10))
    await supervise(server, bot_main, bot)


if __name__ == "__main__":
    asyncio.run(run())
