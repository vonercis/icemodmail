import discord
from discord.ext import commands
import os
from dotenv import load_dotenv

load_dotenv()

intents = discord.Intents.default()
intents.members = True
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents)

COGS = [
    "cogs.saga",
    "cogs.bloxlink",
    "cogs.flights",
]

@bot.event
async def on_ready():
    print(f"Logged in as {bot.user} ({bot.user.id})")
    for cog in COGS:
        try:
            await bot.load_extension(cog)
            print(f"Loaded cog: {cog}")
        except Exception as e:
            print(f"Failed to load cog {cog}: {e}")
    try:
        guild = discord.Object(id=int(os.getenv("DISCORD_GUILD_ID")))
        bot.tree.copy_global_to(guild=guild)
        synced = await bot.tree.sync(guild=guild)
        print(f"Synced {len(synced)} slash commands to guild")
    except Exception as e:
        print(f"Failed to sync commands: {e}")

if __name__ == "__main__":
    bot.run(os.getenv("DISCORD_TOKEN"))
