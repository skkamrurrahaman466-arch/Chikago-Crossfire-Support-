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
except 
