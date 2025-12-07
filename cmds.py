"""
This file contains all the commands (functions)
for the Bober discord bot (˶ᵔ ᵕ ᵔ˶)
"""

async def bober(message):
    await message.channel.send("I AM BOBER!")

async def bober_feast(message):
    gif_url = "https://media.tenor.com/z3sxfsXpHtUAAAAM/beaver-carrot.gif"
    await message.channel.send(gif_url)

async def bober_art(message):
    with open("bober_art.txt", "r") as f:
        ascii_art = f.read()
    await message.channel.send(f"```\n{ascii_art}\n```")