"""
Yapper bot: antinuke, antiraid, music queue, leveling, welcome, tickets, economy.
Built on PyYapper (pip install pyyapper). Prefix commands as registered with @bot.command.

Run:   YAPPER_TOKEN=yb_xxx python bot.py
Render: set YAPPER_TOKEN in Environment, start command `python bot.py`.

Anything marked VERIFY is a Yapper API call/event name I could not confirm from
the public docs. Those calls go through api(), which logs instead of crashing if
your pyyapper version doesn't have the method, so you can fix names one by one.
"""
import os, sqlite3, time, random, collections, logging
from pyyapper import Bot, Embed, Button, Row

log = logging.getLogger("bot")
logging.basicConfig(level=logging.INFO)

TOKEN = os.environ["YAPPER_TOKEN"]          # never hardcode the token
OWNER_IDS = set(filter(None, os.environ.get("OWNER_IDS", "").split(",")))
DB_PATH = os.environ.get("DB_PATH", "bot.db")

bot = Bot(TOKEN)

# ---------------------------------------------------------------- storage
db = sqlite3.connect(DB_PATH)
db.executescript("""
CREATE TABLE IF NOT EXISTS users(id TEXT PRIMARY KEY, xp INT DEFAULT 0, level INT DEFAULT 0,
                                 coins INT DEFAULT 0, last_daily REAL DEFAULT 0, last_work REAL DEFAULT 0);
CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS whitelist(id TEXT PRIMARY KEY);
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

def put_setting(key, value):
    db.execute("INSERT OR REPLACE INTO settings VALUES(?,?)", (key, str(value)))
    db.commit()

def is_trusted(uid):
    return str(uid) in OWNER_IDS or bool(db.execute("SELECT 1 FROM whitelist WHERE id=?", (str(uid),)).fetchone())

async def api(name, *a, **kw):
    """Call a bot method if it exists in this pyyapper version; log otherwise."""
    fn = getattr(bot, name, None)
    if fn is None:
        log.warning("pyyapper has no bot.%s - fix the name in bot.py (VERIFY)", name)
        return None
    try:
        return await fn(*a, **kw)
    except Exception as e:
        log.warning("bot.%s failed: %s", name, e)

# ---------------------------------------------------------------- leveling
xp_cd = {}

def level_for(xp):
    return int((xp / 100) ** 0.5)

@bot.event
async def on_ready(me):
    log.info("online as %s", me.username)

@bot.event
async def on_message(msg):                       # VERIFY: event name/shape
    author = getattr(msg, "author", None)
    if not author or getattr(author, "bot", False):
        return
    uid = author.id
    if time.time() - xp_cd.get(uid, 0) < 30:
        return
    xp_cd[uid] = time.time()
    xp, lvl, coins, *_ = get_user(uid)
    xp += random.randint(10, 20)
    new = level_for(xp)
    set_user(uid, xp=xp, level=new, coins=coins + 2)
    if new > lvl:
        await api("send", msg.channel_id, f"GG <@{uid}>, you reached level {new}!")   # VERIFY

@bot.command("rank", description="Show your level and XP")
async def rank(ctx):
    xp, lvl, coins, *_ = get_user(ctx.author.id)
    nxt = ((lvl + 1) ** 2) * 100
    await ctx.reply(f"{ctx.author_mention} level {lvl} | {xp}/{nxt} XP")

@bot.command("leaderboard", description="Top 10 by XP")
async def leaderboard(ctx):
    rows = db.execute("SELECT id,xp,level FROM users ORDER BY xp DESC LIMIT 10").fetchall()
    await ctx.reply("\n".join(f"{i+1}. <@{r[0]}> lvl {r[2]} ({r[1]} XP)" for i, r in enumerate(rows)) or "No data yet.")

# ---------------------------------------------------------------- economy
@bot.command("balance", description="Show your coins")
async def balance(ctx):
    await ctx.reply(f"{ctx.author_mention} you have {get_user(ctx.author.id)[2]} coins")

@bot.command("daily", description="Claim daily coins")
async def daily(ctx):
    _, _, coins, last, _ = get_user(ctx.author.id)
    if time.time() - last < 86400:
        left = int(86400 - (time.time() - last))
        return await ctx.reply(f"Come back in {left // 3600}h {left % 3600 // 60}m")
    set_user(ctx.author.id, coins=coins + 250, last_daily=time.time())
    await ctx.reply("You claimed 250 coins")

@bot.command("work", description="Work for coins (1h cooldown)")
async def work(ctx):
    _, _, coins, _, last = get_user(ctx.author.id)
    if time.time() - last < 3600:
        return await ctx.reply(f"Rest for {int(3600 - (time.time() - last)) // 60}m")
    pay = random.randint(40, 120)
    set_user(ctx.author.id, coins=coins + pay, last_work=time.time())
    await ctx.reply(f"You earned {pay} coins")

@bot.command("pay", description="pay <user_id> <amount>")
async def pay(ctx):
    if len(ctx.argv) < 2 or not ctx.argv[1].isdigit():
        return await ctx.reply("Usage: .pay <user_id> <amount>")
    target, amt = ctx.argv[0].strip("<@>"), int(ctx.argv[1])
    coins = get_user(ctx.author.id)[2]
    if amt <= 0 or amt > coins or target == str(ctx.author.id):
        return await ctx.reply("Invalid amount")
    set_user(ctx.author.id, coins=coins - amt)
    set_user(target, coins=get_user(target)[2] + amt)
    await ctx.reply(f"Sent {amt} coins to <@{target}>")

@bot.command("gamble", description="gamble <amount> (50/50)")
async def gamble(ctx):
    if not ctx.argv or not ctx.argv[0].isdigit():
        return await ctx.reply("Usage: .gamble <amount>")
    amt, coins = int(ctx.argv[0]), get_user(ctx.author.id)[2]
    if amt <= 0 or amt > coins:
        return await ctx.reply("Invalid amount")
    win = random.random() < 0.48
    set_user(ctx.author.id, coins=coins + (amt if win else -amt))
    await ctx.reply(f"You {'won' if win else 'lost'} {amt} coins")

@bot.command("richest", description="Top 10 by coins")
async def richest(ctx):
    rows = db.execute("SELECT id,coins FROM users ORDER BY coins DESC LIMIT 10").fetchall()
    await ctx.reply("\n".join(f"{i+1}. <@{r[0]}> {r[1]}" for i, r in enumerate(rows)) or "No data yet.")

# ---------------------------------------------------------------- welcome
@bot.command("setwelcome", description="setwelcome <channel_id> <message> ({user} = mention)")
async def setwelcome(ctx):
    if not is_trusted(ctx.author.id):
        return await ctx.reply("Owner/whitelist only")
    if len(ctx.argv) < 2:
        return await ctx.reply("Usage: .setwelcome <channel_id> Welcome {user}!")
    put_setting("welcome_channel", ctx.argv[0])
    put_setting("welcome_msg", " ".join(ctx.argv[1:]))
    await ctx.reply("Welcome message saved")

# ---------------------------------------------------------------- antiraid
joins = collections.deque()
lockdown_until = 0

@bot.event
async def on_member_join(member):                # VERIFY: event name/shape
    global lockdown_until
    now = time.time()
    joins.append(now)
    while joins and now - joins[0] > 10:
        joins.popleft()
    if len(joins) >= int(setting("raid_threshold", 6)) and setting("antiraid", "on") == "on":
        lockdown_until = now + 300
        log.warning("raid detected, lockdown for 5 min")
    if now < lockdown_until and not is_trusted(member.id):
        await api("kick", member.id, reason="Antiraid lockdown")                      # VERIFY
        return
    ch, msg = setting("welcome_channel"), setting("welcome_msg")
    if ch and msg:
        await api("send", ch, msg.replace("{user}", f"<@{member.id}>"))               # VERIFY

@bot.command("antiraid", description="antiraid on|off|threshold <n>")
async def antiraid(ctx):
    if not is_trusted(ctx.author.id):
        return await ctx.reply("Owner/whitelist only")
    a = ctx.argv
    if a and a[0] in ("on", "off"):
        put_setting("antiraid", a[0])
    elif len(a) == 2 and a[0] == "threshold" and a[1].isdigit():
        put_setting("raid_threshold", a[1])
    else:
        return await ctx.reply("Usage: .antiraid on|off|threshold <n>")
    await ctx.reply("Antiraid updated")

# ---------------------------------------------------------------- antinuke
actions = collections.defaultdict(collections.deque)

async def nuke_check(actor_id, what):
    if setting("antinuke", "on") != "on" or not actor_id or is_trusted(actor_id):
        return
    now, q = time.time(), actions[actor_id]
    q.append(now)
    while q and now - q[0] > 10:
        q.popleft()
    if len(q) >= int(setting("nuke_threshold", 3)):
        log.warning("antinuke: %s did %s x%d in 10s", actor_id, what, len(q))
        await api("ban", actor_id, reason="Antinuke: mass destructive actions")        # VERIFY
        q.clear()

# VERIFY: the event names below. Each should receive something carrying the actor's id.
def _actor(ev):
    return getattr(ev, "actor_id", None) or getattr(ev, "user_id", None) or getattr(getattr(ev, "actor", None), "id", None)

@bot.event
async def on_member_ban(ev):      await nuke_check(_actor(ev), "ban")
@bot.event
async def on_member_kick(ev):     await nuke_check(_actor(ev), "kick")
@bot.event
async def on_channel_delete(ev):  await nuke_check(_actor(ev), "channel_delete")
@bot.event
async def on_role_delete(ev):     await nuke_check(_actor(ev), "role_delete")

@bot.command("antinuke", description="antinuke on|off|threshold <n>|whitelist <user_id>")
async def antinuke(ctx):
    if str(ctx.author.id) not in OWNER_IDS:
        return await ctx.reply("Owner only (set OWNER_IDS)")
    a = ctx.argv
    if a and a[0] in ("on", "off"):
        put_setting("antinuke", a[0])
    elif len(a) == 2 and a[0] == "threshold" and a[1].isdigit():
        put_setting("nuke_threshold", a[1])
    elif len(a) == 2 and a[0] == "whitelist":
        db.execute("INSERT OR IGNORE INTO whitelist VALUES(?)", (a[1].strip("<@>"),)); db.commit()
    else:
        return await ctx.reply("Usage: .antinuke on|off|threshold <n>|whitelist <user_id>")
    await ctx.reply("Antinuke updated")

# ---------------------------------------------------------------- tickets
@bot.command("ticketpanel", description="Post the ticket button")
async def ticketpanel(ctx):
    if not is_trusted(ctx.author.id):
        return await ctx.reply("Owner/whitelist only")
    emb = Embed(title="Support", description="Press the button to open a ticket.")
    await ctx.reply(embed=emb, components=[Row(Button("Open ticket", id="ticket_open"))])  # VERIFY kwargs

@bot.button("ticket_open")
async def ticket_open(i):
    ch = await api("create_channel", name=f"ticket-{i.user.id}", private=True,
                   members=[i.user.id])                                                # VERIFY
    if ch is None:
        return await i.ephemeral("Couldn't create the ticket channel, ask an admin.")
    await i.ephemeral("Ticket created.")

# ---------------------------------------------------------------- music (queue only)
queues = collections.defaultdict(list)

@bot.command("play", description="play <url or search>")
async def play(ctx):
    if not ctx.argv:
        return await ctx.reply("Usage: .play <url or search>")
    queues[ctx.guild_id if hasattr(ctx, "guild_id") else "g"].append(" ".join(ctx.argv))
    await ctx.reply("Queued. (Actual voice playback needs Yapper's voice API, see notes.)")

@bot.command("queue", description="Show the queue")
async def queue(ctx):
    q = queues[ctx.guild_id if hasattr(ctx, "guild_id") else "g"]
    await ctx.reply("\n".join(f"{i+1}. {t}" for i, t in enumerate(q)) or "Queue is empty")

@bot.command("skip", description="Skip current song")
async def skip(ctx):
    q = queues[ctx.guild_id if hasattr(ctx, "guild_id") else "g"]
    if q: q.pop(0)
    await ctx.reply("Skipped")

@bot.command("stop", description="Clear the queue")
async def stop(ctx):
    queues[ctx.guild_id if hasattr(ctx, "guild_id") else "g"].clear()
    await ctx.reply("Stopped")

bot.run()
