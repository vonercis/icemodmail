import discord
from discord.ext import commands, tasks
from discord import app_commands
import os
from datetime import datetime, timezone
from database_aircraft import get_all_aircraft, get_aircraft
from database_bookings import count_cabin_bookings, get_bookings_for_flight, get_all_bookings_for_flight
from database import get_member, SAGA_CLASS_ALLOWANCE
from database_flights import (
    create_flight, get_flight, get_active_flights, update_flight,
    purge_old_flights, STATUSES, STATUS_COLORS, STATUS_EMOJI,
    subscribe, unsubscribe, get_subscribers, clear_subscriptions,
)
from dotenv import load_dotenv

load_dotenv()

GUILD_ID                = int(os.getenv("DISCORD_GUILD_ID"))
FLIGHT_BOARD_CHANNEL_ID = int(os.getenv("FLIGHT_BOARD_CHANNEL_ID", 0))
DISPATCHER_ROLE_ID      = int(os.getenv("DISPATCHER_ROLE_ID", 0))

REASON_TEMPLATES = [
    "Engineering / Technical fault",
    "Crewing requirements",
    "Late inbound aircraft",
    "Slot restrictions",
    "Ground equipment failure",
    "Weather — origin",
    "Weather — destination",
    "Air traffic control (ATC)",
    "Airport closure",
    "Government / regulatory restriction",
    "Low passenger load (consolidation)",
    "Schedule change",
    "Custom reason",
]


def is_dispatcher(interaction: discord.Interaction) -> bool:
    if not DISPATCHER_ROLE_ID:
        return interaction.user.guild_permissions.manage_roles
    return any(r.id == DISPATCHER_ROLE_ID for r in interaction.user.roles)


def fmt_time(val) -> str:
    if not val:
        return "—"
    if isinstance(val, datetime):
        return val.strftime("%H:%M")
    return str(val)


# ── Embed builders ────────────────────────────────────────────────────────────

def build_main_board_embed(active_flights: list) -> discord.Embed:
    embed = discord.Embed(
        title="<:basiclogo:1374682220891209799> Icelandair Flight Board",
        description="Select a flight from the dropdown below to view its full itinerary.",
        color=0x003B6F,
    )
    embed.set_thumbnail(url="https://www.icelandair.com/favicon.ico")

    if not active_flights:
        embed.add_field(name="No active flights", value="There are currently no flights on the board.", inline=False)
    else:
        for f in active_flights:
            status = f.get("status", "Scheduled")
            emoji  = STATUS_EMOJI.get(status, "🕐")
            embed.add_field(
                name=f"{emoji} {f['flight_number']}  •  {f['origin']} → {f['destination']}",
                value=(
                    f"**{f['date']}** · {f['aircraft_type']}\n"
                    f"STD **{fmt_time(f.get('std'))}** · STA **{fmt_time(f.get('sta'))}** · Status: **{status}**"
                ),
                inline=False,
            )

    embed.set_footer(text="Icelandair Operations • Updated", icon_url="https://www.icelandair.com/favicon.ico")
    embed.timestamp = datetime.now(timezone.utc)
    return embed


async def build_flight_embed(f: dict) -> discord.Embed:
    status = f.get("status", "Scheduled")
    color  = STATUS_COLORS.get(status, 0x003B6F)
    emoji  = STATUS_EMOJI.get(status, "🕐")

    checkin_str = "✅ Open" if f.get("checkin_open") else "❌ Closed"
    description = f"{emoji} **{status}**  ·  {f['date']}"
    if f.get("reason"):
        description += f"\n> {f['reason']}"

    embed = discord.Embed(
        title=f"<:basiclogo:1374682220891209799> {f['flight_number']}  ·  {f['origin']} → {f['destination']}",
        description=description,
        color=color,
    )
    embed.set_thumbnail(url="https://www.icelandair.com/favicon.ico")

    embed.add_field(name="<:dblaptopbg:1374617774693023754> Aircraft",   value=f"{f.get('aircraft_type', '—')}\n`{f.get('registration', '—')}`", inline=True)
    embed.add_field(name="<:lbcheckin:1374689021472669738> Check-in",    value=checkin_str, inline=True)

    eco   = f.get("economy_count", 0)
    prem  = f.get("premium_count", 0)
    eco_booked  = await count_cabin_bookings(f["flight_number"], "Economy")
    saga_booked = await count_cabin_bookings(f["flight_number"], "Saga Class")
    embed.add_field(
        name="<:lbseated:1374689017777492019> Cabin",
        value=(
            f"Economy: **{eco_booked}/{eco}** booked · **{max(0,eco-eco_booked)}** available\n"
            f"Saga Class: **{saga_booked}/{prem}** booked · **{max(0,prem-saga_booked)}** available"
        ),
        inline=False
    )
    embed.add_field(name="<:dbtakeoffbg:1374617776504832001> Departure", value=f"STD: **{fmt_time(f.get('std'))}**\nETD: **{fmt_time(f.get('etd'))}**\nATD: **{fmt_time(f.get('atd'))}**", inline=True)
    embed.add_field(name="🛬 Arrival",                                   value=f"STA: **{fmt_time(f.get('sta'))}**\nETA: **{fmt_time(f.get('eta'))}**\nATA: **{fmt_time(f.get('ata'))}**", inline=True)
    embed.add_field(name="⏱ Block Time",                                 value=f.get("block_time", "—"), inline=True)

    embed.set_footer(text="Icelandair Operations", icon_url="https://www.icelandair.com/favicon.ico")
    embed.timestamp = datetime.now(timezone.utc)
    return embed


def build_announcement_embed(f: dict, event: str) -> discord.Embed:
    """Builds a channel announcement embed for a flight event."""
    status = f.get("status", "Scheduled")
    color  = STATUS_COLORS.get(status, 0x003B6F)
    fn     = f["flight_number"]
    route  = f"{f['origin']} → {f['destination']}"

    titles = {
        "scheduled":  f"<:dbtakeoffbg:1374617776504832001> Flight Scheduled — {fn}",
        "delayed":    f"⚠️ Flight Delayed — {fn}",
        "cancelled":  f"❌ Flight Cancelled — {fn}",
        "boarding":   f"🚪 Boarding Now — {fn}",
        "departed":   f"✈️ Flight Departed — {fn}",
        "arrived":    f"🛬 Flight Arrived — {fn}",
        "checkin":    f"<:lbcheckin:1374689021472669738> Check-in Open — {fn}",
        "update":     f"ℹ️ Flight Update — {fn}",
    }

    descriptions = {
        "scheduled": f"**{fn}** {route} has been scheduled.\n**Date:** {f['date']} · **STD:** {fmt_time(f.get('std'))} · **STA:** {fmt_time(f.get('sta'))}",
        "delayed":   f"**{fn}** {route} has been delayed.\n**New ETD:** {fmt_time(f.get('etd'))}" + (f"\n**Reason:** {f['reason']}" if f.get("reason") else ""),
        "cancelled": (
            f"**{fn}** {route} on **{f['date']}** has been cancelled."
            + (f"\n**Reason:** {f['reason']}" if f.get("reason") else "")
            + "\n\nAll affected passengers have been contacted directly with follow-up instructions. "
            "If you require further assistance, please contact our customer service team."
        ),
        "boarding":  f"**{fn}** {route} is now boarding.\n**Gate closes at:** {fmt_time(f.get('etd') or f.get('std'))}",
        "departed":  f"**{fn}** {route} has departed.\n**ATD:** {fmt_time(f.get('atd'))} · **ETA:** {fmt_time(f.get('eta') or f.get('sta'))}",
        "arrived":   f"**{fn}** {route} has arrived.\n**ATA:** {fmt_time(f.get('ata'))}",
        "checkin":   f"Check-in is now open for **{fn}** {route}.\n**Date:** {f['date']} · **STD:** {fmt_time(f.get('std'))}",
        "update":    f"**{fn}** {route} has been updated.",
    }

    embed = discord.Embed(
        title=titles.get(event, titles["update"]),
        description=descriptions.get(event, descriptions["update"]),
        color=color,
    )
    embed.set_thumbnail(url="https://www.icelandair.com/favicon.ico")
    embed.set_footer(text="Icelandair Operations", icon_url="https://www.icelandair.com/favicon.ico")
    embed.timestamp = datetime.now(timezone.utc)
    return embed


async def build_subscriber_dm(f: dict, event: str, discord_id: int) -> discord.Embed:
    """Builds a personalised DM embed for a subscriber."""
    fn    = f["flight_number"]
    route = f"{f['origin']} → {f['destination']}"
    color = STATUS_COLORS.get(f.get("status", "Scheduled"), 0x003B6F)

    if event == "cancelled":
        from database import get_member
        member_doc  = await get_member(discord_id)
        roblox_name = member_doc.get("roblox_username", "valued passenger") if member_doc else "valued passenger"
        embed = discord.Embed(
            title=f"Important Notice — Flight {fn} Cancelled",
            color=STATUS_COLORS["Cancelled"],
            description=(
                f"Dear {roblox_name},\n\n"
                f"We regret to inform you that flight **{fn}** ({route}) "
                f"on **{f['date']}** has been cancelled"
                + (f" due to {f['reason'].lower()}." if f.get("reason") else ".")
                + f"\n\n"
                f"We sincerely apologise for any inconvenience this may cause. "
                f"If you have purchased any products or services in connection with this flight, "
                f"please contact our customer service team who will be happy to assist you.\n\n"
                f"Thank you for your understanding, and we look forward to welcoming you on board a future Icelandair service."
            ),
        )
    elif event == "delayed":
        embed = discord.Embed(
            title=f"Flight Update — {fn} Delayed",
            color=STATUS_COLORS["Delayed"],
            description=(
                f"Your flight **{fn}** ({route}) on **{f['date']}** has been delayed.\n\n"
                + (f"**Reason:** {f['reason']}\n" if f.get("reason") else "")
                + f"**New estimated departure:** {fmt_time(f.get('etd'))}\n\n"
                f"We apologise for the inconvenience and thank you for your patience."
            ),
        )
    elif event == "checkin":
        embed = discord.Embed(
            title=f"Check-in Open — {fn}",
            color=STATUS_COLORS["Boarding"],
            description=(
                f"Check-in is now open for your flight **{fn}** ({route}) on **{f['date']}**.\n\n"
                f"**Scheduled departure:** {fmt_time(f.get('std'))}\n\n"
                f"We look forward to welcoming you on board."
            ),
        )
    elif event == "boarding":
        embed = discord.Embed(
            title=f"Boarding Now — {fn}",
            color=STATUS_COLORS["Boarding"],
            description=(
                f"Your flight **{fn}** ({route}) is now boarding.\n\n"
                f"Please make your way to the gate. We look forward to welcoming you on board."
            ),
        )
    elif event == "departed":
        embed = discord.Embed(
            title=f"Flight Departed — {fn}",
            color=STATUS_COLORS["Departed"],
            description=(
                f"Your flight **{fn}** ({route}) has departed.\n\n"
                f"**ATD:** {fmt_time(f.get('atd'))} · **ETA:** {fmt_time(f.get('eta') or f.get('sta'))}\n\n"
                f"We hope you enjoy your flight."
            ),
        )
    elif event == "arrived":
        embed = discord.Embed(
            title=f"Flight Arrived — {fn}",
            color=STATUS_COLORS["Arrived"],
            description=(
                f"Your flight **{fn}** ({route}) has arrived.\n\n"
                f"**ATA:** {fmt_time(f.get('ata'))}\n\n"
                f"Thank you for flying with Icelandair. We hope to see you again soon."
            ),
        )
    else:
        embed = discord.Embed(
            title=f"Flight Update — {fn}",
            color=color,
            description=f"There has been an update to your flight **{fn}** ({route}) on **{f['date']}**.",
        )

    embed.set_thumbnail(url="https://www.icelandair.com/favicon.ico")
    embed.set_footer(text="Icelandair Saga Club — Flight Notifications", icon_url="https://www.icelandair.com/favicon.ico")
    embed.timestamp = datetime.now(timezone.utc)
    return embed


# ── Reason select menus ───────────────────────────────────────────────────────

class CustomReasonModal(discord.ui.Modal, title="Enter Custom Reason"):
    reason = discord.ui.TextInput(label="Reason", placeholder="Enter the reason...", max_length=200, style=discord.TextStyle.short)

    def __init__(self, flight_number: str, action: str, etd: str = None, cog=None):
        super().__init__()
        self.flight_number = flight_number
        self.action        = action
        self.etd           = etd
        self.cog           = cog

    async def on_submit(self, interaction: discord.Interaction):
        await self.cog.apply_reason_action(interaction, self.flight_number, self.action, str(self.reason), self.etd)


class ReasonSelectMenu(discord.ui.Select):
    def __init__(self, flight_number: str, action: str, etd: str = None, cog=None):
        self.flight_number = flight_number
        self.action        = action
        self.etd           = etd
        self.cog           = cog
        options = [discord.SelectOption(label=r, value=r) for r in REASON_TEMPLATES]
        super().__init__(placeholder="Select a reason...", options=options, min_values=1, max_values=1)

    async def callback(self, interaction: discord.Interaction):
        selected = self.values[0]
        if selected == "Custom reason":
            await interaction.response.send_modal(CustomReasonModal(self.flight_number, self.action, self.etd, self.cog))
        else:
            await self.cog.apply_reason_action(interaction, self.flight_number, self.action, selected, self.etd)


class ReasonSelectView(discord.ui.View):
    def __init__(self, flight_number: str, action: str, etd: str = None, cog=None):
        super().__init__(timeout=120)
        self.add_item(ReasonSelectMenu(flight_number, action, etd, cog))


# ── Flight creation modal ─────────────────────────────────────────────────────

class FlightCreateModal(discord.ui.Modal, title="Create New Flight"):
    flight_number = discord.ui.TextInput(label="Flight Number",      placeholder="e.g. FI123",       max_length=10)
    origin        = discord.ui.TextInput(label="Origin (IATA)",      placeholder="e.g. KEF",         max_length=3)
    destination   = discord.ui.TextInput(label="Destination (IATA)", placeholder="e.g. LHR",         max_length=3)
    date          = discord.ui.TextInput(label="Date",                placeholder="e.g. 23 Apr 2025", max_length=20)

    def __init__(self, registration: str, std: str, sta: str, block_time: str, cog=None):
        super().__init__()
        self.reg        = registration
        self.std        = std
        self.sta        = sta
        self.block_time = block_time
        self.cog        = cog

    async def on_submit(self, interaction: discord.Interaction):
        existing = await get_flight(str(self.flight_number))
        if existing:
            await interaction.response.send_message(f"Flight `{str(self.flight_number).upper()}` already exists. Use `/flight-update` to modify it.", ephemeral=True)
            return

        # Pull aircraft details from registry
        aircraft_doc = await get_aircraft(self.reg)
        if not aircraft_doc:
            await interaction.response.send_message(f"Aircraft `{self.reg}` not found in the registry. Please add it first using `/aircraft-add`.", ephemeral=True)
            return

        doc = await create_flight({
            "flight_number": str(self.flight_number),
            "origin":        str(self.origin),
            "destination":   str(self.destination),
            "date":          str(self.date),
            "aircraft_type": aircraft_doc["aircraft_type"],
            "registration":  aircraft_doc["registration"],
            "std":           self.std,
            "sta":           self.sta,
            "block_time":    self.block_time,
            "economy_count": aircraft_doc["economy_seats"],
            "premium_count": aircraft_doc["premium_seats"],
        })

        await self.cog.refresh_board()
        await self.cog.post_announcement(doc, "scheduled")

        embed = discord.Embed(
            title="Flight Created", color=0x003B6F,
            description=(
                f"**{doc['flight_number']}** {str(self.origin).upper()} → {str(self.destination).upper()} added to the board.\n"
                f"**Aircraft:** {aircraft_doc['aircraft_type']} `{aircraft_doc['registration']}`\n"
                f"**Date:** {str(self.date)} · **STD:** {self.std} · **STA:** {self.sta}\n"
                f"**Seats:** Economy {aircraft_doc['economy_seats']} · Saga Premium {aircraft_doc['premium_seats']}"
            )
        )
        embed.set_footer(text="Icelandair Operations", icon_url="https://www.icelandair.com/favicon.ico")
        await interaction.response.send_message(embed=embed, ephemeral=True)


# ── Flight board views ────────────────────────────────────────────────────────

class SubscribeButton(discord.ui.Button):
    def __init__(self, flight_number: str):
        super().__init__(label="🔔 Subscribe to Updates", style=discord.ButtonStyle.success)
        self.flight_number = flight_number

    async def callback(self, interaction: discord.Interaction):
        newly = await subscribe(interaction.user.id, self.flight_number)
        if newly:
            embed = discord.Embed(
                title="🔔 Subscribed",
                description=(
                    f"You are now subscribed to updates for flight **{self.flight_number}**.\n\n"
                    f"You will receive a direct message whenever this flight is delayed, cancelled, "
                    f"starts boarding, departs, arrives, or check-in opens.\n\n"
                    f"You can unsubscribe at any time using `/flight-unsubscribe`."
                ),
                color=STATUS_COLORS["Boarding"],
            )
            embed.set_footer(text="Icelandair Operations", icon_url="https://www.icelandair.com/favicon.ico")
            await interaction.response.send_message(embed=embed, ephemeral=True)
        else:
            await interaction.response.send_message(f"You are already subscribed to updates for flight **{self.flight_number}**.", ephemeral=True)


class FlightSelectMenu(discord.ui.Select):
    def __init__(self, active_flights: list):
        options = [
            discord.SelectOption(
                label=f"{f['flight_number']}  •  {f['origin']} → {f['destination']}",
                description=f"{f['date']} · {STATUS_EMOJI.get(f.get('status','Scheduled'), '')} {f.get('status','Scheduled')}",
                value=f["flight_number"],
                emoji="✈️",
            )
            for f in active_flights[:25]
        ]
        if not options:
            options = [discord.SelectOption(label="No active flights", value="none", emoji="❌")]
        super().__init__(placeholder="Select a flight to view...", options=options, min_values=1, max_values=1)

    async def callback(self, interaction: discord.Interaction):
        if self.values[0] == "none":
            await interaction.response.defer()
            return
        flight = await get_flight(self.values[0])
        if not flight:
            await interaction.response.send_message("Flight not found.", ephemeral=True)
            return
        embed = await build_flight_embed(flight)
        view  = FlightDetailView(flight)
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)


class FlightBoardView(discord.ui.View):
    def __init__(self, active_flights: list):
        super().__init__(timeout=None)
        self.add_item(FlightSelectMenu(active_flights))


class BookFlightButton(discord.ui.Button):
    def __init__(self, flight: dict):
        bookable = flight.get("status") in ("Scheduled", "Boarding", "Delayed")
        super().__init__(
            label="🎫 Book Flight",
            style=discord.ButtonStyle.primary,
            disabled=not bookable,
        )
        self.flight = flight

    async def callback(self, interaction: discord.Interaction):
        from cogs.bookings import CabinSelectView
        flight = await get_flight(self.flight["flight_number"])
        if not flight:
            await interaction.response.send_message("Flight not found.", ephemeral=True)
            return

        # Check if flight is bookable
        if flight.get("status") not in ("Scheduled", "Boarding", "Delayed"):
            await interaction.response.send_message("This flight is no longer accepting bookings.", ephemeral=True)
            return

        member_doc = await get_member(interaction.user.id)
        roblox_username = interaction.user.display_name
        if member_doc:
            roblox_username = member_doc.get("roblox_username", roblox_username)

        from cogs.bookings import BookingsCog
        bookings_cog = interaction.client.cogs.get("BookingsCog")

        view = CabinSelectView(flight, member_doc, roblox_username, bookings_cog)

        tier            = member_doc.get("tier", "blue") if member_doc else "blue"
        saga_remaining  = member_doc.get("saga_class_flights_remaining", 0) if member_doc else 0
        allowance       = SAGA_CLASS_ALLOWANCE.get(tier, 0)

        embed = discord.Embed(
            title=f"Book Flight {flight['flight_number']}",
            description=(
                f"**{flight['origin']} → {flight['destination']}**  ·  {flight['date']}

"
                f"Select your cabin class below.

"
                f"**Your Saga Club tier:** {tier.title()}
"
                f"**Saga Class flights remaining this month:** {saga_remaining}/{allowance}"
            ),
            color=0x003B6F,
        )
        embed.set_footer(text="Icelandair", icon_url="https://www.icelandair.com/favicon.ico")
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)


class FlightDetailView(discord.ui.View):
    def __init__(self, flight: dict):
        super().__init__(timeout=None)
        self.flight = flight
        self.add_item(SubscribeButton(flight["flight_number"]))
        self.add_item(BookFlightButton(flight))

    @discord.ui.button(label="← Main Menu", style=discord.ButtonStyle.secondary)
    async def back_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        active_flights = await get_active_flights()
        embed = build_main_board_embed(active_flights)
        view  = FlightBoardView(active_flights)
        await interaction.response.edit_message(embed=embed, view=view, ephemeral=True)


# ── Cog ───────────────────────────────────────────────────────────────────────

class FlightsCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot           = bot
        self.board_message = None
        self.purge_loop.start()

    def cog_unload(self):
        self.purge_loop.cancel()

    @tasks.loop(hours=1)
    async def purge_loop(self):
        count = await purge_old_flights()
        if count:
            print(f"[Flights] Purged {count} old flight(s)")
            await self.refresh_board()

    @purge_loop.before_loop
    async def before_purge(self):
        await self.bot.wait_until_ready()

    async def get_board_channel(self) -> discord.TextChannel | None:
        if not FLIGHT_BOARD_CHANNEL_ID:
            return None
        return self.bot.get_channel(FLIGHT_BOARD_CHANNEL_ID)

    async def refresh_board(self):
        channel = await self.get_board_channel()
        if not channel:
            return
        active_flights = await get_active_flights()
        embed          = build_main_board_embed(active_flights)
        view           = FlightBoardView(active_flights)

        if self.board_message:
            try:
                await self.board_message.edit(embed=embed, view=view)
                return
            except discord.NotFound:
                self.board_message = None

        async for msg in channel.history(limit=20):
            if msg.author == self.bot.user and msg.embeds:
                if "Icelandair Flight Board" in (msg.embeds[0].title or ""):
                    self.board_message = msg
                    await self.board_message.edit(embed=embed, view=view)
                    return

        self.board_message = await channel.send(embed=embed, view=view)

    async def post_announcement(self, flight: dict, event: str):
        """Posts a public announcement in the board channel for a flight event."""
        channel = await self.get_board_channel()
        if not channel:
            return
        embed = build_announcement_embed(flight, event)
        await channel.send(embed=embed)

    async def notify_subscribers(self, flight: dict, event: str):
        """Sends personalised DMs to all subscribers of a flight."""
        subscribers = await get_subscribers(flight["flight_number"])
        for discord_id in subscribers:
            try:
                user  = await self.bot.fetch_user(discord_id)
                embed = await build_subscriber_dm(flight, event, discord_id)
                await user.send(embed=embed)
            except Exception as e:
                print(f"[Flights] Could not DM subscriber {discord_id}: {e}")

        if event in ("cancelled", "arrived"):
            await clear_subscriptions(flight["flight_number"])

        # On arrival, deduct Saga Class flight from anyone who booked Saga Class
        if event == "arrived":
            await self.deduct_saga_class_on_arrival(flight["flight_number"])

    async def deduct_saga_class_on_arrival(self, flight_number: str):
        from database_bookings import get_bookings_for_flight
        from database import set_saga_class_remaining, SAGA_CLASS_ALLOWANCE
        flight_bookings = await get_bookings_for_flight(flight_number)
        for booking in flight_bookings:
            if booking.get("cabin") != "Saga Class":
                continue
            discord_id = booking["discord_id"]
            member     = await get_member(discord_id)
            if not member:
                continue
            current   = member.get("saga_class_flights_remaining", 0)
            # Already deducted at booking time — no further deduction needed
            # But if booking was made before this system, deduct now
            print(f"[Flights] Saga Class flight arrived for {booking['roblox_username']} on {flight_number}")

    async def apply_reason_action(self, interaction: discord.Interaction, flight_number: str, action: str, reason: str, etd: str = None):
        updates = {"reason": reason}
        if action == "cancel":
            updates["status"] = "Cancelled"
            event = "cancelled"
            confirm_msg = f"Flight `{flight_number}` has been **cancelled**.\n**Reason:** {reason}\nIt will be removed from the board in 24 hours."
        else:
            updates["status"] = "Delayed"
            if etd:
                updates["etd"] = etd
            event = "delayed"
            confirm_msg = f"Flight `{flight_number}` has been marked as **delayed**.\n**Reason:** {reason}" + (f"\n**New ETD:** {etd}" if etd else "")

        updated = await update_flight(flight_number, updates)
        await self.refresh_board()
        await self.post_announcement(updated, event)
        await self.notify_subscribers(updated, event)

        embed = discord.Embed(title="Flight Updated", color=STATUS_COLORS.get(updates["status"], 0x003B6F), description=confirm_msg)
        embed.set_footer(text="Icelandair Operations", icon_url="https://www.icelandair.com/favicon.ico")

        if interaction.response.is_done():
            await interaction.edit_original_response(embed=embed, view=None)
        else:
            await interaction.response.edit_message(embed=embed, view=None)

    # ── /flight-create ────────────────────────────────────────────────────────
    @app_commands.command(name="flight-create", description="[Dispatcher] Create a new flight on the board")
    @app_commands.guilds(discord.Object(id=GUILD_ID))
    async def flight_create(self, interaction: discord.Interaction, registration: str, std: str, sta: str, block_time: str):
        if not is_dispatcher(interaction):
            await interaction.response.send_message("You don't have permission to use this command.", ephemeral=True)
            return
        modal = FlightCreateModal(registration=registration, std=std, sta=sta, block_time=block_time, cog=self)
        await interaction.response.send_modal(modal)

    @flight_create.autocomplete("registration")
    async def aircraft_registration_autocomplete(self, interaction: discord.Interaction, current: str):
        all_aircraft = await get_all_aircraft()
        return [
            app_commands.Choice(
                name=f"{a['registration']} — {a['aircraft_type']}",
                value=a["registration"]
            )
            for a in all_aircraft
            if current.lower() in a["registration"].lower() or current.lower() in a["aircraft_type"].lower()
        ][:25]

    # ── /flight-update ────────────────────────────────────────────────────────
    @app_commands.command(name="flight-update", description="[Dispatcher] Update a flight's details or status")
    @app_commands.guilds(discord.Object(id=GUILD_ID))
    async def flight_update(
        self, interaction: discord.Interaction,
        flight_number: str, status: str = None,
        etd: str = None, eta: str = None, atd: str = None, ata: str = None,
        economy_count: int = None, premium_count: int = None,
        checkin_open: bool = None, block_time: str = None, registration: str = None,
    ):
        await interaction.response.defer(ephemeral=True)
        if not is_dispatcher(interaction):
            await interaction.followup.send("You don't have permission to use this command.", ephemeral=True)
            return

        flight = await get_flight(flight_number)
        if not flight:
            await interaction.followup.send(f"Flight `{flight_number.upper()}` not found.", ephemeral=True)
            return

        if status and status not in STATUSES:
            await interaction.followup.send(f"Invalid status. Choose from: {', '.join(STATUSES)}", ephemeral=True)
            return

        updates = {}
        if status:                    updates["status"]        = status
        if etd:                       updates["etd"]           = etd
        if eta:                       updates["eta"]           = eta
        if atd:                       updates["atd"]           = atd
        if ata:                       updates["ata"]           = ata
        if block_time:                updates["block_time"]    = block_time
        if registration:              updates["registration"]  = registration
        if economy_count is not None: updates["economy_count"] = economy_count
        if premium_count is not None: updates["premium_count"] = premium_count
        if checkin_open is not None:  updates["checkin_open"]  = checkin_open

        if not updates:
            await interaction.followup.send("No updates provided.", ephemeral=True)
            return

        updated = await update_flight(flight_number, updates)
        await self.refresh_board()

        # Auto-close check-in when boarding starts
        if status == "Boarding":
            updates["checkin_open"] = False

        # Determine event type for announcement and subscriber DMs
        event = "update"
        if status == "Boarding":      event = "boarding"
        elif status == "Departed":    event = "departed"
        elif status == "Arrived":     event = "arrived"
        elif checkin_open is True:    event = "checkin"

        if event != "update" or checkin_open is True:
            await self.post_announcement(updated, event)
            await self.notify_subscribers(updated, event)

        embed = discord.Embed(
            title="Flight Updated",
            color=STATUS_COLORS.get(updated.get("status", "Scheduled"), 0x003B6F),
            description=f"**{updated['flight_number']}** {updated['origin']} → {updated['destination']} updated."
        )
        for k, v in updates.items():
            embed.add_field(name=k.replace("_", " ").title(), value=str(v), inline=True)
        embed.set_footer(text="Icelandair Operations", icon_url="https://www.icelandair.com/favicon.ico")
        await interaction.followup.send(embed=embed, ephemeral=True)

    @flight_update.autocomplete("status")
    async def status_autocomplete(self, interaction: discord.Interaction, current: str):
        return [app_commands.Choice(name=s, value=s) for s in STATUSES if current.lower() in s.lower()]

    # ── /flight-cancel ────────────────────────────────────────────────────────
    @app_commands.command(name="flight-cancel", description="[Dispatcher] Cancel a flight with a reason")
    @app_commands.guilds(discord.Object(id=GUILD_ID))
    async def flight_cancel(self, interaction: discord.Interaction, flight_number: str):
        await interaction.response.defer(ephemeral=True)
        if not is_dispatcher(interaction):
            await interaction.followup.send("You don't have permission to use this command.", ephemeral=True)
            return
        flight = await get_flight(flight_number)
        if not flight:
            await interaction.followup.send(f"Flight `{flight_number.upper()}` not found.", ephemeral=True)
            return
        embed = discord.Embed(title=f"Cancel {flight_number.upper()}", description="Select a cancellation reason from the dropdown below.", color=STATUS_COLORS["Cancelled"])
        embed.set_footer(text="Icelandair Operations", icon_url="https://www.icelandair.com/favicon.ico")
        await interaction.followup.send(embed=embed, view=ReasonSelectView(flight_number.upper(), "cancel", cog=self), ephemeral=True)

    # ── /flight-delay ─────────────────────────────────────────────────────────
    @app_commands.command(name="flight-delay", description="[Dispatcher] Delay a flight with a reason and new ETD")
    @app_commands.guilds(discord.Object(id=GUILD_ID))
    async def flight_delay(self, interaction: discord.Interaction, flight_number: str, new_etd: str = None):
        await interaction.response.defer(ephemeral=True)
        if not is_dispatcher(interaction):
            await interaction.followup.send("You don't have permission to use this command.", ephemeral=True)
            return
        flight = await get_flight(flight_number)
        if not flight:
            await interaction.followup.send(f"Flight `{flight_number.upper()}` not found.", ephemeral=True)
            return
        etd_str = f" · New ETD: **{new_etd}**" if new_etd else ""
        embed = discord.Embed(title=f"Delay {flight_number.upper()}", description=f"Select a delay reason from the dropdown below.{etd_str}", color=STATUS_COLORS["Delayed"])
        embed.set_footer(text="Icelandair Operations", icon_url="https://www.icelandair.com/favicon.ico")
        await interaction.followup.send(embed=embed, view=ReasonSelectView(flight_number.upper(), "delay", etd=new_etd, cog=self), ephemeral=True)

    # ── /flight-unsubscribe ───────────────────────────────────────────────────
    @app_commands.command(name="flight-unsubscribe", description="Unsubscribe from updates for a specific flight")
    @app_commands.guilds(discord.Object(id=GUILD_ID))
    async def flight_unsubscribe(self, interaction: discord.Interaction, flight_number: str):
        await interaction.response.defer(ephemeral=True)
        await unsubscribe(interaction.user.id, flight_number.upper())
        await interaction.followup.send(f"You have been unsubscribed from updates for flight **{flight_number.upper()}**.", ephemeral=True)

    # ── /flight-bookings ──────────────────────────────────────────────────────
    @app_commands.command(name="flight-bookings", description="[Dispatcher] View all bookings for a flight — active first, then history")
    @app_commands.guilds(discord.Object(id=GUILD_ID))
    async def flight_bookings(self, interaction: discord.Interaction, flight_number: str):
        await interaction.response.defer(ephemeral=True)
        if not is_dispatcher(interaction):
            await interaction.followup.send("You don't have permission to use this command.", ephemeral=True)
            return

        flight = await get_flight(flight_number)
        if not flight:
            await interaction.followup.send(f"Flight `{flight_number.upper()}` not found.", ephemeral=True)
            return

        all_bookings    = await get_all_bookings_for_flight(flight_number)
        active_bookings = [b for b in all_bookings if b.get("status") != "Cancelled"]
        past_bookings   = [b for b in all_bookings if b.get("status") == "Cancelled"]

        color = STATUS_COLORS.get(flight.get("status", "Scheduled"), 0x003B6F)

        embed = discord.Embed(
            title=f"📋 Bookings — {flight['flight_number']} {flight['origin']} → {flight['destination']}",
            description=(
                f"**{flight['date']}** · {flight.get('aircraft_type','—')}
"
                f"**{len(active_bookings)}** active · **{len(past_bookings)}** cancelled"
            ),
            color=color,
        )

        # Active bookings section
        if active_bookings:
            eco_lines  = [f"• `{b['booking_ref']}` {b['roblox_username']}" for b in active_bookings if b["cabin"] == "Economy"]
            saga_lines = [f"• `{b['booking_ref']}` {b['roblox_username']}" for b in active_bookings if b["cabin"] == "Saga Class"]
            if eco_lines:
                embed.add_field(
                    name=f"💺 Economy — {len(eco_lines)}/{flight.get('economy_count',0)} seats",
                    value="\n".join(eco_lines)[:1024],
                    inline=False
                )
            if saga_lines:
                embed.add_field(
                    name=f"🛋️ Saga Class — {len(saga_lines)}/{flight.get('premium_count',0)} seats",
                    value="\n".join(saga_lines)[:1024],
                    inline=False
                )
        else:
            embed.add_field(name="No active bookings", value="No passengers are booked on this flight.", inline=False)

        # Cancelled bookings section
        if past_bookings:
            cancelled_lines = [
                f"• `{b['booking_ref']}` {b['roblox_username']} ({b['cabin']})"
                for b in past_bookings
            ]
            embed.add_field(
                name=f"❌ Cancelled ({len(past_bookings)})",
                value="\n".join(cancelled_lines[:10])[:1024]
                    + ("\n*...and more*" if len(cancelled_lines) > 10 else ""),
                inline=False
            )

        embed.set_footer(text="Icelandair Operations — Internal Use Only", icon_url="https://www.icelandair.com/favicon.ico")
        embed.timestamp = datetime.now(timezone.utc)
        await interaction.followup.send(embed=embed, ephemeral=True)

    # ── /flight-board ─────────────────────────────────────────────────────────
    @app_commands.command(name="flight-board", description="Post or refresh the flight board in the board channel")
    @app_commands.guilds(discord.Object(id=GUILD_ID))
    async def flight_board(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        if not is_dispatcher(interaction):
            await interaction.followup.send("You don't have permission to use this command.", ephemeral=True)
            return
        await self.refresh_board()
        await interaction.followup.send("Flight board refreshed.", ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(FlightsCog(bot))
