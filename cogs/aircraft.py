import discord
from discord.ext import commands
from discord import app_commands
import os
from datetime import datetime, timezone
from database_aircraft import (
    create_aircraft, get_aircraft, get_all_aircraft,
    update_aircraft, delete_aircraft,
)
from dotenv import load_dotenv

load_dotenv()

GUILD_ID           = int(os.getenv("DISCORD_GUILD_ID"))
DISPATCHER_ROLE_ID = int(os.getenv("DISPATCHER_ROLE_ID", 0))


def is_dispatcher(interaction: discord.Interaction) -> bool:
    if not DISPATCHER_ROLE_ID:
        return interaction.user.guild_permissions.manage_roles
    return any(r.id == DISPATCHER_ROLE_ID for r in interaction.user.roles)


def build_aircraft_embed(a: dict) -> discord.Embed:
    embed = discord.Embed(
        title=f"<:dblaptopbg:1374617774693023754> {a['registration']}  ·  {a['aircraft_type']}",
        color=0x003B6F,
    )
    embed.add_field(name="Registration",     value=f"`{a['registration']}`",  inline=True)
    embed.add_field(name="Aircraft Type",    value=a["aircraft_type"],         inline=True)
    embed.add_field(name="\u200b",           value="\u200b",                   inline=True)
    embed.add_field(name="<:lbseated:1374689017777492019> Economy Seats",  value=str(a["economy_seats"]), inline=True)
    embed.add_field(name="<:sagaprem1:1374621596547027025><:sagaprem2:1374621441391071272> Saga Premium Seats", value=str(a["premium_seats"]), inline=True)
    embed.add_field(name="Total Seats",      value=str(a["total_seats"]),      inline=True)
    if a.get("notes"):
        embed.add_field(name="Notes", value=a["notes"], inline=False)
    embed.set_footer(text="Icelandair Aircraft Registry", icon_url="https://www.icelandair.com/favicon.ico")
    embed.timestamp = datetime.now(timezone.utc)
    return embed


def build_registry_embed(all_aircraft: list) -> discord.Embed:
    embed = discord.Embed(
        title="<:dblaptopbg:1374617774693023754> Icelandair Aircraft Registry",
        description=f"{len(all_aircraft)} aircraft registered",
        color=0x003B6F,
    )
    if not all_aircraft:
        embed.add_field(name="No aircraft", value="No aircraft have been added to the registry yet.", inline=False)
    else:
        for a in all_aircraft:
            embed.add_field(
                name=f"`{a['registration']}`  ·  {a['aircraft_type']}",
                value=(
                    f"<:lbseated:1374689017777492019> Economy: **{a['economy_seats']}** · "
                    f"Saga Premium: **{a['premium_seats']}** · "
                    f"Total: **{a['total_seats']}**"
                ),
                inline=False,
            )
    embed.set_footer(text="Icelandair Aircraft Registry", icon_url="https://www.icelandair.com/favicon.ico")
    embed.timestamp = datetime.now(timezone.utc)
    return embed


class AircraftCreateModal(discord.ui.Modal, title="Register New Aircraft"):
    registration  = discord.ui.TextInput(label="Registration",   placeholder="e.g. TF-FIA",          max_length=10)
    aircraft_type = discord.ui.TextInput(label="Aircraft Type",  placeholder="e.g. Boeing 757-200",   max_length=50)
    economy_seats = discord.ui.TextInput(label="Economy Seats",  placeholder="e.g. 168",              max_length=5)
    premium_seats = discord.ui.TextInput(label="Saga Premium Seats", placeholder="e.g. 21",           max_length=5)
    notes         = discord.ui.TextInput(label="Notes (optional)", placeholder="Any additional info", max_length=200, required=False, style=discord.TextStyle.short)

    async def on_submit(self, interaction: discord.Interaction):
        existing = await get_aircraft(str(self.registration))
        if existing:
            await interaction.response.send_message(
                f"Aircraft `{str(self.registration).upper()}` is already in the registry.",
                ephemeral=True
            )
            return

        try:
            eco  = int(str(self.economy_seats))
            prem = int(str(self.premium_seats))
        except ValueError:
            await interaction.response.send_message("Economy and Saga Premium seat counts must be numbers.", ephemeral=True)
            return

        doc = await create_aircraft({
            "registration":  str(self.registration),
            "aircraft_type": str(self.aircraft_type),
            "economy_seats": eco,
            "premium_seats": prem,
            "notes":         str(self.notes),
        })

        embed = build_aircraft_embed(doc)
        await interaction.response.send_message(
            content="✅ Aircraft registered successfully.",
            embed=embed,
            ephemeral=True
        )


class AircraftEditModal(discord.ui.Modal, title="Edit Aircraft"):
    aircraft_type = discord.ui.TextInput(label="Aircraft Type",      max_length=50,  required=False)
    economy_seats = discord.ui.TextInput(label="Economy Seats",      max_length=5,   required=False)
    premium_seats = discord.ui.TextInput(label="Saga Premium Seats", max_length=5,   required=False)
    notes         = discord.ui.TextInput(label="Notes",              max_length=200, required=False, style=discord.TextStyle.short)

    def __init__(self, registration: str, current: dict):
        super().__init__()
        self.registration         = registration
        self.aircraft_type.default = current.get("aircraft_type", "")
        self.economy_seats.default = str(current.get("economy_seats", ""))
        self.premium_seats.default = str(current.get("premium_seats", ""))
        self.notes.default         = current.get("notes", "")

    async def on_submit(self, interaction: discord.Interaction):
        updates = {}
        if str(self.aircraft_type).strip():
            updates["aircraft_type"] = str(self.aircraft_type).strip()
        if str(self.economy_seats).strip():
            try:
                eco = int(str(self.economy_seats).strip())
                updates["economy_seats"] = eco
            except ValueError:
                await interaction.response.send_message("Economy seats must be a number.", ephemeral=True)
                return
        if str(self.premium_seats).strip():
            try:
                prem = int(str(self.premium_seats).strip())
                updates["premium_seats"] = prem
            except ValueError:
                await interaction.response.send_message("Saga Premium seats must be a number.", ephemeral=True)
                return
        if str(self.notes).strip():
            updates["notes"] = str(self.notes).strip()

        if "economy_seats" in updates or "premium_seats" in updates:
            current     = await get_aircraft(self.registration)
            eco         = updates.get("economy_seats",  current.get("economy_seats", 0))
            prem        = updates.get("premium_seats", current.get("premium_seats", 0))
            updates["total_seats"] = eco + prem

        if not updates:
            await interaction.response.send_message("No changes provided.", ephemeral=True)
            return

        updated = await update_aircraft(self.registration, updates)
        embed   = build_aircraft_embed(updated)
        await interaction.response.send_message(
            content="✅ Aircraft updated successfully.",
            embed=embed,
            ephemeral=True
        )


class AircraftCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    # ── /aircraft-add ─────────────────────────────────────────────────────────
    @app_commands.command(name="aircraft-add", description="[Dispatcher] Register a new aircraft")
    @app_commands.guilds(discord.Object(id=GUILD_ID))
    async def aircraft_add(self, interaction: discord.Interaction):
        if not is_dispatcher(interaction):
            await interaction.response.send_message("You don't have permission to use this command.", ephemeral=True)
            return
        await interaction.response.send_modal(AircraftCreateModal())

    # ── /aircraft-edit ────────────────────────────────────────────────────────
    @app_commands.command(name="aircraft-edit", description="[Dispatcher] Edit an existing aircraft's details")
    @app_commands.guilds(discord.Object(id=GUILD_ID))
    async def aircraft_edit(self, interaction: discord.Interaction, registration: str):
        if not is_dispatcher(interaction):
            await interaction.response.send_message("You don't have permission to use this command.", ephemeral=True)
            return
        current = await get_aircraft(registration)
        if not current:
            await interaction.response.send_message(f"Aircraft `{registration.upper()}` not found in the registry.", ephemeral=True)
            return
        await interaction.response.send_modal(AircraftEditModal(registration.upper(), current))

    # ── /aircraft-remove ──────────────────────────────────────────────────────
    @app_commands.command(name="aircraft-remove", description="[Dispatcher] Remove an aircraft from the registry")
    @app_commands.guilds(discord.Object(id=GUILD_ID))
    async def aircraft_remove(self, interaction: discord.Interaction, registration: str):
        await interaction.response.defer(ephemeral=True)
        if not is_dispatcher(interaction):
            await interaction.followup.send("You don't have permission to use this command.", ephemeral=True)
            return
        success = await delete_aircraft(registration)
        if not success:
            await interaction.followup.send(f"Aircraft `{registration.upper()}` not found in the registry.", ephemeral=True)
            return
        await interaction.followup.send(f"Aircraft `{registration.upper()}` has been removed from the registry.", ephemeral=True)

    # ── /aircraft-view ────────────────────────────────────────────────────────
    @app_commands.command(name="aircraft-view", description="View a specific aircraft's details")
    @app_commands.guilds(discord.Object(id=GUILD_ID))
    async def aircraft_view(self, interaction: discord.Interaction, registration: str):
        await interaction.response.defer(ephemeral=True)
        a = await get_aircraft(registration)
        if not a:
            await interaction.followup.send(f"Aircraft `{registration.upper()}` not found in the registry.", ephemeral=True)
            return
        await interaction.followup.send(embed=build_aircraft_embed(a), ephemeral=True)

    # ── /aircraft-registry ────────────────────────────────────────────────────
    @app_commands.command(name="aircraft-registry", description="View all registered aircraft")
    @app_commands.guilds(discord.Object(id=GUILD_ID))
    async def aircraft_registry(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        all_aircraft = await get_all_aircraft()
        embed = build_registry_embed(all_aircraft)
        await interaction.followup.send(embed=embed, ephemeral=True)

    # ── Autocomplete for registration across cogs ─────────────────────────────
    @aircraft_edit.autocomplete("registration")
    @aircraft_remove.autocomplete("registration")
    @aircraft_view.autocomplete("registration")
    async def registration_autocomplete(self, interaction: discord.Interaction, current: str):
        all_aircraft = await get_all_aircraft()
        return [
            app_commands.Choice(
                name=f"{a['registration']} — {a['aircraft_type']}",
                value=a["registration"]
            )
            for a in all_aircraft
            if current.lower() in a["registration"].lower() or current.lower() in a["aircraft_type"].lower()
        ][:25]


async def setup(bot: commands.Bot):
    await bot.add_cog(AircraftCog(bot))
