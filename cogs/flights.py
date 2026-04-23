import discord
from discord.ext import commands, tasks
from discord import app_commands
import os
from datetime import datetime, timezone
from database_flights import (
    create_flight, get_flight, get_active_flights,
    update_flight, set_board_message, purge_old_flights,
    STATUSES, STATUS_COLORS, STATUS_EMOJI
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

    embed.set_footer(
        text="Icelandair Operations • Updated",
        icon_url="https://www.icelandair.com/favicon.ico"
    )
    embed.timestamp = datetime.now(timezone.utc)
    return embed


def build_flight_embed(f: dict) -> discord.Embed:
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

    embed.add_field(
        name="<:dblaptopbg:1374617774693023754> Aircraft",
        value=f"{f.get('aircraft_type', '—')}\n`{f.get('registration', '—')}`",
        inline=True,
    )
    embed.add_field(
        name="<:lbcheckin:1374689021472669738> Check-in",
        value=checkin_str,
        inline=True,
    )
    eco   = f.get("economy_count", 0)
    prem  = f.get("premium_count", 0)
    total = eco + prem
    embed.add_field(
        name="<:lbseated:1374689017777492019> Cabin",
        value=f"Economy: **{eco}**\nSaga Premium: **{prem}**\nTotal: **{total}**",
        inline=True,
    )
    embed.add_field(
        name="<:dbtakeoffbg:1374617776504832001> Departure",
        value=(
            f"STD: **{fmt_time(f.get('std'))}**\n"
            f"ETD: **{fmt_time(f.get('etd'))}**\n"
            f"ATD: **{fmt_time(f.get('atd'))}**"
        ),
        inline=True,
    )
    embed.add_field(
        name="🛬 Arrival",
        value=(
            f"STA: **{fmt_time(f.get('sta'))}**\n"
            f"ETA: **{fmt_time(f.get('eta'))}**\n"
            f"ATA: **{fmt_time(f.get('ata'))}**"
        ),
        inline=True,
    )
    embed.add_field(
        name="⏱ Block Time",
        value=f.get("block_time", "—"),
        inline=True,
    )

    embed.set_footer(
        text="Icelandair Operations",
        icon_url="https://www.icelandair.com/favicon.ico"
    )
    embed.timestamp = datetime.now(timezone.utc)
    return embed


# ── Reason select menus ───────────────────────────────────────────────────────

class CustomReasonModal(discord.ui.Modal, title="Enter Custom Reason"):
    reason = discord.ui.TextInput(
        label="Reason",
        placeholder="Enter the reason...",
        max_length=200,
        style=discord.TextStyle.short,
    )

    def __init__(self, flight_number: str, action: str, etd: str = None, cog=None):
        super().__init__()
        self.flight_number = flight_number
        self.action        = action
        self.etd           = etd
        self.cog           = cog

    async def on_submit(self, interaction: discord.Interaction):
        await self.cog.apply_reason_action(
            interaction, self.flight_number, self.action,
            str(self.reason), self.etd
        )


class ReasonSelectMenu(discord.ui.Select):
    def __init__(self, flight_number: str, action: str, etd: str = None, cog=None):
        self.flight_number = flight_number
        self.action        = action
        self.etd           = etd
        self.cog           = cog

        options = [
            discord.SelectOption(label=r, value=r)
            for r in REASON_TEMPLATES
        ]
        super().__init__(
            placeholder="Select a reason...",
            options=options,
            min_values=1,
            max_values=1,
        )

    async def callback(self, interaction: discord.Interaction):
        selected = self.values[0]
        if selected == "Custom reason":
            modal = CustomReasonModal(
                self.flight_number, self.action, self.etd, self.cog
            )
            await interaction.response.send_modal(modal)
        else:
            await self.cog.apply_reason_action(
                interaction, self.flight_number, self.action,
                selected, self.etd
            )


class ReasonSelectView(discord.ui.View):
    def __init__(self, flight_number: str, action: str, etd: str = None, cog=None):
        super().__init__(timeout=120)
        self.add_item(ReasonSelectMenu(flight_number, action, etd, cog))


# ── Flight board views ────────────────────────────────────────────────────────

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
        super().__init__(
            placeholder="Select a flight to view...",
            options=options,
            min_values=1,
            max_values=1,
        )

    async def callback(self, interaction: discord.Interaction):
        if self.values[0] == "none":
            await interaction.response.defer()
            return
        flight = await get_flight(self.values[0])
        if not flight:
            await interaction.response.send_message("Flight not found.", ephemeral=True)
            return
        embed = build_flight_embed(flight)
        view  = FlightDetailView(flight)
        await interaction.response.edit_message(embed=embed, view=view)


class FlightBoardView(discord.ui.View):
    def __init__(self, active_flights: list):
        super().__init__(timeout=None)
        self.add_item(FlightSelectMenu(active_flights))


class FlightDetailView(discord.ui.View):
    def __init__(self, flight: dict):
        super().__init__(timeout=None)
        self.flight = flight

    @discord.ui.button(label="← Main Menu", style=discord.ButtonStyle.secondary)
    async def back_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        active_flights = await get_active_flights()
        embed = build_main_board_embed(active_flights)
        view  = FlightBoardView(active_flights)
        await interaction.response.edit_message(embed=embed, view=view)


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
            print(f"[Flights] Purged {count} old flight(s) from the board")
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

    async def apply_reason_action(
        self, interaction: discord.Interaction,
        flight_number: str, action: str,
        reason: str, etd: str = None
    ):
        """Shared handler for both cancel and delay after reason is selected."""
        updates = {"reason": reason}

        if action == "cancel":
            updates["status"] = "Cancelled"
            confirm_msg = (
                f"Flight `{flight_number}` has been **cancelled**.\n"
                f"**Reason:** {reason}\n"
                f"It will be removed from the board in 24 hours."
            )
        else:  # delay
            updates["status"] = "Delayed"
            if etd:
                updates["etd"] = etd
            confirm_msg = (
                f"Flight `{flight_number}` has been marked as **delayed**.\n"
                f"**Reason:** {reason}"
                + (f"\n**New ETD:** {etd}" if etd else "")
            )

        await update_flight(flight_number, updates)
        await self.refresh_board()

        embed = discord.Embed(
            title="Flight Updated",
            color=STATUS_COLORS.get(updates["status"], 0x003B6F),
            description=confirm_msg,
        )
        embed.set_footer(text="Icelandair Operations", icon_url="https://www.icelandair.com/favicon.ico")

        if interaction.response.is_done():
            await interaction.edit_original_response(embed=embed, view=None)
        else:
            await interaction.response.edit_message(embed=embed, view=None)

    # ── /flight-create ────────────────────────────────────────────────────────
    @app_commands.command(name="flight-create", description="[Dispatcher] Create a new flight on the board")
    @app_commands.guilds(discord.Object(id=GUILD_ID))
    async def flight_create(
        self, interaction: discord.Interaction,
        flight_number: str, origin: str, destination: str,
        date: str, aircraft_type: str, registration: str,
        std: str, sta: str, block_time: str,
        economy_count: int = 0, premium_count: int = 0,
        etd: str = None, eta: str = None,
    ):
        await interaction.response.defer(ephemeral=True)
        if not is_dispatcher(interaction):
            await interaction.followup.send("You don't have permission to use this command.", ephemeral=True)
            return

        existing = await get_flight(flight_number)
        if existing:
            await interaction.followup.send(f"Flight `{flight_number.upper()}` already exists. Use `/flight-update` to modify it.", ephemeral=True)
            return

        doc = await create_flight({
            "flight_number": flight_number, "origin": origin,
            "destination": destination, "date": date,
            "aircraft_type": aircraft_type, "registration": registration,
            "std": std, "sta": sta, "etd": etd, "eta": eta,
            "block_time": block_time, "economy_count": economy_count,
            "premium_count": premium_count,
        })
        await self.refresh_board()

        embed = discord.Embed(
            title="Flight Created", color=0x003B6F,
            description=(
                f"**{doc['flight_number']}** {doc['origin']} → {doc['destination']} added to the board.\n"
                f"**Date:** {doc['date']} · **STD:** {std} · **STA:** {sta}"
            )
        )
        embed.set_footer(text="Icelandair Operations", icon_url="https://www.icelandair.com/favicon.ico")
        await interaction.followup.send(embed=embed, ephemeral=True)

    # ── /flight-update ────────────────────────────────────────────────────────
    @app_commands.command(name="flight-update", description="[Dispatcher] Update a flight's details or status")
    @app_commands.guilds(discord.Object(id=GUILD_ID))
    async def flight_update(
        self, interaction: discord.Interaction,
        flight_number: str, status: str = None,
        etd: str = None, eta: str = None,
        atd: str = None, ata: str = None,
        economy_count: int = None, premium_count: int = None,
        checkin_open: bool = None, block_time: str = None,
        registration: str = None,
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
        if status:                        updates["status"]         = status
        if etd:                           updates["etd"]            = etd
        if eta:                           updates["eta"]            = eta
        if atd:                           updates["atd"]            = atd
        if ata:                           updates["ata"]            = ata
        if block_time:                    updates["block_time"]     = block_time
        if registration:                  updates["registration"]   = registration
        if economy_count is not None:     updates["economy_count"]  = economy_count
        if premium_count is not None:     updates["premium_count"]  = premium_count
        if checkin_open is not None:      updates["checkin_open"]   = checkin_open

        if not updates:
            await interaction.followup.send("No updates provided.", ephemeral=True)
            return

        updated = await update_flight(flight_number, updates)
        await self.refresh_board()

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
        return [
            app_commands.Choice(name=s, value=s)
            for s in STATUSES if current.lower() in s.lower()
        ]

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

        embed = discord.Embed(
            title=f"Cancel {flight_number.upper()}",
            description="Select a cancellation reason from the dropdown below.",
            color=STATUS_COLORS["Cancelled"],
        )
        embed.set_footer(text="Icelandair Operations", icon_url="https://www.icelandair.com/favicon.ico")
        view = ReasonSelectView(flight_number.upper(), "cancel", cog=self)
        await interaction.followup.send(embed=embed, view=view, ephemeral=True)

    # ── /flight-delay ─────────────────────────────────────────────────────────
    @app_commands.command(name="flight-delay", description="[Dispatcher] Delay a flight with a reason and new ETD")
    @app_commands.guilds(discord.Object(id=GUILD_ID))
    async def flight_delay(
        self, interaction: discord.Interaction,
        flight_number: str,
        new_etd: str = None,
    ):
        await interaction.response.defer(ephemeral=True)
        if not is_dispatcher(interaction):
            await interaction.followup.send("You don't have permission to use this command.", ephemeral=True)
            return

        flight = await get_flight(flight_number)
        if not flight:
            await interaction.followup.send(f"Flight `{flight_number.upper()}` not found.", ephemeral=True)
            return

        etd_str = f" · New ETD: **{new_etd}**" if new_etd else ""
        embed = discord.Embed(
            title=f"Delay {flight_number.upper()}",
            description=f"Select a delay reason from the dropdown below.{etd_str}",
            color=STATUS_COLORS["Delayed"],
        )
        embed.set_footer(text="Icelandair Operations", icon_url="https://www.icelandair.com/favicon.ico")
        view = ReasonSelectView(flight_number.upper(), "delay", etd=new_etd, cog=self)
        await interaction.followup.send(embed=embed, view=view, ephemeral=True)

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
