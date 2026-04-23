from motor.motor_asyncio import AsyncIOMotorClient
import os
from datetime import datetime, timezone
from dotenv import load_dotenv

load_dotenv()

client   = AsyncIOMotorClient(os.getenv("MONGO_URI"))
db       = client[os.getenv("MONGO_DB", "icelandair")]
aircraft = db["aircraft"]


async def create_aircraft(data: dict) -> dict:
    doc = {
        "registration":  data["registration"].upper(),
        "aircraft_type": data["aircraft_type"],
        "economy_seats": int(data.get("economy_seats", 0)),
        "premium_seats": int(data.get("premium_seats", 0)),
        "total_seats":   int(data.get("economy_seats", 0)) + int(data.get("premium_seats", 0)),
        "notes":         data.get("notes", ""),
        "created_at":    datetime.now(timezone.utc),
    }
    await aircraft.insert_one(doc)
    return doc


async def get_aircraft(registration: str) -> dict:
    return await aircraft.find_one({"registration": registration.upper()})


async def get_all_aircraft() -> list:
    cursor = aircraft.find().sort("registration", 1)
    return await cursor.to_list(length=100)


async def update_aircraft(registration: str, updates: dict) -> dict:
    await aircraft.update_one(
        {"registration": registration.upper()},
        {"$set": updates}
    )
    return await get_aircraft(registration)


async def delete_aircraft(registration: str) -> bool:
    result = await aircraft.delete_one({"registration": registration.upper()})
    return result.deleted_count > 0
