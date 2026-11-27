# =====================================================================
# CHICAGO CROSSFIRE — TICKET BOT V5 (SUPER ADVANCED EDITION)
# Keeps your exact familiar buttons + adds:
#   • Development Application button (Separate In-Game / Bot / Creative forms)
#   • Live Roblox API Account, Age & Ban Verification inside tickets
#   • Dual Transcripts: Dark-Mode Discord HTML + Plain TXT (DM + Staff Archive)
#   • Confidential Staff-Only Internal Notes (archived ONLY in Staff Logs!)
#   • Instant Private Support / Dev Interview Voice Rooms (auto-deleted on close)
#   • Post-Ticket 5-Star Rating System in DMs + Staff Analytics & Leaderboard
#   • Live Queue & Estimated Wait Time + Anti-Spam Cooldown & Ticket Blacklist
#   • Smart On-Call Routing, One-Tap Staff Tools & Macros, Transfer & Dashboard
#   • Auto-SLA Unclaimed Alerts + 12h Nudge + 24h Auto-Close (with Pause toggle)
# Requirements: discord.py, aiohttp | Env Var: BOT_TOKEN
# =====================================================================
import asyncio
import datetime
import html
import io
import json
import os
import re
import time
import aiohttp
from aiohttp import web
import discord
from discord.ext import commands, tasks

TOKEN = os.getenv("BOT_TOKEN", "").strip().strip('"').strip("'")
DB_FILE = "chicago_tickets_v5.json"
START_TIME, BOT_STATUS = time.time(), "Starting..."

# Anti-spam cooldown tracker: {(guild_id, user_id): last_open_timestamp}
OPEN_COOLDOWN: dict[tuple[int, int], float] = {}

# Exact 6 Original Ticket Categories: key -> (Title, Prefix, Emoji, Color, Welcome, SubjectLabel)
CATEGORIES = {
    "support": ("General Support", "support", "💬", discord.Color.blurple(), "Describe your question and our staff will assist shortly.", "Question / Topic"),
    "report": ("Report Player / Exploiter", "report", "🚨", discord.Color.red(), "Attach uncropped video clips (30s+), screenshots, or proof of the rulebreaker.", "Reported Roblox Username"),
    "appeal": ("Ban / Mute Appeal", "appeal", "⚖️", discord.Color.gold(), "Explain why you were punished and why it should be lifted.", "Punishment Type"),
    "bug": ("Bug & Glitch Report", "bug", "🐛", discord.Color.orange(), "Share your device (Mobile/PC), steps to reproduce the bug, location, and clips.", "Where did the bug happen?"),
    "shop": ("Gamepass / Shop Issue", "shop", "💳", discord.Color.purple(), "Share the item name and a screenshot of your Roblox purchase receipt.", "Gamepass / Item Name"),
    "faction": ("Faction / PD / Partner", "faction", "🤝", discord.Color.green(), "Share your faction/PD details or partnership proposal.", "Faction / PD / Server Name"),
}

DEFAULT_MACROS = {
    "video_proof": "🎥 **Evidence Required:** Please share an uncropped video clip (20–30s+ showing context and usernames) so staff can investigate fairly.",
    "receipt": "💳 **Receipt Needed:** Please send a screenshot of your **Roblox Transactions ➔ Purchases** page showing the item name and date (never share your password!).",
    "bloxlink": "🔗 **Bloxlink Fix:** Run `/verify` in commands, link your Roblox account, then run `/update` to sync your roles.",
    "inactivity": "⏳ **Inactivity Check:** Do you still need help? If there is no reply within **12 hours**, this ticket will auto-close and DM your transcript.",
    "dev_test": "🧪 **Dev Test Rules:** Complete the assigned test task using 100% original work (no free models/stolen code) and submit your showcase or file here.",
}


# =====================================================================
# 1. PERSISTENT DATABASE & PERMISSION HELPERS
# =====================================================================
def load_db() -> dict:
    try:
        return json.load(open(DB_FILE, encoding="utf-8")) if os.path.exists(DB_FILE) else {}
    except Exception:
        return {}


def save_db() -> None:
    try:
        json.dump(DB, open(DB_FILE, "w", encoding="utf-8"), indent=2)
    except Exception:
        pass


DB = load_db()


def gcfg(gid: int) -> dict:
    return DB.setdefault(
        str(gid),
        {
            "staff_role": 0,
            "hc_role": 0,
            "dev_lead_role": 0,
            "ingame_dev_role": 0,
            "bot_dev_role": 0,
            "cat_id": 0,
            "dev_cat_id": 0,
            "log_ch": 0,
            "sla_mins": 15,
            "inactive_hrs": 12,
            "max_open": 2,
            "counter": 0,
            "oncall": [],
            "blacklist": {},
            "macros": dict(DEFAULT_MACROS),
            "staff_stats": {},
            "open_tickets": {},
        },
    )


def get_staff_stat(gid: int, uid: int) -> dict:
    return gcfg(gid)["staff_stats"].setdefault(
        str(uid), {"claimed": 0, "closed": 0, "resp_sum": 0.0, "resp_cnt": 0, "stars_sum": 0, "stars_cnt": 0}
    )


def get_avg_wait_text(gid: int) -> str:
    stats = gcfg(gid)["staff_stats"].values()
    total_s = sum(s.get("resp_sum", 0.0) for s in stats)
    total_c = sum(s.get("resp_cnt", 0) for s in stats)
    if not total_c:
        return "~3 mins"
    avg = int(total_s / total_c)
    return f"~{avg}s" if avg < 60 else f"~{max(1, avg // 60)}m"


def is_staff(m: discord.Member) -> bool:
    if not isinstance(m, discord.Member):
        return False
    if m.guild_permissions.administrator or m.guild_permissions.manage_channels:
        return True
    c = gcfg(m.guild.id)
    rids = {c["staff_role"], c["hc_role"], c["dev_lead_role"]} - {0}
    return any(r.id in rids for r in m.roles)


def is_dev_reviewer(m: discord.Member) -> bool:
    if not isinstance(m, discord.Member):
        return False
    if m.guild_permissions.administrator:
        return True
    c = gcfg(m.guild.id)
    rids = {c["dev_lead_role"], c["hc_role"]} - {0}
    return any(r.id in rids for r in m.roles) if rids else is_staff(m)


# =====================================================================
# 2. 24/7 KEEP-ALIVE WEB SERVER & LIVE ROBLOX ACCOUNT VERIFIER
# =====================================================================
async def run_web():
    app = web.Application()
    fn = lambda r: web.Response(text=f"Chicago Crossfire Tickets V5 | Up:{int((time.time()-START_TIME)//60)}m | {BOT_STATUS}")
    app.router.add_get("/", fn)
    app.router.add_get("/health", fn)
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", int(os.environ.get("PORT", 10000))).start()


async def self_ping():
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


async def lookup_roblox_user(username: str) -> str:
    """Queries Roblox's public Users API to verify profile, User ID, account age, and ban status."""
    clean = re.sub(r"[^A-Za-z0-9_]", "", (username or "").strip().split()[0])[:25]
    if len(clean) < 3:
        return "⚠️ *Could not parse Roblox username*"
    try:
        async with aiohttp.ClientSession() as s:
            async with s.post(
                "https://users.roblox.com/v1/usernames/users",
                json={"usernames": [clean], "excludeBannedUsers": False},
                timeout=5,
            ) as r:
                if r.status != 200:
                    return f"`{clean}` *(Roblox API unavailable)*"
                data = (await r.json()).get("data", [])
                if not data:
                    return f"❌ `{clean}` — **Not Found on Roblox** *(Possible typo/fake)*"
                u = data[0]
                uid = u["id"]
                async with s.get(f"https://users.roblox.com/v1/users/{uid}", timeout=5) as r2:
                    age_str, ban_str = "Unknown", ""
                    if r2.status == 200:
                        d2 = await r2.json()
                        created = d2.get("created", "")
                        if created:
                            dt = datetime.datetime.fromisoformat(created.replace("Z", "+00:00"))
                            days = (datetime.datetime.now(datetime.timezone.utc) - dt).days
                            age_str = f"{days}d old" + (" ⚠️ NEW ALT" if days < 30 else "")
                        if d2.get("isBanned"):
                            ban_str = " • ⛔ **ROBLOX BANNED**"
                    return f"✅ **[{u['name']}](https://www.roblox.com/users/{uid}/profile)** (ID: `{uid}` • {age_str}{ban_str})"
    except Exception:
        return f"`{clean}` *(Lookup timed out)*"


# =====================================================================
# 3. DUAL TRANSCRIPTS (HTML + TXT, WITH CONFIDENTIAL STAFF NOTES) & CLOSER
# =====================================================================
async def build_transcripts(
    ch: discord.TextChannel,
    closer_name: str,
    closer_id: int,
    reason: str,
    internal_notes: list[str],
) -> tuple[bytes, bytes, bytes, bytes, int]:
    """Builds Player transcripts (clean) and Staff Archive transcripts (includes internal staff notes)."""
    now = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    header = [
        "==============================================================",
        f"     {ch.guild.name.upper()} — TICKET V5 OFFICIAL TRANSCRIPT",
        "==============================================================",
        f"Channel   : #{ch.name} ({ch.id})",
        f"Closed By : {closer_name} ({closer_id})",
        f"Reason    : {reason}",
        f"Closed At : {now}",
        "==============================================================\n",
    ]
    txt_lines = list(header)
    html_msgs = []
    count = 0

    async for m in ch.history(limit=None, oldest_first=True):
        count += 1
        ts = m.created_at.strftime("%Y-%m-%d %H:%M:%S UTC")
        tag = " [BOT]" if m.author.bot else ""
        txt_lines.append(f"[{ts}] {m.author.display_name} (@{m.author.name}){tag}:")
        if m.content:
            txt_lines.append(f"  {m.content}")
        for e in m.embeds:
            if e.title:
                txt_lines.append(f"  [Embed] {e.title}")
            for f in e.fields:
                txt_lines.append(f"  • {f.name}: {f.value}")
        for a in m.attachments:
            txt_lines.append(f"  [Attachment] {a.filename} -> {a.url}")
        txt_lines.append("")

        esc_name = html.escape(m.author.display_name)
        bot_badge = '<span class="bot">BOT</span>' if m.author.bot else ""
        esc_body = html.escape(m.content or "").replace("\n", "<br>")
        emb_html = "".join(
            f"<div class='emb'><div class='et'>{html.escape(e.title or '')}</div>"
            f"<div class='ed'>{html.escape(e.description or '')[:500]}</div>"
            + "".join(f"<div class='ef'><b>{html.escape(f.name)}</b>: {html.escape(str(f.value))}</div>" for f in e.fields)
            + "</div>"
            for e in m.embeds
        )
        att_html = "".join(
            f"<div class='att'>📎 <a href='{html.escape(a.url)}' target='_blank'>{html.escape(a.filename)}</a></div>"
            for a in m.attachments
        )
        av = html.escape(m.author.display_avatar.url)
        html_msgs.append(
            f"<div class='msg'><img class='av' src='{av}'/><div>"
            f"<div class='hdr'><b>{esc_name}</b>{bot_badge}<span class='ts'>{ts}</span></div>"
            f"<div class='txt'>{esc_body}</div>{emb_html}{att_html}</div></div>"
        )

    def wrap_html(extra_block: str = "") -> bytes:
        doc = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>Transcript #{html.escape(ch.name)}</title>
<style>
body{{background:#1e1f22;color:#dbdee1;font-family:Segoe UI,Arial,sans-serif;margin:0;padding:20px;}}
.top{{background:#2b2d31;padding:16px;border-radius:10px;border-left:4px solid #dc3545;margin-bottom:18px;}}
.notes{{background:#35291e;padding:14px;border-radius:8px;border-left:4px solid #f0b232;margin-bottom:16px;}}
.msg{{display:flex;gap:12px;padding:10px;border-radius:8px;margin-bottom:6px;background:#2b2d31;}}
.av{{width:40px;height:40px;border-radius:50%;object-fit:cover;}}
.hdr{{font-size:14px;margin-bottom:4px;}}
.ts{{color:#949ba4;font-size:11px;margin-left:8px;}}
.bot{{background:#5865f2;color:#fff;font-size:10px;padding:2px 5px;border-radius:4px;margin-left:6px;}}
.txt{{font-size:14px;line-height:1.45;word-break:break-word;}}
.emb{{background:#1e1f22;border-left:3px solid #5865f2;padding:8px 10px;margin-top:6px;border-radius:4px;font-size:13px;}}
.et{{font-weight:700;margin-bottom:4px;}} .ef{{margin-top:3px;}}
.att a{{color:#00a8fc;text-decoration:none;font-size:13px;}}
</style></head><body>
<div class="top"><h2>📄 {html.escape(ch.guild.name)} — #{html.escape(ch.name)}</h2>
<div><b>Closed By:</b> {html.escape(closer_name)} | <b>Messages:</b> {count} | <b>At:</b> {now}</div>
<div><b>Reason:</b> {html.escape(reason)}</div></div>
{extra_block}{"".join(html_msgs)}</body></html>"""
        return doc.encode("utf-8")

    player_txt = "\n".join(txt_lines).encode("utf-8")
    player_html = wrap_html("")

    if internal_notes:
        staff_txt_lines = list(txt_lines) + ["\n=== CONFIDENTIAL INTERNAL STAFF NOTES ==="] + [f"• {n}" for n in internal_notes]
        notes_html = "<div class='notes'><h3>🔒 Confidential Internal Staff Notes</h3>" + "".join(
            f"<div>• {html.escape(n)}</div>" for n in internal_notes
        ) + "</div>"
        staff_txt = "\n".join(staff_txt_lines).encode("utf-8")
        staff_html = wrap_html(notes_html)
    else:
        staff_txt, staff_html = player_txt, player_html

    return player_html, player_txt, staff_html, staff_txt, count


async def finalize_close_ticket(
    bot: commands.Bot,
    ch: discord.TextChannel,
    closer,
    reason: str = "Resolved by staff",
) -> None:
    """Core ticket closer: cleans up linked VC, DMs HTML+TXT & 5-Star Rating, archives Staff Log, deletes channel."""
    g = ch.guild
    topic = ch.topic or ""
    c = gcfg(g.id)
    tinfo = c["open_tickets"].pop(str(ch.id), {})

    uid_m = re.search(r"UserID:(\d+)", topic)
    typ_m = re.search(r"Type:([a-z0-9_]+)", topic)
    opener_id = int(uid_m.group(1)) if uid_m else tinfo.get("opener_id")
    t_key = typ_m.group(1) if typ_m else tinfo.get("type", "support")
    claimer_id = tinfo.get("claimer_id") or closer.id
    notes = tinfo.get("notes", [])

    # Clean up linked Private Voice Room if one was created
    vc_id = tinfo.get("vc_id")
    if vc_id:
        vc = g.get_channel(vc_id)
        if vc:
            try:
                await vc.delete(reason=f"Linked ticket #{ch.name} closed")
            except Exception:
                pass

    if not getattr(closer, "bot", False):
        st = get_staff_stat(g.id, closer.id)
        st["closed"] += 1
    save_db()

    p_html, p_txt, s_html, s_txt, msg_n = await build_transcripts(ch, str(closer), closer.id, reason, notes)
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%d-%H%M")
    h_name, t_name = f"transcript-{ch.name}-{stamp}.html", f"transcript-{ch.name}-{stamp}.txt"

    # 1. DM Transcript + 5-Star Rating Buttons to Ticket Opener
    dm_note = "⚠️ Opener not found"
    if opener_id:
        usr = g.get_member(opener_id)
        if not usr:
            try:
                usr = await bot.fetch_user(opener_id)
            except Exception:
                usr = None
        if usr:
            dm_emb = discord.Embed(
                title=f"📄 {g.name} — Ticket Transcript",
                description=(
                    f"Your ticket **`#{ch.name}`** (`{t_key}`) has been closed.\n"
                    f"Open the **`.html`** file for a Discord-styled view or read the **`.txt`** file anytime!\n\n"
                    f"⭐ **Rate your support experience below (1–5 Stars):**"
                ),
                color=discord.Color.from_rgb(220, 53, 69),
                timestamp=discord.utils.utcnow(),
            )
            dm_emb.add_field(name="🛡️ Handled By", value=f"<@{claimer_id}>", inline=True)
            dm_emb.add_field(name="💬 Messages", value=f"`{msg_n}`", inline=True)
            dm_emb.add_field(name="📝 Reason", value=reason[:400], inline=False)
            dm_emb.set_footer(text=f"GuildID:{g.id} | StaffID:{claimer_id} | Ticket:{ch.name}")
            try:
                await usr.send(
                    embed=dm_emb,
                    files=[discord.File(io.BytesIO(p_html), filename=h_name), discord.File(io.BytesIO(p_txt), filename=t_name)],
                    view=StarRatingView(),
                )
                dm_note = "✅ Delivered + 5⭐ Prompt"
            except discord.Forbidden:
                dm_note = "⚠️ User DMs Disabled"
            except Exception as e:
                dm_note = f"⚠️ DM Error ({type(e).__name__})"

    # 2. Archive Full Copy (including Confidential Staff Notes) in Staff Log Channel
    log_ch = g.get_channel(c["log_ch"])
    if log_ch:
        log_emb = discord.Embed(
            title=f"📁 Ticket Closed — #{ch.name}",
            description=(
                f"🎫 **Type:** `{t_key}` • 👤 **Opener:** {f'<@{opener_id}>' if opener_id else 'Unknown'}\n"
                f"🙋‍♂️ **Claimed By:** <@{claimer_id}> • 🔒 **Closed By:** {closer.mention}\n"
                f"💬 **Messages:** `{msg_n}` • 🔒 **Staff Notes:** `{len(notes)}` • 📬 **DM:** {dm_note}\n"
                f"📝 **Reason:** {reason[:400]}"
            ),
            color=discord.Color.dark_grey(),
            timestamp=discord.utils.utcnow(),
        )
        try:
            await log_ch.send(
                embed=log_emb,
                files=[discord.File(io.BytesIO(s_html), filename=h_name), discord.File(io.BytesIO(s_txt), filename=t_name)],
            )
        except Exception:
            pass

    await asyncio.sleep(5)
    try:
        await ch.delete(reason=f"Closed by {closer}: {reason}")
    except Exception:
        pass


async def close_ticket_interaction(i: discord.Interaction, reason: str = "Resolved by staff") -> None:
    if not (i.channel.topic or "").startswith("CCT5 |"):
        return await i.response.send_message("❌ Use this inside an active Ticket V5 channel.", ephemeral=True)
    await i.response.send_message(f"📄 Generating **HTML + TXT transcripts** & DMing 5-Star Rating... Closing in **5s**.\n> **Reason:** {reason}")
    asyncio.create_task(finalize_close_ticket(i.client, i.channel, i.user, reason))


async def spawn_ticket_voice(i: discord.Interaction) -> None:
    """Creates a private voice room locked to the ticket opener + staff (auto-deleted on close)."""
    g, ch = i.guild, i.channel
    c = gcfg(g.id)
    tinfo = c["open_tickets"].setdefault(str(ch.id), {})
    if tinfo.get("vc_id"):
        existing = g.get_channel(tinfo["vc_id"])
        if existing:
            return await i.response.send_message(f"🎙️ Voice room already active: {existing.mention}", ephemeral=True)

    m = re.search(r"UserID:(\d+)", ch.topic or "")
    opener = g.get_member(int(m.group(1))) if m else None
    ow = {
        g.default_role: discord.PermissionOverwrite(view_channel=False, connect=False),
        g.me: discord.PermissionOverwrite(view_channel=True, connect=True, manage_channels=True),
    }
    if opener:
        ow[opener] = discord.PermissionOverwrite(view_channel=True, connect=True, speak=True)
    for rid in (c["staff_role"], c["hc_role"], c["dev_lead_role"]):
        role = g.get_role(rid)
        if role:
            ow[role] = discord.PermissionOverwrite(view_channel=True, connect=True, speak=True, mute_members=True)

    try:
        vc = await g.create_voice_channel(name=f"🎙️-{ch.name}"[:80], category=ch.category, overwrites=ow)
        tinfo["vc_id"] = vc.id
        save_db()
        await i.response.send_message(f"🎙️ **Private Support / Interview Voice Room created:** {vc.mention}\n*(Automatically deleted when this ticket closes!)*")
except discord.Forbidden:
        await i.response.send_message("❌ Bot needs **Manage Channels** permission to create a voice room!", ephemeral=True)


# =====================================================================
# 4. MODALS (STANDARD TICKETS, 3 SEPARATE DEV FORMS & STAFF TOOLS)
# =====================================================================
def check_open_limits(g: discord.Guild, u: discord.Member, key: str) -> str | None:
    """Enforces Blacklist, Anti-Spam Cooldown, Duplicate Type Block, and Max Open Tickets."""
    c = gcfg(g.id)
    if str(u.id) in c["blacklist"]:
        return f"🚫 You are blacklisted from opening tickets.\n**Reason:** `{c['blacklist'][str(u.id)]}`"
    if not is_staff(u):
        last = OPEN_COOLDOWN.get((g.id, u.id), 0.0)
        wait = int(30 - (time.time() - last))
        if wait > 0:
            return f"⏳ **Anti-Spam Cooldown:** Please wait **`{wait}s`** before opening another ticket."
    user_open = [ch for ch in g.text_channels if ch.topic and f"UserID:{u.id}" in ch.topic and ch.topic.startswith("CCT5 |")]
    for ch in user_open:
        if f"Type:{key}" in ch.topic:
            return f"⚠️ You already have an open ticket in this category: {ch.mention}"
    if not is_staff(u) and len(user_open) >= int(c.get("max_open", 2)):
        return f"⚠️ You already have **`{len(user_open)}`** open tickets (max `{c.get('max_open', 2)}`). Please close one first."
    return None


class StandardTicketModal(discord.ui.Modal):
    def __init__(self, key: str):
        title, _, emoji, _, _, subj = CATEGORIES[key]
        super().__init__(title=f"{emoji} {title}"[:45])
        self.key = key
        self.rblx = discord.ui.TextInput(label="Your Roblox Username", placeholder="Exact Roblox username...", required=True, max_length=50)
        self.subj = discord.ui.TextInput(label=subj[:45], required=True, max_length=120)
        self.info = discord.ui.TextInput(label="Full Details / Proof Links", style=discord.TextStyle.paragraph, required=True, max_length=950)
        for item in (self.rblx, self.subj, self.info):
            self.add_item(item)

    async def on_submit(self, i: discord.Interaction):
        await i.response.defer(ephemeral=True)
        g, u, c = i.guild, i.user, gcfg(i.guild.id)
        err = check_open_limits(g, u, self.key)
        if err:
            return await i.followup.send(err, ephemeral=True)

        OPEN_COOLDOWN[(g.id, u.id)] = time.time()
        c["counter"] += 1
        t_num = c["counter"]
        title, pfx, emoji, col, tip, subj = CATEGORIES[self.key]
        cat = g.get_channel(c["cat_id"])
        cat = cat if isinstance(cat, discord.CategoryChannel) else None

        ow = {
            g.default_role: discord.PermissionOverwrite(view_channel=False),
            u: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True, attach_files=True, embed_links=True),
            g.me: discord.PermissionOverwrite(view_channel=True, send_messages=True, manage_channels=True, read_message_history=True, attach_files=True, embed_links=True),
        }
        for rid in (c["staff_role"], c["hc_role"]):
            role = g.get_role(rid)
            if role:
                ow[role] = discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True, attach_files=True)

        safe = re.sub(r"[^a-z0-9-]", "", u.name.lower())[:14] or "player"
        try:
            tch = await g.create_text_channel(name=f"{pfx}-{t_num:03d}-{safe}", category=cat, overwrites=ow, topic=f"CCT5 | Type:{self.key} | UserID:{u.id}")
        except discord.Forbidden:
            return await i.followup.send("❌ Missing **Manage Channels** permission!", ephemeral=True)

        if c["oncall"]:
            ping = " ".join(f"<@{uid}>" for uid in c["oncall"][:12])
        elif c["staff_role"]:
            ping = f"<@&{c['staff_role']}>"
        else:
            ping = "**Staff**"

        unclaimed_q = sum(1 for t in c["open_tickets"].values() if not t.get("claimer_id")) + 1
        rblx_check = await lookup_roblox_user(self.rblx.value)
        c["open_tickets"][str(tch.id)] = {
            "opener_id": u.id, "type": self.key, "created_at": time.time(), "last_msg": time.time(),
            "claimer_id": 0, "sla_alerted": False, "nudged": False, "nudged_at": 0.0,
            "locked": False, "pause_autoclose": False, "notes": [], "vc_id": 0,
        }
        save_db()

        emb = discord.Embed(
            title=f"{emoji} Chicago Crossfire — {title} (#{t_num:03d})",
            description=f"Welcome {u.mention}! {tip}\n📊 **Queue Position:** `#{unclaimed_q}` • ⏱️ **Est. Wait:** `{get_avg_wait_text(g.id)}`",
            color=col,
            timestamp=discord.utils.utcnow(),
        )
        emb.set_author(name=f"{u.display_name} (@{u.name})", icon_url=u.display_avatar.url)
        emb.set_thumbnail(url=u.display_avatar.url)
        emb.add_field(name="🎮 Live Roblox Check", value=rblx_check, inline=False)
        emb.add_field(name="🙋‍♂️ Claimed By", value="*Unclaimed*", inline=True)
        emb.add_field(name="🚦 Priority", value="🟢 `Normal`", inline=True)
        emb.add_field(name=f"📌 {subj}", value=self.subj.value, inline=False)
        emb.add_field(name="📝 Details / Evidence", value=self.info.value, inline=False)
        emb.set_footer(text="Ticket V5 • HTML + TXT Transcript & 5⭐ Rating sent to DMs on close")

        await tch.send(content=f"{u.mention} {ping}", embed=emb, view=TicketControlsV5())
        await i.followup.send(f"✅ Ticket **#{t_num:03d}** opened: {tch.mention} *(Queue `#{unclaimed_q}`)*", ephemeral=True)


async def _create_dev_ticket(
    i: discord.Interaction,
    track_key: str,
    track_title: str,
    pfx: str,
    color: discord.Color,
    fields: list[tuple[str, str, bool]],
    roblox_to_verify: str = "",
) -> None:
    """Shared launcher for In-Game Roblox Dev, Bot Dev & Creative Dev applications."""
    g, u, c = i.guild, i.user, gcfg(i.guild.id)
    err = check_open_limits(g, u, track_key)
    if err:
        return await i.followup.send(err, ephemeral=True)

    OPEN_COOLDOWN[(g.id, u.id)] = time.time()
    c["counter"] += 1
    t_num = c["counter"]
    cat = g.get_channel(c["dev_cat_id"]) or g.get_channel(c["cat_id"])
    cat = cat if isinstance(cat, discord.CategoryChannel) else None

    ow = {
        g.default_role: discord.PermissionOverwrite(view_channel=False),
        u: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True, attach_files=True, embed_links=True),
        g.me: discord.PermissionOverwrite(view_channel=True, send_messages=True, manage_channels=True, read_message_history=True, attach_files=True, embed_links=True),
    }
    review_roles = [g.get_role(rid) for rid in (c["dev_lead_role"], c["hc_role"]) if g.get_role(rid)]
    if not review_roles and g.get_role(c["staff_role"]):
        review_roles = [g.get_role(c["staff_role"])]
    for role in review_roles:
        ow[role] = discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True, attach_files=True, embed_links=True)

    safe = re.sub(r"[^a-z0-9-]", "", u.name.lower())[:13] or "dev"
    try:
        tch = await g.create_text_channel(name=f"{pfx}-{t_num:03d}-{safe}", category=cat, overwrites=ow, topic=f"CCT5 | Type:{track_key} | UserID:{u.id}")
    except discord.Forbidden:
        return await i.followup.send("❌ Missing **Manage Channels** permission!", ephemeral=True)

    c["open_tickets"][str(tch.id)] = {
        "opener_id": u.id, "type": track_key, "created_at": time.time(), "last_msg": time.time(),
        "claimer_id": 0, "sla_alerted": False, "nudged": False, "nudged_at": 0.0,
        "locked": False, "pause_autoclose": True, "notes": [], "vc_id": 0,
    }
    save_db()

    emb = discord.Embed(
        title=f"{track_title} (#{t_num:03d})",
        description=f"Welcome {u.mention}! Feel free to drop extra **clips, `.rbxl` showcases, GitHub repos, or screenshots** below for our Dev Lead!",
        color=color,
        timestamp=discord.utils.utcnow(),
    )
    emb.set_author(name=f"Applicant: {u.display_name} (@{u.name})", icon_url=u.display_avatar.url)
    emb.set_thumbnail(url=u.display_avatar.url)
    emb.add_field(name="📊 Stage", value="🟡 `Pending Review`", inline=True)
    emb.add_field(name="🙋‍♂️ Reviewer", value="*Unclaimed*", inline=True)
    emb.add_field(name="🎯 Rubric Score", value="*Not Scored*", inline=True)
    if roblox_to_verify:
        emb.add_field(name="🎮 Live Roblox Verification", value=await lookup_roblox_user(roblox_to_verify), inline=False)
    for name, val, inl in fields:
        emb.add_field(name=name, value=val[:1000], inline=inl)
    emb.set_footer(text=f"Track:{track_key} • Auto-Close Paused for Dev Review • Transcript DMed on close")

    pings = " ".join([u.mention] + [r.mention for r in review_roles])
    await tch.send(content=pings, embed=emb, view=DevReviewControlsV5())
    await i.followup.send(f"✅ Your **{track_title}** is open: {tch.mention}", ephemeral=True)


class InGameDevModal(discord.ui.Modal, title="🎮 In-Game Roblox Developer App"):
    rblx = discord.ui.TextInput(label="Roblox Username", placeholder="Exact Roblox username (auto-verified live!)", required=True, max_length=50)
    spec = discord.ui.TextInput(label="In-Game Specialty", placeholder="Luau Scripter, Builder, 3D Modeler, Animator, VFX, UI...", required=True, max_length=100)
    exp = discord.ui.TextInput(label="Studio Experience, Timezone & Weekly Hours", placeholder="e.g. 2 yrs Luau + DataStores | EST/IST | 12 hrs/week", required=True, max_length=180)
    port = discord.ui.TextInput(label="Portfolio / Roblox Game / Clip Links", style=discord.TextStyle.paragraph, placeholder="Paste Roblox place links, DevForum, YouTube, or Imgur showcases...", required=True, max_length=850)
    pay = discord.ui.TextInput(label="Payment Expectation & Why Chicago Crossfire?", style=discord.TextStyle.paragraph, placeholder="Per-task Robux / % Rev Share / Volunteer + what you want to build", required=True, max_length=600)

    async def on_submit(self, i: discord.Interaction):
        await i.response.defer(ephemeral=True)
        await _create_dev_ticket(
            i, "dev_ingame", "🎮 In-Game Roblox Development Application", "dev-game", discord.Color.teal(),
            [("🛠️ In-Game Specialty", f"`{self.spec.value}`", True), ("⏱️ Experience & Schedule", self.exp.value, False), ("📁 Portfolio & Showcases", self.port.value, False), ("💰 Payment & Pitch", self.pay.value, False)],
            roblox_to_verify=self.rblx.value,
        )


class BotDevModal(discord.ui.Modal, title="🤖 Discord Bot & Systems Dev App"):
    stack = discord.ui.TextInput(label="Languages & Libraries", placeholder="e.g. Python (discord.py), Node.js (discord.js), APIs, SQL/JSON", required=True, max_length=120)
    hosting = discord.ui.TextInput(label="24/7 Hosting, Security & API Experience", placeholder="e.g. Render/VPS, rate-limit handling, Anti-Nuke, Roblox Open Cloud", required=True, max_length=200)
    github = discord.ui.TextInput(label="GitHub / Bot Portfolio / Live Demo Links", style=discord.TextStyle.paragraph, placeholder="Paste GitHub repos, bot demos, or code showcase links...", required=True, max_length=850)
    best = discord.ui.TextInput(label="Most Complex Bot/System You Built", style=discord.TextStyle.paragraph, placeholder="Explain the hardest bot or automation system you built from scratch...", required=True, max_length=700)
    pay = discord.ui.TextInput(label="Timezone, Hours & Payment Expectation", placeholder="e.g. 10 hrs/wk | Robux per bot / Volunteer / Rev Share", required=True, max_length=250)

    async def on_submit(self, i: discord.Interaction):
        await i.response.defer(ephemeral=True)
        await _create_dev_ticket(
            i, "dev_bot", "🤖 Discord Bot & Systems Development Application", "dev-bot", discord.Color.from_rgb(88, 101, 242),
            [("💻 Tech Stack", f"`{self.stack.value}`", True), ("☁️ Hosting, Security & APIs", self.hosting.value, False), ("🔗 GitHub / Bot Portfolio", self.github.value, False), ("⚙️ Hardest System Built", self.best.value, False), ("💰 Availability & Payment", self.pay.value, False)],
        )


class CreativeDevModal(discord.ui.Modal, title="🎨 GFX / UI / Uniforms Dev App"):
    spec = discord.ui.TextInput(label="Creative Specialty", placeholder="UI/UX Design, 3D GFX, Police/Gang Uniforms, Audio, Video Editing...", required=True, max_length=100)
    tools = discord.ui.TextInput(label="Software / Tools Used", placeholder="e.g. Blender, Photoshop, Figma, ibisPaint X, CapCut", required=True, max_length=120)
    port = discord.ui.TextInput(label="Portfolio / Showcase Links", style=discord.TextStyle.paragraph, placeholder="Paste Behance, ArtStation, Imgur, YouTube, or Roblox links...", required=True, max_length=850)
    pay = discord.ui.TextInput(label="Availability & Payment Expectation", placeholder="e.g. 6 hrs/wk | Robux per asset / Volunteer", required=True, max_length=250)

    async def on_submit(self, i: discord.Interaction):
        await i.response.defer(ephemeral=True)
        await _create_dev_ticket(
            i, "dev_creative", "🎨 Creative / UI / GFX Development Application", "dev-art", discord.Color.magenta(),
            [("🎨 Specialty", f"`{self.spec.value}`", True), ("🧰 Tools Used", f"`{self.tools.value}`", True), ("📁 Portfolio & Showcases", self.port.value, False), ("💰 Availability & Payment", self.pay.value, False)],
        )


class DevTestTaskModal(discord.ui.Modal, title="🧪 Assign Live Developer Test Task"):
    task_desc = discord.ui.TextInput(label="Test Task Instructions", style=discord.TextStyle.paragraph, required=True, max_length=800)
    deadline = discord.ui.TextInput(label="Deadline", default="48 Hours", required=True, max_length=50)

    async def on_submit(self, i: discord.Interaction):
        emb = i.message.embeds[0] if i.message and i.message.embeds else None
        if emb and len(emb.fields) > 0:
            emb.color = discord.Color.gold()
            emb.set_field_at(0, name="📊 Stage", value="🧪 `TEST TASK ASSIGNED`", inline=True)
            await i.message.edit(embed=emb)
        t_emb = discord.Embed(title="🧪 OFFICIAL DEVELOPER TEST TASK", description=f"Assigned by {i.user.mention} • ⏰ **Deadline:** `{self.deadline.value}`\n\n{self.task_desc.value}", color=discord.Color.gold(), timestamp=discord.utils.utcnow())
        t_emb.set_footer(text="100% original work required — stolen code/assets = instant blacklist.")
        await i.response.send_message(embed=t_emb)


class DevScoreModal(discord.ui.Modal, title="📊 Score Developer Applicant (1-10)"):
    skill = discord.ui.TextInput(label="Technical Skill (1-10)", placeholder="e.g. 9", required=True, max_length=2)
    port = discord.ui.TextInput(label="Portfolio Quality (1-10)", placeholder="e.g. 8", required=True, max_length=2)
    trust = discord.ui.TextInput(label="Communication & Trust (1-10)", placeholder="e.g. 9", required=True, max_length=2)
    notes = discord.ui.TextInput(label="Private Staff Notes (Hidden from Applicant)", style=discord.TextStyle.paragraph, required=True, max_length=400)

    async def on_submit(self, i: discord.Interaction):
        try:
            s1, s2, s3 = [max(1, min(10, int(x.value.strip()))) for x in (self.skill, self.port, self.trust)]
        except ValueError:
            return await i.response.send_message("❌ Enter whole numbers from `1` to `10`!", ephemeral=True)
        avg = round((s1 + s2 + s3) / 3.0, 1)
        tinfo = gcfg(i.guild.id)["open_tickets"].setdefault(str(i.channel.id), {"notes": []})
        tinfo.setdefault("notes", []).append(f"[{i.user.display_name} Score {avg}/10] {self.notes.value}")
        save_db()
        emb = i.message.embeds[0] if i.message and i.message.embeds else None
        if emb and len(emb.fields) > 2:
            emb.set_field_at(2, name="🎯 Rubric Score", value=f"**`{avg}/10`** *(S:{s1} P:{s2} T:{s3})*", inline=True)
            await i.message.edit(embed=emb)
        await i.response.send_message(f"📊 **Score Saved (`{avg}/10`):** Private note archived for Staff Logs only!", ephemeral=True)


class DeclineDevModal(discord.ui.Modal, title="❌ Decline Development Application"):
    reason = discord.ui.TextInput(label="Feedback / Reason", default="Portfolio does not meet current requirements yet.", required=True, max_length=350)

    async def on_submit(self, i: discord.Interaction):
        emb = i.message.embeds[0] if i.message and i.message.embeds else None
        if emb and len(emb.fields) > 0:
            emb.color = discord.Color.dark_red()
            emb.set_field_at(0, name="📊 Stage", value="🔴 `DECLINED`", inline=True)
            await i.message.edit(embed=emb)
        m = re.search(r"UserID:(\d+)", i.channel.topic or "")
        uid = int(m.group(1)) if m else None
        await i.response.send_message(f"❌ {f'<@{uid}>' if uid else 'Applicant'} — Application **declined** by {i.user.mention}.\n> **Feedback:** {self.reason.value}")
        if uid:
            try:
                usr = i.guild.get_member(uid) or await i.client.fetch_user(uid)
                await usr.send(f"📋 **{i.guild.name} — Development Application Update:** Declined.\n> **Feedback:** {self.reason.value}")
            except Exception:
                pass


class StaffNoteModal(discord.ui.Modal, title="🔒 Add Confidential Staff Note"):
    note = discord.ui.TextInput(label="Internal Note (Hidden from Player)", style=discord.TextStyle.paragraph, placeholder="Saved only for staff & Staff Log transcripts...", required=True, max_length=500)

    async def on_submit(self, i: discord.Interaction):
        tinfo = gcfg(i.guild.id)["open_tickets"].setdefault(str(i.channel.id), {"notes": []})
        tinfo.setdefault("notes", []).append(f"[{i.user.display_name}] {self.note.value.strip()}")
        save_db()
        await i.response.send_message(f"🔒 **Private Staff Note Saved** *(Total notes on this ticket: `{len(tinfo['notes'])}` — hidden from player)*.", ephemeral=True)


class RobloxCheckModal(discord.ui.Modal, title="🔍 Live Roblox Account Lookup"):
    username = discord.ui.TextInput(label="Roblox Username to Inspect", placeholder="Enter exact Roblox username...", required=True, max_length=50)

    async def on_submit(self, i: discord.Interaction):
        await i.response.defer(ephemeral=True)
        await i.followup.send(f"🔍 **Live Roblox Verification:** {await lookup_roblox_user(self.username.value)}", ephemeral=True)


class CloseModalV5(discord.ui.Modal, title="🔒 Close Ticket & DM Transcript"):
    reason = discord.ui.TextInput(label="Closing Reason (Sent to Player DM)", default="Issue resolved", required=True, max_length=300)

    async def on_submit(self, i: discord.Interaction):
        await close_ticket_interaction(i, self.reason.value)
