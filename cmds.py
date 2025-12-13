"""
This file contains all the commands (functions)
for the Bober discord bot (˶ᵔ ᵕ ᵔ˶)
"""
import discord
import requests
import asyncio
import yt_dlp
import os
from discord import app_commands
from discord.ext import commands
from datetime import datetime
from dotenv import load_dotenv

GUILD_ID = 1446565791452364935

class GeneralCommands(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def search_ytdlp_async(self, query,ydl_opts):
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, lambda: self._extract(query, ydl_opts))
    
    def _extract(self, query, ydl_opts):
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            return ydl.extract_info(query, download=False)

    @app_commands.guilds(GUILD_ID)
    @app_commands.command(name="bober-time", description="Bober gives you the time... kinda.")
    async def bober_time(self, interaction: discord.Interaction):
        """Bober gives the time.... in French Revolutionary Decimal Time"""
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

    @app_commands.guilds(GUILD_ID)
    @app_commands.command(name="bober-play", description="Bober plays music for you")
    async def bober_play(self, interaction: discord.Interaction, song_query: str):
        """Bober plays music for you"""
        await interaction.response.defer()

        if not interaction.user.voice:
            await interaction.followup.send("Must be connected to a voice channel")

        voice_channel = interaction.user.voice.channel

        if not voice_channel:
            await interaction.followup.send("Must be connected to a voice channel to play music")
            return
        
        voice_client = interaction.guild.voice_client

        if not voice_client:
            voice_client = await voice_channel.connect()
        elif voice_channel != voice_client.channel:
            await voice_client.move_to(voice_channel)

        ydl_options = {
            "format": "bestaudio[abr<=96]/bestaudio",
            "noplaylist": True,
            "youtube_include_dash_manifest": False,
            "youtube_include_hls_manifest": False
        }

        query = "ytsearch1: " + song_query
        results = await self.search_ytdlp_async(query, ydl_options)
        tracks = results.get("entries", [])

        if not tracks:
            await interaction.followup.send("No results found")
            return
        
        first_track = tracks[0]
        audio_url = first_track["url"]
        title = first_track.get("title", "Untitled")

        ffmpeg_options = {"before_options": "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5",
                          "options": "-vn -c:a libopus -b:a 96k"}
        
        source = discord.FFmpegOpusAudio(audio_url, **ffmpeg_options, executable="bin\\ffmpeg\\ffmpeg.exe")

        try:
            voice_client.play(source)
            await interaction.followup.send(f"Now playing: {first_track}")
        except Exception as e:
            await interaction.followup.send("Could not play track, please try again.")
            print(f"Track could not be played with error: {e}")



async def setup(bot):
    await bot.add_cog(GeneralCommands(bot))