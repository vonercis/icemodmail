import discord
from discord.ext import commands
from discord import app_commands
import os
from datetime import datetime, timezone
from database import (
    get_member, create_member, add_points, set_points,
    log_flight, TIER_THRESHOLDS, EARNING_MULTIPLIERS
)
from bloxlink import get_roblox_user
from dotenv import load_dotenv
import math

load_dotenv()

GUILD_ID = int(os.getenv("DISCORD_GUILD_ID"))

TIER_COLORS = {
    "blue":   0x003B6F,
    "silver": 0xD3D1C7,
    "gold":   0xFFA500,
}

TIER_LABELS = {
    "blue":   "● Saga Blue",
    "silver": "● Saga Silver",
    "gold":   "● Saga Gold",
}

TIER_NEXT = {
    "blue":   ("silver", 40000),
    "silver": ("gold",   80000),
    "gold":   (None,     None),
}

FARE_CLASS_LABELS = {
    "economy_standard": "Economy Standard",
    "economy_flex":     "Economy Flex",
    "saga_premium":     "Saga Premium",
    "partner_flight":   "Partner Flight",
}

FLIGHTS_PER_PAGE = 5


def build_progress_bar(current: int, target: int, length: int = 10) -> str:
    if target is None:
        return "▓" * length + " Max Tier"
    ratio    = min(current / target, 1.0)
    filled   = math.floor(ratio * length)
    empty    = length - filled
    percent  = int(ratio * 100)
    return f"{'▓' * filled}{'░' * empty} {percent}%"


def build_profile_embed(doc: dict) -> discord.Embed:
    tier        = doc.get("tier", "blue")
    tc          = doc.get("tier_credits", 0)
    points      = doc.get("saga_points", 0)
    flights     = doc.get("flights_completed", 0)
    last_flight = doc.get("last_flight")
    expiry      = doc.get("points_expiry")
    since       = doc.get("member_since")

    next_tier, next_threshold = TIER_NEXT[tier]
    if next_threshold:
        tc_display    = f"{tc:,} TC\n{build_progress_bar(tc, next_threshold)} to {next_tier.title()}\n{next_threshold:,} TC required"
    else:
        tc_display    = f"{tc:,} TC\n{build_progress_bar(tc, None)}"

    last_flight_str = "No flights yet"
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

    embed.add_field(name="<:dbpersonbg:1374617772855660637> Member Name",  value=doc.get("roblox_username", "Unknown"), inline=True)
    embed.add_field(name="<:dbsagacard:1374617767097008148> Saga Club No.", value=f"`{doc.get('saga_number', 'N/A')}`",   inline=True)
    embed.add_field(name="<:dbcalenderbg:1374617779067551786> Member Since", value=since_str,                              inline=True)
    embed.add_field(name="Membership Tier",                                  value=TIER_LABELS[tier],                      inline=True)
    embed.add_field(name="\u200b",                                           value="\u200b",                               inline=True)
    embed.add_field(name="\u200b",                                           value="\u200b",                               inline=True)
    embed.add_field(name="<:dbtakeoffbg:1374617776504832001> Tier Credits", value=tc_display,                             inline=False)
    embed.add_field(name="<:dbstarcard:1374694940856025128> Saga Points",   value=f"{points:,} pts\n*Expires {expiry_str}*", inline=True)
    embed.add_field(name="<:dbtakeoffbg:1374617776504832001> Flights Completed", value=last_flight_str,                   inline=True)

    if tier in ("silver", "gold"):
        benefits = {
            "silver": (
                "<:lbcheckin:1374689021472669738> Priority check-in\n"
                "<:lblounge:1374689001151135877> Lounge access\n"
                "<:lbcarryon:1374689023389597797> Extra baggage\n"
                "<:lbwalking:1374689019593756842> Priority boarding\n"
                "1x complimentary upgrade"
            ),
            "gold": (
                "<:lbcheckin:1374689021472669738> Priority check-in\n"
                "<:lblounge:1374689001151135877> Lounge access\n"
                "<:lbcarryon:1374689023389597797> Extra baggage\n"
                "<:lbwalking:1374689019593756842> Priority boarding\n"
                "<:lbwifi:1374689006289424425> Complimentary Wi-Fi\n"
                "<:lbstarcard:1374688997132996681> Companion card\n"
                "Unlimited complimentary upgrades"
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
    history      = list(reversed(doc.get("flight_history", [])))
    total_pages  = max(1, math.ceil(len(history) / FLIGHTS_PER_PAGE))
    page         = max(1, min(page, total_pages))
    start        = (page - 1) * FLIGHTS_PER_PAGE
    slice_       = history[start:start + FLIGHTS_PER_PAGE]

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
            date_str     = f["date"].strftime("%-d %b %Y") if f.get("date") else "N/A"
            fare_label   = FARE_CLASS_LABELS.get(f.get("fare_class", ""), f.get("fare_class", "N/A"))
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


class ProfileView(discord.ui.View):
    def __init__(self, doc: dict):
        super().__init__(timeout=120)
        self.doc = doc

    @discord.ui.button(label="✈ View Flight History", style=discord.ButtonStyle.primary)
    async def history_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        embed, _ = build_history_embed(self.doc, page=1)
        view     = FlightHistoryView(self.doc, page=1)
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)


class SagaCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def get_verified_member(self, interaction: discord.Interaction) -> dict | None:
        """
        Checks Bloxlink verification. If not verified, sends an error and returns None.
        If verified but not in DB, creates a new profile and returns the doc.
        """
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
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return None

        doc = await get_member(interaction.user.id)
        if not doc:
            doc = await create_member(interaction.user.id, roblox["roblox_id"], roblox["roblox_username"])
            bloxlink_cog = self.bot.cogs.get("BloxlinkCog")
            if bloxlink_cog:
                await bloxlink_cog.update_member_tier(interaction.user.id, doc["tier"])

        return doc

    @app_commands.command(name="saga-profile", description="View your Icelandair Saga Club profile")
    @app_commands.guilds(discord.Object(id=GUILD_ID))
    async def saga_profile(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        doc = await self.get_verified_member(interaction)
        if not doc:
            return
        embed = build_profile_embed(doc)
        view  = ProfileView(doc)
        await interaction.followup.send(embed=embed, view=view, ephemeral=True)

    @app_commands.command(name="saga-add", description="[Staff] Add Saga Points and Tier Credits to a member")
    @app_commands.guilds(discord.Object(id=GUILD_ID))
    @app_commands.checks.has_permissions(manage_roles=True)
    async def saga_add(
        self,
        interaction: discord.Interaction,
        member: discord.Member,
        points: int,
        tier_credits: int,
    ):
        await interaction.response.defer(ephemeral=True)
        doc = await get_member(member.id)
        if not doc:
            await interaction.followup.send("That member does not have a Saga Club profile yet.", ephemeral=True)
            return

        updated = await add_points(member.id, points, tier_credits)
        bloxlink_cog = self.bot.cogs.get("BloxlinkCog")
        if bloxlink_cog:
            await bloxlink_cog.update_member_tier(member.id, updated["tier"])

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

    @app_commands.command(name="saga-set", description="[Staff] Set a member's Saga Points or Tier Credits to a specific value")
    @app_commands.guilds(discord.Object(id=GUILD_ID))
    @app_commands.checks.has_permissions(manage_roles=True)
    async def saga_set(
        self,
        interaction: discord.Interaction,
        member: discord.Member,
        points: int = None,
        tier_credits: int = None,
    ):
        await interaction.response.defer(ephemeral=True)
        if points is None and tier_credits is None:
            await interaction.followup.send("Please provide at least one value to set (points or tier_credits).", ephemeral=True)
            return

        doc = await get_member(member.id)
        if not doc:
            await interaction.followup.send("That member does not have a Saga Club profile yet.", ephemeral=True)
            return

        updated = await set_points(member.id, points=points, tier_credits=tier_credits)
        bloxlink_cog = self.bot.cogs.get("BloxlinkCog")
        if bloxlink_cog:
            await bloxlink_cog.update_member_tier(member.id, updated["tier"])

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

    @app_commands.command(name="saga-logflight", description="[Staff] Log a completed flight for a member")
    @app_commands.guilds(discord.Object(id=GUILD_ID))
    @app_commands.checks.has_permissions(manage_roles=True)
    async def saga_logflight(
        self,
        interaction: discord.Interaction,
        member: discord.Member,
        origin: str,
        destination: str,
        aircraft: str,
        fare_class: str,
        base_points: int,
    ):
        await interaction.response.defer(ephemeral=True)
        doc = await get_member(member.id)
        if not doc:
            await interaction.followup.send("That member does not have a Saga Club profile yet.", ephemeral=True)
            return

        updated = await log_flight(member.id, origin, destination, fare_class, aircraft, base_points)
        bloxlink_cog = self.bot.cogs.get("BloxlinkCog")
        if bloxlink_cog:
            await bloxlink_cog.update_member_tier(member.id, updated["tier"])

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
        choices = [
            app_commands.Choice(name=label, value=value)
            for value, label in FARE_CLASS_LABELS.items()
            if current.lower() in label.lower()
        ]
        return choices

    @app_commands.command(name="saga-leaderboard", description="View the Saga Club points leaderboard")
    @app_commands.guilds(discord.Object(id=GUILD_ID))
    async def saga_leaderboard(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        from database import members as members_col
        cursor  = members_col.find().sort("saga_points", -1).limit(10)
        docs    = await cursor.to_list(length=10)

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
                value=(
                    f"{TIER_LABELS[doc.get('tier', 'blue')]} • "
                    f"{doc.get('saga_points', 0):,} pts"
                ),
                inline=False,
            )

        embed.set_footer(text="Icelandair Saga Club", icon_url="https://www.icelandair.com/favicon.ico")
        embed.timestamp = datetime.now(timezone.utc)
        await interaction.followup.send(embed=embed, ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(SagaCog(bot))
