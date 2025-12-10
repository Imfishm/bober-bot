import discord
import os
import cmds

from dotenv import load_dotenv

load_dotenv()

DISCORD_TOKEN = os.getenv("SUPER_SECRET_DISCORD_TOKEN")

intents = discord.Intents.default()
intents.message_content = True

bot = discord.Client(intents=intents)

COMMANDS = {
    "/Bober" : cmds.bober,
    "/Bober FEAST" : cmds.bober_feast,
    "/Bober ART" : cmds.bober_art,
    "/Bober TIME" : cmds.bober_time,
}

@bot.event
async def on_message(message):
    handler = COMMANDS.get(message.content)
    if handler:
        await handler(message)

bot.run(DISCORD_TOKEN)