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


async def get_member_by_username(roblox_username: str):
    return await members.find_one({"roblox_username": {"$regex": roblox_username, "$options": "i"}})


async def create_member(discord_id: int, roblox_id: int, roblox_username: str) -> dict:
    now = datetime.now(timezone.utc)
    doc = {
        "discord_id":            discord_id,
        "roblox_id":             roblox_id,
        "roblox_username":       roblox_username,
        "saga_number":           generate_saga_number(roblox_id),
        "member_since":          now,
        "tier":                  "blue",
        "saga_points":           0,
        "tier_credits":          0,
        "points_expiry":         now + timedelta(days=365 * POINTS_EXPIRY_YEARS),
        "tier_window_start":     now,
        "flights_completed":     0,
        "last_flight":           None,
        "flight_history":        [],
        "complimentary_upgrades":        0,
        "upgrade_last_reset":            now,
        "saga_class_flights_remaining":  0,
        "saga_class_last_reset":         now,
        "internal_notes":                [],
    }
    await members.insert_one(doc)
    return doc


async def add_points(discord_id: int, points: int, tier_credits: int) -> dict:
    now = datetime.now(timezone.utc)
    member = await get_member(discord_id)
    if not member:
        return None

    new_points  = member["saga_points"] + points
    new_tc      = member["tier_credits"] + tier_credits
    new_tier    = calculate_tier(new_tc)
    new_expiry  = now + timedelta(days=365 * POINTS_EXPIRY_YEARS)
    old_tier    = member["tier"]

    await members.update_one(
        {"discord_id": discord_id},
        {"$set": {
            "saga_points":   new_points,
            "tier_credits":  new_tc,
            "tier":          new_tier,
            "points_expiry": new_expiry,
        }}
    )
    return {**member, "saga_points": new_points, "tier_credits": new_tc, "tier": new_tier, "old_tier": old_tier}


async def set_points(discord_id: int, points: int = None, tier_credits: int = None) -> dict:
    member = await get_member(discord_id)
    if not member:
        return None

    old_tier = member["tier"]
    update = {}
    if points is not None:
        update["saga_points"] = points
    if tier_credits is not None:
        update["tier_credits"] = tier_credits
        update["tier"] = calculate_tier(tier_credits)

    await members.update_one({"discord_id": discord_id}, {"$set": update})
    return {**member, **update, "old_tier": old_tier}


async def log_flight(discord_id: int, origin: str, destination: str,
                     fare_class: str, aircraft: str, base_points: int) -> dict:
    member = await get_member(discord_id)
    if not member:
        return None

    multiplier    = EARNING_MULTIPLIERS.get(fare_class, 1.0)
    points_earned = int(base_points * multiplier)
    now           = datetime.now(timezone.utc)
    old_tier      = member["tier"]

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

    new_points = member["saga_points"] + points_earned
    new_tc     = member["tier_credits"] + points_earned
    new_tier   = calculate_tier(new_tc)
    new_expiry = now + timedelta(days=365 * POINTS_EXPIRY_YEARS)

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
        "old_tier":          old_tier,
        "flights_completed": member["flights_completed"] + 1,
        "last_flight":       flight_entry,
        "flight_history":    history,
        "points_earned":     points_earned,
    }


async def set_upgrades(discord_id: int, count: int) -> dict:
    member = await get_member(discord_id)
    if not member:
        return None
    await members.update_one(
        {"discord_id": discord_id},
        {"$set": {"complimentary_upgrades": count}}
    )
    return {**member, "complimentary_upgrades": count}


async def use_upgrade(discord_id: int) -> dict | None:
    """Uses one complimentary upgrade. Returns None if none available."""
    member = await get_member(discord_id)
    if not member:
        return None
    current = member.get("complimentary_upgrades", 0)
    if current <= 0:
        return None
    await members.update_one(
        {"discord_id": discord_id},
        {"$set": {"complimentary_upgrades": current - 1}}
    )
    return {**member, "complimentary_upgrades": current - 1}


async def add_note(discord_id: int, note: str, staff_name: str) -> dict:
    member = await get_member(discord_id)
    if not member:
        return None
    now = datetime.now(timezone.utc)
    note_entry = {
        "text":       note,
        "staff":      staff_name,
        "created_at": now,
    }
    await members.update_one(
        {"discord_id": discord_id},
        {"$push": {"internal_notes": note_entry}}
    )
    notes = member.get("internal_notes", [])
    notes.append(note_entry)
    return {**member, "internal_notes": notes}


async def delete_note(discord_id: int, note_index: int) -> dict:
    member = await get_member(discord_id)
    if not member:
        return None
    notes = member.get("internal_notes", [])
    if note_index < 0 or note_index >= len(notes):
        return None
    notes.pop(note_index)
    await members.update_one(
        {"discord_id": discord_id},
        {"$set": {"internal_notes": notes}}
    )
    return {**member, "internal_notes": notes}


SAGA_CLASS_ALLOWANCE = {
    "blue":   0,
    "silver": 2,
    "gold":   5,
}


async def get_saga_class_remaining(discord_id: int) -> int:
    member = await get_member(discord_id)
    if not member:
        return 0
    return member.get("saga_class_flights_remaining", 0)


async def use_saga_class_flight(discord_id: int) -> bool:
    """Deducts one Saga Class flight. Returns False if none remaining."""
    member = await get_member(discord_id)
    if not member:
        return False
    remaining = member.get("saga_class_flights_remaining", 0)
    tier      = member.get("tier", "blue")
    if tier == "gold":
        return True  # Gold has 5/month but we still track usage
    if remaining <= 0:
        return False
    await members.update_one(
        {"discord_id": discord_id},
        {"$inc": {"saga_class_flights_remaining": -1}}
    )
    return True


async def restore_saga_class_flight(discord_id: int):
    """Restores one Saga Class flight (e.g. after flight arrival deducts it)."""
    member = await get_member(discord_id)
    if not member:
        return
    tier      = member.get("tier", "blue")
    allowance = SAGA_CLASS_ALLOWANCE.get(tier, 0)
    current   = member.get("saga_class_flights_remaining", 0)
    # Don't exceed monthly allowance
    if current < allowance:
        await members.update_one(
            {"discord_id": discord_id},
            {"$inc": {"saga_class_flights_remaining": 1}}
        )


async def set_saga_class_remaining(discord_id: int, count: int) -> dict:
    member = await get_member(discord_id)
    if not member:
        return None
    await members.update_one(
        {"discord_id": discord_id},
        {"$set": {"saga_class_flights_remaining": count}}
    )
    return {**member, "saga_class_flights_remaining": count}


async def reset_saga_class_monthly():
    """Resets saga class flight allowances for all members based on their tier."""
    now = datetime.now(timezone.utc)
    for tier, allowance in SAGA_CLASS_ALLOWANCE.items():
        await members.update_many(
            {"tier": tier},
            {"$set": {
                "saga_class_flights_remaining": allowance,
                "saga_class_last_reset":        now,
            }}
        )
