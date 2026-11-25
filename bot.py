# =====================================================================
# PART 1 OF 2 — PASTE THIS FIRST AT LINE 1 OF YOUR GITHUB FILE
# CHICAGO CROSSFIRE — 24/7 TICKET & TRANSCRIPT BOT (V2 MOBILE-READY)
# Requirements: discord.py, aiohttp | Env Var: BOT_TOKEN
# =====================================================================
import asyncio, datetime, io, json, os, re, time, aiohttp
from aiohttp import web
import discord
from discord.ext import commands

TOKEN = os.getenv("BOT_TOKEN", "").strip().strip('"').strip("'")
CFG_FILE = "chicago_ticket_cfg.json"
START, STATUS = time.time(), "Starting..."

# 6 Chicago Crossfire Ticket Categories: (Title, Prefix, Emoji, Color, Welcome, Subject Label)
CATEGORIES = {
    "support": ("General Support", "support", "💬", discord.Color.blurple(), "Describe your question and our staff will assist shortly.", "Question / Topic"),
    "report": ("Report Player / Exploiter", "report", "🚨", discord.Color.red(), "Attach video clips, screenshots, or proof of the rulebreaker.", "Reported Roblox Username"),
    "appeal": ("Ban / Mute Appeal", "appeal", "⚖️", discord.Color.gold(), "Explain why you were punished and why it should be lifted.", "Punishment Type"),
    "bug": ("Bug & Glitch Report", "bug", "🐛", discord.Color.orange(), "Share steps to reproduce the bug, location, and clips.", "Where did the bug happen?"),
    "shop": ("Gamepass / Shop Issue", "shop", "💳", discord.Color.purple(), "Share the item name and a screenshot of your Roblox receipt.", "Gamepass / Item Name"),
    "faction": ("Faction / PD / Partner", "faction", "🤝", discord.Color.green(), "Share your faction/PD details or partnership proposal.", "Faction / PD / Server Name"),
}


def load_cfg() -> dict:
    """Loads saved guild ticket configuration from disk."""
    try:
        return json.load(open(CFG_FILE, encoding="utf-8")) if os.path.exists(CFG_FILE) else {}
    except Exception:
        return {}


def save_cfg() -> None:
    """Saves guild ticket configuration to disk."""
    try:
        json.dump(CFG, open(CFG_FILE, "w", encoding="utf-8"), indent=2)
    except Exception:
        pass


CFG = load_cfg()


def gcfg(gid: int) -> dict:
    return CFG.get(str(gid), {})


def is_staff(m: discord.Member) -> bool:
    """Checks if a user is Admin, Channel Manager, Staff, or High Command."""
    if not isinstance(m, discord.Member):
        return False
    if m.guild_permissions.administrator or m.guild_permissions.manage_channels:
        return True
    c = gcfg(m.guild.id)
    rids = {c.get("staff_role", 0), c.get("hc_role", 0)}
    return any(r.id in rids and r.id != 0 for r in m.roles)


# --- 1. 24/7 RENDER KEEP-ALIVE WEB SERVER ---
async def run_web():
    """Binds to Render's PORT and serves /health for cron-job.org pings."""
    app = web.Application()
    health = lambda r: web.Response(text=f"Chicago Crossfire Tickets V2 | Up:{int((time.time()-START)//60)}m | {STATUS}")
    app.router.add_get("/", health)
    app.router.add_get("/health", health)
    r = web.AppRunner(app)
    await r.setup()
    await web.TCPSite(r, "0.0.0.0", int(os.environ.get("PORT", 10000))).start()


async def self_ping():
    """Backup keep-alive ping loop."""
    await asyncio.sleep(25)
    url = os.environ.get("RENDER_EXTERNAL_URL") or f"http://127.0.0.1:{os.environ.get('PORT', 10000)}/"
    async with aiohttp.ClientSession() as s:
        while True:
            try:
                async with s.get(url, timeout=10):
                    pass
            except Exception:
                pass
            await asyncio.sleep(240)


# --- 2. TRANSCRIPT GENERATOR & CLOSE HANDLER ---
async def make_transcript(ch: discord.TextChannel, closer: discord.Member, reason: str) -> tuple[bytes, int]:
    """Exports every message, embed summary, and attachment URL into a .txt transcript."""
    now = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines = [
        "==========================================================",
        "        CHICAGO CROSSFIRE — OFFICIAL TICKET TRANSCRIPT    ",
        "==========================================================",
        f"Server    : {ch.guild.name}",
        f"Channel   : #{ch.name} ({ch.id})",
        f"Closed By : {closer} ({closer.id})",
        f"Reason    : {reason}",
        f"Closed At : {now}",
        "==========================================================\n",
    ]
    count = 0
    async for m in ch.history(limit=None, oldest_first=True):
        count += 1
        ts = m.created_at.strftime("%Y-%m-%d %H:%M:%S")
        tag = " [BOT]" if m.author.bot else ""
        lines.append(f"[{ts}] {m.author.display_name} (@{m.author.name}){tag}:")
        if m.content:
            lines.append(f"  {m.content}")
        for e in m.embeds:
            if e.title:
                lines.append(f"  [Embed] {e.title}")
            for f in e.fields:
                lines.append(f"  • {f.name}: {f.value}")
        for a in m.attachments:
            lines.append(f"  [File] {a.filename} -> {a.url}")
        lines.append("")
    return "\n".join(lines).encode("utf-8"), count


async def close_ticket(i: discord.Interaction, reason: str = "Resolved by staff") -> None:
    """DMs the transcript to the opener, archives to staff logs, and deletes the ticket."""
    ch, g = i.channel, i.guild
    topic = ch.topic or ""
    if not topic.startswith("CCTicket |"):
        return await i.response.send_message("❌ Use this inside an active Chicago Crossfire ticket channel.", ephemeral=True)

    await i.response.send_message(f"📄 Generating transcript & DMing the player... Closing in **5s**.\n> **Reason:** {reason}")
    uid_m = re.search(r"UserID:(\d+)", topic)
    typ_m = re.search(r"Type:([a-z]+)", topic)
    opener_id = int(uid_m.group(1)) if uid_m else None
    t_title = CATEGORIES.get(typ_m.group(1) if typ_m else "support", CATEGORIES["support"])[0]

    raw, msg_n = await make_transcript(ch, i.user, reason)
    fname = f"transcript-{ch.name}-{datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%d-%H%M')}.txt"

    # 1. DM Transcript to Ticket Opener (safe lookup if user left server)
    dm_note = "⚠️ Player not found"
    if opener_id:
        user = g.get_member(opener_id)
        if not user:
            try:
                user = await i.client.fetch_user(opener_id)
            except Exception:
                user = None
        if user:
            dm_emb = discord.Embed(
                title=f"📄 {g.name} — Ticket Transcript",
                description=f"Your **`{t_title}`** ticket (`#{ch.name}`) has been closed. Your full chat transcript is attached below.",
                color=discord.Color.from_rgb(220, 53, 69),
            )
            dm_emb.add_field(name="🛡️ Closed By", value=i.user.display_name, inline=True)
            dm_emb.add_field(name="💬 Messages", value=f"`{msg_n}`", inline=True)
            dm_emb.add_field(name="📝 Reason", value=reason[:400], inline=False)
            dm_emb.set_footer(text="Chicago Crossfire Support System")
            try:
                await user.send(embed=dm_emb, file=discord.File(io.BytesIO(raw), filename=fname))
                dm_note = "✅ Delivered to Player DMs"
            except discord.Forbidden:
                dm_note = "⚠️ Player has DMs disabled"
            except Exception as e:
                dm_note = f"⚠️ Failed ({type(e).__name__})"

    # 2. Archive Copy in Staff Transcript Log Channel
    log_ch = g.get_channel(gcfg(g.id).get("log_ch", 0))
    if log_ch:
        log_emb = discord.Embed(
            title=f"📁 Ticket Closed — #{ch.name}",
            description=(
                f"🎫 **Category:** `{t_title}`\n"
                f"👤 **Opener:** {f'<@{opener_id}>' if opener_id else 'Unknown'}\n"
                f"🛡️ **Closed By:** {i.user.mention}\n"
                f"💬 **Messages:** `{msg_n}` • 📬 **DM:** {dm_note}\n"
                f"📝 **Reason:** {reason[:400]}"
            ),
            color=discord.Color.dark_grey(),
            timestamp=discord.utils.utcnow(),
        )
        try:
            await log_ch.send(embed=log_emb, file=discord.File(io.BytesIO(raw), filename=fname))
        except Exception:
            pass

    await asyncio.sleep(5)
    try:
        await ch.delete(reason=f"Closed by {i.user}: {reason}")
    except Exception:
        pass


# --- 3. MODALS ---
class IntakeModal(discord.ui.Modal):
    """Popup form shown when a player opens any Chicago Crossfire ticket."""

    def __init__(self, key: str):
        title, _, emoji, _, _, subj = CATEGORIES[key]
        super().__init__(title=f"{emoji} {title}"[:45])
        self.key = key
        self.rblx = discord.ui.TextInput(label="Your Roblox Username", placeholder="Exact Roblox username...", required=True, max_length=50)
        self.subj = discord.ui.TextInput(label=subj[:45], required=True, max_length=120)
        self.info = discord.ui.TextInput(label="Full Details / Proof Links", style=discord.TextStyle.paragraph, required=True, max_length=900)
        for item in (self.rblx, self.subj, self.info):
            self.add_item(item)

    async def on_submit(self, i: discord.Interaction):
        await i.response.defer(ephemeral=True)
        g, u = i.guild, i.user
        title, pfx, emoji, col, tip, subj = CATEGORIES[self.key]

        for ch in g.text_channels:
            if ch.topic and f"UserID:{u.id}" in ch.topic and f"Type:{self.key}" in ch.topic:
                return await i.followup.send(f"⚠️ You already have an open **{title}** ticket: {ch.mention}", ephemeral=True)

        c = gcfg(g.id)
        s_role = g.get_role(c.get("staff_role", 0))
        hc_role = g.get_role(c.get("hc_role", 0))
        cat = g.get_channel(c.get("cat_id", 0))
        if not isinstance(cat, discord.CategoryChannel):
            cat = None

        ow = {
            g.default_role: discord.PermissionOverwrite(view_channel=False),
            u: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True, attach_files=True, embed_links=True),
            g.me: discord.PermissionOverwrite(view_channel=True, send_messages=True, manage_channels=True, read_message_history=True, attach_files=True, embed_links=True),
        }
        pings = [u.mention]
        for role in (s_role, hc_role):
            if role:
                ow[role] = discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True, attach_files=True)
        if s_role:
            pings.append(s_role.mention)

        safe = re.sub(r"[^a-z0-9-]", "", u.name.lower())[:16] or "player"
        try:
            tch = await g.create_text_channel(name=f"{pfx}-{safe}", category=cat, overwrites=ow, topic=f"CCTicket | Type:{self.key} | UserID:{u.id}")
        except discord.Forbidden:
            return await i.followup.send("❌ Missing **Manage Channels** permission! Contact an Admin.", ephemeral=True)

        emb = discord.Embed(title=f"{emoji} Chicago Crossfire — {title}", description=f"Welcome {u.mention}! {tip}", color=col, timestamp=discord.utils.utcnow())
        emb.add_field(name="🎮 Roblox Username", value=f"`{self.rblx.value}`", inline=True)
        emb.add_field(name="🙋‍♂️ Claimed By", value="*Unclaimed*", inline=True)
        emb.add_field(name="🚦 Priority", value="🟢 `Normal`", inline=True)
        emb.add_field(name=f"📌 {subj}", value=self.subj.value, inline=False)
        emb.add_field(name="📝 Details / Evidence", value=self.info.value, inline=False)
        emb.set_footer(text="Full transcript is automatically sent to your DMs when closed")

        await tch.send(content=" ".join(pings), embed=emb, view=TicketControls())
        await i.followup.send(f"✅ Your **{title}** ticket is ready: {tch.mention}", ephemeral=True)


class CloseModal(discord.ui.Modal, title="🔒 Close Ticket & DM Transcript"):
    reason = discord.ui.TextInput(label="Closing Reason (Sent to Player DM)", default="Issue resolved", required=True, max_length=300)

    async def on_submit(self, i: discord.Interaction):
        await close_ticket(i, self.reason.value)

# =====================================================================
# PART 2 OF 2 — PASTE THIS DIRECTLY BELOW PART 1 (FAR LEFT EDGE)
# =====================================================================
class TicketControls(discord.ui.View):
    """Persistent Claim, Escalate & Close buttons inside every ticket channel."""

    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Claim Ticket", style=discord.ButtonStyle.success, emoji="🙋‍♂️", custom_id="ccf:claim")
    async def b_claim(self, i: discord.Interaction, b: discord.ui.Button):
        if not is_staff(i.user):
            return await i.response.send_message("❌ Staff only.", ephemeral=True)
        b.disabled, b.style, b.label = True, discord.ButtonStyle.secondary, f"Claimed by {i.user.display_name}"[:75]
        emb = i.message.embeds[0] if i.message.embeds else None
        if emb and len(emb.fields) > 1:
            emb.set_field_at(1, name="🙋‍♂️ Claimed By", value=i.user.mention, inline=True)
            await i.response.edit_message(embed=emb, view=self)
        else:
            await i.response.edit_message(view=self)
        await i.channel.send(f"✅ {i.user.mention} has claimed this ticket and will assist you now!")

    @discord.ui.button(label="Escalate (Urgent)", style=discord.ButtonStyle.primary, emoji="⬆️", custom_id="ccf:esc")
    async def b_esc(self, i: discord.Interaction, b: discord.ui.Button):
        if not is_staff(i.user):
            return await i.response.send_message("❌ Staff only.", ephemeral=True)
        b.disabled, b.label = True, "Escalated 🔴"
        emb = i.message.embeds[0] if i.message.embeds else None
        if emb:
            emb.color = discord.Color.dark_red()
            if len(emb.fields) > 2:
                emb.set_field_at(2, name="🚦 Priority", value="🔴 `URGENT / ESCALATED`", inline=True)
            await i.response.edit_message(embed=emb, view=self)
        else:
            await i.response.edit_message(view=self)
        try:
            if not i.channel.name.startswith("urgent-"):
                await i.channel.edit(name=f"urgent-{i.channel.name}"[:90])
        except Exception:
            pass
        hc_id = gcfg(i.guild.id).get("hc_role", 0)
        ping = f"<@&{hc_id}>" if hc_id else "**High Command**"
        await i.channel.send(f"🚨 {ping} — Ticket escalated to **URGENT** by {i.user.mention}!")

    @discord.ui.button(label="Close & DM Transcript", style=discord.ButtonStyle.danger, emoji="🔒", custom_id="ccf:close")
    async def b_close(self, i: discord.Interaction, b: discord.ui.Button):
        opener = f"UserID:{i.user.id}" in (i.channel.topic or "")
        if not (is_staff(i.user) or opener):
            return await i.response.send_message("❌ Only staff or the ticket opener can close this.", ephemeral=True)
        await i.response.send_modal(CloseModal())


class TicketPanel(discord.ui.View):
    """6-Button Chicago Crossfire Support Center Panel."""

    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="General Support", style=discord.ButtonStyle.primary, emoji="💬", custom_id="ccfp:sup", row=0)
    async def b1(self, i: discord.Interaction, b: discord.ui.Button): await i.response.send_modal(IntakeModal("support"))

    @discord.ui.button(label="Report Player / Exploiter", style=discord.ButtonStyle.danger, emoji="🚨", custom_id="ccfp:rep", row=0)
    async def b2(self, i: discord.Interaction, b: discord.ui.Button): await i.response.send_modal(IntakeModal("report"))

    @discord.ui.button(label="Ban / Mute Appeal", style=discord.ButtonStyle.secondary, emoji="⚖️", custom_id="ccfp:app", row=1)
    async def b3(self, i: discord.Interaction, b: discord.ui.Button): await i.response.send_modal(IntakeModal("appeal"))

    @discord.ui.button(label="Bug & Glitch Report", style=discord.ButtonStyle.secondary, emoji="🐛", custom_id="ccfp:bug", row=1)
    async def b4(self, i: discord.Interaction, b: discord.ui.Button): await i.response.send_modal(IntakeModal("bug"))

    @discord.ui.button(label="Gamepass / Shop Issue", style=discord.ButtonStyle.primary, emoji="💳", custom_id="ccfp:shp", row=2)
    async def b5(self, i: discord.Interaction, b: discord.ui.Button): await i.response.send_modal(IntakeModal("shop"))

    @discord.ui.button(label="Faction / PD / Partner", style=discord.ButtonStyle.success, emoji="🤝", custom_id="ccfp:fac", row=2)
    async def b6(self, i: discord.Interaction, b: discord.ui.Button): await i.response.send_modal(IntakeModal("faction"))


# --- 4. BOT CORE & SLASH COMMANDS ---
class ChicagoBot(commands.Bot):
    def __init__(self, mi: bool = True):
        it = discord.Intents.default()
        it.message_content = mi
        super().__init__(command_prefix="!", intents=it, help_command=None)
        self._synced = False

    async def setup_hook(self):
        self.add_view(TicketPanel())
        self.add_view(TicketControls())
        if not self._synced:
            try:
                await self.tree.sync()
                self._synced = True
            except Exception as e:
                print(f"Sync notice: {e}")

    async def on_ready(self):
        global STATUS
        STATUS = f"ONLINE as {self.user}"
        print(f"✅ Chicago Crossfire Ticket Bot ONLINE as {self.user}")
        try:
            await self.change_presence(activity=discord.Activity(type=discord.ActivityType.watching, name="Chicago Crossfire Support 🎫"))
        except Exception:
            pass


def reg_cmds(bot: ChicagoBot):
    @bot.tree.command(name="ticket_setup", description="Set Staff Role, Ticket Category, Transcript Logs & optional High Command Role")
    async def s_setup(i: discord.Interaction, staff_role: discord.Role, ticket_category: discord.CategoryChannel, transcript_log_channel: discord.TextChannel, high_command_role: discord.Role = None):
        if not i.user.guild_permissions.administrator:
            return await i.response.send_message("❌ Administrators only.", ephemeral=True)
        CFG[str(i.guild.id)] = {
            "staff_role": staff_role.id,
            "hc_role": high_command_role.id if high_command_role else 0,
            "cat_id": ticket_category.id,
            "log_ch": transcript_log_channel.id,
        }
        save_cfg()
        await i.response.send_message(
            f"✅ **Chicago Crossfire Tickets Configured!**\n"
            f"🛡️ Staff: {staff_role.mention} | 👑 Escalation Role: {high_command_role.mention if high_command_role else '*None*'}\n"
            f"📁 Category: `{ticket_category.name}` | 📜 Logs: {transcript_log_channel.mention}\n"
            f"👉 Next: Run **`/ticket_panel`** in your support channel!",
            ephemeral=True,
        )

    @bot.tree.command(name="ticket_panel", description="Post the 6-button Chicago Crossfire Support & Ticket Panel")
    async def s_panel(i: discord.Interaction):
        if not is_staff(i.user):
            return await i.response.send_message("❌ Staff only.", ephemeral=True)
        emb = discord.Embed(
            title="🔥 CHICAGO CROSSFIRE — SUPPORT & TICKET CENTER",
            description=(
                "Need help in **Chicago Crossfire**? Pick a category below to open a private ticket with our team!\n\n"
                "💬 **General Support** — Questions & gameplay assistance\n"
                "🚨 **Report Player / Exploiter** — Report cheaters, RDM, or rulebreakers\n"
                "⚖️ **Ban / Mute Appeal** — Appeal an in-game or Discord punishment\n"
                "🐛 **Bug & Glitch Report** — Report map glitches, weapon bugs, or crashes\n"
                "💳 **Gamepass / Shop Issue** — Missing gamepasses or Roblox store issues\n"
                "🤝 **Faction / PD / Partner** — Faction approvals, PD & partnerships\n\n"
                "📄 *Every closed ticket automatically DMs you a full transcript!*"
            ),
            color=discord.Color.from_rgb(220, 53, 69),
        )
        emb.set_footer(text="Chicago Crossfire Support • Do not open troll tickets")
        await i.response.send_message(embed=emb, view=TicketPanel())

    @bot.tree.command(name="add_user", description="Add another player to this ticket")
    async def s_add(i: discord.Interaction, member: discord.Member):
        if not is_staff(i.user) or not (i.channel.topic or "").startswith("CCTicket |"):
            return await i.response.send_message("❌ Staff inside a ticket channel only.", ephemeral=True)
        await i.channel.set_permissions(member, view_channel=True, send_messages=True, read_message_history=True)
        await i.response.send_message(f"➕ Added {member.mention} to this ticket.")

    @bot.tree.command(name="remove_user", description="Remove a player from this ticket")
    async def s_rem(i: discord.Interaction, member: discord.Member):
        if not is_staff(i.user) or not (i.channel.topic or "").startswith("CCTicket |"):
            return await i.response.send_message("❌ Staff inside a ticket channel only.", ephemeral=True)
        await i.channel.set_permissions(member, overwrite=None)
        await i.response.send_message(f"➖ Removed {member.mention} from this ticket.")

    @bot.tree.command(name="rename_ticket", description="Rename this ticket channel cleanly")
    async def s_ren(i: discord.Interaction, new_name: str):
        if not is_staff(i.user) or not (i.channel.topic or "").startswith("CCTicket |"):
            return await i.response.send_message("❌ Staff inside a ticket channel only.", ephemeral=True)
        clean = re.sub(r"[^a-z0-9-]", "-", new_name.lower().strip()).strip("-")[:60] or "ticket"
        await i.channel.edit(name=clean)
        await i.response.send_message(f"✏️ Renamed ticket channel to **`#{clean}`**.")

    @bot.tree.command(name="close", description="Close this ticket and DM the full transcript to the player")
    async def s_cls(i: discord.Interaction, reason: str = "Resolved by staff"):
        if not is_staff(i.user):
            return await i.response.send_message("❌ Staff only.", ephemeral=True)
        await close_ticket(i, reason)


# --- 5. 24/7 ENTRYPOINT WITH 429 AUTO-RETRY ---
async def main():
    global STATUS
    await run_web()
    asyncio.create_task(self_ping())
    if not TOKEN:
        STATUS = "MISSING BOT_TOKEN"
        print("❌ Add BOT_TOKEN in Render Environment Variables!")
        while True:
            await asyncio.sleep(3600)
    mi, w = True, 90
    while True:
        bot = ChicagoBot(mi)
        reg_cmds(bot)
        try:
            await bot.start(TOKEN)
            break
        except discord.PrivilegedIntentsRequired:
            await bot.close()
            mi = False
        except discord.HTTPException as e:
            await bot.close()
            if e.status == 429:
                STATUS = f"429 wait {w}s"
                await asyncio.sleep(w)
                w = min(w + 60, 300)
            else:
                await asyncio.sleep(60)
        except Exception as e:
            await bot.close()
            STATUS = f"Retry ({type(e).__name__})"
            await asyncio.sleep(60)


if __name__ == "__main__":
    asyncio.run(main())
    
