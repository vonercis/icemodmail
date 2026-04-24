import discord
from discord.ext import commands, tasks
from discord import app_commands
import os
from datetime import datetime, timezone, timedelta
from database_bookings import get_all_bookings_for_user
from database import (
    get_member, create_member, add_points, set_points, log_flight,
    set_upgrades, use_upgrade, add_note, delete_note,
    get_member_by_username, TIER_THRESHOLDS, EARNING_MULTIPLIERS, members as members_col
)
from bloxlink_api import get_roblox_user
from dotenv import load_dotenv
import math

load_dotenv()

GUILD_ID      = int(os.getenv("DISCORD_GUILD_ID"))
STAFF_ROLE_ID = int(os.getenv("STAFF_ROLE_ID", 0))

TIER_COLORS = {
    "blue":   0x001B71,
    "silver": 0xD6CFD5,
    "gold":   0xEBE4C1,
}

TIER_LABELS = {
    "blue":   "<:sagablue1:1497039979141005342><:sagablue2:1497039949940134098>",
    "silver": "<:sagasilver1:1497040977980817419><:sagasilver2:1497041027905618061>",
    "gold":   "<:sagagold1:1497040600279678986><:sagagold2:1497040638091198567>",
}

TIER_NEXT = {
    "blue":   ("silver", 40000),
    "silver": ("gold",   80000),
    "gold":   (None,     None),
}

TIER_CONGRATS = {
    "silver": (
        "<:sagasilver1:1497040977980817419><:sagasilver2:1497041027905618061> **Congratulations — you've reached Saga Silver!**\n\n"
        "As a Saga Silver member you now have access to:\n"
        "<:lbcheckin:1374689021472669738> Priority check-in\n"
        "<:lblounge:1374689001151135877> Lounge access\n"
        "<:lbcarryon:1374689023389597797> Extra baggage allowance\n"
        "<:lbwalking:1374689019593756842> Priority boarding\n"
        "<:dbtakeoffbg:1374617776504832001> 2× complimentary upgrades per month\n"
        "<:lbrecline:1374689009900458015> 2 complimentary upgrades to Saga Class per month\n"
        "<:dbstarcard:1374694940856025128> 1.5× Saga Points on Economy Flex fares\n"
        "<:dbstarcard:1374694940856025128> 2× Saga Points on Saga Class fares\n\n"
        "Thank you for flying with Icelandair. We look forward to welcoming you on board."
    ),
    "gold": (
        "<:sagagold1:1497040600279678986><:sagagold2:1497040638091198567> **Congratulations — you've reached Saga Gold!**\n\n"
        "As a Saga Gold member — our highest tier — you now have access to:\n"
        "<:lbcheckin:1374689021472669738> Priority check-in\n"
        "<:lblounge:1374689001151135877> Lounge access\n"
        "<:lbcarryon:1374689023389597797> Extra baggage allowance\n"
        "<:lbwalking:1374689019593756842> Priority boarding\n"
        "<:lbwifi:1374689006289424425> Complimentary Wi-Fi on every flight\n"
        "<:lbstarcard:1374688997132996681> Companion card — a designated member shares your tier benefits\n"
        "<:lbrecline:1374689009900458015> 5 complimentary upgrades to Saga Class per month\n"
        "<:dbpersonbg:1374617772855660637> Dedicated Gold member customer service line\n"
        "<:dbstarcard:1374694940856025128> 2× Saga Points multiplier on all fares\n\n"
        "Thank you for your loyalty to Icelandair. We're honoured to have you as a Gold member."
    ),
}

FARE_CLASS_LABELS = {
    "economy_standard": "Economy Standard",
    "economy_flex":     "Economy Flex",
    "saga_premium":     "Saga Premium",
    "partner_flight":   "Partner Flight",
}

FLIGHTS_PER_PAGE = 5
NOTES_PER_PAGE   = 5


def is_staff(interaction: discord.Interaction) -> bool:
    if not STAFF_ROLE_ID:
        return interaction.user.guild_permissions.manage_roles
    return any(r.id == STAFF_ROLE_ID for r in interaction.user.roles)


def build_progress_bar(current: int, target: int, length: int = 10) -> str:
    if target is None:
        return "▓" * length + " Max Tier"
    ratio   = min(current / target, 1.0)
    filled  = math.floor(ratio * length)
    empty   = length - filled
    percent = int(ratio * 100)
    return f"{'▓' * filled}{'░' * empty} {percent}%"


def build_profile_embed(doc: dict) -> discord.Embed:
    tier        = doc.get("tier", "blue")
    tc          = doc.get("tier_credits", 0)
    points      = doc.get("saga_points", 0)
    flights     = doc.get("flights_completed", 0)
    last_flight = doc.get("last_flight")
    expiry      = doc.get("points_expiry")
    since       = doc.get("member_since")
    upgrades    = doc.get("complimentary_upgrades", 0)

    next_tier, next_threshold = TIER_NEXT[tier]
    if next_threshold:
        tc_display = f"{tc:,} TC\n{build_progress_bar(tc, next_threshold)} to {next_tier.title()}\n{next_threshold:,} TC required"
    else:
        tc_display = f"{tc:,} TC\n{build_progress_bar(tc, None)}"

    if last_flight:
        last_flight_str = f"{flights:,} flights\n*Last: {last_flight['origin']} → {last_flight['destination']}*"
    else:
        last_flight_str = f"{flights:,} flights"

    expiry_str = expiry.strftime("%-d %b %Y") if expiry else "N/A"
    since_str  = since.strftime("%-d %b %Y") if since else "N/A"

    embed = discord.Embed(
        title="Saga Club Profile",
        description="Icelandair Frequent Flyer",
        color=TIER_COLORS[tier],
    )
    embed.set_thumbnail(url="https://www.icelandair.com/favicon.ico")

    embed.add_field(name="<:dbpersonbg:1374617772855660637> Member Name",       value=doc.get("roblox_username", "Unknown"), inline=True)
    embed.add_field(name="<:dbsagacard:1374617767097008148> Saga Club No.",      value=f"`{doc.get('saga_number', 'N/A')}`",  inline=True)
    embed.add_field(name="<:dbcalenderbg:1374617779067551786> Member Since",     value=since_str,                             inline=True)
    embed.add_field(name="Membership Tier",                                       value=TIER_LABELS[tier],                     inline=True)
    embed.add_field(name="\u200b",                                                value="\u200b",                              inline=True)
    embed.add_field(name="\u200b",                                                value="\u200b",                              inline=True)
    embed.add_field(name="<:dbtakeoffbg:1374617776504832001> Tier Credits",      value=tc_display,                            inline=False)
    embed.add_field(name="<:dbstarcard:1374694940856025128> Saga Points",        value=f"{points:,} pts\n*Expires {expiry_str}*", inline=True)
    embed.add_field(name="<:dbtakeoffbg:1374617776504832001> Flights Completed", value=last_flight_str,                       inline=True)

    if tier == "blue":
        embed.add_field(
            name="<:sagablue1:1497039979141005342><:sagablue2:1497039949940134098> Blue Benefits",
            value=(
                "<:dbsagacard:1374617767097008148> Access to the Saga Club programme\n"
                "<:dbstarcard:1374694940856025128> Earn Saga Points on every flight (1× multiplier)\n"
                "<:dbcalenderbg:1374617779067551786> Points valid for 4 years\n"
                "<:lbseated:1374689017777492019> Ability to book Economy class flights\n"
                "<:dbtakeoffbg:1374617776504832001> Access to the flight board and booking system"
            ),
            inline=False
        )

    if tier in ("silver", "gold"):
        upgrade_str    = "Unlimited" if tier == "gold" else f"{upgrades} remaining this month"
        saga_remaining = doc.get("saga_class_flights_remaining", 0)
        saga_allowance = {"silver": 2, "gold": 5}.get(tier, 0)
        saga_str       = f"{saga_remaining} complimentary upgrade(s) remaining this month"
        benefits = {
            "silver": (
                "<:lbcheckin:1374689021472669738> Priority check-in\n"
                "<:lblounge:1374689001151135877> Lounge access\n"
                "<:lbcarryon:1374689023389597797> Extra baggage allowance\n"
                "<:lbwalking:1374689019593756842> Priority boarding\n"
                f"<:dbtakeoffbg:1374617776504832001> Complimentary upgrades: {upgrade_str}\n"
                f"<:lbrecline:1374689009900458015> Complimentary upgrades (Saga Class): {saga_str}\n"
                "<:dbstarcard:1374694940856025128> 1.5× Saga Points on Economy Flex fares\n"
                "<:dbstarcard:1374694940856025128> 2× Saga Points on Saga Class fares"
            ),
            "gold": (
                "<:lbcheckin:1374689021472669738> Priority check-in\n"
                "<:lblounge:1374689001151135877> Lounge access\n"
                "<:lbcarryon:1374689023389597797> Extra baggage allowance\n"
                "<:lbwalking:1374689019593756842> Priority boarding\n"
                "<:lbwifi:1374689006289424425> Complimentary Wi-Fi on every flight\n"
                "<:lbstarcard:1374688997132996681> Companion card — a designated member shares your tier benefits\n"
                f"<:lbrecline:1374689009900458015> Complimentary upgrades (Saga Class): {saga_str}\n"
                "<:dbpersonbg:1374617772855660637> Dedicated Gold member customer service line\n"
                "<:dbstarcard:1374694940856025128> 2× Saga Points multiplier on all fares"
            ),
        }
        embed.add_field(
            name=f"<:dbpenbg:1374617771010424912> {tier.title()} Benefits",
            value=benefits[tier],
            inline=False
        )

    embed.set_footer(
        text="Icelandair Saga Club • Points valid for 4 years",
        icon_url="https://www.icelandair.com/favicon.ico"
    )
    embed.timestamp = datetime.now(timezone.utc)
    return embed


def build_history_embed(doc: dict, page: int) -> tuple[discord.Embed, int]:
    history     = list(reversed(doc.get("flight_history", [])))
    total_pages = max(1, math.ceil(len(history) / FLIGHTS_PER_PAGE))
    page        = max(1, min(page, total_pages))
    start       = (page - 1) * FLIGHTS_PER_PAGE
    slice_      = history[start:start + FLIGHTS_PER_PAGE]

    tier  = doc.get("tier", "blue")
    embed = discord.Embed(
        title=f"Flight History — {doc.get('roblox_username', 'Unknown')}",
        description=f"Showing page {page} of {total_pages}",
        color=TIER_COLORS[tier],
    )
    embed.set_thumbnail(url="https://www.icelandair.com/favicon.ico")

    if not slice_:
        embed.add_field(name="No flights yet", value="Complete a flight to see your history here.", inline=False)
    else:
        for f in slice_:
            date_str   = f["date"].strftime("%-d %b %Y") if f.get("date") else "N/A"
            fare_label = FARE_CLASS_LABELS.get(f.get("fare_class", ""), f.get("fare_class", "N/A"))
            embed.add_field(
                name=f"<:dbtakeoffbg:1374617776504832001> {f['origin']} → {f['destination']}",
                value=(
                    f"**Date:** {date_str}\n"
                    f"**Aircraft:** {f.get('aircraft', 'N/A')}\n"
                    f"**Fare class:** {fare_label}\n"
                    f"**Points earned:** {f.get('points_earned', 0):,} pts"
                ),
                inline=False
            )

    embed.set_footer(
        text=f"Icelandair Saga Club • Page {page}/{total_pages}",
        icon_url="https://www.icelandair.com/favicon.ico"
    )
    embed.timestamp = datetime.now(timezone.utc)
    return embed, total_pages


def build_notes_embed(doc: dict, page: int) -> tuple[discord.Embed, int]:
    notes       = doc.get("internal_notes", [])
    total_pages = max(1, math.ceil(len(notes) / NOTES_PER_PAGE))
    page        = max(1, min(page, total_pages))
    start       = (page - 1) * NOTES_PER_PAGE
    slice_      = notes[start:start + NOTES_PER_PAGE]

    embed = discord.Embed(
        title=f"Internal Notes — {doc.get('roblox_username', 'Unknown')}",
        description=f"Page {page} of {total_pages}",
        color=0x444441,
    )

    if not slice_:
        embed.add_field(name="No notes", value="No internal notes have been added yet.", inline=False)
    else:
        for i, note in enumerate(slice_):
            created = note.get("created_at")
            date_str = created.strftime("%-d %b %Y %H:%M") if created else "N/A"
            embed.add_field(
                name=f"Note #{start + i + 1} — {note.get('staff', 'Unknown')} • {date_str}",
                value=note.get("text", ""),
                inline=False
            )

    embed.set_footer(text="Icelandair Saga Club — Internal Use Only")
    embed.timestamp = datetime.now(timezone.utc)
    return embed, total_pages


async def send_tier_congrats(bot: commands.Bot, discord_id: int, new_tier: str):
    if new_tier not in TIER_CONGRATS:
        return
    try:
        user = await bot.fetch_user(discord_id)
        embed = discord.Embed(
            title="Icelandair Saga Club",
            description=TIER_CONGRATS[new_tier],
            color=TIER_COLORS[new_tier],
        )
        embed.set_thumbnail(url="https://www.icelandair.com/favicon.ico")
        embed.set_footer(
            text="Icelandair Saga Club",
            icon_url="https://www.icelandair.com/favicon.ico"
        )
        embed.timestamp = datetime.now(timezone.utc)
        await user.send(embed=embed)
    except Exception as e:
        print(f"[Saga] Could not DM tier congrats to {discord_id}: {e}")



HISTORY_PER_PAGE = 5


def build_booking_upgrade_history_embed(doc: dict, all_bookings: list, page: int) -> tuple[discord.Embed, int]:
    """Combined booking and upgrade history embed for staff profile view."""
    tier     = doc.get("tier", "blue")
    username = doc.get("roblox_username", "Unknown")

    # Build combined entries: bookings + upgrade usage from flight history
    entries = []

    for b in all_bookings:
        status_emoji = "✅" if b.get("status") == "Confirmed" else "❌"
        date_str     = b["booked_at"].strftime("%-d %b %Y") if b.get("booked_at") else "N/A"
        entries.append({
            "type":  "booking",
            "emoji": status_emoji,
            "title": f"`{b['booking_ref']}` — {b['flight_number']}",
            "value": f"**Cabin:** {b['cabin']} · **Status:** {b.get('status','—')} · **Date:** {date_str}",
            "date":  b.get("booked_at"),
        })

    # Pull upgrade usage from flight history
    for f in reversed(doc.get("flight_history", [])):
        if f.get("fare_class") == "saga_premium":
            date_str = f["date"].strftime("%-d %b %Y") if f.get("date") else "N/A"
            entries.append({
                "type":  "upgrade",
                "emoji": "<:lbrecline:1374689009900458015>",
                "title": f"Saga Class — {f.get('origin','?')} → {f.get('destination','?')}",
                "value": f"**Aircraft:** {f.get('aircraft','—')} · **Date:** {date_str} · **Points earned:** {f.get('points_earned',0):,}",
                "date":  f.get("date"),
            })

    # Sort combined by date descending
    entries.sort(key=lambda x: x["date"] or datetime.min.replace(tzinfo=timezone.utc), reverse=True)

    total_pages = max(1, math.ceil(len(entries) / HISTORY_PER_PAGE))
    page        = max(1, min(page, total_pages))
    start       = (page - 1) * HISTORY_PER_PAGE
    slice_      = entries[start:start + HISTORY_PER_PAGE]

    embed = discord.Embed(
        title=f"Booking & Upgrade History — {username}",
        description=f"Page {page} of {total_pages} · {len(entries)} total entries",
        color=TIER_COLORS[tier],
    )

    if not slice_:
        embed.add_field(name="No history", value="No bookings or upgrades found.", inline=False)
    else:
        for e in slice_:
            embed.add_field(name=f"{e['emoji']} {e['title']}", value=e["value"], inline=False)

    embed.set_footer(
        text="Icelandair Saga Club — Internal Use Only",
        icon_url="https://www.icelandair.com/favicon.ico"
    )
    embed.timestamp = datetime.now(timezone.utc)
    return embed, total_pages


class BookingUpgradeHistoryView(discord.ui.View):
    def __init__(self, doc: dict, all_bookings: list, page: int = 1):
        super().__init__(timeout=120)
        self.doc          = doc
        self.all_bookings = all_bookings
        self.page         = page
        self._update_buttons()

    def _update_buttons(self):
        _, total_pages = build_booking_upgrade_history_embed(self.doc, self.all_bookings, self.page)
        self.prev_button.disabled = self.page <= 1
        self.next_button.disabled = self.page >= total_pages

    @discord.ui.button(label="◀ Previous", style=discord.ButtonStyle.secondary)
    async def prev_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.page -= 1
        self._update_buttons()
        embed, _ = build_booking_upgrade_history_embed(self.doc, self.all_bookings, self.page)
        await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(label="Next ▶", style=discord.ButtonStyle.secondary)
    async def next_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.page += 1
        self._update_buttons()
        embed, _ = build_booking_upgrade_history_embed(self.doc, self.all_bookings, self.page)
        await interaction.response.edit_message(embed=embed, view=self)

class FlightHistoryView(discord.ui.View):
    def __init__(self, doc: dict, page: int = 1):
        super().__init__(timeout=120)
        self.doc  = doc
        self.page = page
        self._update_buttons()

    def _update_buttons(self):
        _, total_pages = build_history_embed(self.doc, self.page)
        self.prev_button.disabled = self.page <= 1
        self.next_button.disabled = self.page >= total_pages

    @discord.ui.button(label="◀ Previous", style=discord.ButtonStyle.secondary)
    async def prev_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.page -= 1
        self._update_buttons()
        embed, _ = build_history_embed(self.doc, self.page)
        await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(label="Next ▶", style=discord.ButtonStyle.secondary)
    async def next_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.page += 1
        self._update_buttons()
        embed, _ = build_history_embed(self.doc, self.page)
        await interaction.response.edit_message(embed=embed, view=self)


class NotesView(discord.ui.View):
    def __init__(self, doc: dict, page: int = 1):
        super().__init__(timeout=120)
        self.doc  = doc
        self.page = page
        self._update_buttons()

    def _update_buttons(self):
        _, total_pages = build_notes_embed(self.doc, self.page)
        self.prev_button.disabled = self.page <= 1
        self.next_button.disabled = self.page >= total_pages

    @discord.ui.button(label="◀ Previous", style=discord.ButtonStyle.secondary)
    async def prev_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.page -= 1
        self._update_buttons()
        embed, _ = build_notes_embed(self.doc, self.page)
        await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(label="Next ▶", style=discord.ButtonStyle.secondary)
    async def next_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.page += 1
        self._update_buttons()
        embed, _ = build_notes_embed(self.doc, self.page)
        await interaction.response.edit_message(embed=embed, view=self)


class ProfileView(discord.ui.View):
    def __init__(self, doc: dict, show_notes_button: bool = False):
        super().__init__(timeout=120)
        self.doc = doc
        if not show_notes_button:
            self.remove_item(self.notes_button)
            self.remove_item(self.booking_history_button)

    @discord.ui.button(label="✈ Flight History", style=discord.ButtonStyle.primary)
    async def history_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        embed, _ = build_history_embed(self.doc, page=1)
        view     = FlightHistoryView(self.doc, page=1)
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

    @discord.ui.button(label="🗂 Booking & Upgrade History", style=discord.ButtonStyle.secondary)
    async def booking_history_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not is_staff(interaction):
            await interaction.response.send_message("You don't have permission to view this.", ephemeral=True)
            return
        discord_id  = self.doc.get("discord_id")
        all_bookings = await get_all_bookings_for_user(discord_id)
        embed, _    = build_booking_upgrade_history_embed(self.doc, all_bookings, page=1)
        view        = BookingUpgradeHistoryView(self.doc, all_bookings, page=1)
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

    @discord.ui.button(label="🔒 Internal Notes", style=discord.ButtonStyle.danger)
    async def notes_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not is_staff(interaction):
            await interaction.response.send_message("You don't have permission to view internal notes.", ephemeral=True)
            return
        embed, _ = build_notes_embed(self.doc, page=1)
        view     = NotesView(self.doc, page=1)
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)


class SagaCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.monthly_upgrade_reset.start()

    def cog_unload(self):
        self.monthly_upgrade_reset.cancel()

    @tasks.loop(hours=24)
    async def monthly_upgrade_reset(self):
        """Resets Silver members' complimentary upgrades to 1 on the 1st of each month."""
        now = datetime.now(timezone.utc)
        if now.day != 1:
            return
        await members_col.update_many(
            {"tier": "silver"},
            {"$set": {"complimentary_upgrades": 1, "upgrade_last_reset": now}}
        )
        print(f"[Saga] Monthly upgrade reset complete for Silver members")

    @monthly_upgrade_reset.before_loop
    async def before_reset(self):
        await self.bot.wait_until_ready()

    async def get_verified_member(self, interaction: discord.Interaction) -> dict | None:
        roblox = await get_roblox_user(interaction.user.id)
        if not roblox:
            embed = discord.Embed(
                title="Verification Required",
                description=(
                    "You must be verified with Bloxlink before using Saga Club commands.\n\n"
                    "Please verify your Roblox account at [blox.link](https://blox.link) and try again."
                ),
                color=0xE24B4A,
            )
            embed.set_footer(text="Icelandair Saga Club", icon_url="https://www.icelandair.com/favicon.ico")
            if interaction.response.is_done():
                await interaction.followup.send(embed=embed, ephemeral=True)
            else:
                await interaction.response.send_message(embed=embed, ephemeral=True)
            return None

        doc = await get_member(interaction.user.id)
        if not doc:
            doc = await create_member(interaction.user.id, roblox["roblox_id"], roblox["roblox_username"])
            bloxlink_cog = self.bot.cogs.get("BloxlinkCog")
            if bloxlink_cog:
                await bloxlink_cog.update_member_tier(interaction.user.id, doc["tier"])
        return doc

    async def handle_tier_change(self, discord_id: int, old_tier: str, new_tier: str):
        """Handles role update and DM congratulations on tier change."""
        if old_tier == new_tier:
            return
        bloxlink_cog = self.bot.cogs.get("BloxlinkCog")
        if bloxlink_cog:
            await bloxlink_cog.update_member_tier(discord_id, new_tier)
        if new_tier in ("silver", "gold") and (
            new_tier == "gold" or old_tier == "blue"
        ):
            await send_tier_congrats(self.bot, discord_id, new_tier)

    # ── /saga-profile ────────────────────────────────────────────────────────
    @app_commands.command(name="saga-profile", description="View your Icelandair Saga Club profile")
    @app_commands.guilds(discord.Object(id=GUILD_ID))
    async def saga_profile(self, interaction: discord.Interaction):
        await interaction.response.defer()
        doc = await self.get_verified_member(interaction)
        if not doc:
            return
        embed = build_profile_embed(doc)
        view  = ProfileView(doc, show_notes_button=is_staff(interaction))
        await interaction.followup.send(embed=embed, view=view)

    # ── /saga-search ─────────────────────────────────────────────────────────
    @app_commands.command(name="saga-search", description="[Staff] Search for a member's Saga Club profile")
    @app_commands.guilds(discord.Object(id=GUILD_ID))
    async def saga_search(self, interaction: discord.Interaction, roblox_username: str):
        await interaction.response.defer(ephemeral=True)
        if not is_staff(interaction):
            await interaction.followup.send("You don't have permission to use this command.", ephemeral=True)
            return
        doc = await get_member_by_username(roblox_username)
        if not doc:
            await interaction.followup.send(f"No Saga Club profile found for `{roblox_username}`.", ephemeral=True)
            return
        embed = build_profile_embed(doc)
        view  = ProfileView(doc, show_notes_button=True)
        await interaction.followup.send(embed=embed, view=view, ephemeral=True)

    # ── /saga-add ────────────────────────────────────────────────────────────
    @app_commands.command(name="saga-add", description="[Staff] Add Saga Points and Tier Credits to a member")
    @app_commands.guilds(discord.Object(id=GUILD_ID))
    async def saga_add(self, interaction: discord.Interaction, member: discord.Member, points: int, tier_credits: int):
        await interaction.response.defer(ephemeral=True)
        if not is_staff(interaction):
            await interaction.followup.send("You don't have permission to use this command.", ephemeral=True)
            return
        doc = await get_member(member.id)
        if not doc:
            await interaction.followup.send("That member does not have a Saga Club profile yet.", ephemeral=True)
            return
        updated = await add_points(member.id, points, tier_credits)
        await self.handle_tier_change(member.id, updated["old_tier"], updated["tier"])

        embed = discord.Embed(
            title="Saga Points Updated",
            color=TIER_COLORS[updated["tier"]],
            description=(
                f"**Member:** {doc.get('roblox_username')}\n"
                f"**Points added:** +{points:,} pts\n"
                f"**TC added:** +{tier_credits:,} TC\n"
                f"**New balance:** {updated['saga_points']:,} pts\n"
                f"**New TC:** {updated['tier_credits']:,} TC\n"
                f"**Tier:** {TIER_LABELS[updated['tier']]}"
            )
        )
        embed.set_footer(text="Icelandair Saga Club", icon_url="https://www.icelandair.com/favicon.ico")
        await interaction.followup.send(embed=embed, ephemeral=True)

    # ── /saga-set ────────────────────────────────────────────────────────────
    @app_commands.command(name="saga-set", description="[Staff] Set a member's Saga Points or Tier Credits to a specific value")
    @app_commands.guilds(discord.Object(id=GUILD_ID))
    async def saga_set(self, interaction: discord.Interaction, member: discord.Member, points: int = None, tier_credits: int = None):
        await interaction.response.defer(ephemeral=True)
        if not is_staff(interaction):
            await interaction.followup.send("You don't have permission to use this command.", ephemeral=True)
            return
        if points is None and tier_credits is None:
            await interaction.followup.send("Please provide at least one value to set.", ephemeral=True)
            return
        doc = await get_member(member.id)
        if not doc:
            await interaction.followup.send("That member does not have a Saga Club profile yet.", ephemeral=True)
            return
        updated = await set_points(member.id, points=points, tier_credits=tier_credits)
        await self.handle_tier_change(member.id, updated["old_tier"], updated["tier"])

        embed = discord.Embed(
            title="Saga Profile Updated",
            color=TIER_COLORS[updated["tier"]],
            description=(
                f"**Member:** {doc.get('roblox_username')}\n"
                f"**Saga Points:** {updated.get('saga_points', doc['saga_points']):,} pts\n"
                f"**Tier Credits:** {updated.get('tier_credits', doc['tier_credits']):,} TC\n"
                f"**Tier:** {TIER_LABELS[updated['tier']]}"
            )
        )
        embed.set_footer(text="Icelandair Saga Club", icon_url="https://www.icelandair.com/favicon.ico")
        await interaction.followup.send(embed=embed, ephemeral=True)

    # ── /saga-logflight ──────────────────────────────────────────────────────
    @app_commands.command(name="saga-logflight", description="[Staff] Log a completed flight for a member")
    @app_commands.guilds(discord.Object(id=GUILD_ID))
    async def saga_logflight(
        self, interaction: discord.Interaction,
        member: discord.Member, origin: str, destination: str,
        aircraft: str, fare_class: str, base_points: int,
    ):
        await interaction.response.defer(ephemeral=True)
        if not is_staff(interaction):
            await interaction.followup.send("You don't have permission to use this command.", ephemeral=True)
            return
        doc = await get_member(member.id)
        if not doc:
            await interaction.followup.send("That member does not have a Saga Club profile yet.", ephemeral=True)
            return
        updated = await log_flight(member.id, origin, destination, fare_class, aircraft, base_points)
        await self.handle_tier_change(member.id, updated["old_tier"], updated["tier"])

        embed = discord.Embed(
            title="Flight Logged",
            color=TIER_COLORS[updated["tier"]],
            description=(
                f"**Member:** {doc.get('roblox_username')}\n"
                f"**Route:** {origin.upper()} → {destination.upper()}\n"
                f"**Aircraft:** {aircraft}\n"
                f"**Fare class:** {FARE_CLASS_LABELS.get(fare_class, fare_class)}\n"
                f"**Points earned:** +{updated['points_earned']:,} pts\n"
                f"**New balance:** {updated['saga_points']:,} pts\n"
                f"**Tier:** {TIER_LABELS[updated['tier']]}"
            )
        )
        embed.set_footer(text="Icelandair Saga Club", icon_url="https://www.icelandair.com/favicon.ico")
        await interaction.followup.send(embed=embed, ephemeral=True)

    @saga_logflight.autocomplete("fare_class")
    async def fare_class_autocomplete(self, interaction: discord.Interaction, current: str):
        return [
            app_commands.Choice(name=label, value=value)
            for value, label in FARE_CLASS_LABELS.items()
            if current.lower() in label.lower()
        ]

    # ── /saga-upgrade ────────────────────────────────────────────────────────
    @app_commands.command(name="saga-upgrade", description="Use a complimentary upgrade on your booking")
    @app_commands.guilds(discord.Object(id=GUILD_ID))
    async def saga_upgrade(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        doc = await self.get_verified_member(interaction)
        if not doc:
            return
        tier = doc.get("tier", "blue")
        if tier == "blue":
            await interaction.followup.send("Complimentary upgrades are available for Saga Silver and Gold members only.", ephemeral=True)
            return
        if tier == "gold":
            await interaction.followup.send("<:dbtakeoffbg:1374617776504832001> As a Saga Gold member, your complimentary upgrades are unlimited. Please contact staff to apply your upgrade.", ephemeral=True)
            return
        result = await use_upgrade(interaction.user.id)
        if result is None:
            await interaction.followup.send("You have no complimentary upgrades remaining this month. They reset on the 1st of each month.", ephemeral=True)
            return
        remaining = result.get("complimentary_upgrades", 0)
        embed = discord.Embed(
            title="<:dbtakeoffbg:1374617776504832001> Upgrade Applied",
            description=f"Your complimentary upgrade has been used.\n**Remaining this month:** {remaining}",
            color=TIER_COLORS[tier],
        )
        embed.set_footer(text="Icelandair Saga Club", icon_url="https://www.icelandair.com/favicon.ico")
        await interaction.followup.send(embed=embed, ephemeral=True)

    # ── /saga-set-upgrades ───────────────────────────────────────────────────
    @app_commands.command(name="saga-set-upgrades", description="[Staff] Manually set a member's complimentary upgrade count")
    @app_commands.guilds(discord.Object(id=GUILD_ID))
    async def saga_set_upgrades(self, interaction: discord.Interaction, member: discord.Member, count: int):
        await interaction.response.defer(ephemeral=True)
        if not is_staff(interaction):
            await interaction.followup.send("You don't have permission to use this command.", ephemeral=True)
            return
        doc = await get_member(member.id)
        if not doc:
            await interaction.followup.send("That member does not have a Saga Club profile yet.", ephemeral=True)
            return
        updated = await set_upgrades(member.id, count)
        embed = discord.Embed(
            title="Upgrades Updated",
            color=TIER_COLORS[doc.get("tier", "blue")],
            description=(
                f"**Member:** {doc.get('roblox_username')}\n"
                f"**Complimentary upgrades set to:** {count}"
            )
        )
        embed.set_footer(text="Icelandair Saga Club", icon_url="https://www.icelandair.com/favicon.ico")
        await interaction.followup.send(embed=embed, ephemeral=True)

    # ── /saga-note-add ───────────────────────────────────────────────────────
    @app_commands.command(name="saga-note-add", description="[Staff] Add an internal note to a member's profile")
    @app_commands.guilds(discord.Object(id=GUILD_ID))
    async def saga_note_add(self, interaction: discord.Interaction, member: discord.Member, note: str):
        await interaction.response.defer(ephemeral=True)
        if not is_staff(interaction):
            await interaction.followup.send("You don't have permission to use this command.", ephemeral=True)
            return
        doc = await get_member(member.id)
        if not doc:
            await interaction.followup.send("That member does not have a Saga Club profile yet.", ephemeral=True)
            return
        await add_note(member.id, note, interaction.user.display_name)
        embed = discord.Embed(
            title="Note Added",
            color=0x444441,
            description=f"**Member:** {doc.get('roblox_username')}\n**Note:** {note}"
        )
        embed.set_footer(text="Icelandair Saga Club — Internal Use Only")
        await interaction.followup.send(embed=embed, ephemeral=True)

    # ── /saga-note-delete ────────────────────────────────────────────────────
    @app_commands.command(name="saga-note-delete", description="[Staff] Delete an internal note by number")
    @app_commands.guilds(discord.Object(id=GUILD_ID))
    async def saga_note_delete(self, interaction: discord.Interaction, member: discord.Member, note_number: int):
        await interaction.response.defer(ephemeral=True)
        if not is_staff(interaction):
            await interaction.followup.send("You don't have permission to use this command.", ephemeral=True)
            return
        doc = await get_member(member.id)
        if not doc:
            await interaction.followup.send("That member does not have a Saga Club profile yet.", ephemeral=True)
            return
        updated = await delete_note(member.id, note_number - 1)
        if not updated:
            await interaction.followup.send(f"Note #{note_number} not found.", ephemeral=True)
            return
        await interaction.followup.send(f"Note #{note_number} deleted from {doc.get('roblox_username')}'s profile.", ephemeral=True)

    # ── /saga-leaderboard ────────────────────────────────────────────────────
    @app_commands.command(name="saga-leaderboard", description="View the Saga Club points leaderboard")
    @app_commands.guilds(discord.Object(id=GUILD_ID))
    async def saga_leaderboard(self, interaction: discord.Interaction):
        await interaction.response.defer()
        cursor = members_col.find().sort("saga_points", -1).limit(10)
        docs   = await cursor.to_list(length=10)

        embed = discord.Embed(
            title="<:dbstarcard:1374694940856025128> Saga Club Leaderboard",
            description="Top 10 members by Saga Points",
            color=0x003B6F,
        )
        embed.set_thumbnail(url="https://www.icelandair.com/favicon.ico")

        medals = ["🥇", "🥈", "🥉"]
        for i, doc in enumerate(docs):
            prefix = medals[i] if i < 3 else f"`#{i+1}`"
            embed.add_field(
                name=f"{prefix} {doc.get('roblox_username', 'Unknown')}",
                value=f"{TIER_LABELS[doc.get('tier', 'blue')]} • {doc.get('saga_points', 0):,} pts",
                inline=False,
            )

        embed.set_footer(text="Icelandair Saga Club", icon_url="https://www.icelandair.com/favicon.ico")
        embed.timestamp = datetime.now(timezone.utc)
        await interaction.followup.send(embed=embed)


async def setup(bot: commands.Bot):
    await bot.add_cog(SagaCog(bot))
