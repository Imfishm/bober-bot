import discord
import os
import cmds

from dotenv import load_dotenv

load_dotenv()

DISCORD_TOKEN = os.getenv("SUPER_SECRET_DISCORD_TOKEN")

intents = discord.Intents.default()
intents.message_content = True

bot = discord.Client(intents=intents)

@bot.event
async def on_message(message):
    handler = cmds.COMMANDS.get(message.content)
    if handler:
        await handler(message)

bot.run(DISCORD_TOKEN)