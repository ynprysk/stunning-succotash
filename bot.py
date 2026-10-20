"""
Yapper bot: leveling, economy, welcome, antiraid, antinuke (watchdog), tickets,
with per-server enable/disable for every command.
Built on PyYapper 1.4 (pip install pyyapper).

Env vars:
  YAPPER_TOKEN  bot token (Settings -> Developer). Never hardcode it.
  OWNER_IDS     comma-separated Yapper user IDs allowed to run admin commands
  PREFIX        command prefix (default "!")
  DB_PATH       sqlite file (default bot.db; use /data/bot.db with a Render disk)
"""
import os, re, time, random, sqlite3, asyncio, logging, collections
from pyyapper import Bot, Embed, Button, Row

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("bot")

TOKEN = os.environ["YAPPER_TOKEN"]
OWNER_IDS = {x.strip() for x in os.environ.get("OWNER_IDS", "").split(",") if x.strip()}
PREFIX = os.environ.get("PREFIX", "!")
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
try:
    db.execute("ALTER TABLE users ADD COLUMN streak INT DEFAULT 0")
except sqlite3.OperationalError:
    pass  # column already exists

def get_user(uid):
    db.execute("INSERT OR IGNORE INTO users(id) VALUES(?)", (str(uid),))
    return db.execute("SELECT xp,level,coins,last_daily,last_work,streak FROM users WHERE id=?", (str(uid),)).fetchone()

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

def n(x):
    return f"{int(x):,}"

def parse_uid(s):
    return s.strip("<@!>") if s else ""

def fmt_time(sec):
    sec = int(sec)
    h, m = sec // 3600, sec % 3600 // 60
    return f"{h}h {m}m" if h else f"{m}m {sec % 60}s"

async def say(channel_id, text):
    try:
        await bot.send(channel_id, text)
    except Exception as e:
        log.warning("send failed: %s", e)

async def card(ctx, title, desc, color=None):
    e = Embed().title(title).description(desc)
    if color:
        e.color(color)
    return await ctx.reply("", embeds=[e])

# ------------------------------------------------------------------ enable / disable system
CATEGORIES = {
    "leveling": "Leveling",
    "economy": "Economy",
    "tickets": "Tickets",
    "admin": "Admin & setup",
    "general": "General",
}
PROTECTED = {"help", "commands", "enable", "disable"}   # can never be switched off
HELP = {}  # name -> (usage, description, category), filled by @cmd

def disabled_set(sid):
    return {x for x in (setting(f"{sid}:disabled", "") or "").split(",") if x}

def is_off(sid, name, category):
    if not sid:
        return False
    if name in PROTECTED:
        return False
    if setting(f"{sid}:all_off") == "1":
        return True
    d = disabled_set(sid)
    return name in d or category in d

def cmd(name, category, usage, desc):
    """Register a command that respects the per-server enable/disable switches."""
    HELP[name] = (usage, desc, category)
    def deco(fn):
        async def wrapper(ctx):
            if is_off(ctx.server_id, name, category):
                return await ctx.reply(f"`{PREFIX}{name}` is disabled in this server.")
            return await fn(ctx)
        wrapper.__name__ = fn.__name__
        bot.command(name, description=desc)(wrapper)
        return fn
    return deco

def resolve_target(t):
    t = t.lower().lstrip(PREFIX)
    if t == "all" or t in CATEGORIES or t in HELP:
        return t
    return None

@cmd("disable", "admin", "disable <command | category | all>",
     "Turn off a command, a whole category (leveling, economy, tickets, admin, general) or every command in this server. Owner/trusted only. help, commands, enable and disable can't be disabled.")
async def disable(ctx):
    if not trusted(ctx.author_id):
        return await ctx.reply("Owner/trusted only")
    if not ctx.argv:
        return await ctx.reply(f"Usage: `{PREFIX}disable <command | category | all>`")
    t = resolve_target(ctx.argv[0])
    if not t:
        return await ctx.reply(f"Unknown command or category. See `{PREFIX}commands`.")
    if t in PROTECTED:
        return await ctx.reply(f"`{t}` can't be disabled, so you can never lock yourself out.")
    sid = ctx.server_id
    if t == "all":
        put(f"{sid}:all_off", "1")
        return await ctx.reply(f"All commands are now disabled (except help, commands, enable, disable). Use `{PREFIX}enable all` to turn them back on.")
    d = disabled_set(sid); d.add(t)
    put(f"{sid}:disabled", ",".join(sorted(d)))
    await ctx.reply(f"Disabled `{t}`." + (" XP gain is paused too." if t == "leveling" else ""))

@cmd("enable", "admin", "enable <command | category | all>",
     "Turn a command, category, or everything back on in this server. Owner/trusted only.")
async def enable(ctx):
    if not trusted(ctx.author_id):
        return await ctx.reply("Owner/trusted only")
    if not ctx.argv:
        return await ctx.reply(f"Usage: `{PREFIX}enable <command | category | all>`")
    t = resolve_target(ctx.argv[0])
    if not t:
        return await ctx.reply(f"Unknown command or category. See `{PREFIX}commands`.")
    sid = ctx.server_id
    if t == "all":
        put(f"{sid}:all_off", "0")
        put(f"{sid}:disabled", "")
        return await ctx.reply("Everything is enabled again.")
    d = disabled_set(sid); d.discard(t)
    put(f"{sid}:disabled", ",".join(sorted(d)))
    note = f" Note: everything is still switched off, run `{PREFIX}enable all`." if setting(f"{sid}:all_off") == "1" else ""
    await ctx.reply(f"Enabled `{t}`." + note)

@cmd("commands", "general", "commands", "Show which commands are enabled or disabled in this server.")
async def commands_cmd(ctx):
    sid = ctx.server_id
    all_off = setting(f"{sid}:all_off") == "1"
    e = Embed().title("Command status")
    if all_off:
        e.description(f"All commands are disabled. Use `{PREFIX}enable all` to restore them.")
    for cat, label in CATEGORIES.items():
        lines = []
        for name in sorted(k for k, v in HELP.items() if v[2] == cat):
            mark = "on" if not is_off(sid, name, cat) else "off"
            lines.append(f"`{PREFIX}{name}` {mark}")
        if lines:
            e.field(label, "\n".join(lines))
    await ctx.reply("", embeds=[e])

# ------------------------------------------------------------------ ready
@bot.event
async def on_ready(me):
    log.info("online as %s", getattr(me, "username", me))
    asyncio.create_task(watchdog())

# ------------------------------------------------------------------ leveling
xp_cd = {}

def level_for(xp):
    return int((xp / 100) ** 0.5)

def bar(cur, total, width=10):
    filled = max(0, min(width, int(width * cur / total))) if total else 0
    return "▰" * filled + "▱" * (width - filled)

@bot.event
async def on_message(ctx):
    uid = ctx.author_id
    if not uid or ctx.content.startswith(PREFIX):
        return
    if ctx.server_id and "leveling" in disabled_set(ctx.server_id):
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

@cmd("rank", "leveling", "rank [user]", "Show your level, XP progress and server rank (or another member's).")
async def rank(ctx):
    uid = parse_uid(ctx.argv[0]) if ctx.argv else str(ctx.author_id)
    xp, lvl, *_ = get_user(uid)
    base, nxt = (lvl ** 2) * 100, ((lvl + 1) ** 2) * 100
    pos = db.execute("SELECT COUNT(*)+1 FROM users WHERE xp>?", (xp,)).fetchone()[0]
    await card(ctx, f"Level {lvl}",
               f"<@{uid}> is **#{pos}** on the leaderboard\n{bar(xp - base, nxt - base)}  {n(xp)} / {n(nxt)} XP")

@cmd("leaderboard", "leveling", "leaderboard", "Top 10 members by XP.")
async def leaderboard(ctx):
    rows = db.execute("SELECT id,xp,level FROM users WHERE xp>0 ORDER BY xp DESC LIMIT 10").fetchall()
    if not rows:
        return await ctx.reply("No data yet. Start chatting to earn XP!")
    await card(ctx, "XP leaderboard", "\n".join(f"**{i+1}.** <@{r[0]}> level {r[2]} ({n(r[1])} XP)" for i, r in enumerate(rows)))

# ------------------------------------------------------------------ economy
@cmd("balance", "economy", "balance [user]", "Show your coins (or another member's).")
async def balance(ctx):
    uid = parse_uid(ctx.argv[0]) if ctx.argv else str(ctx.author_id)
    await card(ctx, "Balance", f"<@{uid}> has **{n(get_user(uid)[2])}** coins")

@cmd("daily", "economy", "daily", "Claim 250 coins every 24 hours. Claim on consecutive days for a streak bonus (+25 per day, up to +250).")
async def daily(ctx):
    _, _, coins, last, _, streak = get_user(ctx.author_id)
    elapsed = time.time() - last
    if elapsed < 86400:
        return await ctx.reply(f"Already claimed. Come back in {fmt_time(86400 - elapsed)}.")
    streak = streak + 1 if elapsed < 172800 else 1
    reward = 250 + min(streak - 1, 10) * 25
    set_user(ctx.author_id, coins=coins + reward, last_daily=time.time(), streak=streak)
    await card(ctx, "Daily reward", f"You claimed **{n(reward)}** coins\nStreak: {streak} day{'s' if streak != 1 else ''}")

JOBS = ["delivered pizzas", "fixed a few bugs", "walked some dogs", "moderated a chat", "mowed lawns", "streamed for an hour"]

@cmd("work", "economy", "work", "Earn 40-120 coins. 1 hour cooldown.")
async def work(ctx):
    _, _, coins, _, last, _ = get_user(ctx.author_id)
    if time.time() - last < 3600:
        return await ctx.reply(f"You're tired. Rest for {fmt_time(3600 - (time.time() - last))}.")
    amt = random.randint(40, 120)
    set_user(ctx.author_id, coins=coins + amt, last_work=time.time())
    await card(ctx, "Work", f"You {random.choice(JOBS)} and earned **{n(amt)}** coins")

@cmd("pay", "economy", "pay <user> <amount>", "Send some of your coins to another member.")
async def pay(ctx):
    if len(ctx.argv) < 2 or not ctx.argv[1].isdigit():
        return await ctx.reply(f"Usage: `{PREFIX}pay <user> <amount>`")
    target, amt = parse_uid(ctx.argv[0]), int(ctx.argv[1])
    coins = get_user(ctx.author_id)[2]
    if target == str(ctx.author_id):
        return await ctx.reply("You can't pay yourself.")
    if amt <= 0:
        return await ctx.reply("Amount must be above 0.")
    if amt > coins:
        return await ctx.reply(f"You only have {n(coins)} coins.")
    set_user(ctx.author_id, coins=coins - amt)
    set_user(target, coins=get_user(target)[2] + amt)
    await card(ctx, "Payment sent", f"You sent **{n(amt)}** coins to <@{target}>")

@cmd("gamble", "economy", "gamble <amount | half | all>", "Bet coins. Slightly under 50% to win double, otherwise you lose the bet.")
async def gamble(ctx):
    coins = get_user(ctx.author_id)[2]
    arg = ctx.argv[0].lower() if ctx.argv else ""
    amt = coins if arg == "all" else coins // 2 if arg == "half" else int(arg) if arg.isdigit() else 0
    if not arg or (not arg.isdigit() and arg not in ("all", "half")):
        return await ctx.reply(f"Usage: `{PREFIX}gamble <amount | half | all>`")
    if amt <= 0 or amt > coins:
        return await ctx.reply(f"Invalid bet. You have {n(coins)} coins.")
    win = random.random() < 0.48
    set_user(ctx.author_id, coins=coins + (amt if win else -amt))
    await card(ctx, "You won!" if win else "You lost", f"{'+' if win else '-'}{n(amt)} coins. Balance: **{n(coins + (amt if win else -amt))}**")

@cmd("richest", "economy", "richest", "Top 10 members by coins.")
async def richest(ctx):
    rows = db.execute("SELECT id,coins FROM users WHERE coins>0 ORDER BY coins DESC LIMIT 10").fetchall()
    if not rows:
        return await ctx.reply("Nobody has any coins yet.")
    await card(ctx, "Richest members", "\n".join(f"**{i+1}.** <@{r[0]}> {n(r[1])} coins" for i, r in enumerate(rows)))

@cmd("addcoins", "admin", "addcoins <user> <amount>", "Give (or remove with a negative number) coins from a member. Owner/trusted only.")
async def addcoins(ctx):
    if not trusted(ctx.author_id):
        return await ctx.reply("Owner/trusted only")
    if len(ctx.argv) < 2 or not re.fullmatch(r"-?\d+", ctx.argv[1]):
        return await ctx.reply(f"Usage: `{PREFIX}addcoins <user> <amount>`")
    uid, amt = parse_uid(ctx.argv[0]), int(ctx.argv[1])
    new = max(0, get_user(uid)[2] + amt)
    set_user(uid, coins=new)
    await ctx.reply(f"<@{uid}> now has {n(new)} coins.")

# ------------------------------------------------------------------ admin
@cmd("trust", "admin", "trust <user_id>", "Let a member use the admin commands. Owner only.")
async def trust(ctx):
    if str(ctx.author_id) not in OWNER_IDS:
        return await ctx.reply("Owner only (set OWNER_IDS)")
    if not ctx.argv:
        return await ctx.reply(f"Usage: `{PREFIX}trust <user_id>`")
    db.execute("INSERT OR IGNORE INTO trusted VALUES(?)", (parse_uid(ctx.argv[0]),)); db.commit()
    await ctx.reply("Added.")

@cmd("ping", "general", "ping", "Check that the bot is alive.")
async def ping(ctx):
    await ctx.reply("Pong!")

# ------------------------------------------------------------------ welcome
@cmd("setwelcome", "admin", "setwelcome <channel_id> <message>",
     "Set the welcome channel and message. Use {user} to mention the new member. Owner/trusted only.")
async def setwelcome(ctx):
    if not trusted(ctx.author_id):
        return await ctx.reply("Owner/trusted only")
    if len(ctx.argv) < 2:
        return await ctx.reply(f"Usage: `{PREFIX}setwelcome <channel_id> Welcome {{user}}!`")
    put(f"{ctx.server_id}:welcome_channel", ctx.argv[0].strip("<#>"))
    put(f"{ctx.server_id}:welcome_msg", " ".join(ctx.argv[1:]))
    await ctx.reply("Welcome message saved.")

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

@cmd("antiraid", "admin", "antiraid on|off | antiraid threshold <joins>",
     "Kicks new joiners for 5 minutes when that many people join within 10 seconds (default 6). Owner/trusted only.")
async def antiraid(ctx):
    if not trusted(ctx.author_id):
        return await ctx.reply("Owner/trusted only")
    a, s = ctx.argv, ctx.server_id
    if a and a[0] in ("on", "off"):
        put(f"{s}:antiraid", a[0])
    elif len(a) == 2 and a[0] == "threshold" and a[1].isdigit():
        put(f"{s}:raid_threshold", a[1])
    else:
        return await ctx.reply(f"Usage: `{PREFIX}antiraid on|off|threshold <joins in 10s>`")
    await ctx.reply("Antiraid updated.")

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

@cmd("antinuke", "admin", "antinuke on|off | alert <channel_id> | channels <n> | bans <n>",
     "Watches for mass channel deletions (recreates them) and mass bans (reverts them). 'alert' sets where warnings are posted. Owner/trusted only.")
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
        return await ctx.reply(f"Usage: `{PREFIX}antinuke on|off|alert <channel_id>|channels <n>|bans <n>`")
    await ctx.reply("Antinuke updated.")

# ------------------------------------------------------------------ tickets
@cmd("ticketpanel", "tickets", "ticketpanel", "Post the 'Open ticket' button in this channel. Owner/trusted only.")
async def ticketpanel(ctx):
    if not trusted(ctx.author_id):
        return await ctx.reply("Owner/trusted only")
    emb = Embed().title("Support").description("Press the button to open a ticket.")
    await ctx.say(embeds=[emb], components=[Row(Button.primary("Open ticket", "ticket_open"))])

@cmd("ticketstaff", "tickets", "ticketstaff <role_id>", "Make new ticket channels private to this role. Owner/trusted only.")
async def ticketstaff(ctx):
    if not trusted(ctx.author_id) or not ctx.argv:
        return await ctx.reply(f"Usage (owner/trusted): `{PREFIX}ticketstaff <role_id>`")
    put(f"{ctx.server_id}:ticket_staff", ctx.argv[0])
    await ctx.reply("Saved.")

@bot.button("ticket_open")
async def ticket_open(ic):
    if is_off(ic.server_id, "ticket_open", "tickets"):
        return await ic.ephemeral("Tickets are disabled in this server.")
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

@cmd("close", "tickets", "close", "Close (delete) the ticket channel you're in. Ticket owner or staff only.")
async def close(ctx):
    if not (ctx.channel_name or "").startswith("ticket-"):
        return await ctx.reply("This isn't a ticket channel.")
    uname = re.sub(r"[^a-z0-9-]", "", str((ctx.author or {}).get("username", "")).lower())[:20]
    if not trusted(ctx.author_id) and ctx.channel_name != f"ticket-{uname}":
        return await ctx.reply("Only the ticket owner or staff can close this.")
    await bot.delete_channel(ctx.channel_id)

# ------------------------------------------------------------------ help
@cmd("help", "general", "help [command]", "List all commands, or show details for one command.")
async def help_cmd(ctx):
    sid = ctx.server_id
    if not ctx.argv:
        e = Embed().title("Commands").footer(f"{PREFIX}help <command> for details")
        for cat, label in CATEGORIES.items():
            names = sorted(k for k, v in HELP.items() if v[2] == cat)
            if names:
                e.field(label, ", ".join(f"`{PREFIX}{x}`" + (" (off)" if is_off(sid, x, cat) else "") for x in names))
        return await ctx.reply("", embeds=[e])
    name = ctx.argv[0].lower().lstrip(PREFIX)
    if name not in HELP:
        return await ctx.reply(f"No command called `{name}`. Try `{PREFIX}help`.")
    usage, desc, cat = HELP[name]
    status = "disabled in this server" if is_off(sid, name, cat) else "enabled"
    e = Embed().title(f"{PREFIX}{name}").description(desc)
    e.field("Usage", f"`{PREFIX}{usage}`").field("Category", CATEGORIES[cat], True).field("Status", status, True)
    await ctx.reply("", embeds=[e])

# ------------------------------------------------------------------ health server
# Render Web Services only turn "Live" once something listens on $PORT.
# This tiny server answers "ok" so the deploy completes (and uptime pingers can hit it).
def start_health_server():
    port = os.environ.get("PORT")
    if not port:
        return
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer

    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200); self.end_headers(); self.wfile.write(b"ok")
        do_HEAD = do_GET
        def log_message(self, *a):
            pass

    threading.Thread(target=HTTPServer(("0.0.0.0", int(port)), H).serve_forever, daemon=True).start()
    log.info("health server listening on %s", port)

start_health_server()
bot.run()
