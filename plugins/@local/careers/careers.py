"""
Icelandair Careers Plugin for icemodmail
-----------------------------------------
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


# ── Role / Channel constants ──────────────────────────────────────────────────
INELIGIBLE_ROLE_ID   = 1374336945563500655   # Cannot apply
MANAGER_ROLE_IDS     = {1373841474374074399, 1373841026233532416}  # Can create jobs & review
TIMEOUT_LOG_CHANNEL  = 1375709482947969185   # Channel to post timeout notices

# ── Branding constants ────────────────────────────────────────────────────────
EMBED_COLOR          = 0x0E2B52
LOGO_CIRCLE_BLUE_URL = "https://cdn.discordapp.com/emojis/1374682227719405588.png"
BASIC_LOGO_URL       = "https://cdn.discordapp.com/emojis/1374682220891209799.png"

EMOJI_TAKEOFF        = "<:dbtakeoffbg:1374617776504832001>"
EMOJI_CALENDAR       = "<:dbcalenderbg:1374617779067551786>"
EMOJI_PERSON         = "<:dbpersonbg:1374617772855660637>"
EMOJI_ALERT          = "<:dbalertbg:1374617765142331432>"
EMOJI_PEN            = "<:dbpenbg:1374617771010424912>"
EMOJI_LOGO_BLUE      = "<:Flogocirclebluebg:1374682227719405588>"

APPLICATION_TIMEOUT  = 3600   # 1 hour in seconds
MAX_QUESTIONS        = 15

# Standard questions prepended to every application
STANDARD_QUESTIONS = [
    "What is your Discord username (e.g. username#0000 or just username if no discriminator)?",
    "What is your Discord User ID?",
    "What is your timezone (e.g. UTC+0, GMT+10, EST)?",
    "What is your Roblox username?",
]


# ── Helpers ───────────────────────────────────────────────────────────────────

def is_manager(member: discord.Member) -> bool:
    return any(r.id in MANAGER_ROLE_IDS for r in member.roles)


def short_id() -> str:
    return str(uuid.uuid4())[:8].upper()


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


# ── Persistent Select Menu ────────────────────────────────────────────────────

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
        cog = interaction.client.get_cog("IcelandairCareers")
        if cog is None:
            return await interaction.response.send_message(
                "The careers system is currently unavailable. Please try again later.",
                ephemeral=True,
            )
        await cog.handle_application_start(interaction, self.values[0])


class JobSelectView(discord.ui.View):
    def __init__(self, jobs: list):
        super().__init__(timeout=None)
        self.add_item(JobSelectMenu(jobs))


class EmptyPanelView(discord.ui.View):
    """Shown on the panel when there are no open jobs — no interactive components."""
    def __init__(self):
        super().__init__(timeout=None)


# ── Confirmation View (sent to applicant at end of DM flow) ──────────────────

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


# ── Review Buttons (posted to staff channel with each submission) ─────────────

class ReviewView(discord.ui.View):
    def __init__(self, application_id: str):
        super().__init__(timeout=None)
        self.application_id = application_id
        # Encode app ID into custom_ids so they survive restarts
        self.children[0].custom_id = f"accept_{application_id}"
        self.children[1].custom_id = f"decline_{application_id}"

    @discord.ui.button(label="Accept", style=discord.ButtonStyle.success, emoji="✅", custom_id="accept_placeholder")
    async def accept_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        cog = interaction.client.get_cog("IcelandairCareers")
        if cog:
            await cog.handle_review(interaction, self.application_id, accepted=True)

    @discord.ui.button(label="Decline", style=discord.ButtonStyle.danger, emoji="✖️", custom_id="decline_placeholder")
    async def decline_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        cog = interaction.client.get_cog("IcelandairCareers")
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
        self.accepted = accepted
        self.submitted_reason = None
        self.submitted_message = None

    async def on_submit(self, interaction: discord.Interaction):
        self.submitted_reason = self.reason.value
        self.submitted_message = self.message_to_applicant.value
        await interaction.response.defer()
        self.stop()


# ── Main Cog ──────────────────────────────────────────────────────────────────

class IcelandairCareers(commands.Cog):
    """Icelandair Careers — job postings and applications system."""

    def __init__(self, bot):
        self.bot = bot
        self.coll = bot.api.get_plugin_partition(self)
        # Track active DM sessions: user_id -> job_id
        self.active_sessions: dict[int, str] = {}
        # Re-register persistent views on load
        bot.loop.create_task(self._register_persistent_views())

    async def _register_persistent_views(self):
        """Re-register all persistent views so buttons/selects survive restarts."""
        await self.bot.wait_until_ready()
        # Register the select menu with current open jobs
        jobs = await self._get_open_jobs()
        if jobs:
            self.bot.add_view(JobSelectView(jobs))
        else:
            self.bot.add_view(EmptyPanelView())

        # Re-register all pending review views
        async for app in self.coll.find({"type": "application", "status": "pending"}):
            view = ReviewView(app["application_id"])
            self.bot.add_view(view)

    async def _block_from_modmail(self, user_id: int):
        """Temporarily block a user from creating Modmail threads during their application."""
        try:
            self.bot.blocked_users[str(user_id)] = "careers_application_in_progress"
        except Exception:
            pass

    async def _unblock_from_modmail(self, user_id: int):
        """Remove the temporary Modmail block once the application is complete."""
        try:
            self.bot.blocked_users.pop(str(user_id), None)
        except Exception:
            pass

    # ── DB helpers ────────────────────────────────────────────────────────────

    async def _get_open_jobs(self) -> list:
        return await self.coll.find({"type": "job", "open": True}).to_list(length=25)

    async def _get_job(self, job_id: str) -> dict | None:
        return await self.coll.find_one({"type": "job", "job_id": job_id})

    async def _get_application(self, application_id: str) -> dict | None:
        return await self.coll.find_one({"type": "application", "application_id": application_id})

    async def _update_panel(self):
        """Edit the persistent panel message to reflect current open jobs."""
        config = await self.coll.find_one({"type": "panel_config"})
        if not config:
            return
        try:
            guild = self.bot.get_guild(config["guild_id"])
            channel = guild.get_channel(config["channel_id"])
            message = await channel.fetch_message(config["message_id"])
        except Exception:
            return

        jobs = await self._get_open_jobs()
        embed = self._build_panel_embed(jobs)

        if jobs:
            view = JobSelectView(jobs)
            self.bot.add_view(view)
        else:
            view = EmptyPanelView()

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
                f"`{job['title']}` - {job.get('description', 'No description provided.')}"
                for job in jobs
            )
        else:
            positions_value = (
                "We're not currently looking for the newest recruits to join our team. "
                "Check back in with us in the future for your desired opportunity."
            )

        embed.add_field(
            name=f"{EMOJI_CALENDAR} Open Positions",
            value=positions_value,
            inline=False,
        )
        embed.set_footer(text="Icelandair Careers · Last updated", icon_url=BASIC_LOGO_URL)
        embed.timestamp = utcnow()
        return embed

    # ── Application flow ──────────────────────────────────────────────────────

    async def handle_application_start(self, interaction: discord.Interaction, job_id: str):
        """Called when a user selects a job from the panel dropdown."""
        member = interaction.user

        # Check ineligible role
        if any(r.id == INELIGIBLE_ROLE_ID for r in member.roles):
            return await interaction.response.send_message(
                f"{EMOJI_ALERT} You are not eligible to apply for positions at this time.",
                ephemeral=True,
            )

        # Check already in a session
        if member.id in self.active_sessions:
            return await interaction.response.send_message(
                f"{EMOJI_ALERT} You already have an active application in progress. "
                "Please complete or wait for it to expire before starting a new one.",
                ephemeral=True,
            )

        # Check existing pending application
        existing = await self.coll.find_one({
            "type": "application",
            "user_id": member.id,
            "status": "pending",
        })
        if existing:
            return await interaction.response.send_message(
                f"{EMOJI_ALERT} You already have a pending application under review. "
                "Please wait for it to be processed before applying again.",
                ephemeral=True,
            )

        job = await self._get_job(job_id)
        if not job or not job.get("open"):
            return await interaction.response.send_message(
                f"{EMOJI_ALERT} This position is no longer accepting applications.",
                ephemeral=True,
            )

        # Try to open DMs
        try:
            await member.send(
                f"{EMOJI_LOGO_BLUE} **Icelandair Careers**\n\n"
                f"Thank you for your interest in the **{job['title']}** position. "
                "I'll now guide you through the application. Please answer each question carefully.\n\n"
                f"You have **60 minutes** to complete this application. "
                "If you go quiet for an hour, you'll receive a reminder and then a further hour before the application expires."
            )
        except discord.Forbidden:
            return await interaction.response.send_message(
                f"{EMOJI_ALERT} I couldn't open a DM with you. "
                "Please enable **Allow Direct Messages** from server members in your Privacy Settings and try again.",
                ephemeral=True,
            )

        await interaction.response.send_message(
            f"{EMOJI_TAKEOFF} Check your DMs! Your application for **{job['title']}** has started.",
            ephemeral=True,
        )

        self.active_sessions[member.id] = job_id
        await self._block_from_modmail(member.id)
        self.bot.loop.create_task(self._run_application_dm(member, job))

    async def _run_application_dm(self, user: discord.Member, job: dict):
        """Runs the full Q&A flow in the user's DMs."""
        all_questions = STANDARD_QUESTIONS + job.get("questions", [])
        answers = []

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
            answer = None

            while True:
                try:
                    msg = await self.bot.wait_for("message", check=check, timeout=APPLICATION_TIMEOUT)
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
                            msg = await self.bot.wait_for("message", check=check, timeout=APPLICATION_TIMEOUT)
                            answer = msg.content
                            break
                        except asyncio.TimeoutError:
                            # Application expired
                            self.active_sessions.pop(user.id, None)
                            await self._unblock_from_modmail(user.id)
                            await user.send(
                                f"{EMOJI_ALERT} **Application Expired**\n\n"
                                f"Your application for **{job['title']}** has expired due to inactivity. "
                                "You're welcome to apply again in the future."
                            )
                            # Log to staff channel
                            log_channel = self.bot.get_channel(TIMEOUT_LOG_CHANNEL)
                            if log_channel:
                                embed = discord.Embed(
                                    title="Application Expired",
                                    description=(
                                        f"{EMOJI_ALERT} **{user}** (`{user.id}`) did not complete their "
                                        f"application for **{job['title']}** and it has timed out."
                                    ),
                                    color=0xED4245,
                                    timestamp=utcnow(),
                                )
                                embed.set_author(name="Icelandair | Careers", icon_url=LOGO_CIRCLE_BLUE_URL)
                                embed.set_footer(icon_url=BASIC_LOGO_URL, text="Icelandair Careers")
                                await log_channel.send(embed=embed)
                            return

            answers.append({"question": question, "answer": answer})

        # All questions answered — show summary
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
        await self._unblock_from_modmail(user.id)

        if not view.confirmed:
            await user.send(
                f"{EMOJI_ALERT} Your application has been cancelled. You're welcome to apply again at any time."
            )
            return

        # Save to DB
        application_id = short_id()
        await self.coll.insert_one({
            "type": "application",
            "application_id": application_id,
            "job_id": job["job_id"],
            "job_title": job["title"],
            "user_id": user.id,
            "user_tag": str(user),
            "answers": answers,
            "status": "pending",
            "reviewer_notes": None,
            "submitted_at": utcnow(),
        })

        await user.send(
            f"✅ **Application Submitted!**\n\n"
            f"Your application for **{job['title']}** has been received. "
            f"Our team will review it and get back to you. Your application reference is `{application_id}`."
        )

        # Post to staff channel
        await self._post_submission_to_staff(user, job, answers, application_id)

    async def _post_submission_to_staff(
        self,
        user: discord.Member,
        job: dict,
        answers: list,
        application_id: str,
    ):
        submission_channel_id = job.get("submission_channel_id")
        if not submission_channel_id:
            return
        channel = self.bot.get_channel(submission_channel_id)
        if not channel:
            return

        guild = channel.guild
        member = guild.get_member(user.id)

        embed = discord.Embed(
            title=f"{EMOJI_PERSON} New Application — {job['title']}",
            color=EMBED_COLOR,
            timestamp=utcnow(),
        )
        embed.set_author(name="Icelandair | Careers", icon_url=LOGO_CIRCLE_BLUE_URL)
        embed.set_footer(text=f"Application ID: {application_id}", icon_url=BASIC_LOGO_URL)

        # Applicant info
        embed.add_field(name="Applicant", value=f"{user.mention} (`{user}` · `{user.id}`)", inline=False)

        if member:
            joined = discord.utils.format_dt(member.joined_at, style="D") if member.joined_at else "Unknown"
            created = discord.utils.format_dt(user.created_at, style="D")
            embed.add_field(name="Account Created", value=created, inline=True)
            embed.add_field(name="Joined Server", value=joined, inline=True)
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
                f"{EMOJI_ALERT} You don't have permission to review applications.",
                ephemeral=True,
            )

        app = await self._get_application(application_id)
        if not app:
            return await interaction.response.send_message(
                f"{EMOJI_ALERT} Could not find application `{application_id}`.",
                ephemeral=True,
            )
        if app["status"] != "pending":
            return await interaction.response.send_message(
                f"{EMOJI_ALERT} This application has already been reviewed (status: **{app['status']}**).",
                ephemeral=True,
            )

        modal = ReviewReasonModal(accepted=accepted)
        await interaction.response.send_modal(modal)
        await modal.wait()

        status = "accepted" if accepted else "declined"
        color = 0x57F287 if accepted else 0xED4245
        result_word = "Accepted" if accepted else "Declined"

        # Update DB
        await self.coll.update_one(
            {"application_id": application_id},
            {
                "$set": {
                    "status": status,
                    "reviewer_id": interaction.user.id,
                    "reviewer_notes": modal.submitted_reason,
                    "reviewed_at": utcnow(),
                }
            },
        )

        # DM applicant
        applicant = self.bot.get_user(app["user_id"])
        if applicant:
            if accepted:
                dm_embed = discord.Embed(
                    title=f"✅ Application Accepted — {app['job_title']}",
                    description=(
                        f"Congratulations — we are pleased to offer you the position of "
                        f"**{app['job_title']}** with Icelandair. On behalf of the entire team, "
                        f"welcome aboard. A member of our team will be in touch shortly with "
                        f"further details regarding your onboarding."
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
                        f"we regret to inform you that we will not be moving forward with your "
                        f"application at this time. We wish you the very best in your future endeavours."
                    ),
                    color=0xED4245,
                    timestamp=utcnow(),
                )

            dm_embed.set_author(name="Icelandair | Careers", icon_url=LOGO_CIRCLE_BLUE_URL)

            if modal.submitted_message:
                quote_label = "Message from the review team"
                dm_embed.add_field(
                    name=quote_label,
                    value=f"*\"{modal.submitted_message}\"*",
                    inline=False,
                )

            dm_embed.set_footer(text=f"Icelandair Careers · Application ID: {application_id}", icon_url=BASIC_LOGO_URL)

            try:
                await applicant.send(embed=dm_embed)
            except discord.Forbidden:
                pass

        # Update the staff embed
        original_embed = interaction.message.embeds[0] if interaction.message.embeds else None
        if original_embed:
            original_embed.colour = discord.Colour(color)
            original_embed.add_field(
                name=f"{'✅' if accepted else '✖️'} {result_word} by",
                value=f"{interaction.user.mention}",
                inline=True,
            )

        disabled_view = discord.ui.View()
        accept_btn = discord.ui.Button(label="Accept", style=discord.ButtonStyle.success, disabled=True)
        decline_btn = discord.ui.Button(label="Decline", style=discord.ButtonStyle.danger, disabled=True)
        disabled_view.add_item(accept_btn)
        disabled_view.add_item(decline_btn)

        await interaction.message.edit(embed=original_embed, view=disabled_view)
        notified = " The applicant has been notified." if applicant and modal.submitted_message else ""
        await interaction.followup.send(
            f"✅ Application `{application_id}` has been **{status}**.{notified}",
            ephemeral=True,
        )

    # ── Job creation modal ───────────────────────────────────────────────────────

    async def _run_job_creation_wizard(self, interaction: discord.Interaction):
        """DM-based job creation wizard triggered after slash command."""
        author = interaction.user

        def dm_check(m):
            return m.author.id == author.id and isinstance(m.channel, discord.DMChannel)

        async def ask(prompt: str) -> str | None:
            await author.send(prompt)
            try:
                msg = await self.bot.wait_for("message", check=dm_check, timeout=300)
                return msg.content.strip()
            except asyncio.TimeoutError:
                await author.send(f"{EMOJI_ALERT} Job creation timed out.")
                return None

        title = await ask(f"{EMOJI_PEN} **Step 1/4** — What is the job title?")
        if not title:
            return

        description = await ask(f"{EMOJI_PEN} **Step 2/4** — Provide a short description for this role (shown in the panel and dropdown).")
        if not description:
            return

        channel_raw = await ask(
            f"{EMOJI_PEN} **Step 3/4** — Paste the ID of the channel where applications should be submitted."
        )
        if not channel_raw:
            return
        try:
            submission_channel_id = int(channel_raw)
            channel = self.bot.get_channel(submission_channel_id)
            if not channel:
                return await author.send(f"{EMOJI_ALERT} Could not find that channel. Job creation cancelled.")
        except ValueError:
            return await author.send(f"{EMOJI_ALERT} Invalid channel ID. Job creation cancelled.")

        await author.send(
            f"{EMOJI_PEN} **Step 4/4** — Now enter your custom questions one by one.\n"
            f"You can add up to **{MAX_QUESTIONS}** questions. Send `done` when finished.\n\n"
            "Note: Discord username, Discord ID, timezone, and Roblox username are already collected automatically."
        )

        questions = []
        while len(questions) < MAX_QUESTIONS:
            try:
                msg = await self.bot.wait_for("message", check=dm_check, timeout=300)
            except asyncio.TimeoutError:
                await author.send(f"{EMOJI_ALERT} Job creation timed out.")
                return

            if msg.content.strip().lower() == "done":
                break
            questions.append(msg.content.strip())
            await author.send(
                f"✅ Question {len(questions)} saved. Send another or type `done` to finish "
                f"({MAX_QUESTIONS - len(questions)} remaining)."
            )

        if not questions:
            return await author.send(f"{EMOJI_ALERT} You must provide at least one custom question. Job creation cancelled.")

        job_id = short_id()
        await self.coll.insert_one({
            "type": "job",
            "job_id": job_id,
            "title": title,
            "description": description[:100],
            "submission_channel_id": submission_channel_id,
            "questions": questions,
            "open": True,
            "created_by": author.id,
            "created_at": utcnow(),
        })

        await author.send(
            f"✅ **Job posting created!**\n"
            f"Title: **{title}**\n"
            f"Job ID: `{job_id}`\n"
            f"Questions: {len(questions)} custom + {len(STANDARD_QUESTIONS)} standard\n\n"
            "The application panel will update automatically."
        )
        await self._update_panel()

    # ── Slash commands ────────────────────────────────────────────────────────

    job_slash = app_commands.Group(name="job", description="Icelandair Careers management")

    @job_slash.command(name="create", description="Create a new job posting via DM wizard")
    async def slash_job_create(self, interaction: discord.Interaction):
        if not is_manager(interaction.user):
            return await interaction.response.send_message(
                f"{EMOJI_ALERT} You don't have permission to create job postings.", ephemeral=True
            )
        try:
            await interaction.user.send(f"{EMOJI_PEN} **Icelandair Careers — Job Creation**\n\nLet's get started!")
        except discord.Forbidden:
            return await interaction.response.send_message(
                f"{EMOJI_ALERT} I couldn't DM you. Please enable Direct Messages and try again.", ephemeral=True
            )
        await interaction.response.send_message(
            f"{EMOJI_ALERT} Check your DMs — the job creation wizard has started.", ephemeral=True
        )
        self.bot.loop.create_task(self._run_job_creation_wizard(interaction))

    @job_slash.command(name="list", description="List all job postings")
    async def slash_job_list(self, interaction: discord.Interaction):
        if not is_manager(interaction.user):
            return await interaction.response.send_message(
                f"{EMOJI_ALERT} You don't have permission to view job postings.", ephemeral=True
            )
        jobs = await self.coll.find({"type": "job"}).to_list(length=100)
        if not jobs:
            return await interaction.response.send_message("No job postings found.", ephemeral=True)

        embed = discord.Embed(title="Job Postings", color=EMBED_COLOR)
        embed.set_author(name="Icelandair | Careers", icon_url=LOGO_CIRCLE_BLUE_URL)
        for job in jobs:
            status = "🟢 Open" if job.get("open") else "🔴 Closed"
            embed.add_field(
                name=f"`{job['job_id']}` — {job['title']}",
                value=f"{status} · {job.get('description', '')}",
                inline=False,
            )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @job_slash.command(name="close", description="Close a job posting")
    @app_commands.describe(job_id="The job ID to close")
    async def slash_job_close(self, interaction: discord.Interaction, job_id: str):
        if not is_manager(interaction.user):
            return await interaction.response.send_message(
                f"{EMOJI_ALERT} You don't have permission to close job postings.", ephemeral=True
            )
        job = await self._get_job(job_id)
        if not job:
            return await interaction.response.send_message(
                f"{EMOJI_ALERT} Job `{job_id}` not found.", ephemeral=True
            )
        await self.coll.update_one({"job_id": job_id}, {"$set": {"open": False}})
        await interaction.response.send_message(
            f"✅ Job `{job_id}` (**{job['title']}**) has been closed. Existing applications are still processable.",
            ephemeral=True,
        )
        await self._update_panel()

    @job_slash.command(name="reopen", description="Reopen a previously closed job posting")
    @app_commands.describe(job_id="The job ID to reopen")
    async def slash_job_reopen(self, interaction: discord.Interaction, job_id: str):
        if not is_manager(interaction.user):
            return await interaction.response.send_message(
                f"{EMOJI_ALERT} You don't have permission to reopen job postings.", ephemeral=True
            )
        job = await self._get_job(job_id)
        if not job:
            return await interaction.response.send_message(
                f"{EMOJI_ALERT} Job `{job_id}` not found.", ephemeral=True
            )
        await self.coll.update_one({"job_id": job_id}, {"$set": {"open": True}})
        await interaction.response.send_message(
            f"✅ Job `{job_id}` (**{job['title']}**) has been reopened.",
            ephemeral=True,
        )
        await self._update_panel()

    @job_slash.command(name="applications", description="List all applications for a job")
    @app_commands.describe(job_id="The job ID to list applications for")
    async def slash_job_applications(self, interaction: discord.Interaction, job_id: str):
        if not is_manager(interaction.user):
            return await interaction.response.send_message(
                f"{EMOJI_ALERT} You don't have permission to view applications.", ephemeral=True
            )
        job = await self._get_job(job_id)
        if not job:
            return await interaction.response.send_message(
                f"{EMOJI_ALERT} Job `{job_id}` not found.", ephemeral=True
            )
        apps = await self.coll.find({"type": "application", "job_id": job_id}).to_list(length=100)
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

    @job_slash.command(name="view", description="View a specific application in full")
    @app_commands.describe(application_id="The application ID to view")
    async def slash_job_view(self, interaction: discord.Interaction, application_id: str):
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
            embed.add_field(name="🔒 Internal Notes", value=app["reviewer_notes"], inline=False)
        for i, qa in enumerate(app["answers"], 1):
            label = "Standard" if i <= len(STANDARD_QUESTIONS) else f"Q{i - len(STANDARD_QUESTIONS)}"
            embed.add_field(
                name=f"`{label}` {qa['question'][:80]}",
                value=qa["answer"][:1024] or "*No answer*",
                inline=False,
            )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @job_slash.command(name="panel", description="Post the persistent careers panel in this channel")
    async def slash_job_panel(self, interaction: discord.Interaction):
        if not is_manager(interaction.user):
            return await interaction.response.send_message(
                f"{EMOJI_ALERT} You don't have permission to post the careers panel.", ephemeral=True
            )
        jobs = await self._get_open_jobs()
        embed = self._build_panel_embed(jobs)
        if jobs:
            view = JobSelectView(jobs)
            self.bot.add_view(view)
        else:
            view = EmptyPanelView()

        await interaction.response.send_message("✅ Posting panel...", ephemeral=True)
        msg = await interaction.channel.send(embed=embed, view=view)

        await self.coll.update_one(
            {"type": "panel_config"},
            {
                "$set": {
                    "type": "panel_config",
                    "guild_id": interaction.guild.id,
                    "channel_id": interaction.channel.id,
                    "message_id": msg.id,
                }
            },
            upsert=True,
        )

    @job_slash.command(name="help", description="Show the careers plugin command reference")
    async def slash_job_help(self, interaction: discord.Interaction):
        is_mgr = is_manager(interaction.user)
        embed = discord.Embed(
            title="<:dblaptopbg:1374617774693023754> Careers Plugin — Command Reference",
            description="All commands are slash commands starting with `/job`.",
            color=EMBED_COLOR,
        )
        embed.set_author(name="Icelandair | Careers", icon_url=LOGO_CIRCLE_BLUE_URL)
        embed.set_footer(text="Icelandair Careers", icon_url=BASIC_LOGO_URL)

        embed.add_field(
            name=f"{EMOJI_PEN} Setup",
            value=(
                "`/job panel` — Post the persistent careers panel *(run once only)*"
            ),
            inline=False,
        )
        if is_mgr:
            embed.add_field(
                name=f"{EMOJI_TAKEOFF} Job Management",
                value=(
                    "`/job create` — Start the DM wizard to create a new job posting\n"
                    "`/job list` — List all jobs with IDs and open/closed status\n"
                    "`/job close <id>` — Close a job posting *(panel updates automatically)*\n"
                    "`/job reopen <id>` — Reopen a previously closed job posting"
                ),
                inline=False,
            )
            embed.add_field(
                name=f"{EMOJI_PERSON} Application Review",
                value=(
                    "`/job applications <id>` — List all applications for a job\n"
                    "`/job view <app_id>` — View a full application including internal notes\n"
                    "`Accept / Decline buttons` — Appear on each submission in the staff channel"
                ),
                inline=False,
            )
        embed.add_field(
            name=f"{EMOJI_ALERT} Automatic Behaviours",
            value=(
                "**Panel auto-update** — Edits itself with latest roles and a new timestamp on any job change\n"
                "**Application timeout** — Reminder DM after 1 hour of inactivity, expires after a further hour "
                "with a log posted to the timeout channel\n"
                "**Standard questions** — Discord username, ID, timezone, and Roblox username are always "
                "collected first before any custom questions"
            ),
            inline=False,
        )
        if not is_mgr:
            embed.set_footer(
                text="Icelandair Careers · Management commands hidden — insufficient permissions",
                icon_url=BASIC_LOGO_URL,
            )
        await interaction.response.send_message(embed=embed, ephemeral=True)


async def setup(bot):
    cog = IcelandairCareers(bot)
    await bot.add_cog(cog)
    bot.tree.add_command(cog.job_slash)
    await bot.tree.sync()
