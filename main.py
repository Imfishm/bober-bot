import discord
import os
import asyncio
import cmds

from dotenv import load_dotenv
from discord.ext import commands

load_dotenv()

DISCORD_TOKEN = os.getenv("SUPER_SECRET_DISCORD_TOKEN")
GUILD_ID = 1446565791452364935

intents = discord.Intents.default()

bot = commands.Bot(command_prefix="None", intents=intents)

@bot.event
async def on_ready():
    print(f"Logged in as {bot.user.name}")

    try:
        await bot.tree.sync(guild=discord.Object(id=GUILD_ID))
        print("Slash commands synced successfully")
    except Exception as e:
        print(f"Failed to sync slash commands: {e}")

async def load_extensions():
    await bot.load_extension("cmds")
    print("Commands module loaded")

async def main():
    await load_extensions()
    await bot.start(DISCORD_TOKEN)

if __name__ == "__main__":
    asyncio.run(main())