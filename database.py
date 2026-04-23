from motor.motor_asyncio import AsyncIOMotorClient
import os
from datetime import datetime, timedelta, timezone
from dotenv import load_dotenv

load_dotenv()

client = AsyncIOMotorClient(os.getenv("MONGO_URI"))
db = client[os.getenv("MONGO_DB", "icelandair")]
members = db["saga_members"]

TIER_THRESHOLDS = {
    "blue":   0,
    "silver": 40000,
    "gold":   80000,
}

POINTS_EXPIRY_YEARS = 4
TIER_WINDOW_MONTHS  = 12
FLIGHT_HISTORY_CAP  = 20

EARNING_MULTIPLIERS = {
    "economy_standard": 1.0,
    "economy_flex":     1.5,
    "saga_premium":     2.0,
    "partner_flight":   0.5,
}


def calculate_tier(tier_credits: int) -> str:
    if tier_credits >= TIER_THRESHOLDS["gold"]:
        return "gold"
    elif tier_credits >= TIER_THRESHOLDS["silver"]:
        return "silver"
    return "blue"


def generate_saga_number(roblox_id: int) -> str:
    base = str(roblox_id).zfill(10)[:10]
    return f"FI-{base}"


async def get_member(discord_id: int):
    return await members.find_one({"discord_id": discord_id})


async def get_member_by_roblox(roblox_id: int):
    return await members.find_one({"roblox_id": roblox_id})


async def create_member(discord_id: int, roblox_id: int, roblox_username: str) -> dict:
    now = datetime.now(timezone.utc)
    doc = {
        "discord_id":        discord_id,
        "roblox_id":         roblox_id,
        "roblox_username":   roblox_username,
        "saga_number":       generate_saga_number(roblox_id),
        "member_since":      now,
        "tier":              "blue",
        "saga_points":       0,
        "tier_credits":      0,
        "points_expiry":     now + timedelta(days=365 * POINTS_EXPIRY_YEARS),
        "tier_window_start": now,
        "flights_completed": 0,
        "last_flight":       None,
        "flight_history":    [],
    }
    await members.insert_one(doc)
    return doc


async def add_points(discord_id: int, points: int, tier_credits: int) -> dict:
    now = datetime.now(timezone.utc)
    member = await get_member(discord_id)
    if not member:
        return None

    new_points       = member["saga_points"] + points
    new_tc           = member["tier_credits"] + tier_credits
    new_tier         = calculate_tier(new_tc)
    new_expiry       = now + timedelta(days=365 * POINTS_EXPIRY_YEARS)

    await members.update_one(
        {"discord_id": discord_id},
        {"$set": {
            "saga_points":   new_points,
            "tier_credits":  new_tc,
            "tier":          new_tier,
            "points_expiry": new_expiry,
        }}
    )
    return {**member, "saga_points": new_points, "tier_credits": new_tc, "tier": new_tier}


async def set_points(discord_id: int, points: int = None, tier_credits: int = None) -> dict:
    member = await get_member(discord_id)
    if not member:
        return None

    update = {}
    if points is not None:
        update["saga_points"] = points
    if tier_credits is not None:
        update["tier_credits"] = tier_credits
        update["tier"] = calculate_tier(tier_credits)

    await members.update_one({"discord_id": discord_id}, {"$set": update})
    return {**member, **update}


async def log_flight(discord_id: int, origin: str, destination: str,
                     fare_class: str, aircraft: str, base_points: int) -> dict:
    member = await get_member(discord_id)
    if not member:
        return None

    multiplier     = EARNING_MULTIPLIERS.get(fare_class, 1.0)
    points_earned  = int(base_points * multiplier)
    now            = datetime.now(timezone.utc)

    flight_entry = {
        "origin":        origin.upper(),
        "destination":   destination.upper(),
        "date":          now,
        "fare_class":    fare_class,
        "aircraft":      aircraft,
        "points_earned": points_earned,
    }

    history = member.get("flight_history", [])
    history.append(flight_entry)
    if len(history) > FLIGHT_HISTORY_CAP:
        history = history[-FLIGHT_HISTORY_CAP:]

    new_points  = member["saga_points"] + points_earned
    new_tc      = member["tier_credits"] + points_earned
    new_tier    = calculate_tier(new_tc)
    new_expiry  = now + timedelta(days=365 * POINTS_EXPIRY_YEARS)

    await members.update_one(
        {"discord_id": discord_id},
        {"$set": {
            "saga_points":       new_points,
            "tier_credits":      new_tc,
            "tier":              new_tier,
            "points_expiry":     new_expiry,
            "flights_completed": member["flights_completed"] + 1,
            "last_flight":       flight_entry,
            "flight_history":    history,
        }}
    )
    return {
        **member,
        "saga_points":       new_points,
        "tier_credits":      new_tc,
        "tier":              new_tier,
        "flights_completed": member["flights_completed"] + 1,
        "last_flight":       flight_entry,
        "flight_history":    history,
        "points_earned":     points_earned,
    }
