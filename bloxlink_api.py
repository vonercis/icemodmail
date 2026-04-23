import aiohttp
import os
from dotenv import load_dotenv

load_dotenv()

BLOXLINK_API_KEY  = os.getenv("BLOXLINK_API_KEY")
BLOXLINK_BASE_URL = "https://api.blox.link/v4/public"
GUILD_ID          = os.getenv("DISCORD_GUILD_ID")


async def get_roblox_user(discord_id: int) -> dict | None:
    """
    Given a Discord user ID, returns their linked Roblox account via Bloxlink.
    Returns dict with 'roblox_id' and 'roblox_username', or None if not verified.
    """
    if not BLOXLINK_API_KEY:
        print("[Bloxlink] ERROR: BLOXLINK_API_KEY is not set")
        return None
    if not GUILD_ID:
        print("[Bloxlink] ERROR: DISCORD_GUILD_ID is not set")
        return None

    url = f"{BLOXLINK_BASE_URL}/guilds/{GUILD_ID}/discord-to-roblox/{discord_id}"
    headers = {"Authorization": BLOXLINK_API_KEY}
    print(f"[Bloxlink] Requesting: {url}")

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                print(f"[Bloxlink] Response status: {resp.status}")
                if resp.status == 200:
                    data = await resp.json()
                    roblox_id = data.get("robloxID")
                    if not roblox_id:
                        print(f"[Bloxlink] No robloxID in response: {data}")
                        return None
                    username = await get_roblox_username(roblox_id)
                    return {
                        "roblox_id":       int(roblox_id),
                        "roblox_username": username,
                    }
                else:
                    body = await resp.text()
                    print(f"[Bloxlink] Non-200 response: {body}")
                    return None
    except aiohttp.ClientConnectorDNSError as e:
        print(f"[Bloxlink] DNS error - cannot reach api.blox.link: {e}")
        return None
    except Exception as e:
        print(f"[Bloxlink] Unexpected error: {e}")
        return None


async def get_roblox_username(roblox_id: int) -> str:
    """
    Fetches the Roblox username for a given Roblox user ID from the Roblox API.
    """
    try:
        url = f"https://users.roblox.com/v1/users/{roblox_id}"
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return data.get("name", "Unknown")
                return "Unknown"
    except Exception as e:
        print(f"[Bloxlink] Error fetching Roblox username: {e}")
        return "Unknown"
