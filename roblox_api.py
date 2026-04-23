from aiohttp import web
import os
import asyncio
from database import log_flight, get_member_by_roblox, get_member
from dotenv import load_dotenv

load_dotenv()

ROBLOX_API_SECRET = os.getenv("ROBLOX_API_SECRET")
PORT              = int(os.getenv("ROBLOX_API_PORT", 8080))

_bot_ref = None


def set_bot(bot):
    global _bot_ref
    _bot_ref = bot


def _auth(request: web.Request) -> bool:
    return request.headers.get("X-API-Secret") == ROBLOX_API_SECRET


async def handle_log_flight(request: web.Request) -> web.Response:
    if not _auth(request):
        return web.json_response({"error": "Unauthorized"}, status=401)

    try:
        data = await request.json()
    except Exception:
        return web.json_response({"error": "Invalid JSON"}, status=400)

    required = ["roblox_id", "origin", "destination", "aircraft", "fare_class", "base_points"]
    if not all(k in data for k in required):
        return web.json_response({"error": f"Missing fields: {required}"}, status=400)

    member_doc = await get_member_by_roblox(int(data["roblox_id"]))
    if not member_doc:
        return web.json_response({"error": "Member not found"}, status=404)

    discord_id = member_doc["discord_id"]
    updated    = await log_flight(
        discord_id,
        data["origin"],
        data["destination"],
        data["fare_class"],
        data["aircraft"],
        int(data["base_points"]),
    )

    if _bot_ref:
        bloxlink_cog = _bot_ref.cogs.get("BloxlinkCog")
        if bloxlink_cog:
            asyncio.create_task(bloxlink_cog.update_member_tier(discord_id, updated["tier"]))

    return web.json_response({
        "success":       True,
        "points_earned": updated["points_earned"],
        "saga_points":   updated["saga_points"],
        "tier_credits":  updated["tier_credits"],
        "tier":          updated["tier"],
        "flights":       updated["flights_completed"],
    })


async def handle_add_points(request: web.Request) -> web.Response:
    if not _auth(request):
        return web.json_response({"error": "Unauthorized"}, status=401)

    try:
        data = await request.json()
    except Exception:
        return web.json_response({"error": "Invalid JSON"}, status=400)

    required = ["roblox_id", "points", "tier_credits"]
    if not all(k in data for k in required):
        return web.json_response({"error": f"Missing fields: {required}"}, status=400)

    from database import add_points
    member_doc = await get_member_by_roblox(int(data["roblox_id"]))
    if not member_doc:
        return web.json_response({"error": "Member not found"}, status=404)

    discord_id = member_doc["discord_id"]
    updated    = await add_points(discord_id, int(data["points"]), int(data["tier_credits"]))

    if _bot_ref:
        bloxlink_cog = _bot_ref.cogs.get("BloxlinkCog")
        if bloxlink_cog:
            asyncio.create_task(bloxlink_cog.update_member_tier(discord_id, updated["tier"]))

    return web.json_response({
        "success":      True,
        "saga_points":  updated["saga_points"],
        "tier_credits": updated["tier_credits"],
        "tier":         updated["tier"],
    })


async def start_server(bot):
    set_bot(bot)
    app = web.Application()
    app.router.add_post("/flight/log",    handle_log_flight)
    app.router.add_post("/points/add",    handle_add_points)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()
    print(f"Roblox API server listening on port {PORT}")
