"""
Icelandair Careers Cog
-----------------------
Allows staff to create job postings with custom questions.
Members apply via a persistent panel in a designated channel.
Applications are handled entirely through DMs.
"""

import asyncio
import uuid
from datetime import datetime, timezone

import discord
from discord import app_commands
from discord.ext import commands
from motor.motor_asyncio import AsyncIOMotorClient
import os
from dotenv import load_dotenv

load_dotenv()

# ── Database ──────────────────────────────────────────────────────────────────
client = AsyncIOMotorClient(os.getenv("MONGO_URI"))
db     = client[os.getenv("MONGO_DB", "icelandair")]
coll   = db["careers"]

# ── Role constants ────────────────────────────────────────────────────────────
GUILD_ID             = int(os.getenv("DISCORD_GUILD_ID"))
INELIGIBLE_ROLE_ID   = int(os.getenv("INELIGIBLE_ROLE_ID", 0))
MANAGER_ROLE_IDS     = {
    int(x.strip())
    for x in os.getenv("MANAGER_ROLE_IDS", "").split(",")
    if x.strip()
}
TIMEOUT_LOG_CHANNEL  = int(os.getenv("TIMEOUT_LOG_CHANNEL", 0))

# ── Branding ──────────────────────────────────────────────────────────────────
EMBED_COLOR          = 0x003B6F
LOGO_CIRCLE_BLUE_URL = "https://cdn.discordapp.com/emojis/1374682227719405588.png"
BASIC_LOGO_URL       = "https://cdn.discordapp.com/emojis/1374682220891209799.png"

EMOJI_TAKEOFF        = "<:dbtakeoffbg:1374617776504832001>"
EMOJI_CALENDAR       = "<:dbcalenderbg:1374617779067551786>"
EMOJI_PERSON         = "<:dbpersonbg:1374617772855660637>"
EMOJI_ALERT          = "<:dbalertbg:1374617765142331432>"
EMOJI_PEN            = "<:dbpenbg:1374617771010424912>"
EMOJI_LOGO_BLUE      = "<:Flogocirclebluebg:1374682227719405588>"

APPLICATION_TIMEOUT  = 3600
MAX_QUESTIONS        = 15

STANDARD_QUESTIONS = [
    "What is your Discord username?",
    "What is your Discord User ID?",
    "What is your timezone (e.g. UTC+0, GMT+10, EST)?",
    "What is your Roblox username?",
]


# ── Helpers ───────────────────────────────────────────────────────────────────

def is_manager(member: discord.Member) -> bool:
    if not MANAGER_ROLE_IDS:
        return member.guild_permissions.manage_roles
    return any(r.id in MANAGER_ROLE_IDS for r in member.roles)


def short_id() -> str:
    return str(uuid.uuid4())[:8].upper()


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


# ── Persistent Views ──────────────────────────────────────────────────────────

class JobSelectMenu(discord.ui.Select):
    def __init__(self, jobs: list):
        options = [
            discord.SelectOption(
                label=job["title"],
                description=job.get("description", "")[:100],
                value=job["job_id"],
            )
            for job in jobs
        ]
        super().__init__(
            custom_id="icelandair_apply_select",
            placeholder="Select a role to apply for...",
            min_values=1,
            max_values=1,
            options=options,
        )

    async def callback(self, interaction: discord.Interaction):
        cog = interaction.client.get_cog("CareersCog")
        if cog is None:
            return await interaction.response.send_message(
                "The careers system is currently unavailable.", ephemeral=True
            )
        await cog.handle_application_start(interaction, self.values[0])


class JobSelectView(discord.ui.View):
    def __init__(self, jobs: list):
        super().__init__(timeout=None)
        self.add_item(JobSelectMenu(jobs))


class EmptyPanelView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)


class ConfirmApplicationView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=APPLICATION_TIMEOUT)
        self.confirmed = None

    @discord.ui.button(label="Submit Application", style=discord.ButtonStyle.success, emoji="✅")
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.confirmed = True
        self.stop()
        await interaction.response.defer()

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.danger, emoji="✖️")
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.confirmed = False
        self.stop()
        await interaction.response.defer()


class ReviewView(discord.ui.View):
    def __init__(self, application_id: str):
        super().__init__(timeout=None)
        self.application_id          = application_id
        self.children[0].custom_id  = f"accept_{application_id}"
        self.children[1].custom_id  = f"decline_{application_id}"

    @discord.ui.button(label="Accept", style=discord.ButtonStyle.success, emoji="✅", custom_id="accept_placeholder")
    async def accept_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        cog = interaction.client.get_cog("CareersCog")
        if cog:
            await cog.handle_review(interaction, self.application_id, accepted=True)

    @discord.ui.button(label="Decline", style=discord.ButtonStyle.danger, emoji="✖️", custom_id="decline_placeholder")
    async def decline_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        cog = interaction.client.get_cog("CareersCog")
        if cog:
            await cog.handle_review(interaction, self.application_id, accepted=False)


class ReviewReasonModal(discord.ui.Modal, title="Review Decision"):
    reason = discord.ui.TextInput(
        label="Reason / Notes (not shown to applicant)",
        style=discord.TextStyle.paragraph,
        placeholder="Internal notes for this decision...",
        required=False,
        max_length=1000,
    )
    message_to_applicant = discord.ui.TextInput(
        label="Message to applicant",
        style=discord.TextStyle.paragraph,
        placeholder="This will be sent directly to the applicant.",
        required=False,
        max_length=1000,
    )

    def __init__(self, accepted: bool):
        super().__init__()
        self.accepted          = accepted
        self.submitted_reason  = None
        self.submitted_message = None

    async def on_submit(self, interaction: discord.Interaction):
        self.submitted_reason  = self.reason.value
        self.submitted_message = self.message_to_applicant.value
        await interaction.response.defer()
        self.stop()


class JobCreateModal(discord.ui.Modal, title="Create Job Posting — Step 1"):
    title_field       = discord.ui.TextInput(label="Job Title",          placeholder="e.g. Flight Dispatcher",    max_length=80)
    description_field = discord.ui.TextInput(label="Short Description",  placeholder="Shown in the panel dropdown", max_length=100)
    channel_id_field  = discord.ui.TextInput(label="Submission Channel ID", placeholder="Paste the channel ID here", max_length=20)
    questions_field   = discord.ui.TextInput(
        label="Custom Questions (one per line)",
        placeholder="What experience do you have?\nWhy do you want to join?",
        style=discord.TextStyle.paragraph,
        max_length=2000,
    )

    def __init__(self, cog):
        super().__init__()
        self.cog = cog

    async def on_submit(self, interaction: discord.Interaction):
        try:
            channel_id = int(str(self.channel_id_field).strip())
            channel    = interaction.client.get_channel(channel_id)
            if not channel:
                await interaction.response.send_message(
                    f"{EMOJI_ALERT} Could not find that channel. Please check the ID and try again.",
                    ephemeral=True
                )
                return
        except ValueError:
            await interaction.response.send_message(
                f"{EMOJI_ALERT} Invalid channel ID.", ephemeral=True
            )
            return

        questions = [
            q.strip()
            for q in str(self.questions_field).split("\n")
            if q.strip()
        ][:MAX_QUESTIONS]

        if not questions:
            await interaction.response.send_message(
                f"{EMOJI_ALERT} You must provide at least one custom question.", ephemeral=True
            )
            return

        job_id = short_id()
        await coll.insert_one({
            "job_id":               job_id,
            "title":                str(self.title_field).strip(),
            "description":          str(self.description_field).strip()[:100],
            "submission_channel_id": channel_id,
            "questions":            questions,
            "open":                 True,
            "created_by":           interaction.user.id,
            "created_at":           utcnow(),
        })

        await self.cog._update_panel()

        embed = discord.Embed(
            title="✅ Job Posting Created",
            color=EMBED_COLOR,
            description=(
                f"**Title:** {str(self.title_field).strip()}\n"
                f"**Job ID:** `{job_id}`\n"
                f"**Submission channel:** {channel.mention}\n"
                f"**Custom questions:** {len(questions)}\n"
                f"**Standard questions:** {len(STANDARD_QUESTIONS)}\n\n"
                "The careers panel has been updated automatically."
            )
        )
        embed.set_author(name="Icelandair | Careers", icon_url=LOGO_CIRCLE_BLUE_URL)
        embed.set_footer(text="Icelandair Careers", icon_url=BASIC_LOGO_URL)
        await interaction.response.send_message(embed=embed, ephemeral=True)


# ── Cog ───────────────────────────────────────────────────────────────────────

class CareersCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot            = bot
        self.active_sessions: dict[int, str] = {}
        bot.loop.create_task(self._register_persistent_views())

    async def _register_persistent_views(self):
        await self.bot.wait_until_ready()
        jobs = await self._get_open_jobs()
        if jobs:
            self.bot.add_view(JobSelectView(jobs))
        else:
            self.bot.add_view(EmptyPanelView())
        async for app in coll.find({"status": "pending"}):
            self.bot.add_view(ReviewView(app["application_id"]))

    # ── DB helpers ────────────────────────────────────────────────────────────

    async def _get_open_jobs(self) -> list:
        cursor = coll.find({"open": True, "job_id": {"$exists": True}})
        return await cursor.to_list(length=25)

    async def _get_job(self, job_id: str) -> dict | None:
        return await coll.find_one({"job_id": job_id})

    async def _get_application(self, application_id: str) -> dict | None:
        return await coll.find_one({"application_id": application_id})

    async def _update_panel(self):
        config = await coll.find_one({"_id": "panel_config"})
        if not config:
            return
        try:
            guild   = self.bot.get_guild(config["guild_id"])
            channel = guild.get_channel(config["channel_id"])
            message = await channel.fetch_message(config["message_id"])
        except Exception:
            return
        jobs  = await self._get_open_jobs()
        embed = self._build_panel_embed(jobs)
        view  = JobSelectView(jobs) if jobs else EmptyPanelView()
        if jobs:
            self.bot.add_view(view)
        await message.edit(embed=embed, view=view)

    def _build_panel_embed(self, jobs: list) -> discord.Embed:
        embed = discord.Embed(
            title=f"{EMOJI_LOGO_BLUE} Join the Icelandair Team",
            description=(
                "We are always looking for passionate and dedicated individuals to join our team. "
                "Browse our open positions below and select a role you'd like to apply for to get started.\n\n"
                "Applications are completed entirely through direct messages with the bot. "
                "Please ensure your DMs are open before applying.\n\n"
                f"{EMOJI_TAKEOFF} **Select a role below to begin your application.**"
            ),
            color=EMBED_COLOR,
        )
        embed.set_author(name="Icelandair | Careers", icon_url=LOGO_CIRCLE_BLUE_URL)

        if jobs:
            positions_value = "\n".join(
                f"`{job['title']}` — {job.get('description', 'No description provided.')}"
                for job in jobs
            )
        else:
            positions_value = (
                "We're not currently looking for new recruits at this time. "
                "Check back in the future for your desired opportunity."
            )

        embed.add_field(name=f"{EMOJI_CALENDAR} Open Positions", value=positions_value, inline=False)
        embed.set_footer(text="Icelandair Careers · Last updated", icon_url=BASIC_LOGO_URL)
        embed.timestamp = utcnow()
        return embed

    # ── Application flow ──────────────────────────────────────────────────────

    async def handle_application_start(self, interaction: discord.Interaction, job_id: str):
        member = interaction.user

        if INELIGIBLE_ROLE_ID and any(r.id == INELIGIBLE_ROLE_ID for r in member.roles):
            return await interaction.response.send_message(
                f"{EMOJI_ALERT} You are not eligible to apply for positions at this time.",
                ephemeral=True,
            )

        if member.id in self.active_sessions:
            return await interaction.response.send_message(
                f"{EMOJI_ALERT} You already have an active application in progress.",
                ephemeral=True,
            )

        existing = await coll.find_one({"user_id": member.id, "status": "pending"})
        if existing:
            return await interaction.response.send_message(
                f"{EMOJI_ALERT} You already have a pending application under review.",
                ephemeral=True,
            )

        job = await self._get_job(job_id)
        if not job or not job.get("open"):
            return await interaction.response.send_message(
                f"{EMOJI_ALERT} This position is no longer accepting applications.",
                ephemeral=True,
            )

        try:
            await member.send(
                f"{EMOJI_LOGO_BLUE} **Icelandair Careers**\n\n"
                f"Thank you for your interest in the **{job['title']}** position. "
                "I'll now guide you through the application. Please answer each question carefully.\n\n"
                f"You have **60 minutes** to complete this application."
            )
        except discord.Forbidden:
            return await interaction.response.send_message(
                f"{EMOJI_ALERT} I couldn't open a DM with you. Please enable Direct Messages and try again.",
                ephemeral=True,
            )

        await interaction.response.send_message(
            f"{EMOJI_TAKEOFF} Check your DMs! Your application for **{job['title']}** has started.",
            ephemeral=True,
        )

        self.active_sessions[member.id] = job_id
        self.bot.loop.create_task(self._run_application_dm(member, job))

    async def _run_application_dm(self, user: discord.Member, job: dict):
        all_questions = STANDARD_QUESTIONS + job.get("questions", [])
        answers       = []

        def check(m: discord.Message):
            return m.author.id == user.id and isinstance(m.channel, discord.DMChannel)

        for i, question in enumerate(all_questions, 1):
            embed = discord.Embed(
                description=f"**Question {i} of {len(all_questions)}**\n\n{question}",
                color=EMBED_COLOR,
            )
            embed.set_author(name="Icelandair | Careers", icon_url=LOGO_CIRCLE_BLUE_URL)
            embed.set_footer(text=f"Application for {job['title']}", icon_url=BASIC_LOGO_URL)
            await user.send(embed=embed)

            reminded = False
            answer   = None

            while True:
                try:
                    msg    = await self.bot.wait_for("message", check=check, timeout=APPLICATION_TIMEOUT)
                    answer = msg.content
                    break
                except asyncio.TimeoutError:
                    if not reminded:
                        reminded = True
                        await user.send(
                            f"{EMOJI_ALERT} **Reminder:** You still have an active application for "
                            f"**{job['title']}**. Please answer the current question within the next hour "
                            "or your application will expire."
                        )
                        try:
                            msg    = await self.bot.wait_for("message", check=check, timeout=APPLICATION_TIMEOUT)
                            answer = msg.content
                            break
                        except asyncio.TimeoutError:
                            self.active_sessions.pop(user.id, None)
                            await user.send(
                                f"{EMOJI_ALERT} **Application Expired**\n\n"
                                f"Your application for **{job['title']}** has expired due to inactivity. "
                                "You're welcome to apply again in the future."
                            )
                            if TIMEOUT_LOG_CHANNEL:
                                log_channel = self.bot.get_channel(TIMEOUT_LOG_CHANNEL)
                                if log_channel:
                                    log_embed = discord.Embed(
                                        title="Application Expired",
                                        description=(
                                            f"{EMOJI_ALERT} **{user}** (`{user.id}`) did not complete their "
                                            f"application for **{job['title']}** and it has timed out."
                                        ),
                                        color=0xED4245,
                                        timestamp=utcnow(),
                                    )
                                    log_embed.set_author(name="Icelandair | Careers", icon_url=LOGO_CIRCLE_BLUE_URL)
                                    log_embed.set_footer(icon_url=BASIC_LOGO_URL, text="Icelandair Careers")
                                    await log_channel.send(embed=log_embed)
                            return

            answers.append({"question": question, "answer": answer})

        # Summary
        summary_embed = discord.Embed(
            title=f"{EMOJI_PEN} Application Summary",
            description=f"Please review your answers for the **{job['title']}** position below.",
            color=EMBED_COLOR,
        )
        summary_embed.set_author(name="Icelandair | Careers", icon_url=LOGO_CIRCLE_BLUE_URL)
        summary_embed.set_footer(text="Icelandair Careers · Press Submit to finalise", icon_url=BASIC_LOGO_URL)

        for i, qa in enumerate(answers, 1):
            label = "Standard" if i <= len(STANDARD_QUESTIONS) else f"Q{i - len(STANDARD_QUESTIONS)}"
            summary_embed.add_field(
                name=f"`{label}` {qa['question'][:80]}",
                value=qa["answer"][:1024] or "*No answer*",
                inline=False,
            )

        view = ConfirmApplicationView()
        await user.send(embed=summary_embed, view=view)
        await view.wait()

        self.active_sessions.pop(user.id, None)

        if not view.confirmed:
            await user.send(
                f"{EMOJI_ALERT} Your application has been cancelled. You're welcome to apply again at any time."
            )
            return

        application_id = short_id()
        await coll.insert_one({
            "application_id": application_id,
            "job_id":         job["job_id"],
            "job_title":      job["title"],
            "user_id":        user.id,
            "user_tag":       str(user),
            "answers":        answers,
            "status":         "pending",
            "reviewer_notes": None,
            "submitted_at":   utcnow(),
        })

        await user.send(
            f"✅ **Application Submitted!**\n\n"
            f"Your application for **{job['title']}** has been received. "
            f"Our team will review it shortly. Your reference is `{application_id}`."
        )

        await self._post_submission_to_staff(user, job, answers, application_id)

    async def _post_submission_to_staff(self, user, job, answers, application_id):
        submission_channel_id = job.get("submission_channel_id")
        if not submission_channel_id:
            return
        channel = self.bot.get_channel(submission_channel_id)
        if not channel:
            return

        guild  = channel.guild
        member = guild.get_member(user.id)

        embed = discord.Embed(
            title=f"{EMOJI_PERSON} New Application — {job['title']}",
            color=EMBED_COLOR,
            timestamp=utcnow(),
        )
        embed.set_author(name="Icelandair | Careers", icon_url=LOGO_CIRCLE_BLUE_URL)
        embed.set_footer(text=f"Application ID: {application_id}", icon_url=BASIC_LOGO_URL)
        embed.add_field(name="Applicant", value=f"{user.mention} (`{user}` · `{user.id}`)", inline=False)

        if member:
            joined  = discord.utils.format_dt(member.joined_at, style="D") if member.joined_at else "Unknown"
            created = discord.utils.format_dt(user.created_at, style="D")
            embed.add_field(name="Account Created", value=created,  inline=True)
            embed.add_field(name="Joined Server",   value=joined,   inline=True)
        else:
            embed.add_field(name="Account Created", value=discord.utils.format_dt(user.created_at, style="D"), inline=True)

        embed.add_field(name="\u200b", value="**── Answers ──**", inline=False)

        for i, qa in enumerate(answers, 1):
            label = "Standard" if i <= len(STANDARD_QUESTIONS) else f"Q{i - len(STANDARD_QUESTIONS)}"
            embed.add_field(
                name=f"`{label}` {qa['question'][:80]}",
                value=qa["answer"][:1024] or "*No answer*",
                inline=False,
            )

        view = ReviewView(application_id)
        self.bot.add_view(view)
        await channel.send(embed=embed, view=view)

    # ── Review handler ────────────────────────────────────────────────────────

    async def handle_review(self, interaction: discord.Interaction, application_id: str, accepted: bool):
        if not is_manager(interaction.user):
            return await interaction.response.send_message(
                f"{EMOJI_ALERT} You don't have permission to review applications.", ephemeral=True
            )

        app = await self._get_application(application_id)
        if not app:
            return await interaction.response.send_message(
                f"{EMOJI_ALERT} Application `{application_id}` not found.", ephemeral=True
            )
        if app["status"] != "pending":
            return await interaction.response.send_message(
                f"{EMOJI_ALERT} This application has already been reviewed (status: **{app['status']}**).",
                ephemeral=True,
            )

        modal = ReviewReasonModal(accepted=accepted)
        await interaction.response.send_modal(modal)
        await modal.wait()

        status     = "accepted" if accepted else "declined"
        color      = 0x57F287 if accepted else 0xED4245
        result_word = "Accepted" if accepted else "Declined"

        await coll.update_one(
            {"application_id": application_id},
            {"$set": {
                "status":         status,
                "reviewer_id":    interaction.user.id,
                "reviewer_notes": modal.submitted_reason,
                "reviewed_at":    utcnow(),
            }}
        )

        applicant = self.bot.get_user(app["user_id"])
        if applicant:
            if accepted:
                dm_embed = discord.Embed(
                    title=f"✅ Application Accepted — {app['job_title']}",
                    description=(
                        f"Congratulations — we are pleased to offer you the position of "
                        f"**{app['job_title']}** with Icelandair. On behalf of the entire team, "
                        "welcome aboard. A member of our team will be in touch shortly with "
                        "further details regarding your onboarding."
                    ),
                    color=0x57F287,
                    timestamp=utcnow(),
                )
            else:
                dm_embed = discord.Embed(
                    title=f"Application Outcome — {app['job_title']}",
                    description=(
                        f"Thank you for taking the time to apply for the position of "
                        f"**{app['job_title']}** with Icelandair. After careful consideration, "
                        "we regret to inform you that we will not be moving forward with your "
                        "application at this time. We wish you the very best in your future endeavours."
                    ),
                    color=0xED4245,
                    timestamp=utcnow(),
                )

            dm_embed.set_author(name="Icelandair | Careers", icon_url=LOGO_CIRCLE_BLUE_URL)
            if modal.submitted_message:
                dm_embed.add_field(
                    name="Message from the review team",
                    value=f'*"{modal.submitted_message}"*',
                    inline=False,
                )
            dm_embed.set_footer(
                text=f"Icelandair Careers · Application ID: {application_id}",
                icon_url=BASIC_LOGO_URL,
            )
            try:
                await applicant.send(embed=dm_embed)
            except discord.Forbidden:
                pass

        original_embed = interaction.message.embeds[0] if interaction.message.embeds else None
        if original_embed:
            original_embed.colour = discord.Colour(color)
            original_embed.add_field(
                name=f"{'✅' if accepted else '✖️'} {result_word} by",
                value=f"{interaction.user.mention}",
                inline=True,
            )

        disabled_view = discord.ui.View()
        disabled_view.add_item(discord.ui.Button(label="Accept",  style=discord.ButtonStyle.success, disabled=True))
        disabled_view.add_item(discord.ui.Button(label="Decline", style=discord.ButtonStyle.danger,  disabled=True))
        await interaction.message.edit(embed=original_embed, view=disabled_view)

        notified = " The applicant has been notified." if applicant and modal.submitted_message else ""
        await interaction.followup.send(
            f"✅ Application `{application_id}` has been **{status}**.{notified}", ephemeral=True
        )

    # ── Slash commands ────────────────────────────────────────────────────────

    job_group = app_commands.Group(
        name="job",
        description="Icelandair Careers management",
        guild_ids=[GUILD_ID],
    )

    @job_group.command(name="create", description="[Manager] Create a new job posting")
    async def job_create(self, interaction: discord.Interaction):
        if not is_manager(interaction.user):
            return await interaction.response.send_message(
                f"{EMOJI_ALERT} You don't have permission to create job postings.", ephemeral=True
            )
        await interaction.response.send_modal(JobCreateModal(cog=self))

    @job_group.command(name="list", description="[Manager] List all job postings")
    async def job_list(self, interaction: discord.Interaction):
        if not is_manager(interaction.user):
            return await interaction.response.send_message(
                f"{EMOJI_ALERT} You don't have permission to view job postings.", ephemeral=True
            )
        jobs = await coll.find({
            "job_id": {"$exists": True},
            "title":  {"$exists": True},
        }).to_list(length=100)
        if not jobs:
            return await interaction.response.send_message("No job postings found.", ephemeral=True)

        embed = discord.Embed(title="Job Postings", color=EMBED_COLOR)
        embed.set_author(name="Icelandair | Careers", icon_url=LOGO_CIRCLE_BLUE_URL)
        for job in jobs:
            status = "🟢 Open" if job.get("open") else "🔴 Closed"
            embed.add_field(
                name=f"`{job.get('job_id', '?')}` — {job.get('title', 'Untitled')}",
                value=f"{status} · {job.get('description', '')}",
                inline=False,
            )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @job_group.command(name="close", description="[Manager] Close a job posting")
    @app_commands.describe(job_id="The job ID to close")
    async def job_close(self, interaction: discord.Interaction, job_id: str):
        if not is_manager(interaction.user):
            return await interaction.response.send_message(
                f"{EMOJI_ALERT} You don't have permission to close job postings.", ephemeral=True
            )
        job = await self._get_job(job_id)
        if not job:
            return await interaction.response.send_message(f"{EMOJI_ALERT} Job `{job_id}` not found.", ephemeral=True)
        await coll.update_one({"job_id": job_id}, {"$set": {"open": False}})
        await self._update_panel()
        await interaction.response.send_message(
            f"✅ Job `{job_id}` (**{job['title']}**) has been closed.", ephemeral=True
        )

    @job_group.command(name="reopen", description="[Manager] Reopen a closed job posting")
    @app_commands.describe(job_id="The job ID to reopen")
    async def job_reopen(self, interaction: discord.Interaction, job_id: str):
        if not is_manager(interaction.user):
            return await interaction.response.send_message(
                f"{EMOJI_ALERT} You don't have permission to reopen job postings.", ephemeral=True
            )
        job = await self._get_job(job_id)
        if not job:
            return await interaction.response.send_message(f"{EMOJI_ALERT} Job `{job_id}` not found.", ephemeral=True)
        await coll.update_one({"job_id": job_id}, {"$set": {"open": True}})
        await self._update_panel()
        await interaction.response.send_message(
            f"✅ Job `{job_id}` (**{job['title']}**) has been reopened.", ephemeral=True
        )

    @job_group.command(name="applications", description="[Manager] List all applications for a job")
    @app_commands.describe(job_id="The job ID")
    async def job_applications(self, interaction: discord.Interaction, job_id: str):
        if not is_manager(interaction.user):
            return await interaction.response.send_message(
                f"{EMOJI_ALERT} You don't have permission to view applications.", ephemeral=True
            )
        job = await self._get_job(job_id)
        if not job:
            return await interaction.response.send_message(f"{EMOJI_ALERT} Job `{job_id}` not found.", ephemeral=True)
        apps = await coll.find({"job_id": job_id}).to_list(length=100)
        if not apps:
            return await interaction.response.send_message(
                f"No applications found for **{job['title']}**.", ephemeral=True
            )
        embed = discord.Embed(title=f"Applications — {job['title']}", color=EMBED_COLOR)
        embed.set_author(name="Icelandair | Careers", icon_url=LOGO_CIRCLE_BLUE_URL)
        for app in apps:
            status_emoji = {"pending": "🟡", "accepted": "🟢", "declined": "🔴"}.get(app["status"], "⚪")
            embed.add_field(
                name=f"`{app['application_id']}` — {app['user_tag']}",
                value=f"{status_emoji} {app['status'].capitalize()} · <t:{int(app['submitted_at'].timestamp())}:D>",
                inline=False,
            )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @job_group.command(name="view", description="[Manager] View a specific application in full")
    @app_commands.describe(application_id="The application ID")
    async def job_view(self, interaction: discord.Interaction, application_id: str):
        if not is_manager(interaction.user):
            return await interaction.response.send_message(
                f"{EMOJI_ALERT} You don't have permission to view applications.", ephemeral=True
            )
        app = await self._get_application(application_id)
        if not app:
            return await interaction.response.send_message(
                f"{EMOJI_ALERT} Application `{application_id}` not found.", ephemeral=True
            )
        embed = discord.Embed(
            title=f"Application — {app['job_title']}",
            color=EMBED_COLOR,
            timestamp=app["submitted_at"],
        )
        embed.set_author(name="Icelandair | Careers", icon_url=LOGO_CIRCLE_BLUE_URL)
        embed.set_footer(
            text=f"Application ID: {application_id} · Status: {app['status'].capitalize()}",
            icon_url=BASIC_LOGO_URL,
        )
        embed.add_field(name="Applicant", value=f"`{app['user_tag']}` (`{app['user_id']}`)", inline=False)
        if app.get("reviewer_notes"):
            embed.add_field(name="<:dbpersonbg:1374617772855660637> Internal Notes", value=app["reviewer_notes"], inline=False)
        for i, qa in enumerate(app["answers"], 1):
            label = "Standard" if i <= len(STANDARD_QUESTIONS) else f"Q{i - len(STANDARD_QUESTIONS)}"
            embed.add_field(
                name=f"`{label}` {qa['question'][:80]}",
                value=qa["answer"][:1024] or "*No answer*",
                inline=False,
            )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @job_group.command(name="panel", description="[Manager] Post the careers panel in this channel")
    async def job_panel(self, interaction: discord.Interaction):
        if not is_manager(interaction.user):
            return await interaction.response.send_message(
                f"{EMOJI_ALERT} You don't have permission to post the careers panel.", ephemeral=True
            )
        jobs  = await self._get_open_jobs()
        embed = self._build_panel_embed(jobs)
        view  = JobSelectView(jobs) if jobs else EmptyPanelView()
        if jobs:
            self.bot.add_view(view)

        await interaction.response.send_message("✅ Posting panel...", ephemeral=True)
        msg = await interaction.channel.send(embed=embed, view=view)

        await coll.update_one(
            {"_id": "panel_config"},
            {"$set": {
                "guild_id":   interaction.guild.id,
                "channel_id": interaction.channel.id,
                "message_id": msg.id,
            }},
            upsert=True,
        )

    @job_group.command(name="help", description="Show careers command reference")
    async def job_help(self, interaction: discord.Interaction):
        is_mgr = is_manager(interaction.user)
        embed  = discord.Embed(
            title="<:dblaptopbg:1374617774693023754> Careers — Command Reference",
            description="All commands use `/job`.",
            color=EMBED_COLOR,
        )
        embed.set_author(name="Icelandair | Careers", icon_url=LOGO_CIRCLE_BLUE_URL)
        embed.add_field(
            name=f"{EMOJI_PEN} Setup",
            value="`/job panel` — Post the persistent careers panel *(run once only)*",
            inline=False,
        )
        if is_mgr:
            embed.add_field(
                name=f"{EMOJI_TAKEOFF} Job Management",
                value=(
                    "`/job create` — Create a new job posting via modal\n"
                    "`/job list` — List all jobs with IDs and status\n"
                    "`/job close <id>` — Close a job posting\n"
                    "`/job reopen <id>` — Reopen a closed job posting"
                ),
                inline=False,
            )
            embed.add_field(
                name=f"{EMOJI_PERSON} Application Review",
                value=(
                    "`/job applications <id>` — List all applications for a job\n"
                    "`/job view <app_id>` — View a full application\n"
                    "`Accept / Decline buttons` — Appear on each submission in the staff channel"
                ),
                inline=False,
            )
        embed.add_field(
            name=f"{EMOJI_ALERT} Automatic Behaviours",
            value=(
                "**Panel auto-update** — Updates on any job change\n"
                "**Application timeout** — Reminder after 1 hour, expires after a further hour\n"
                "**Standard questions** — Discord username, ID, timezone, and Roblox username always collected first"
            ),
            inline=False,
        )
        embed.set_footer(text="Icelandair Careers", icon_url=BASIC_LOGO_URL)
        await interaction.response.send_message(embed=embed, ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(CareersCog(bot))
