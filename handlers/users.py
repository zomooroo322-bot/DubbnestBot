import asyncio
import random
import datetime
from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.types import Message

from config import (
    ADMINS, GROUP_ID, PURCHASES_LOG_ID, STORE, ITEM_EMOJI, ITEM_DESCRIPTIONS,
    CLIP_LIBRARY_LINK, CHECKIN_PTS, CHECKIN_STREAK_BONUS, CHECKIN_STREAK_DAYS,
    OUTBURST_EVERY, OUTBURSTS, RATINGS, REVIEWER_IDS, SHOP_MIN_POINTS,
)
from core.database import (
    fetch_one, fetch_all, execute,
    upsert_user, get_user_by_tgid, get_user_by_username,
    log_points, add_to_inventory,
)
from core.helpers import (
    user_link, calculate_rank, parse_args, strip_at,
    is_admin, check_banned, resolve_user, build_inv_text,
)

# ── outburst counter ──────────────────────────────────────────────────────
_msg_counter: dict = {}

async def track_outburst(message: Message, bot: Bot):
    if message.chat.id != GROUP_ID or message.from_user.is_bot:
        return
    _msg_counter[GROUP_ID] = _msg_counter.get(GROUP_ID, 0) + 1
    if _msg_counter[GROUP_ID] >= OUTBURST_EVERY:
        _msg_counter[GROUP_ID] = 0
        await asyncio.sleep(random.uniform(1.0, 3.5))
        await bot.send_message(GROUP_ID, random.choice(OUTBURSTS))

# ── profile helpers ───────────────────────────────────────────────────────
async def get_badges(db_user) -> str:
    now = datetime.datetime.now()
    month_start = now.replace(day=1, hour=0, minute=0, second=0).strftime("%Y-%m-%d %H:%M:%S")
    penalty_row = await fetch_one(
        "SELECT COUNT(*) AS c FROM points_history WHERE user_id = ? AND change < 0 "
        "AND reason LIKE '%Late penalty%' AND ts >= ?",
        (db_user["id"], month_start)
    )
    badges = []
    if penalty_row and penalty_row["c"] == 0 and db_user["projects"] > 0:
        badges.append("🏅 Clean Month")
    if db_user["checkin_streak"] >= 7:
        badges.append("🔥 Streak Master")
    if db_user["projects"] >= 5:
        badges.append("🎬 Veteran")
    if db_user["artist_points"] >= 500:
        badges.append("⭐ High Earner")
    return "  ".join(badges) if badges else ""

async def build_info_text(tg_user, db_user) -> str:
    rank = calculate_rank(db_user["artist_points"])
    if db_user["is_vip"]:
        vip_exp = db_user["vip_expires_at"]
        vip_tag = f" 👑 VIP (expires {vip_exp[:10]})" if vip_exp else " 👑 VIP"
    else:
        vip_tag = ""
    link       = user_link(tg_user.first_name or "User", tg_user.id)
    badges     = await get_badges(db_user)
    badge_line = f"\n🏅 Badges: {badges}" if badges else ""
    spent      = db_user["total_points"] - db_user["remaining_points"]
    return (
        f"╔══ 🎙 <b>DUBBNEST PROFILE</b> ══╗\n\n"
        f"👤 {link}{vip_tag}\n"
        f"🎤 <b>Speciality:</b> {db_user['speciality']}\n"
        f"🏆 <b>Rank:</b> {rank}\n"
        f"🔥 <b>Streak:</b> {db_user['checkin_streak']} day(s)\n"
        f"🎬 <b>Projects:</b> {db_user['projects']}\n\n"
        f"━━━━ 💠 POINTS ━━━━\n"
        f"🎨 <b>Artist Points:</b> {db_user['artist_points']} pts\n"
        f"   <i>(dubbing work only — never decreases)</i>\n"
        f"📊 <b>Total Points:</b> {db_user['total_points']} pts\n"
        f"   <i>(lifetime earnings — never decreases)</i>\n"
        f"💰 <b>Remaining Points:</b> {db_user['remaining_points']} pts\n"
        f"   <i>(your spendable wallet)</i>\n"
        f"💸 <b>Spent:</b> {spent} pts"
        f"{badge_line}\n\n"
        f"╚══════════════════╝"
    )

async def build_stats_text(tg_user, db_user) -> str:
    avg_pts = round(db_user["total_points"] / db_user["projects"], 1) if db_user["projects"] else 0
    spent   = db_user["total_points"] - db_user["remaining_points"]
    vip_tag = " 👑 VIP" if db_user["is_vip"] else ""
    link    = user_link(tg_user.first_name or "User", tg_user.id)
    return (
        f"📊 <b>STATS — {link}{vip_tag}</b>\n\n"
        f"🎨 Artist Points: <b>{db_user['artist_points']}</b>\n"
        f"⭐ Total Points Earned: <b>{db_user['total_points']}</b>\n"
        f"💰 Remaining Points: <b>{db_user['remaining_points']}</b>\n"
        f"💸 Points Spent: <b>{spent}</b>\n"
        f"🎬 Projects Completed: <b>{db_user['projects']}</b>\n"
        f"📈 Avg Points/Project: <b>{avg_pts}</b>\n"
        f"🔥 Streak: <b>{db_user['checkin_streak']}</b> day(s)\n"
        f"📅 Last Check-in: <b>{db_user['last_checkin'] or 'Never'}</b>\n"
        f"⚠️ Penalties: <b>{db_user['penalties_received']}</b>\n"
        f"🛒 Items Bought: <b>{db_user['items_bought']}</b>\n"
        f"⚙️ Items Used: <b>{db_user['items_used']}</b>"
    )

# ═════════════════════════════════════════════════════════════════════════
def register_user_handlers(dp: Dispatcher, bot: Bot):

    @dp.message(Command("start"))
    async def cmd_start(message: Message):
        await track_outburst(message, bot)
        await upsert_user(message.from_user)
        user = await get_user_by_tgid(message.from_user.id)
        if user and user["is_banned"]:
            return await message.reply(
                "🚫 <b>You are banned from Dubbnest.</b>\nContact an admin if you think this is a mistake.",
                parse_mode="HTML"
            )
        await message.reply(
            "👋 Hi, I'm <b>Dubbnest BOT!</b>\n\nSee what you can do: /commands\nNeed Help? — /help",
            parse_mode="HTML"
        )

    @dp.message(Command("help"))
    async def cmd_help(message: Message):
        await track_outburst(message, bot)
        await message.reply(
            "🆘 <b>DUBBNEST SUPPORT</b>\n"
            "━━━━━━━━━━━━━━━━\n\n"
            "Contact staff if you need help:\n\n"
            "• <a href='tg://user?id=8061402854'>ʜɪʀᴏ ʜᴀᴍᴀᴅᴀ ⎋</a>\n"
            "• <a href='tg://user?id=8635661368'>𝗔𝗠𝗣𝗘𝗥𝗘 ✘ 𝗚𝗔𝗟𝗔𝗫𝗬 ✨</a>\n"
            "• <a href='tg://user?id=7826336730'>꧁ᴴᴱᴿᏒᴏм𝑒Ꭷ✧</a>\n"
            "• <a href='tg://user?id=7141606933'>꧁ᵸⁱˢᎫᴜ֟፝ℓiᴇᴛ✧</a>\n"
            "• <a href='tg://user?id=7218769930'>Mary Voiceovers</a>\n\n"
            "<b>Bot / Technical</b>\n"
            "• <a href='tg://user?id=5685840380'>ZOMOOROO [🇵🇸]</a>\n\n"
            "━━━━━━━━━━━━━━━━",
            parse_mode="HTML"
        )

    @dp.message(Command("commands"))
    async def cmd_commands(message: Message):
        await track_outburst(message, bot)
        await message.reply(
            "📜 <b>MEMBER COMMANDS</b>\n\n"
            "/start — Register\n"
            "/profile — Your profile\n"
            "/mywork — Current assignment\n"
            "/submit — Submit work\n"
            "/checkin — Daily check-in (+5 pts)\n"
            "/top — Artist leaderboard\n"
            "/leaderboard_artists — Top 10 by artist points\n"
            "/achievements — Your badges & milestones\n"
            "/ratinghistory — Your past reviews\n"
            "/stats — Your statistics\n"
            "/history — Points history\n"
            "/shop — Browse store\n"
            "/iteminfo &lt;item&gt; — Item details\n"
            "/buy &lt;item&gt; — Purchase item\n"
            "/inv — Your inventory\n"
            "/use &lt;item&gt; — Use an item\n"
            "/bounty @user &lt;amount&gt; — Private bounty\n"
            "/pbounty — Public bounty (reply to video)\n"
            "/market — Marketplace\n"
            "/mybounties — Your bounty history\n"
            "/ask &lt;question&gt; — Nexus AI 👑 VIP only\n"
            "/askreset — Clear Nexus AI history\n"
            "/rules — Point system rules\n",
            parse_mode="HTML"
        )

    @dp.message(Command("rules"))
    async def cmd_rules(message: Message):
        await track_outburst(message, bot)
        await message.reply(
            "📋 <b>POINT SYSTEM — HOW IT WORKS</b>\n\n"
            "━━━━━━━━━━━━━━━━\n"
            "✅ <b>HOW TO EARN</b>\n"
            "━━━━━━━━━━━━━━━━\n\n"
            "🎨 <b>Artist Points</b> (dubbing only, never decreases)\n"
            "  • Excellent → <b>+10 artist pts</b>\n"
            "  • Very Good → <b>+8 artist pts</b>\n"
            "  • Average → <b>+6 artist pts</b>\n"
            "  • Need Improvement → <b>+4 artist pts</b>\n"
            "  • Poor → <b>0 pts</b>\n\n"
            "💰 <b>Remaining Points</b> (wallet)\n"
            "  • Project review → same as artist pts\n"
            "  • On-time submit bonus → <b>+5 pts</b>\n"
            "  • Daily check-in → <b>+5 pts</b>\n"
            "  • 7-day streak bonus → <b>+20 pts</b>\n"
            "  • Class attendance → <b>+1 to +6 pts</b>\n\n"
            "🎯 <b>Milestones</b>\n"
            "  • 3 tasks on time → <b>+10 pts</b>\n"
            "  • 7-day streak → <b>+20 pts</b>\n"
            "  • 1 month no penalties → <b>+100 pts</b>\n\n"
            "━━━━━━━━━━━━━━━━\n"
            "❌ <b>HOW POINTS ARE LOST</b>\n"
            "━━━━━━━━━━━━━━━━\n\n"
            "  • Shop purchases\n"
            "  • Late submission → <b>-15 pts/day</b>\n"
            "  • AI moderation warning → <b>-20 pts</b>\n\n"
            "━━━━━━━━━━━━━━━━\n"
            "🏆 <b>RANKS</b> (by Artist Points)\n"
            "━━━━━━━━━━━━━━━━\n\n"
            "  0 → 🌱 Beginner\n"
            "  100 → 👀 On Watch\n"
            "  170 → ✅ Active Member\n"
            "  300 → 🎨 Skilled Artist\n"
            "  750 → ⭐ Star Artist\n"
            "  1050 → 🏆 Elite Dubber\n\n"
            "━━━━━━━━━━━━━━━━\n"
            "🏅 <b>BADGES</b>\n"
            "━━━━━━━━━━━━━━━━\n\n"
            "  🏅 Clean Month — no late penalties + 1 project\n"
            "  🔥 Streak Master — 7+ day streak\n"
            "  🎬 Veteran — 5+ projects\n"
            "  ⭐ High Earner — 500+ artist points\n\n"
            "━━━━━━━━━━━━━━━━\n"
            "👑 <b>VIP PERKS</b> (14 days)\n"
            "━━━━━━━━━━━━━━━━\n\n"
            "  • 👑 VIP tag on profile & leaderboard\n"
            "  • 🤖 Access to Nexus AI\n"
            "  • 🎙 2x 10-min VC with admin\n"
            "  • ⚡ 1x priority review\n"
            "  • 📚 Clip library access\n",
            parse_mode="HTML"
        )

    @dp.message(Command("info"))
    async def cmd_info(message: Message):
        await track_outburst(message, bot)
        if await check_banned(message): return
        if message.reply_to_message and message.reply_to_message.from_user.id != message.from_user.id:
            target    = message.reply_to_message.from_user
            db_target = await get_user_by_tgid(target.id)
            if not db_target:
                return await message.reply("❌ That user is not registered.")
            return await message.reply(await build_info_text(target, db_target), parse_mode="HTML")
        await upsert_user(message.from_user)
        db_user = await get_user_by_tgid(message.from_user.id)
        await message.reply(await build_info_text(message.from_user, db_user), parse_mode="HTML")

    @dp.message(Command("checkin"))
    async def cmd_checkin(message: Message):
        await track_outburst(message, bot)
        if await check_banned(message): return
        await upsert_user(message.from_user)
        user  = await get_user_by_tgid(message.from_user.id)
        today = datetime.date.today()
        last  = datetime.date.fromisoformat(user["last_checkin"]) if user["last_checkin"] else None
        if last == today:
            return await message.reply("⏰ Already checked in today! Come back tomorrow. 🔥")
        streak    = user["checkin_streak"]
        streak    = streak + 1 if last == today - datetime.timedelta(days=1) else 1
        bonus     = CHECKIN_STREAK_BONUS if streak % CHECKIN_STREAK_DAYS == 0 else 0
        pts_total = CHECKIN_PTS + bonus
        await execute(
            "UPDATE users SET remaining_points = remaining_points + ?, total_points = total_points + ?, "
            "checkin_streak = ?, last_checkin = ? WHERE id = ?",
            (pts_total, pts_total, streak, today.isoformat(), user["id"])
        )
        await log_points(user["id"], pts_total, f"✅ Daily check-in (streak {streak})")
        msg = (
            f"✅ <b>Check-in!</b>\n\n"
            f"🔥 Streak: <b>{streak}</b> day(s)\n"
            f"➕ Points: <b>+{CHECKIN_PTS}</b>"
        )
        if bonus:
            msg += f"\n🎉 <b>7-day streak bonus: +{CHECKIN_STREAK_BONUS}!</b>"
        days_to_next = CHECKIN_STREAK_DAYS - (streak % CHECKIN_STREAK_DAYS)
        msg += f"\n⏭ Next streak bonus in <b>{days_to_next}</b> day(s)"
        await message.reply(msg, parse_mode="HTML")

    @dp.message(Command("top"))
    async def cmd_top(message: Message):
        await track_outburst(message, bot)
        rows = await fetch_all(
            f"SELECT telegram_id, first_name, username, artist_points, remaining_points, is_vip "
            f"FROM users WHERE telegram_id NOT IN ({','.join(str(a) for a in ADMINS)}) "
            f"ORDER BY artist_points DESC LIMIT 10"
        )
        if not rows:
            return await message.reply("No users yet.")
        medals = ["🥇", "🥈", "🥉"]
        lines  = []
        for i, row in enumerate(rows):
            medal    = medals[i] if i < 3 else f"{i+1}."
            vip_tag  = " 👑" if row["is_vip"] else ""
            link     = user_link(row["first_name"], row["telegram_id"], row["username"])
            rank_str = calculate_rank(row["artist_points"])
            lines.append(f"{medal} {link}{vip_tag}\n    🎨 <b>{row['artist_points']}</b> artist pts | {rank_str}")
        await message.reply("🏆 <b>TOP 10 — ARTIST LEADERBOARD</b>\n\n" + "\n".join(lines), parse_mode="HTML")

    @dp.message(Command("stats"))
    async def cmd_stats(message: Message):
        await track_outburst(message, bot)
        if message.reply_to_message and message.reply_to_message.from_user.id != message.from_user.id:
            target    = message.reply_to_message.from_user
            db_target = await get_user_by_tgid(target.id)
            if not db_target:
                return await message.reply("❌ That user is not registered.")
            return await message.reply(await build_stats_text(target, db_target), parse_mode="HTML")
        await upsert_user(message.from_user)
        db_user = await get_user_by_tgid(message.from_user.id)
        await message.reply(await build_stats_text(message.from_user, db_user), parse_mode="HTML")

    @dp.message(Command("history"))
    async def cmd_history(message: Message):
        await track_outburst(message, bot)
        if await check_banned(message): return
        await upsert_user(message.from_user)
        user = await get_user_by_tgid(message.from_user.id)
        rows = await fetch_all(
            "SELECT change, reason, ts FROM points_history WHERE user_id = ? ORDER BY id DESC LIMIT 20",
            (user["id"],)
        )
        if not rows:
            return await message.reply("📭 No points history yet.\nStart earning with /checkin or completing projects!", parse_mode="HTML")
        lines = ["📜 <b>POINTS HISTORY</b> (last 20)\n"]
        for row in rows:
            sign  = "+" if row["change"] >= 0 else ""
            emoji = "🟢" if row["change"] >= 0 else "🔴"
            lines.append(f"{emoji} <b>{sign}{row['change']} pts</b> — {row['reason']}\n<i>{row['ts']}</i>")
        await message.reply("\n\n".join(lines), parse_mode="HTML")

    @dp.message(Command("shop"))
    async def cmd_shop(message: Message):
        await track_outburst(message, bot)
        if await check_banned(message): return
        lines = "\n".join(
            f"{ITEM_EMOJI.get(k,'📦')} <code>{k}</code> — <b>{v}</b> pts"
            for k, v in STORE.items()
        )
        await message.reply(
            f"🛒 <b>STORE</b>\n\nUse /buy &lt;item_name&gt; to purchase.\n\n{lines}",
            parse_mode="HTML"
        )

    @dp.message(Command("iteminfo"))
    async def cmd_iteminfo(message: Message):
        await track_outburst(message, bot)
        args = parse_args(message.text, 2)
        if not args:
            return await message.reply(
                f"Usage: /iteminfo &lt;item&gt;\nItems: {', '.join(f'<code>{k}</code>' for k in STORE)}",
                parse_mode="HTML"
            )
        item = args[1].lower()
        if item not in STORE:
            return await message.reply("❌ Item not found. Use /shop to see all items.")
        desc   = ITEM_DESCRIPTIONS.get(item, "No description available.")
        emoji  = ITEM_EMOJI.get(item, "📦")
        cost   = STORE[item]
        extras = ""
        if item == "vip":
            extras = (
                "\n\n<b>Perks:</b>\n"
                "• 👑 VIP tag on profile & leaderboard\n"
                "• 🤖 Access to Nexus AI\n"
                "• 🎙 2x 10-min VC with admin\n"
                "• ⚡ 1x priority review\n"
                "• 📚 Clip library access\n"
                "• Duration: 14 days"
            )
        elif item == "deadline_extension":
            extras = (
                "\n\n<b>Details:</b>\n"
                "• ⏳ Adds 1 extra day to your deadline\n"
                "• No penalty for the extra day\n"
                "• Use /use deadline_extension to activate"
            )
        await message.reply(
            f"{emoji} <b>{item}</b> — <b>{cost} pts</b>\n\n{desc}{extras}",
            parse_mode="HTML"
        )

    @dp.message(Command("buy"))
    async def cmd_buy(message: Message):
        await track_outburst(message, bot)
        if await check_banned(message): return
        args = parse_args(message.text, 2)
        if not args:
            return await message.reply("Usage: /buy &lt;item&gt;", parse_mode="HTML")
        item = args[1].lower()
        if item not in STORE:
            return await message.reply("❌ Item not found. Check /shop.")
        await upsert_user(message.from_user)
        user = await get_user_by_tgid(message.from_user.id)
        if not is_admin(message.from_user.id) and user["remaining_points"] < SHOP_MIN_POINTS:
            return await message.reply(
                f"🔒 <b>Shop Locked</b>\n\n"
                f"You need at least <b>{SHOP_MIN_POINTS} remaining points</b> to use the shop.\n"
                f"You currently have <b>{user['remaining_points']} pts</b>.\n\n"
                f"💡 Complete projects and check in daily to earn more!",
                parse_mode="HTML"
            )
        cost = STORE[item]
        if not is_admin(message.from_user.id) and user["remaining_points"] < cost:
            return await message.reply(
                f"❌ Not enough points. You have <b>{user['remaining_points']}</b>, need <b>{cost}</b>.",
                parse_mode="HTML"
            )
        if not is_admin(message.from_user.id):
            await execute(
                "UPDATE users SET remaining_points = remaining_points - ? WHERE telegram_id = ?",
                (cost, message.from_user.id)
            )
            await log_points(user["id"], -cost, f"🛒 Bought {item}")
        display = user_link(message.from_user.first_name or "User", message.from_user.id)
        await add_to_inventory(user["id"], item)
        await message.reply(
            f"✅ Purchased {ITEM_EMOJI.get(item,'📦')} <b>{item}</b>!\n"
            f"Check /inv — use /use {item} to activate.",
            parse_mode="HTML"
        )
        try:
            await bot.send_message(message.from_user.id,
                ITEM_DESCRIPTIONS.get(item, f"✅ You bought <b>{item}</b>"), parse_mode="HTML"
            )
        except Exception:
            pass
        await bot.send_message(PURCHASES_LOG_ID,
            f"🛍 <b>New Purchase</b>\n👤 {display}\n📦 <b>{item}</b> — {cost} pts\n"
            f"⏰ {datetime.datetime.now().strftime('%Y-%m-%d %H:%M')}",
            parse_mode="HTML"
        )

    @dp.message(Command("inv"))
    async def cmd_inv(message: Message):
        await track_outburst(message, bot)
        if await check_banned(message): return
        if message.reply_to_message and message.reply_to_message.from_user.id != message.from_user.id:
            target    = message.reply_to_message.from_user
            db_target = await get_user_by_tgid(target.id)
            if not db_target:
                return await message.reply("❌ That user is not registered.")
            items = await fetch_all(
                "SELECT item FROM inventory WHERE user_id = ? ORDER BY obtained_at DESC", (db_target["id"],)
            )
            name = user_link(target.first_name or "User", target.id)
            return await message.reply(await build_inv_text(name, items, own=False), parse_mode="HTML")
        await upsert_user(message.from_user)
        user  = await get_user_by_tgid(message.from_user.id)
        items = await fetch_all(
            "SELECT item FROM inventory WHERE user_id = ? ORDER BY obtained_at DESC", (user["id"],)
        )
        name = user_link(message.from_user.first_name or "User", message.from_user.id)
        await message.reply(await build_inv_text(name, items, own=True), parse_mode="HTML")

    @dp.message(Command("use"))
    async def cmd_use(message: Message):
        await track_outburst(message, bot)
        if await check_banned(message): return
        args = parse_args(message.text, 2)
        if not args:
            return await message.reply("Usage: /use &lt;item&gt;", parse_mode="HTML")
        item = args[1].lower()
        await upsert_user(message.from_user)
        user = await get_user_by_tgid(message.from_user.id)
        inv_row = await fetch_one(
            "SELECT id FROM inventory WHERE user_id = ? AND item = ? LIMIT 1", (user["id"], item)
        )
        if not inv_row:
            return await message.reply(
                f"❌ You don't have <b>{item}</b> in your inventory. Check /inv.", parse_mode="HTML"
            )
        if item == "vip":
            await execute("DELETE FROM inventory WHERE id = ?", (inv_row["id"],))
            vip_expires = (datetime.datetime.now() + datetime.timedelta(days=14)).strftime("%Y-%m-%d %H:%M:%S")
            await execute(
                "UPDATE users SET is_vip = 1, vip_expires_at = ?, items_used = items_used + 1 WHERE id = ?",
                (vip_expires, user["id"])
            )
            await add_to_inventory(user["id"], "admins_voices")
            await add_to_inventory(user["id"], "admins_voices")
            await add_to_inventory(user["id"], "priority_review")
            now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            await execute(
                "INSERT INTO clip_approved (telegram_id, approved_at) VALUES (?, ?) "
                "ON CONFLICT(telegram_id) DO UPDATE SET approved_at = EXCLUDED.approved_at",
                (message.from_user.id, now_str)
            )
            vip_fmt = datetime.datetime.strptime(vip_expires, "%Y-%m-%d %H:%M:%S").strftime("%Y-%m-%d %H:%M")
            await message.reply(
                f"👑 <b>VIP Activated! (14 Days)</b>\n\n"
                f"✅ Expires: <b>{vip_fmt}</b>\n"
                f"🎙 2x 10-min VC with admin added to inventory\n"
                f"⚡ 1x priority review added to inventory\n"
                f"📚 Clip Library access armed — use /use clip_library to get your link!",
                parse_mode="HTML"
            )
            await bot.send_message(PURCHASES_LOG_ID,
                f"👑 <b>VIP Activated</b>\n👤 {user_link(message.from_user.first_name or 'User', message.from_user.id)}\n"
                f"⏳ Expires: {vip_fmt}",
                parse_mode="HTML"
            )
            return
        if item == "deadline_extension":
            work = await fetch_one("SELECT id, deadline, max_days FROM works WHERE user_id = ?", (user["id"],))
            if not work:
                return await message.reply("❌ You have no active work to extend.")
            new_deadline = (
                datetime.datetime.fromisoformat(work["deadline"]) + datetime.timedelta(days=1)
            ).isoformat()
            await execute(
                "UPDATE works SET deadline = ?, max_days = max_days + 1 WHERE id = ?",
                (new_deadline, work["id"])
            )
            await execute("DELETE FROM inventory WHERE id = ?", (inv_row["id"],))
            await execute("UPDATE users SET items_used = items_used + 1 WHERE id = ?", (user["id"],))
            await message.reply("⏳ <b>Deadline Extended by 1 day!</b> No penalty for the extra day.", parse_mode="HTML")
            return
        if item == "clip_library":
            await execute("DELETE FROM inventory WHERE id = ?", (inv_row["id"],))
            await execute("UPDATE users SET items_used = items_used + 1 WHERE id = ?", (user["id"],))
            try:
                now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                await execute(
                    "INSERT INTO clip_approved (telegram_id, approved_at) VALUES (?, ?) "
                    "ON CONFLICT(telegram_id) DO UPDATE SET approved_at = EXCLUDED.approved_at",
                    (message.from_user.id, now_str)
                )
                await bot.send_message(
                    message.from_user.id,
                    f"📚 <b>Clip Library Access</b>\n\n"
                    f"Tap the link below to request access:\n"
                    f"👉 {CLIP_LIBRARY_LINK}\n\n"
                    f"The bot will approve your request automatically.\n"
                    f"⚠️ Sharing this link won't help others — only your account is approved.",
                    parse_mode="HTML"
                )
                await message.reply(
                    "📚 <b>Clip Library link sent to your DMs!</b>\nTap the link and the bot will approve you instantly.",
                    parse_mode="HTML"
                )
                await bot.send_message(PURCHASES_LOG_ID,
                    f"📚 <b>Clip Library Access Granted</b>\n"
                    f"👤 {user_link(message.from_user.first_name or 'User', message.from_user.id, user['username'])}\n"
                    f"🔐 Auto-approve armed for their join request.",
                    parse_mode="HTML"
                )
            except Exception as e:
                print(f"[CLIP_LIBRARY] Failed: {e}")
                await execute("DELETE FROM clip_approved WHERE telegram_id = ?", (message.from_user.id,))
                await add_to_inventory(user["id"], "clip_library")
                await execute("UPDATE users SET items_used = items_used - 1 WHERE id = ?", (user["id"],))
                await message.reply("❌ Something went wrong. Item returned to your inventory. Contact an admin.")
            return
        await execute("DELETE FROM inventory WHERE id = ?", (inv_row["id"],))
        await execute("UPDATE users SET items_used = items_used + 1 WHERE id = ?", (user["id"],))
        try:
            await bot.send_message(message.from_user.id,
                ITEM_DESCRIPTIONS.get(item, f"✅ You used: <b>{item}</b>"), parse_mode="HTML"
            )
        except Exception:
            pass
        await message.reply(
            f"✅ Used {ITEM_EMOJI.get(item,'📦')} <b>{item}</b>! Check your DMs. Item removed from inventory.",
            parse_mode="HTML"
        )
        await bot.send_message(PURCHASES_LOG_ID,
            f"⚙️ <b>Item Used</b>\n👤 {user_link(message.from_user.first_name or 'User', message.from_user.id)} used <b>{item}</b>",
            parse_mode="HTML"
        )

    @dp.message(Command("market"))
    async def cmd_market(message: Message):
        await track_outburst(message, bot)
        if await check_banned(message): return
        parts = message.text.split(maxsplit=3)
        sub   = parts[1].lower() if len(parts) >= 2 else "browse"

        if sub in ("list", "buy"):
            await upsert_user(message.from_user)
            _u = await get_user_by_tgid(message.from_user.id)
            if not is_admin(message.from_user.id) and _u and _u["remaining_points"] < SHOP_MIN_POINTS:
                return await message.reply(
                    f"🔒 <b>Marketplace Locked</b>\n\n"
                    f"You need at least <b>{SHOP_MIN_POINTS} remaining points</b> to use the marketplace.\n"
                    f"You currently have <b>{_u['remaining_points']} pts</b>.\n\n"
                    f"💡 Complete projects and check in daily to earn more!",
                    parse_mode="HTML"
                )

        if sub in ("browse", "market"):
            listings = await fetch_all(
                "SELECT m.id, m.item, m.price, u.first_name, u.username, u.telegram_id "
                "FROM market m JOIN users u ON m.seller_id = u.id ORDER BY m.listed_at DESC LIMIT 30"
            )
            if not listings:
                return await message.reply(
                    "🏪 <b>MARKET</b>\n\nNo listings right now.\nUse /market list &lt;item&gt; &lt;price&gt; to sell.",
                    parse_mode="HTML"
                )
            lines = []
            for row in listings:
                emoji  = ITEM_EMOJI.get(row["item"], "📦")
                seller = user_link(row["first_name"], row["telegram_id"], row["username"])
                lines.append(f"<b>#{row['id']}</b> {emoji} <code>{row['item']}</code> — <b>{row['price']}</b> pts | {seller}")
            await message.reply(
                "🏪 <b>MARKET LISTINGS</b>\n\n" + "\n".join(lines) + "\n\nUse /market buy &lt;id&gt; to purchase.",
                parse_mode="HTML"
            )
            return

        if sub == "list":
            if len(parts) != 4:
                return await message.reply("Usage: /market list &lt;item&gt; &lt;price&gt;", parse_mode="HTML")
            item = parts[2].lower()
            try:
                price = int(parts[3])
                if price <= 0: raise ValueError
            except ValueError:
                return await message.reply("❌ Price must be a positive integer.")
            await upsert_user(message.from_user)
            seller  = await get_user_by_tgid(message.from_user.id)
            inv_row = await fetch_one(
                "SELECT id FROM inventory WHERE user_id = ? AND item = ? LIMIT 1", (seller["id"], item)
            )
            if not inv_row:
                return await message.reply(f"❌ You don't have <b>{item}</b> in your inventory.", parse_mode="HTML")
            await execute("DELETE FROM inventory WHERE id = ?", (inv_row["id"],))
            await execute(
                "INSERT INTO market (seller_id, item, price, listed_at) VALUES (?, ?, ?, ?)",
                (seller["id"], item, price, datetime.datetime.now().strftime("%Y-%m-%d %H:%M"))
            )
            await message.reply(
                f"✅ Listed {ITEM_EMOJI.get(item,'📦')} <b>{item}</b> for <b>{price}</b> pts!",
                parse_mode="HTML"
            )
            return

        if sub == "buy":
            if len(parts) != 3:
                return await message.reply("Usage: /market buy &lt;id&gt;", parse_mode="HTML")
            try:
                lid = int(parts[2])
            except ValueError:
                return await message.reply("❌ Invalid listing ID.")
            listing = await fetch_one(
                "SELECT m.*, u.telegram_id AS seller_tg, u.first_name AS seller_name, u.username AS seller_username "
                "FROM market m JOIN users u ON m.seller_id = u.id WHERE m.id = ?", (lid,)
            )
            if not listing:
                return await message.reply("❌ Listing not found.")
            await upsert_user(message.from_user)
            buyer = await get_user_by_tgid(message.from_user.id)
            if buyer["id"] == listing["seller_id"]:
                return await message.reply("❌ You can't buy your own listing.")
            if not is_admin(message.from_user.id) and buyer["remaining_points"] < listing["price"]:
                return await message.reply(
                    f"❌ Not enough points. Need <b>{listing['price']}</b>, have <b>{buyer['remaining_points']}</b>.",
                    parse_mode="HTML"
                )
            price = listing["price"]
            if not is_admin(message.from_user.id):
                await execute(
                    "UPDATE users SET remaining_points = remaining_points - ? WHERE id = ?",
                    (price, buyer["id"])
                )
            await execute(
                "UPDATE users SET remaining_points = remaining_points + ?, total_points = total_points + ? WHERE id = ?",
                (price, price, listing["seller_id"])
            )
            await add_to_inventory(buyer["id"], listing["item"])
            await execute("DELETE FROM market WHERE id = ?", (lid,))
            seller_link = user_link(listing["seller_name"], listing["seller_tg"], listing["seller_username"])
            buyer_link  = user_link(message.from_user.first_name or "User", message.from_user.id)
            await message.reply(
                f"✅ Bought {ITEM_EMOJI.get(listing['item'],'📦')} <b>{listing['item']}</b> "
                f"from {seller_link} for <b>{price}</b> pts!",
                parse_mode="HTML"
            )
            try:
                await bot.send_message(listing["seller_tg"],
                    f"💰 <b>Listing sold!</b>\n{ITEM_EMOJI.get(listing['item'],'📦')} <b>{listing['item']}</b> "
                    f"bought by {buyer_link} for <b>{price}</b> pts.",
                    parse_mode="HTML"
                )
            except Exception:
                pass
            return

        if sub == "cancel":
            if len(parts) != 3:
                return await message.reply("Usage: /market cancel &lt;id&gt;", parse_mode="HTML")
            try:
                lid = int(parts[2])
            except ValueError:
                return await message.reply("❌ Invalid listing ID.")
            await upsert_user(message.from_user)
            seller  = await get_user_by_tgid(message.from_user.id)
            listing = await fetch_one(
                "SELECT id, item FROM market WHERE id = ? AND seller_id = ?", (lid, seller["id"])
            )
            if not listing:
                return await message.reply("❌ Listing not found or not yours.")
            await execute("DELETE FROM market WHERE id = ?", (lid,))
            await add_to_inventory(seller["id"], listing["item"])
            await message.reply(
                f"✅ Cancelled. {ITEM_EMOJI.get(listing['item'],'📦')} <b>{listing['item']}</b> returned to inventory.",
                parse_mode="HTML"
            )
            return

        await message.reply("Unknown subcommand. Try /market, /market list, /market buy, /market cancel.")

    @dp.message(Command("mywork"))
    async def cmd_mywork(message: Message):
        await track_outburst(message, bot)
        if await check_banned(message): return
        await upsert_user(message.from_user)
        user = await get_user_by_tgid(message.from_user.id)
        work = await fetch_one("SELECT file_id, file_type, deadline FROM works WHERE user_id = ?", (user["id"],))
        if not work:
            return await message.reply("✅ No active work right now.")
        deadline  = datetime.datetime.fromisoformat(work["deadline"])
        days_left = max(0, (deadline - datetime.datetime.now()).days)
        caption   = f"⏳ Deadline: {deadline.strftime('%Y-%m-%d %H:%M')} ({days_left}d left)"
        file_type = work["file_type"] or "video"
        try:
            if file_type == "audio":
                await message.reply_audio(work["file_id"], caption=caption)
            else:
                await message.reply_video(work["file_id"], caption=caption)
        except Exception:
            await message.reply(f"⏳ You have active work.\nDeadline: {deadline.strftime('%Y-%m-%d %H:%M')} ({days_left}d left)")

    @dp.message(Command("submit"))
    async def cmd_submit(message: Message):
        await track_outburst(message, bot)
        if await check_banned(message): return
        if not message.reply_to_message:
            return await message.reply("Reply to your finished clip to submit.")
        await upsert_user(message.from_user)
        user = await get_user_by_tgid(message.from_user.id)
        work = await fetch_one("SELECT * FROM works WHERE user_id = ?", (user["id"],))
        if not work:
            return await message.reply("❌ You have no active work to submit.")
        if work["submitted"]:
            return await message.reply(
                "⏳ Already submitted. Waiting for admin review.\nUse /mywork to see your assignment."
            )
        now      = datetime.datetime.now()
        deadline = datetime.datetime.fromisoformat(work["deadline"])
        late     = now > deadline
        await execute("UPDATE works SET submitted = 1 WHERE user_id = ?", (user["id"],))
        if late:
            await message.reply(
                f"⚠️ <b>Submitted after deadline.</b>\n"
                f"Penalties applied: <b>{work['penalty_days']} day(s) × -15 pts</b>\n"
                f"Waiting for admin review.",
                parse_mode="HTML"
            )
        else:
            await message.reply(
                "✅ <b>Submitted on time!</b> Waiting for review.\n"
                "<i>If rated above Poor, you'll receive +5 bonus pts for submitting on time.</i>",
                parse_mode="HTML"
            )
        reviewer_tags = (
            f'<a href="tg://user?id=8061402854">ʜɪʀᴏ ʜᴀᴍᴀᴅᴀ ⎋</a> '
            f'<a href="tg://user?id=7141606933">꧁ᵸⁱˢᎫᴜ֟፝ℓiᴇᴛ✧</a>'
        )
        name        = user_link(message.from_user.first_name or "User", message.from_user.id)
        notify_text = (
            f"📥 <b>New Submission</b>\n"
            f"👤 {name}\n"
            f"⏰ {now.strftime('%Y-%m-%d %H:%M')}\n"
            f"{'⚠️ LATE' if late else '✅ On time'}\n\n"
            f"👑 {reviewer_tags} — please review!"
        )
        rep = message.reply_to_message
        try:
            if rep.video:
                await bot.send_video(PURCHASES_LOG_ID, rep.video.file_id, caption=notify_text, parse_mode="HTML")
            elif rep.audio:
                await bot.send_audio(PURCHASES_LOG_ID, rep.audio.file_id, caption=notify_text, parse_mode="HTML")
            elif rep.voice:
                await bot.send_voice(PURCHASES_LOG_ID, rep.voice.file_id, caption=notify_text, parse_mode="HTML")
            else:
                await bot.send_message(PURCHASES_LOG_ID, notify_text, parse_mode="HTML")
        except Exception:
            await bot.send_message(PURCHASES_LOG_ID, notify_text, parse_mode="HTML")

    @dp.message(Command("staffs"))
    async def cmd_staffs(message: Message):
        await track_outburst(message, bot)
        await message.reply(
            "👥 <b>DUBBNEST STAFF</b>\n"
            "━━━━━━━━━━━━━━━━\n\n"
            "꧁ᵸⁱˢᎫᴜ֟፝ℓiᴇᴛ✧\n"
            "<i>Creative Director, GFX Director, Merit Record</i>\n\n"
            "𝗔𝗙𝗭𝗔𝗟 ✦\n"
            "<i>Creative Director, Translator, Dubbing Supervisor, GFX Director, Educating Head, Library Manager, Upload Director, Merit Record, Ad Manager</i>\n\n"
            "𝗔𝗠𝗣𝗘𝗥𝗘 ✘\n"
            "<i>Dubbing Director (Quality), Dubbing Engineer (ST), Translator, Educating Head, Chaos Control</i>\n\n"
            "꧁ᴴᴱᴿᏒᴏм𝑒Ꭷ✧\n"
            "<i>Dubbing Director (Quality), Mixing Engineer, Educating Head, Advisor, Representative</i>\n\n"
            "𝗭𝗢𝗠𝗢𝗥𝗥𝗢𝗢 ✦\n"
            "<i>Mixing Engineer, IT Director, Library Manager</i>\n\n"
            "𝗠𝗔𝗥𝗬 ✦\n"
            "<i>GFX Director, Upload Director, Chaos Control, Ad Manager</i>\n\n"
            "━━━━━━━━━━━━━━━━",
            parse_mode="HTML"
        )

    @dp.message(Command("mybounties"))
    async def cmd_mybounties(message: Message):
        await track_outburst(message, bot)
        if await check_banned(message): return
        await upsert_user(message.from_user)
        user = await get_user_by_tgid(message.from_user.id)
        priv_sent = await fetch_all(
            "SELECT b.id, b.amount, b.status, u.first_name, u.username, u.telegram_id "
            "FROM bounties b JOIN users u ON b.performer_id = u.id "
            "WHERE b.requester_id = ? ORDER BY b.id DESC LIMIT 10", (user["id"],)
        )
        priv_recv = await fetch_all(
            "SELECT b.id, b.amount, b.status, u.first_name, u.username, u.telegram_id "
            "FROM bounties b JOIN users u ON b.requester_id = u.id "
            "WHERE b.performer_id = ? ORDER BY b.id DESC LIMIT 10", (user["id"],)
        )
        pub_sent = await fetch_all(
            "SELECT id, reward, status, voice_gender, voice_type FROM pbounties "
            "WHERE requester_id = ? ORDER BY id DESC LIMIT 10", (user["id"],)
        )
        pub_recv = await fetch_all(
            "SELECT pb.id, pb.reward, pb.status, pb.voice_gender, pb.voice_type, "
            "u.first_name, u.username, u.telegram_id "
            "FROM pbounties pb JOIN users u ON pb.requester_id = u.id "
            "WHERE pb.performer_id = ? ORDER BY pb.id DESC LIMIT 10", (user["id"],)
        )
        lines = []
        if priv_sent:
            lines.append("🎯 <b>Private Bounties Sent</b>")
            for b in priv_sent:
                target = user_link(b["first_name"], b["telegram_id"], b["username"])
                lines.append(f"  #{b['id']} → {target} | <b>{b['amount']} pts</b> | {b['status']}")
        if priv_recv:
            lines.append("\n📩 <b>Private Bounties Received</b>")
            for b in priv_recv:
                src = user_link(b["first_name"], b["telegram_id"], b["username"])
                lines.append(f"  #{b['id']} ← {src} | <b>{b['amount']} pts</b> | {b['status']}")
        if pub_sent:
            lines.append("\n📢 <b>Public Bounties Posted</b>")
            for b in pub_sent:
                lines.append(f"  #{b['id']} | {b['voice_gender']} {b['voice_type']} | <b>{b['reward']} pts</b> | {b['status']}")
        if pub_recv:
            lines.append("\n🎤 <b>Public Bounties Assigned to You</b>")
            for b in pub_recv:
                req = user_link(b["first_name"], b["telegram_id"], b["username"])
                lines.append(f"  #{b['id']} ← {req} | <b>{b['reward']} pts</b> | {b['status']}")
        if not lines:
            return await message.reply("📭 You have no bounties yet.")
        await message.reply("\n".join(lines), parse_mode="HTML")

    @dp.message(Command("cancel_pbounty"))
    async def cmd_cancel_pbounty_user(message: Message):
        await track_outburst(message, bot)
        args = parse_args(message.text, 2)
        if not args:
            return await message.reply("Usage: /cancel_pbounty &lt;bounty_id&gt;", parse_mode="HTML")
        try:
            bounty_id = int(args[1])
        except ValueError:
            return await message.reply("❌ Invalid bounty ID.")
        await upsert_user(message.from_user)
        user   = await get_user_by_tgid(message.from_user.id)
        bounty = await fetch_one(
            "SELECT * FROM pbounties WHERE id = ? AND status = 'open'", (bounty_id,)
        )
        if not bounty:
            return await message.reply("❌ Bounty not found or no longer open.")
        if bounty["requester_id"] != user["id"]:
            return await message.reply("❌ You can only cancel your own bounties.")
        await execute(
            "UPDATE users SET remaining_points = remaining_points + ? WHERE id = ?",
            (bounty["reward"], user["id"])
        )
        await execute("UPDATE pbounties SET status = 'cancelled' WHERE id = ?", (bounty_id,))
        await message.reply(
            f"✅ Public Bounty #{bounty_id} cancelled.\n<b>{bounty['reward']} pts</b> refunded.",
            parse_mode="HTML"
        )

    @dp.message(Command("profile"))
    async def cmd_profile(message: Message):
        await track_outburst(message, bot)
        if await check_banned(message): return
        if message.reply_to_message:
            tg_user = message.reply_to_message.from_user
            db_user = await get_user_by_tgid(tg_user.id)
            if not db_user:
                return await message.reply("❌ That user is not registered.")
        else:
            tg_user = message.from_user
            await upsert_user(tg_user)
            db_user = await get_user_by_tgid(tg_user.id)
        await message.reply(await build_info_text(tg_user, db_user), parse_mode="HTML")

    @dp.message(Command("leaderboard_artists"))
    async def cmd_leaderboard_artists(message: Message):
        await track_outburst(message, bot)
        rows = await fetch_all(
            f"SELECT telegram_id, first_name, username, artist_points, projects, is_vip "
            f"FROM users WHERE telegram_id NOT IN ({','.join(str(a) for a in ADMINS)}) "
            f"ORDER BY artist_points DESC LIMIT 10"
        )
        if not rows:
            return await message.reply("No artists yet.")
        medals = ["🥇", "🥈", "🥉"]
        lines  = []
        for i, row in enumerate(rows):
            medal    = medals[i] if i < 3 else f"{i+1}."
            vip_tag  = " 👑" if row["is_vip"] else ""
            link     = user_link(row["first_name"], row["telegram_id"], row["username"])
            rank_str = calculate_rank(row["artist_points"])
            lines.append(
                f"{medal} {link}{vip_tag}\n"
                f"    🎨 <b>{row['artist_points']}</b> pts | {rank_str} | 🎬 {row['projects']} projects"
            )
        await message.reply(
            "🎨 <b>TOP 10 — ARTIST POINTS</b>\n\n" + "\n\n".join(lines),
            parse_mode="HTML"
        )

    @dp.message(Command("achievements"))
    async def cmd_achievements(message: Message):
        await track_outburst(message, bot)
        if await check_banned(message): return
        await upsert_user(message.from_user)
        user = await get_user_by_tgid(message.from_user.id)
        now  = datetime.datetime.now()
        month_start = now.replace(day=1, hour=0, minute=0, second=0).strftime("%Y-%m-%d %H:%M:%S")
        penalty_row = await fetch_one(
            "SELECT COUNT(*) AS c FROM points_history WHERE user_id = ? AND change < 0 "
            "AND reason LIKE '%Late penalty%' AND ts >= ?",
            (user["id"], month_start)
        )
        no_penalties = penalty_row and penalty_row["c"] == 0

        def ach(unlocked, icon, name, desc):
            status = "✅" if unlocked else "🔒"
            return f"{status} {icon} <b>{name}</b>\n    <i>{desc}</i>"

        lines = [
            "🏆 <b>YOUR ACHIEVEMENTS</b>\n",
            "<b>— Badges —</b>",
            ach(no_penalties and user["projects"] > 0, "🏅", "Clean Month", "No late penalties this month + 1 project"),
            ach(user["checkin_streak"] >= 7, "🔥", "Streak Master", "7+ day check-in streak"),
            ach(user["projects"] >= 5, "🎬", "Veteran", "5+ completed projects"),
            ach(user["artist_points"] >= 500, "⭐", "High Earner", "500+ artist points"),
            "",
            "<b>— Milestones —</b>",
            ach(user["tasks_on_time"] >= 3, "🎯", "Consistent", "3 tasks submitted on time"),
            ach(user["checkin_streak"] >= 7, "📅", "Week Warrior", "7-day perfect streak"),
            ach(no_penalties and user["projects"] > 0, "🛡", "Iron Discipline", "Full month with no penalties"),
            "",
            "<b>— Rank Progress —</b>",
            f"🎨 Artist Points: <b>{user['artist_points']}</b>",
            f"🏆 Current Rank: <b>{calculate_rank(user['artist_points'])}</b>",
            f"🎬 Projects Done: <b>{user['projects']}</b>",
            f"🔥 Streak: <b>{user['checkin_streak']}</b> days",
        ]
        await message.reply("\n".join(lines), parse_mode="HTML")

    @dp.message(Command("ratinghistory"))
    async def cmd_ratinghistory(message: Message):
        await track_outburst(message, bot)
        if await check_banned(message): return
        await upsert_user(message.from_user)
        user = await get_user_by_tgid(message.from_user.id)
        rows = await fetch_all(
            "SELECT rating, artist_pts, bonus_pts, reviewed_at FROM rating_history "
            "WHERE user_id = ? ORDER BY id DESC LIMIT 15",
            (user["id"],)
        )
        if not rows:
            return await message.reply("📭 No rating history yet. Complete your first project!")
        RATING_EMOJI = {
            "excellent": "⭐", "verygood": "💚", "average": "🟡",
            "needimprovement": "🟠", "poor": "🔴"
        }
        lines = ["📋 <b>YOUR RATING HISTORY</b>\n"]
        for row in rows:
            emoji = RATING_EMOJI.get(row["rating"], "📋")
            bonus = f" +{row['bonus_pts']} submit bonus" if row["bonus_pts"] else ""
            lines.append(
                f"{emoji} <b>{row['rating'].capitalize()}</b> — "
                f"+{row['artist_pts']} artist pts{bonus}\n"
                f"<i>{row['reviewed_at']}</i>"
            )
        await message.reply("\n\n".join(lines), parse_mode="HTML")
