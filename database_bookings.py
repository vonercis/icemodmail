from motor.motor_asyncio import AsyncIOMotorClient
import os
from datetime import datetime, timezone
from dotenv import load_dotenv
import random
import string

load_dotenv()

client   = AsyncIOMotorClient(os.getenv("MONGO_URI"))
db       = client[os.getenv("MONGO_DB", "icelandair")]
bookings = db["bookings"]


def generate_booking_ref() -> str:
    return "FI-" + "".join(random.choices(string.ascii_uppercase + string.digits, k=6))


async def create_booking(discord_id: int, roblox_username: str, flight_number: str, cabin: str) -> dict:
    ref = generate_booking_ref()
    while await bookings.find_one({"booking_ref": ref}):
        ref = generate_booking_ref()

    now = datetime.now(timezone.utc)
    doc = {
        "booking_ref":    ref,
        "discord_id":     discord_id,
        "roblox_username": roblox_username,
        "flight_number":  flight_number.upper(),
        "cabin":          cabin,
        "status":         "Confirmed",
        "booked_at":      now,
    }
    await bookings.insert_one(doc)
    return doc


async def get_booking(discord_id: int, flight_number: str) -> dict:
    return await bookings.find_one({
        "discord_id":    discord_id,
        "flight_number": flight_number.upper(),
        "status":        {"$ne": "Cancelled"},
    })


async def get_bookings_for_flight(flight_number: str) -> list:
    cursor = bookings.find({
        "flight_number": flight_number.upper(),
        "status":        {"$ne": "Cancelled"},
    })
    return await cursor.to_list(length=500)


async def get_bookings_for_user(discord_id: int) -> list:
    cursor = bookings.find({
        "discord_id": discord_id,
        "status":     {"$ne": "Cancelled"},
    }).sort("booked_at", -1)
    return await cursor.to_list(length=50)


async def cancel_booking(discord_id: int, flight_number: str) -> bool:
    result = await bookings.update_one(
        {"discord_id": discord_id, "flight_number": flight_number.upper(), "status": {"$ne": "Cancelled"}},
        {"$set": {"status": "Cancelled", "cancelled_at": datetime.now(timezone.utc)}}
    )
    return result.modified_count > 0


async def cancel_booking_by_ref(booking_ref: str) -> bool:
    result = await bookings.update_one(
        {"booking_ref": booking_ref.upper()},
        {"$set": {"status": "Cancelled", "cancelled_at": datetime.now(timezone.utc)}}
    )
    return result.modified_count > 0


async def count_cabin_bookings(flight_number: str, cabin: str) -> int:
    return await bookings.count_documents({
        "flight_number": flight_number.upper(),
        "cabin":         cabin,
        "status":        {"$ne": "Cancelled"},
    })


async def get_saga_class_bookings_this_month(discord_id: int) -> int:
    now   = datetime.now(timezone.utc)
    start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    return await bookings.count_documents({
        "discord_id": discord_id,
        "cabin":      "Saga Class",
        "status":     {"$ne": "Cancelled"},
        "booked_at":  {"$gte": start},
    })


async def get_all_bookings_for_user(discord_id: int) -> list:
    """Returns all bookings including cancelled ones, most recent first."""
    cursor = bookings.find({"discord_id": discord_id}).sort("booked_at", -1)
    return await cursor.to_list(length=100)


async def get_all_bookings_for_flight(flight_number: str) -> list:
    """Returns all bookings for a flight including cancelled, active first."""
    cursor = bookings.find({"flight_number": flight_number.upper()}).sort([
        ("status", 1),  # Confirmed before Cancelled
        ("booked_at", -1)
    ])
    return await cursor.to_list(length=500)
