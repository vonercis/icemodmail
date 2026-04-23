import discord
from discord.ext import commands, tasks
import os
from database import members, calculate_tier
from bloxlink import get_roblox_user
from dotenv import load_dotenv
from datetime import datetime, timezone, timedelta

load_dotenv()

GUILD_ID = int(os.getenv("DISCORD_GUILD_ID"))

TIER_ROLE_IDS = {
    "blue":   int(os.getenv("ROLE_ID_BLUE")),
    "silver": int(os.getenv("ROLE_ID_SILVER")),
    "gold":   int(os.getenv("ROLE_ID_GOLD")),
}

ALL_TIER_ROLES = set(TIER_ROLE_IDS.values())


class BloxlinkCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.tier_check_loop.start()

    def cog_unload(self):
        self.tier_check_loop.cancel()

    async def assign_tier_role(self, guild: discord.Guild, member: discord.Member, tier: str):
        """
        Removes all existing tier roles from the member and assigns the correct one.
        """
        correct_role_id = TIER_ROLE_IDS.get(tier)
        if not correct_role_id:
            return

        roles_to_remove = [
            guild.get_role(rid)
            for rid in ALL_TIER_ROLES
            if rid != correct_role_id and guild.get_role(rid) in member.roles
        ]
        if roles_to_remove:
            await member.remove_roles(*roles_to_remove, reason="Saga Club tier update")

        correct_role = guild.get_role(correct_role_id)
        if correct_role and correct_role not in member.roles:
            await member.add_roles(correct_role, reason="Saga Club tier update")

    @tasks.loop(hours=6)
    async def tier_check_loop(self):
        """
        Every 6 hours, checks all members for tier changes and re-evaluates
        tier credits within the rolling 12-month window.
        """
        guild = self.bot.get_guild(GUILD_ID)
        if not guild:
            return

        now = datetime.now(timezone.utc)
        window_start = now - timedelta(days=365)

        async for doc in members.find():
            discord_id = doc.get("discord_id")
            if not discord_id:
                continue

            guild_member = guild.get_member(discord_id)
            if not guild_member:
                continue

            flight_history = doc.get("flight_history", [])
            tc_in_window = sum(
                f.get("points_earned", 0)
                for f in flight_history
                if f.get("date") and f["date"] >= window_start
            )

            current_tier = doc.get("tier", "blue")
            # Use the stored tier_credits (which includes manually added credits)
            # rather than recalculating from flight history — this prevents
            # overwriting credits added via /saga-add
            stored_tc    = doc.get("tier_credits", 0)
            correct_tier = calculate_tier(stored_tc)

            if correct_tier != current_tier:
                await members.update_one(
                    {"discord_id": discord_id},
                    {"$set": {"tier": correct_tier}}
                )

            await self.assign_tier_role(guild, guild_member, correct_tier)

    @tier_check_loop.before_loop
    async def before_tier_check(self):
        await self.bot.wait_until_ready()
        await self.migrate_saga_class_flights()

    async def migrate_saga_class_flights(self):
        """
        One-time migration: set saga_class_flights_remaining for any member
        where it is 0 or missing but their tier entitles them to flights.
        """
        from database import SAGA_CLASS_ALLOWANCE
        count = 0
        async for doc in members.find():
            tier      = doc.get("tier", "blue")
            allowance = SAGA_CLASS_ALLOWANCE.get(tier, 0)
            current   = doc.get("saga_class_flights_remaining", None)
            # Only update if field is missing or is 0 but allowance is > 0
            if allowance > 0 and (current is None or current == 0):
                await members.update_one(
                    {"discord_id": doc["discord_id"]},
                    {"$set": {"saga_class_flights_remaining": allowance}}
                )
                count += 1
        if count:
            print(f"[Bloxlink] Migrated saga_class_flights_remaining for {count} member(s)")

    async def update_member_tier(self, discord_id: int, new_tier: str):
        """
        Called externally (e.g. from saga cog) when points are updated,
        so role changes happen immediately without waiting for the loop.
        """
        guild = self.bot.get_guild(GUILD_ID)
        if not guild:
            return
        guild_member = guild.get_member(discord_id)
        if not guild_member:
            return
        await self.assign_tier_role(guild, guild_member, new_tier)

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        """Auto-assign Saga Blue role when someone joins the server."""
        if member.guild.id != GUILD_ID:
            return
        blue_role = member.guild.get_role(TIER_ROLE_IDS["blue"])
        if blue_role and blue_role not in member.roles:
            await member.add_roles(blue_role, reason="Saga Club — auto Saga Blue on join")


async def setup(bot: commands.Bot):
    await bot.add_cog(BloxlinkCog(bot))
