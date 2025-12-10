"""
This file contains all the commands (functions)
for the Bober discord bot (˶ᵔ ᵕ ᵔ˶)
"""

from datetime import datetime

#Dictionary storing all commands
COMMANDS = {
    "!Bober" : bober,
    "!Bober FEAST" : bober_feast,
    "!Bober ART" : bober_art,
    "!Bober TIME" : bober_time,
    "!Bober COMMANDS" : bober_cmds
}

#Bober shouts his name
async def bober(message):
    await message.channel.send("I AM BOBER!")

#Bober eats food in gif form
async def bober_feast(message):
    gif_url = "https://media.tenor.com/z3sxfsXpHtUAAAAM/beaver-carrot.gif"
    await message.channel.send(gif_url)

#Bober shows off his ascii art
async def bober_art(message):
    with open("bober_art.txt", "r") as f:
        ascii_art = f.read()
    await message.channel.send(f"```\n{ascii_art}\n```")

#Bober gives the time.... in French Revolutionary Decimal Time
async def bober_time(message):
    time = datetime.now()
    seconds = time - time.replace(hour=0, minute=0, second=0, microsecond=0) 
    seconds = seconds.total_seconds()
    fsecs = seconds / 0.864 #86,400 seconds in day - 100,000 seconds in decimal day
    fhours, remaining_seconds = divmod(fsecs, 10000) #10,000 seconds per hour
    fminutes, remaining_seconds = divmod(remaining_seconds, 100) #100 seconds per minute
    fsecs = remaining_seconds
    ftime = f"__**{int(fhours)}:{int(fminutes)}:{int(fsecs)}**__"
    send_msg = f"OKAY! IT'S {ftime} IN FRENCH REVOLUTIONARY DECIMAL TIME! YOU'RE WELCOME!"
    await message.channel.send(send_msg)

#Bober lists all of his commands
async def bober_cmds(message):
    pass