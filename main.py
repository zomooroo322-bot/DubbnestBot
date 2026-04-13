import asyncio
from typing import Callable, Awaitable, Any

from aiogram import Bot, Dispatcher, F
from aiogram.types import TelegramObject, Message, BotCommand, BotCommandScopeChat, BotCommandScopeDefault
from aiogram import BaseMiddleware

from config import BOT_TOKEN, GROUP_ID, PURCHASES_LOG_ID, ADMINS
from core.database import init_db
from core.scheduler import start_scheduler

from handlers.ai      import register_ai_handlers,     moderate_message
from handlers.users   import register_user_handlers
from handlers.admins  import register_admin_handlers
from handlers.bounty  import register_bounty_handlers
from handlers.classes import register_class_handlers

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

# ── Error handler ─────────────────────────────────────────────────────────
@dp.errors()
async def error_handler(event):
    print(f"[ERROR] {event.exception}")

# ── Register all handlers (ORDER MATTERS) ────────────────────────────────
register_ai_handlers(dp, bot)
register_user_handlers(dp, bot)
register_admin_handlers(dp, bot)
register_bounty_handlers(dp, bot)
register_class_handlers(dp, bot)

# ── Entry point ───────────────────────────────────────────────────────────
async def main():
    await init_db()
    await set_commands()
    print("✅ Bot started.")
    asyncio.create_task(start_scheduler(bot))
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
