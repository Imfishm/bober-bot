import asyncio

import discord
from discord.ext import commands

from bober.config import DISCORD_TOKEN, GUILD_ID

intents = discord.Intents.default()

bot = commands.Bot(command_prefix=None, intents=intents)

@bot.event
async def on_ready():
    print(f"Logged in as {bot.user.name}")

    try:
        await bot.tree.sync(guild=discord.Object(id=GUILD_ID))
        print("Slash commands synced successfully")
    except Exception as e:
        print(f"Failed to sync slash commands: {e}")

async def load_extensions():
    await bot.load_extension("bober.cogs.tracking")
    print("Tracking module loaded")

async def main():
    async with bot:
        await load_extensions()
        await bot.start(DISCORD_TOKEN)

if __name__ == "__main__":
    asyncio.run(main())
