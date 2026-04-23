from motor.motor_asyncio import AsyncIOMotorClient
import os
from datetime import datetime, timezone, timedelta
from dotenv import load_dotenv

load_dotenv()

client  = AsyncIOMotorClient(os.getenv("MONGO_URI"))
db      = client[os.getenv("MONGO_DB", "icelandair")]
flights = db["flights"]
flight_subscriptions = db["flight_subscriptions"]

STATUSES = ["Scheduled", "Boarding", "Departed", "Arrived", "Delayed", "Cancelled"]

STATUS_COLORS = {
    "Scheduled": 0x003B6F,  # dark blue
    "Boarding":  0x85B7EB,  # light blue
    "Departed":  0x1D9E75,  # green
    "Arrived":   0xFFFFFF,  # white
    "Delayed":   0xE24B4A,  # red
    "Cancelled": 0x791F1F,  # dark red
}

STATUS_EMOJI = {
    "Scheduled": "🕐",
    "Boarding":  "🚪",
    "Departed":  "✈️",
    "Arrived":   "🛬",
    "Delayed":   "⚠️",
    "Cancelled": "❌",
}


async def create_flight(data: dict) -> dict:
    now = datetime.now(timezone.utc)
    doc = {
        "flight_number":    data["flight_number"].upper(),
        "origin":           data["origin"].upper(),
        "destination":      data["destination"].upper(),
        "date":             data["date"],
        "status":           "Scheduled",
        "aircraft_type":    data["aircraft_type"],
        "registration":     data.get("registration", "N/A"),
        "std":              data.get("std"),
        "etd":              data.get("etd"),
        "sta":              data.get("sta"),
        "eta":              data.get("eta"),
        "atd":              None,
        "ata":              None,
        "block_time":       data.get("block_time", "N/A"),
        "economy_count":    int(data.get("economy_count", 0)),
        "premium_count":    int(data.get("premium_count", 0)),
        "checkin_open":     False,
        "board_message_id": None,
        "created_at":       now,
        "closed_at":        None,
        "reason":           None,
    }
    result = await flights.insert_one(doc)
    doc["_id"] = result.inserted_id
    return doc


async def get_flight(flight_number: str) -> dict:
    return await flights.find_one({"flight_number": flight_number.upper()})


async def get_active_flights() -> list:
    now = datetime.now(timezone.utc)
    cursor = flights.find({
        "$or": [
            {"status": {"$nin": ["Arrived", "Cancelled"]}},
            {"closed_at": {"$gt": now}}
        ]
    }).sort("std", 1)
    return await cursor.to_list(length=50)


async def update_flight(flight_number: str, updates: dict) -> dict:
    now = datetime.now(timezone.utc)
    if "status" in updates and updates["status"] in ("Arrived", "Cancelled"):
        updates["closed_at"] = now
    await flights.update_one(
        {"flight_number": flight_number.upper()},
        {"$set": updates}
    )
    return await get_flight(flight_number)


async def set_board_message(flight_number: str, message_id: int):
    await flights.update_one(
        {"flight_number": flight_number.upper()},
        {"$set": {"board_message_id": message_id}}
    )


async def purge_old_flights():
    cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
    result = await flights.delete_many({"closed_at": {"$lt": cutoff}})
    return result.deleted_count


# ── Subscriptions ─────────────────────────────────────────────────────────────

async def subscribe(discord_id: int, flight_number: str) -> bool:
    """Subscribe a user to flight updates. Returns True if newly subscribed, False if already subscribed."""
    existing = await flight_subscriptions.find_one({
        "discord_id":    discord_id,
        "flight_number": flight_number.upper(),
    })
    if existing:
        return False
    await flight_subscriptions.insert_one({
        "discord_id":    discord_id,
        "flight_number": flight_number.upper(),
        "subscribed_at": datetime.now(timezone.utc),
    })
    return True


async def unsubscribe(discord_id: int, flight_number: str):
    await flight_subscriptions.delete_one({
        "discord_id":    discord_id,
        "flight_number": flight_number.upper(),
    })


async def get_subscribers(flight_number: str) -> list[int]:
    cursor = flight_subscriptions.find({"flight_number": flight_number.upper()})
    docs   = await cursor.to_list(length=500)
    return [d["discord_id"] for d in docs]


async def clear_subscriptions(flight_number: str):
    await flight_subscriptions.delete_many({"flight_number": flight_number.upper()})
