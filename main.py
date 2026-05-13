import asyncio

import uvicorn

import app_core
from web import app


async def start_web():
    config = uvicorn.Config(app, host="0.0.0.0", port=18080, log_level="info")
    server = uvicorn.Server(config)
    await server.serve()


async def start_optional_discord_bot():
    settings = await app_core.get_settings()
    if not settings.get("discord_enabled", True):
        print("ℹ️ Discord bot disabled in settings; Web GUI only.")
        while True:
            await asyncio.sleep(3600)
    from config import TOKEN
    if not TOKEN:
        print("ℹ️ DISCORD_BOT_TOKEN is not set; Web GUI only.")
        while True:
            await asyncio.sleep(3600)
    from bot import start_discord_bot
    await start_discord_bot()


async def main():
    await asyncio.gather(start_optional_discord_bot(), start_web())


if __name__ == "__main__":
    asyncio.run(main())
