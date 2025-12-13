"""
This file contains all the commands (functions)
for the Bober discord bot (˶ᵔ ᵕ ᵔ˶)
"""
import discord
from discord import app_commands
from discord.ext import commands

from datetime import datetime

class GeneralCommands(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.guilds(1446565791452364935)
    @app_commands.command(name="bober-time", description="Bober gives you the time... kinda.")
    #Bober gives the time.... in French Revolutionary Decimal Time
    async def bober_time(self, interaction: discord.Interaction):
        time = datetime.now()
        seconds = time - time.replace(hour=0, minute=0, second=0, microsecond=0) 
        seconds = seconds.total_seconds()
        fsecs = seconds / 0.864 #86,400 seconds in day - 100,000 seconds in decimal day
        fhours, remaining_seconds = divmod(fsecs, 10000) #10,000 seconds per hour
        fminutes, remaining_seconds = divmod(remaining_seconds, 100) #100 seconds per minute
        fsecs = remaining_seconds
        ftime = f"__**{int(fhours)}:{int(fminutes)}:{int(fsecs)}**__"
        message = f"OKAY! IT'S {ftime} IN FRENCH REVOLUTIONARY DECIMAL TIME! YOU'RE WELCOME!"
        await interaction.response.send_message(message)

async def setup(bot):
    await bot.add_cog(GeneralCommands(bot))
