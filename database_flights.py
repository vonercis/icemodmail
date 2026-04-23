from motor.motor_asyncio import AsyncIOMotorClient
import os
from datetime import datetime, timezone
from dotenv import load_dotenv

load_dotenv()

client = AsyncIOMotorClient(os.getenv("MONGO_URI"))
db = client[os.getenv("MONGO_DB", "icelandair")]
flights = db["flights"]

STATUSES = ["Scheduled", "Boarding", "Departed", "Arrived", "Delayed", "Cancelled"]

STATUS_COLORS = {
    "Scheduled":  0x003B6F,
    "Boarding":   0xFFA500,
    "Departed":   0x1D9E75,
    "Arrived":    0x639922,
    "Delayed":    0xE24B4A,
    "Cancelled":  0x444441,
}

STATUS_EMOJI = {
    "Scheduled":  "🕐",
    "Boarding":   "🚪",
    "Departed":   "✈️",
    "Arrived":    "🛬",
    "Delayed":    "⚠️",
    "Cancelled":  "❌",
}


async def create_flight(data: dict) -> dict:
    now = datetime.now(timezone.utc)
    doc = {
        "flight_number":   data["flight_number"].upper(),
        "origin":          data["origin"].upper(),
        "destination":     data["destination"].upper(),
        "date":            data["date"],
        "status":          "Scheduled",
        "aircraft_type":   data["aircraft_type"],
        "registration":    data.get("registration", "N/A"),
        "std":             data.get("std"),
        "etd":             data.get("etd"),
        "sta":             data.get("sta"),
        "eta":             data.get("eta"),
        "atd":             None,
        "ata":             None,
        "block_time":      data.get("block_time", "N/A"),
        "economy_count":   int(data.get("economy_count", 0)),
        "premium_count":   int(data.get("premium_count", 0)),
        "checkin_open":    False,
        "board_message_id": None,
        "created_at":      now,
        "closed_at":       None,
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
    from datetime import timedelta
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
    """Remove flights closed more than 24 hours ago."""
    from datetime import timedelta
    cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
    result = await flights.delete_many({"closed_at": {"$lt": cutoff}})
    return result.deleted_count
