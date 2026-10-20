"""
Yapper bot: leveling, economy, welcome, antiraid, antinuke (watchdog), tickets.
Built on PyYapper 1.4 (pip install pyyapper).
 
Env vars:
  YAPPER_TOKEN  bot token (Settings -> Developer). Never hardcode it.
  OWNER_IDS     comma-separated Yapper user IDs allowed to run admin commands
  PREFIX        command prefix (default ".")
  DB_PATH       sqlite file (default bot.db; use /data/bot.db with a Render disk)
"""
import os, re, time, random, sqlite3, asyncio, logging, collections
from pyyapper import Bot, Embed, Button, Row
 
logging.basicConfig(level=logging.INFO)
log = logging.getLogger("bot")
 
TOKEN = os.environ["YAPPER_TOKEN"]
OWNER_IDS = {x.strip() for x in os.environ.get("OWNER_IDS", "").split(",") if x.strip()}
PREFIX = os.environ.get("PREFIX", ".")
DB_PATH = os.environ.get("DB_PATH", "bot.db")
 
bot = Bot(TOKEN, prefixes=[PREFIX])
 
# ------------------------------------------------------------------ storage
db = sqlite3.connect(DB_PATH)
db.executescript("""
CREATE TABLE IF NOT EXISTS users(id TEXT PRIMARY KEY, xp INT DEFAULT 0, level INT DEFAULT 0,
    coins INT DEFAULT 0, last_daily REAL DEFAULT 0, last_work REAL DEFAULT 0);
CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS trusted(id TEXT PRIMARY KEY);
""")
 
def get_user(uid):
    db.execute("INSERT OR IGNORE INTO users(id) VALUES(?)", (str(uid),))
    return db.execute("SELECT xp,level,coins,last_daily,last_work FROM users WHERE id=?", (str(uid),)).fetchone()
 
def set_user(uid, **kw):
    get_user(uid)
    for k, v in kw.items():
        db.execute(f"UPDATE users SET {k}=? WHERE id=?", (v, str(uid)))
    db.commit()
 
def setting(key, default=None):
    r = db.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return r[0] if r else default
 
def put(key, value):
    db.execute("INSERT OR REPLACE INTO settings VALUES(?,?)", (key, str(value)))
    db.commit()
 
def trusted(uid):
    uid = str(uid)
    return uid in OWNER_IDS or bool(db.execute("SELECT 1 FROM trusted WHERE id=?", (uid,)).fetchone())
 
def oid(x):
    """Get an id from a model, dict or plain value."""
    if isinstance(x, dict):
        return str(x.get("id") or x.get("user_id") or "")
    return str(getattr(x, "id", None) or getattr(x, "user_id", None) or x or "")
 
async def say(channel_id, text):
    try:
        await bot.send(channel_id, text)
    except Exception as e:
        log.warning("send failed: %s", e)
 
# ------------------------------------------------------------------ ready
@bot.event
async def on_ready(me):
    log.info("online as %s", getattr(me, "username", me))
    asyncio.create_task(watchdog())
 
# ------------------------------------------------------------------ leveling
xp_cd = {}
 
def level_for(xp):
    return int((xp / 100) ** 0.5)
 
@bot.event
async def on_message(ctx):
    uid = ctx.author_id
    if not uid or ctx.content.startswith(PREFIX):
        return
    if time.time() - xp_cd.get(uid, 0) < 30:
        return
    xp_cd[uid] = time.time()
    xp, lvl, coins, *_ = get_user(uid)
    xp += random.randint(10, 20)
    new = level_for(xp)
    set_user(uid, xp=xp, level=new, coins=coins + 2)
    if new > lvl:
        await say(ctx.channel_id, f"GG <@{uid}>, you reached level {new}!")
 
@bot.command("rank", description="Show your level and XP")
async def rank(ctx):
    xp, lvl, *_ = get_user(ctx.author_id)
    await ctx.reply(f"{ctx.author_mention} level {lvl} | {xp}/{((lvl + 1) ** 2) * 100} XP")
 
@bot.command("leaderboard", description="Top 10 by XP")
async def leaderboard(ctx):
    rows = db.execute("SELECT id,xp,level FROM users ORDER BY xp DESC LIMIT 10").fetchall()
    await ctx.reply("\n".join(f"{i+1}. <@{r[0]}> lvl {r[2]} ({r[1]} XP)" for i, r in enumerate(rows)) or "No data yet.")
 
# ------------------------------------------------------------------ economy
@bot.command("balance", description="Show your coins")
async def balance(ctx):
    await ctx.reply(f"{ctx.author_mention} you have {get_user(ctx.author_id)[2]} coins")
 
@bot.command("daily", description="Claim daily coins")
async def daily(ctx):
    _, _, coins, last, _ = get_user(ctx.author_id)
    if time.time() - last < 86400:
        left = int(86400 - (time.time() - last))
        return await ctx.reply(f"Come back in {left // 3600}h {left % 3600 // 60}m")
    set_user(ctx.author_id, coins=coins + 250, last_daily=time.time())
    await ctx.reply("You claimed 250 coins")
 
@bot.command("work", description="Work for coins (1h cooldown)")
async def work(ctx):
    _, _, coins, _, last = get_user(ctx.author_id)
    if time.time() - last < 3600:
        return await ctx.reply(f"Rest for {int(3600 - (time.time() - last)) // 60}m")
    amt = random.randint(40, 120)
    set_user(ctx.author_id, coins=coins + amt, last_work=time.time())
    await ctx.reply(f"You earned {amt} coins")
 
@bot.command("pay", description="pay <user_id> <amount>")
async def pay(ctx):
    if len(ctx.argv) < 2 or not ctx.argv[1].isdigit():
        return await ctx.reply(f"Usage: {PREFIX}pay <user_id> <amount>")
    target, amt = ctx.argv[0].strip("<@>"), int(ctx.argv[1])
    coins = get_user(ctx.author_id)[2]
    if amt <= 0 or amt > coins or target == str(ctx.author_id):
        return await ctx.reply("Invalid amount")
    set_user(ctx.author_id, coins=coins - amt)
    set_user(target, coins=get_user(target)[2] + amt)
    await ctx.reply(f"Sent {amt} coins to <@{target}>")
 
@bot.command("gamble", description="gamble <amount> (slightly worse than 50/50)")
async def gamble(ctx):
    if not ctx.argv or not ctx.argv[0].isdigit():
        return await ctx.reply(f"Usage: {PREFIX}gamble <amount>")
    amt, coins = int(ctx.argv[0]), get_user(ctx.author_id)[2]
    if amt <= 0 or amt > coins:
        return await ctx.reply("Invalid amount")
    win = random.random() < 0.48
    set_user(ctx.author_id, coins=coins + (amt if win else -amt))
    await ctx.reply(f"You {'won' if win else 'lost'} {amt} coins")
 
@bot.command("richest", description="Top 10 by coins")
async def richest(ctx):
    rows = db.execute("SELECT id,coins FROM users ORDER BY coins DESC LIMIT 10").fetchall()
    await ctx.reply("\n".join(f"{i+1}. <@{r[0]}> {r[1]}" for i, r in enumerate(rows)) or "No data yet.")
 
# ------------------------------------------------------------------ admin
@bot.command("trust", description="trust <user_id>: let someone use admin commands (owner only)")
async def trust(ctx):
    if str(ctx.author_id) not in OWNER_IDS:
        return await ctx.reply("Owner only (set OWNER_IDS)")
    if not ctx.argv:
        return await ctx.reply(f"Usage: {PREFIX}trust <user_id>")
    db.execute("INSERT OR IGNORE INTO trusted VALUES(?)", (ctx.argv[0].strip("<@>"),)); db.commit()
    await ctx.reply("Added")
 
# ------------------------------------------------------------------ welcome
@bot.command("setwelcome", description="setwelcome <channel_id> <message>  ({user} = mention)")
async def setwelcome(ctx):
    if not trusted(ctx.author_id):
        return await ctx.reply("Owner/trusted only")
    if len(ctx.argv) < 2:
        return await ctx.reply(f"Usage: {PREFIX}setwelcome <channel_id> Welcome {{user}}!")
    put(f"{ctx.server_id}:welcome_channel", ctx.argv[0].strip("<#>"))
    put(f"{ctx.server_id}:welcome_msg", " ".join(ctx.argv[1:]))
    await ctx.reply("Welcome message saved")
 
# ------------------------------------------------------------------ antiraid
joins = collections.defaultdict(collections.deque)
lockdown = {}
 
@bot.event
async def on_member_join(p):
    log.info("member_join payload: %s", p)          # check Render logs once to confirm the keys
    uid = str(p.get("user_id") or p.get("id") or (p.get("user") or {}).get("id") or "")
    sid = str(p.get("server_id") or "")
    if not uid or not sid:
        return
    now = time.time()
    q = joins[sid]
    q.append(now)
    while q and now - q[0] > 10:
        q.popleft()
    if setting(f"{sid}:antiraid", "on") == "on" and len(q) >= int(setting(f"{sid}:raid_threshold", 6)):
        if lockdown.get(sid, 0) < now:
            log.warning("raid detected in %s", sid)
            alert = setting(f"{sid}:alert_channel")
            if alert:
                await say(alert, "Antiraid: join spike detected, kicking new joiners for 5 minutes.")
        lockdown[sid] = now + 300
    if now < lockdown.get(sid, 0) and not trusted(uid):
        try:
            await bot.kick(sid, uid)
        except Exception as e:
            log.warning("kick failed: %s", e)
        return
    ch, msg = setting(f"{sid}:welcome_channel"), setting(f"{sid}:welcome_msg")
    if ch and msg:
        await say(ch, msg.replace("{user}", f"<@{uid}>"))
 
@bot.command("antiraid", description="antiraid on|off|threshold <n>")
async def antiraid(ctx):
    if not trusted(ctx.author_id):
        return await ctx.reply("Owner/trusted only")
    a, s = ctx.argv, ctx.server_id
    if a and a[0] in ("on", "off"):
        put(f"{s}:antiraid", a[0])
    elif len(a) == 2 and a[0] == "threshold" and a[1].isdigit():
        put(f"{s}:raid_threshold", a[1])
    else:
        return await ctx.reply(f"Usage: {PREFIX}antiraid on|off|threshold <joins in 10s>")
    await ctx.reply("Antiraid updated")
 
# ------------------------------------------------------------------ antinuke
# Yapper's bot API has no audit log or delete/ban events, so the bot can't see WHO
# did something. Instead it polls every 20s and undoes mass damage:
#   - 3+ channels deleted within one poll  -> recreate them
#   - 5+ new bans within one poll          -> unban them
snap = {}
 
async def watchdog():
    await asyncio.sleep(5)
    while True:
        try:
            for s in await bot.servers():
                sid = oid(s)
                if sid and setting(f"{sid}:antinuke", "on") == "on":
                    await guard_server(sid)
        except Exception as e:
            log.warning("watchdog: %s", e)
        await asyncio.sleep(20)
 
async def guard_server(sid):
    alert = setting(f"{sid}:alert_channel")
    chans = {c.id: c for c in await bot.channels(sid, force=True)}
    bans = {oid(b) for b in (await bot.bans(sid) or [])}
    prev = snap.get(sid)
    snap[sid] = {"chans": chans, "bans": bans}
    if prev is None:
        return
    gone = [c for cid, c in prev["chans"].items() if cid not in chans]
    if len(gone) >= int(setting(f"{sid}:chan_threshold", 3)):
        for c in gone[:25]:
            try:
                await bot.create_channel(sid, c.name, kind=c.type or "text", category_id=c.category_id)
            except Exception as e:
                log.warning("restore channel failed: %s", e)
        if alert:
            await say(alert, f"Antinuke: {len(gone)} channels were deleted at once, recreated them (messages can't be restored).")
    new_bans = bans - prev["bans"]
    if len(new_bans) >= int(setting(f"{sid}:ban_threshold", 5)):
        for uid in new_bans:
            try:
                await bot.unban(sid, uid)
            except Exception as e:
                log.warning("unban failed: %s", e)
        if alert:
            await say(alert, f"Antinuke: {len(new_bans)} bans at once, reverted them.")
 
@bot.command("antinuke", description="antinuke on|off|alert <channel_id>|channels <n>|bans <n>")
async def antinuke(ctx):
    if not trusted(ctx.author_id):
        return await ctx.reply("Owner/trusted only")
    a, s = ctx.argv, ctx.server_id
    if a and a[0] in ("on", "off"):
        put(f"{s}:antinuke", a[0])
    elif len(a) == 2 and a[0] == "alert":
        put(f"{s}:alert_channel", a[1].strip("<#>"))
    elif len(a) == 2 and a[0] == "channels" and a[1].isdigit():
        put(f"{s}:chan_threshold", a[1])
    elif len(a) == 2 and a[0] == "bans" and a[1].isdigit():
        put(f"{s}:ban_threshold", a[1])
    else:
        return await ctx.reply(f"Usage: {PREFIX}antinuke on|off|alert <channel_id>|channels <n>|bans <n>")
    await ctx.reply("Antinuke updated")
 
# ------------------------------------------------------------------ tickets
@bot.command("ticketpanel", description="Post the ticket button")
async def ticketpanel(ctx):
    if not trusted(ctx.author_id):
        return await ctx.reply("Owner/trusted only")
    emb = Embed().title("Support").description("Press the button to open a ticket.")
    await ctx.say(embeds=[emb], components=[Row(Button.primary("Open ticket", "ticket_open"))])
 
@bot.command("ticketstaff", description="ticketstaff <role_id>: make ticket channels private to this role")
async def ticketstaff(ctx):
    if not trusted(ctx.author_id) or not ctx.argv:
        return await ctx.reply(f"Usage (owner/trusted): {PREFIX}ticketstaff <role_id>")
    put(f"{ctx.server_id}:ticket_staff", ctx.argv[0])
    await ctx.reply("Saved")
 
@bot.button("ticket_open")
async def ticket_open(ic):
    name = "ticket-" + re.sub(r"[^a-z0-9-]", "", (ic.username or ic.user_id).lower())[:20]
    try:
        ch = await bot.create_channel(ic.server_id, name)
        cid = oid(ch)
        staff = setting(f"{ic.server_id}:ticket_staff")
        if staff:
            await bot.edit_channel(cid, private=True, role_ids=[staff])
        await bot.send(cid, f"<@{ic.user_id}> ticket opened. Staff will be with you soon. Use {PREFIX}close when done.")
        await ic.ephemeral("Ticket created.")
    except Exception as e:
        log.warning("ticket failed: %s", e)
        await ic.ephemeral("Couldn't create the ticket, ask an admin.")
 
@bot.command("close", description="Close (delete) this ticket channel")
async def close(ctx):
    if not (ctx.channel_name or "").startswith("ticket-"):
        return await ctx.reply("This isn't a ticket channel")
    if not trusted(ctx.author_id) and ctx.channel_name != "ticket-" + re.sub(r"[^a-z0-9-]", "", str((ctx.author or {}).get("username", "")).lower())[:20]:
        return await ctx.reply("Only the ticket owner or staff can close this")
    await bot.delete_channel(ctx.channel_id)
 
bot.run()
