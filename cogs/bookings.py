import discord
from discord.ext import commands, tasks
from discord import app_commands
import os
from datetime import datetime, timezone
from database_bookings import (
    create_booking, get_booking, get_bookings_for_flight,
    get_bookings_for_user, cancel_booking, cancel_booking_by_ref,
    count_cabin_bookings,
)
from database import (
    get_member, SAGA_CLASS_ALLOWANCE,
    use_saga_class_flight, set_saga_class_remaining, reset_saga_class_monthly,
)
from database_flights import get_flight, STATUS_COLORS
from bloxlink import get_roblox_user
from dotenv import load_dotenv

load_dotenv()

GUILD_ID      = int(os.getenv("DISCORD_GUILD_ID"))
STAFF_ROLE_ID = int(os.getenv("STAFF_ROLE_ID", 0))


def is_staff(interaction: discord.Interaction) -> bool:
    if not STAFF_ROLE_ID:
        return interaction.user.guild_permissions.manage_roles
    return any(r.id == STAFF_ROLE_ID for r in interaction.user.roles)


def build_confirmation_embed(booking: dict, flight: dict) -> discord.Embed:
    cabin_emoji = "🛋️" if booking["cabin"] == "Saga Class" else "💺"
    embed = discord.Embed(
        title="✅ Booking Confirmed",
        color=STATUS_COLORS.get(flight.get("status", "Scheduled"), 0x003B6F),
        description=(
            f"Your booking for flight **{booking['flight_number']}** has been confirmed.\n\n"
            f"**Booking Reference:** `{booking['booking_ref']}`\n"
            f"**Passenger:** {booking['roblox_username']}\n"
            f"{cabin_emoji} **Cabin:** {booking['cabin']}\n"
            f"**Route:** {flight['origin']} → {flight['destination']}\n"
            f"**Date:** {flight['date']}\n"
            f"**STD:** {flight.get('std', '—')}\n\n"
            f"To cancel your booking, use `/booking-cancel`."
        ),
    )
    embed.set_thumbnail(url="https://www.icelandair.com/favicon.ico")
    embed.set_footer(text="Icelandair • Thank you for booking with us", icon_url="https://www.icelandair.com/favicon.ico")
    embed.timestamp = datetime.now(timezone.utc)
    return embed


def build_booking_list_embed(user_bookings: list, roblox_username: str) -> discord.Embed:
    embed = discord.Embed(
        title=f"✈️ Bookings — {roblox_username}",
        color=0x003B6F,
        description=f"{len(user_bookings)} active booking(s)",
    )
    if not user_bookings:
        embed.add_field(name="No bookings", value="You have no active bookings.", inline=False)
    else:
        for b in user_bookings:
            embed.add_field(
                name=f"`{b['booking_ref']}` — {b['flight_number']}",
                value=f"**Cabin:** {b['cabin']} · **Booked:** {b['booked_at'].strftime('%-d %b %Y')}",
                inline=False,
            )
    embed.set_footer(text="Icelandair", icon_url="https://www.icelandair.com/favicon.ico")
    embed.timestamp = datetime.now(timezone.utc)
    return embed


def build_manifest_embed(flight: dict, flight_bookings: list) -> discord.Embed:
    eco_bookings  = [b for b in flight_bookings if b["cabin"] == "Economy"]
    saga_bookings = [b for b in flight_bookings if b["cabin"] == "Saga Class"]

    embed = discord.Embed(
        title=f"📋 Manifest — {flight['flight_number']} {flight['origin']} → {flight['destination']}",
        color=STATUS_COLORS.get(flight.get("status", "Scheduled"), 0x003B6F),
        description=f"**{len(flight_bookings)}** total bookings",
    )

    eco_list  = "\n".join(f"• {b['roblox_username']} `{b['booking_ref']}`" for b in eco_bookings)  or "No economy bookings"
    saga_list = "\n".join(f"• {b['roblox_username']} `{b['booking_ref']}`" for b in saga_bookings) or "No Saga Class bookings"

    embed.add_field(name=f"💺 Economy ({len(eco_bookings)})",         value=eco_list[:1024],  inline=False)
    embed.add_field(name=f"🛋️ Saga Class ({len(saga_bookings)})",     value=saga_list[:1024], inline=False)
    embed.set_footer(text="Icelandair Operations — Internal Use Only", icon_url="https://www.icelandair.com/favicon.ico")
    embed.timestamp = datetime.now(timezone.utc)
    return embed


class CabinSelectView(discord.ui.View):
    def __init__(self, flight: dict, member_doc: dict, roblox_username: str, cog):
        super().__init__(timeout=120)
        self.flight          = flight
        self.member_doc      = member_doc
        self.roblox_username = roblox_username
        self.cog             = cog

        tier             = member_doc.get("tier", "blue") if member_doc else "blue"
        saga_remaining   = member_doc.get("saga_class_flights_remaining", 0) if member_doc else 0
        saga_eligible    = tier in ("silver", "gold") and saga_remaining > 0

        self.eco_button = discord.ui.Button(
            label="💺 Economy",
            style=discord.ButtonStyle.primary,
        )
        self.eco_button.callback = self.book_economy
        self.add_item(self.eco_button)

        saga_label = f"🛋️ Saga Class ({saga_remaining} remaining)" if saga_eligible else "🛋️ Saga Class (not eligible)"
        self.saga_button = discord.ui.Button(
            label=saga_label,
            style=discord.ButtonStyle.success if saga_eligible else discord.ButtonStyle.secondary,
            disabled=not saga_eligible,
        )
        self.saga_button.callback = self.book_saga
        self.add_item(self.saga_button)

    async def book_economy(self, interaction: discord.Interaction):
        await self.cog.process_booking(interaction, self.flight, self.roblox_username, "Economy")

    async def book_saga(self, interaction: discord.Interaction):
        await self.cog.process_booking(interaction, self.flight, self.roblox_username, "Saga Class")


class BookingsCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.monthly_reset_loop.start()

    def cog_unload(self):
        self.monthly_reset_loop.cancel()

    @tasks.loop(hours=24)
    async def monthly_reset_loop(self):
        now = datetime.now(timezone.utc)
        if now.day != 1:
            return
        await reset_saga_class_monthly()
        print("[Bookings] Monthly Saga Class flight allowances reset")

    @monthly_reset_loop.before_loop
    async def before_reset(self):
        await self.bot.wait_until_ready()

    async def process_booking(
        self, interaction: discord.Interaction,
        flight: dict, roblox_username: str, cabin: str
    ):
        fn = flight["flight_number"]

        # Check duplicate
        existing = await get_booking(interaction.user.id, fn)
        if existing:
            await interaction.response.send_message(
                f"You already have a booking on flight **{fn}** (`{existing['booking_ref']}`). Use `/booking-cancel` to cancel it first.",
                ephemeral=True
            )
            return

        # Check seat availability
        eco_total  = flight.get("economy_count", 0)
        saga_total = flight.get("premium_count", 0)

        if cabin == "Economy":
            booked = await count_cabin_bookings(fn, "Economy")
            if booked >= eco_total:
                await interaction.response.send_message("Sorry, Economy class is fully booked on this flight.", ephemeral=True)
                return

        elif cabin == "Saga Class":
            booked = await count_cabin_bookings(fn, "Saga Class")
            if booked >= saga_total:
                await interaction.response.send_message("Sorry, Saga Class is fully booked on this flight.", ephemeral=True)
                return
            # Deduct saga class flight
            success = await use_saga_class_flight(interaction.user.id)
            if not success:
                await interaction.response.send_message("You have no Saga Class flights remaining this month.", ephemeral=True)
                return

        booking = await create_booking(interaction.user.id, roblox_username, fn, cabin)
        embed   = build_confirmation_embed(booking, flight)

        # Send DM confirmation
        try:
            user = await self.bot.fetch_user(interaction.user.id)
            await user.send(embed=embed)
        except Exception:
            pass

        await interaction.response.send_message(
            content="✅ Booking confirmed! A confirmation has been sent to your DMs.",
            embed=embed,
            ephemeral=True
        )

    # ── /booking-mybookings ───────────────────────────────────────────────────
    @app_commands.command(name="booking-mybookings", description="View your active flight bookings")
    @app_commands.guilds(discord.Object(id=GUILD_ID))
    async def booking_mybookings(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        user_bookings = await get_bookings_for_user(interaction.user.id)
        roblox        = await get_roblox_user(interaction.user.id)
        username      = roblox["roblox_username"] if roblox else interaction.user.display_name
        embed         = build_booking_list_embed(user_bookings, username)
        await interaction.followup.send(embed=embed, ephemeral=True)

    # ── /booking-cancel ───────────────────────────────────────────────────────
    @app_commands.command(name="booking-cancel", description="Cancel your booking on a flight")
    @app_commands.guilds(discord.Object(id=GUILD_ID))
    async def booking_cancel(self, interaction: discord.Interaction, flight_number: str):
        await interaction.response.defer(ephemeral=True)
        booking = await get_booking(interaction.user.id, flight_number)
        if not booking:
            await interaction.followup.send(f"You don't have an active booking on flight **{flight_number.upper()}**.", ephemeral=True)
            return

        # Restore saga class flight if applicable
        if booking["cabin"] == "Saga Class":
            member = await get_member(interaction.user.id)
            if member:
                allowance  = SAGA_CLASS_ALLOWANCE.get(member.get("tier", "blue"), 0)
                current    = member.get("saga_class_flights_remaining", 0)
                if current < allowance:
                    await set_saga_class_remaining(interaction.user.id, current + 1)

        await cancel_booking(interaction.user.id, flight_number)
        await interaction.followup.send(
            f"Your booking `{booking['booking_ref']}` on flight **{flight_number.upper()}** has been cancelled.",
            ephemeral=True
        )

    # ── /booking-cancel-ref ───────────────────────────────────────────────────
    @app_commands.command(name="booking-cancel-ref", description="[Staff] Cancel a booking by reference number")
    @app_commands.guilds(discord.Object(id=GUILD_ID))
    async def booking_cancel_ref(self, interaction: discord.Interaction, booking_ref: str):
        await interaction.response.defer(ephemeral=True)
        if not is_staff(interaction):
            await interaction.followup.send("You don't have permission to use this command.", ephemeral=True)
            return
        success = await cancel_booking_by_ref(booking_ref)
        if not success:
            await interaction.followup.send(f"Booking `{booking_ref.upper()}` not found or already cancelled.", ephemeral=True)
            return
        await interaction.followup.send(f"Booking `{booking_ref.upper()}` has been cancelled.", ephemeral=True)

    # ── /booking-manifest ─────────────────────────────────────────────────────
    @app_commands.command(name="booking-manifest", description="[Staff] View all bookings on a flight")
    @app_commands.guilds(discord.Object(id=GUILD_ID))
    async def booking_manifest(self, interaction: discord.Interaction, flight_number: str):
        await interaction.response.defer(ephemeral=True)
        if not is_staff(interaction):
            await interaction.followup.send("You don't have permission to use this command.", ephemeral=True)
            return
        flight          = await get_flight(flight_number)
        flight_bookings = await get_bookings_for_flight(flight_number)
        if not flight:
            await interaction.followup.send(f"Flight `{flight_number.upper()}` not found.", ephemeral=True)
            return
        embed = build_manifest_embed(flight, flight_bookings)
        await interaction.followup.send(embed=embed, ephemeral=True)

    # ── /saga-set-class-flights ───────────────────────────────────────────────
    @app_commands.command(name="saga-set-class-flights", description="[Staff] Manually set a member's Saga Class flight allowance")
    @app_commands.guilds(discord.Object(id=GUILD_ID))
    async def saga_set_class_flights(self, interaction: discord.Interaction, member: discord.Member, count: int):
        await interaction.response.defer(ephemeral=True)
        if not is_staff(interaction):
            await interaction.followup.send("You don't have permission to use this command.", ephemeral=True)
            return
        doc = await get_member(member.id)
        if not doc:
            await interaction.followup.send("That member does not have a Saga Club profile yet.", ephemeral=True)
            return
        await set_saga_class_remaining(member.id, count)
        embed = discord.Embed(
            title="Saga Class Flights Updated",
            color=0x003B6F,
            description=f"**{doc.get('roblox_username')}** now has **{count}** Saga Class flight(s) remaining this month."
        )
        embed.set_footer(text="Icelandair Saga Club", icon_url="https://www.icelandair.com/favicon.ico")
        await interaction.followup.send(embed=embed, ephemeral=True)

    # ── /saga-reset-class-flights ─────────────────────────────────────────────
    @app_commands.command(name="saga-reset-class-flights", description="[Staff] Manually reset all members' Saga Class flight allowances")
    @app_commands.guilds(discord.Object(id=GUILD_ID))
    async def saga_reset_class_flights(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        if not is_staff(interaction):
            await interaction.followup.send("You don't have permission to use this command.", ephemeral=True)
            return
        await reset_saga_class_monthly()
        embed = discord.Embed(
            title="Saga Class Flights Reset",
            color=0x003B6F,
            description=(
                "Monthly Saga Class flight allowances have been reset for all members:\n\n"
                "**Saga Blue:** 0 flights\n"
                "**Saga Silver:** 2 flights\n"
                "**Saga Gold:** 5 flights"
            )
        )
        embed.set_footer(text="Icelandair Saga Club", icon_url="https://www.icelandair.com/favicon.ico")
        await interaction.followup.send(embed=embed, ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(BookingsCog(bot))
