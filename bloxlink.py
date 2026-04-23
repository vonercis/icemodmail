import aiohttp
import os
from dotenv import load_dotenv

load_dotenv()

BLOXLINK_API_KEY  = os.getenv("BLOXLINK_API_KEY")
BLOXLINK_BASE_URL = "https://api.bloxlink.me/api/v5/public"
GUILD_ID          = os.getenv("DISCORD_GUILD_ID")


async def get_roblox_user(discord_id: int) -> dict | None:
    """
    Given a Discord user ID, returns their linked Roblox account via Bloxlink.
    Returns dict with 'roblox_id' and 'roblox_username', or None if not verified.
    """
    url = f"{BLOXLINK_BASE_URL}/guilds/{GUILD_ID}/discord-to-roblox/{discord_id}"
    headers = {"api-key": BLOXLINK_API_KEY}

    async with aiohttp.ClientSession() as session:
        async with session.get(url, headers=headers) as resp:
            if resp.status == 200:
                data = await resp.json()
                roblox_id = data.get("robloxID")
                if not roblox_id:
                    return None
                username = await get_roblox_username(roblox_id)
                return {
                    "roblox_id":       int(roblox_id),
                    "roblox_username": username,
                }
            return None


async def get_roblox_username(roblox_id: int) -> str:
    """
    Fetches the Roblox username for a given Roblox user ID from the Roblox API.
    """
    url = f"https://users.roblox.com/v1/users/{roblox_id}"
    async with aiohttp.ClientSession() as session:
        async with session.get(url) as resp:
            if resp.status == 200:
                data = await resp.json()
                return data.get("name", "Unknown")
            return "Unknown"
