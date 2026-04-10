import asyncio
from typing import Callable, Awaitable, Any

from aiogram import Bot, Dispatcher, F
from aiogram.types import TelegramObject, Message, ChatJoinRequest, BotCommand, BotCommandScopeChat, BotCommandScopeDefault
from aiogram import BaseMiddleware

from config import BOT_TOKEN, GROUP_ID, CLIP_LIBRARY_CHANNEL_ID, PURCHASES_LOG_ID, ADMINS
from core.database import init_db, fetch_one, execute
from core.scheduler import start_scheduler
from core.helpers import user_link

from handlers.ai     import register_ai_handlers,     moderate_message
from handlers.users  import register_user_handlers
from handlers.admins import register_admin_handlers
from handlers.bounty import register_bounty_handlers

# ── Bot + Dispatcher ──────────────────────────────────────────────────────
bot = Bot(BOT_TOKEN)
dp  = Dispatcher()

# ── Command menus ─────────────────────────────────────────────────────────
MEMBER_COMMANDS = [
    BotCommand(command="start",        description="Register / Welcome"),
    BotCommand(command="profile",      description="Your profile"),
    BotCommand(command="checkin",      description="Daily check-in (+5 pts)"),
    BotCommand(command="top",          description="Leaderboard"),
    BotCommand(command="shop",         description="Browse shop"),
    BotCommand(command="inv",          description="Your inventory"),
    BotCommand(command="buy",          description="Buy an item"),
    BotCommand(command="use",          description="Use an item"),
    BotCommand(command="gift",         description="Gift item to someone"),
    BotCommand(command="market",       description="Marketplace"),
    BotCommand(command="mywork",       description="Your active work"),
    BotCommand(command="submit",       description="Submit your work"),
    BotCommand(command="history",      description="Points history"),
    BotCommand(command="achievements", description="Your achievements & badges"),
    BotCommand(command="mybounties",   description="Your bounties"),
    BotCommand(command="bounty",       description="Create private bounty"),
    BotCommand(command="pbounty",      description="Create public bounty"),
    BotCommand(command="ask",          description="Chat with Nexus AI (VIP only)"),
    BotCommand(command="askreset",     description="Reset Nexus AI chat"),
    BotCommand(command="staffs",       description="Staff list"),
    BotCommand(command="rules",        description="Point system rules"),
    BotCommand(command="help",         description="Get help"),
]

ADMIN_COMMANDS = MEMBER_COMMANDS + [
    BotCommand(command="givework",      description="Assign work to user"),
    BotCommand(command="removework",    description="Remove user's work"),
    BotCommand(command="review",        description="Review submission"),
    BotCommand(command="givepoints",    description="Give points to user"),
    BotCommand(command="removepoints",  description="Remove points from user"),
    BotCommand(command="giveartist",    description="Give artist points"),
    BotCommand(command="ban",           description="Ban user"),
    BotCommand(command="unban",         description="Unban user"),
    BotCommand(command="warnuser",      description="Warn a user"),
    BotCommand(command="warnings",      description="See user warnings"),
    BotCommand(command="broadcast",     description="Broadcast message to all"),
    BotCommand(command="announce",      description="Announce in group"),
    BotCommand(command="setdeadline",   description="Change user deadline"),
    BotCommand(command="speciality",    description="Set user speciality"),
    BotCommand(command="pendingworks",  description="All active works"),
    BotCommand(command="pendingreviews",description="Pending reviews"),
    BotCommand(command="activeusers",   description="All registered users"),
    BotCommand(command="topwork",       description="Top performers this month"),
    BotCommand(command="report",        description="Bot stats report"),
    BotCommand(command="status",        description="Bot health status"),
    BotCommand(command="classstart",    description="Start a class session"),
    BotCommand(command="classend",      description="End a class session"),
    BotCommand(command="toggleai",      description="Toggle AI moderation"),
    BotCommand(command="resetpoints",   description="Reset user points"),
    BotCommand(command="resetstreak",   description="Reset user streak"),
    BotCommand(command="resetwarning",  description="Reset AI warnings"),
    BotCommand(command="deleteuser",    description="Delete user data"),
    BotCommand(command="setprice",      description="Change shop price"),
    BotCommand(command="bounty_success",description="Confirm bounty complete"),
    BotCommand(command="pbounty_success",description="Confirm public bounty"),
    BotCommand(command="pbounty_cancel", description="Cancel public bounty"),
    BotCommand(command="remind",        description="Send reminder to user"),
]

async def set_commands():
    # Everyone sees member commands
    await bot.set_my_commands(MEMBER_COMMANDS, scope=BotCommandScopeDefault())
    # Admins see full list in their private chat
    for admin_id in ADMINS:
        try:
            await bot.set_my_commands(
                ADMIN_COMMANDS,
                scope=BotCommandScopeChat(chat_id=admin_id)
            )
        except Exception as e:
            print(f"[CMD SCOPE] Could not set for {admin_id}: {e}")

# ── Group restriction middleware ──────────────────────────────────────────
class GroupRestrictionMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event:   TelegramObject,
        data:    dict[str, Any],
    ) -> Any:
        chat = None
        if hasattr(event, "chat"):
            chat = event.chat
        elif hasattr(event, "message") and event.message:
            chat = event.message.chat
        if chat is not None:
            if chat.type == "private" or chat.id == GROUP_ID:
                return await handler(event, data)
            return
        return await handler(event, data)

dp.message.middleware(GroupRestrictionMiddleware())
dp.callback_query.middleware(GroupRestrictionMiddleware())

# ── Group watcher (outburst + AI moderation) ──────────────────────────────
@dp.message(F.chat.id == GROUP_ID, ~F.text.startswith("/"))
async def group_message_watcher(message: Message):
    from handlers.users import track_outburst
    await track_outburst(message, bot)
    if message.text and not message.from_user.is_bot:
        asyncio.create_task(moderate_message(message, bot))

# ── Clip Library join request handler ────────────────────────────────────
@dp.chat_join_request(F.chat.id == CLIP_LIBRARY_CHANNEL_ID)
async def clip_library_join_request(request: ChatJoinRequest):
    uid      = request.from_user.id
    approved = await fetch_one(
        "SELECT telegram_id FROM clip_approved WHERE telegram_id = ?", (uid,)
    )
    if approved:
        await bot.approve_chat_join_request(CLIP_LIBRARY_CHANNEL_ID, uid)
        await execute("DELETE FROM clip_approved WHERE telegram_id = ?", (uid,))
        try:
            await bot.send_message(uid,
                "✅ <b>Welcome to the Clip Library!</b>\n\nYou now have access to all the clips. Enjoy! 🎬",
                parse_mode="HTML"
            )
        except Exception:
            pass
        await bot.send_message(PURCHASES_LOG_ID,
            f"✅ <b>Clip Library — Approved</b>\n"
            f"👤 {user_link(request.from_user.first_name or 'User', uid, request.from_user.username)}",
            parse_mode="HTML"
        )
    else:
        await bot.decline_chat_join_request(CLIP_LIBRARY_CHANNEL_ID, uid)
        try:
            await bot.send_message(uid,
                "❌ <b>Access Denied — Clip Library</b>\n\n"
                "You need to purchase the 📚 <b>clip_library</b> item from /shop first.\n"
                "After buying, use /use clip_library to get your personal invite link.",
                parse_mode="HTML"
            )
        except Exception:
            pass

# ── Error handler ─────────────────────────────────────────────────────────
@dp.errors()
async def error_handler(event):
    print(f"[ERROR] {event.exception}")

# ── Register all handlers (ORDER MATTERS) ────────────────────────────────
register_ai_handlers(dp, bot)
register_user_handlers(dp, bot)
register_admin_handlers(dp, bot)
register_bounty_handlers(dp, bot)

# ── Entry point ───────────────────────────────────────────────────────────
async def main():
    await init_db()
    await set_commands()
    print("✅ Bot started.")
    asyncio.create_task(start_scheduler(bot))
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
