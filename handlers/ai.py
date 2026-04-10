import asyncio
import aiohttp
from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.types import Message

from config import (
    ADMINS, GROUP_ID,
    OPENROUTER_API_KEY, OPENROUTER_MODEL, OPENROUTER_URL,
    AI_MODERATION_ENABLED, AI_WARN_PENALTY,
    BOT_NAME, BOT_PERSONALITY,
)
from core.database import fetch_one, execute, upsert_user, get_user_by_tgid, log_points
from core.helpers import is_admin, check_banned, user_link

# ── shared state ──────────────────────────────────────────────────────────
ask_history:   dict[int, list] = {}   # per-user conversation history
ai_warn_count: dict[int, int]  = {}   # per-user warning counter

# ── AI call helper ────────────────────────────────────────────────────────
async def ai_call(messages: list, max_tokens: int = 500):
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                OPENROUTER_URL,
                headers={
                    "Authorization": f"Bearer {OPENROUTER_API_KEY}",
                    "Content-Type":  "application/json",
                },
                json={
                    "model":      OPENROUTER_MODEL,
                    "messages":   messages,
                    "max_tokens": max_tokens,
                },
                timeout=aiohttp.ClientTimeout(total=20)
            ) as resp:
                data = await resp.json()
                if resp.status == 200:
                    return data["choices"][0]["message"]["content"].strip()
                print(f"[AI ERROR] status={resp.status} body={data}")
                return None
    except Exception as e:
        print(f"[AI EXCEPTION] {e}")
        return None

# ── AI moderation (called from main group watcher) ────────────────────────
async def moderate_message(message: Message, bot):
    global AI_MODERATION_ENABLED
    if not AI_MODERATION_ENABLED:
        return
    if not message.text:
        return
    text = message.text.strip()
    if len(text) < 5:
        return
    if message.from_user.id in ADMINS:
        return
    if text.startswith("/"):
        return
    if text.replace(" ", "").isdigit():
        return
    if all(c.isdigit() or c in " +-:./," for c in text):
        return

    prompt = [
        {
            "role":    "system",
            "content": (
                "You are a strict content moderator for a Telegram dubbing community group. "
                "You ONLY flag messages that contain: insults, hate speech, slurs, harassment, "
                "threats, sexual content, or targeted personal attacks. "
                "You do NOT flag: numbers, IDs, links, casual chat, dubbing talk, short messages, "
                "random text, off-topic questions, or anything that is merely unusual. "
                "When in doubt, reply OK. Only reply VIOLATION for clear, obvious misbehaviour. "
                "Reply with ONLY one word: 'VIOLATION' or 'OK'."
            )
        },
        {"role": "user", "content": f"Message: {text}"}
    ]

    result = await ai_call(prompt, max_tokens=5)
    print(f"[MOD] user={message.from_user.id} text={text[:50]!r} result={result}")
    if not result or "VIOLATION" not in result.upper():
        return

    await upsert_user(message.from_user)
    user = await get_user_by_tgid(message.from_user.id)
    if not user:
        return

    uid = message.from_user.id
    ai_warn_count[uid] = ai_warn_count.get(uid, 0) + 1
    warn_num = ai_warn_count[uid]

    await execute(
        "UPDATE users SET remaining_points = GREATEST(0, remaining_points - ?) WHERE id = ?",
        (AI_WARN_PENALTY, user["id"])
    )
    await log_points(user["id"], -AI_WARN_PENALTY, f"🤖 AI moderation warning #{warn_num}")

    link = user_link(message.from_user.first_name or "User", uid)
    await message.reply(
        f"⚠️ <b>Warning #{warn_num}</b> — {link}\n"
        f"Your message was flagged for misbehaviour.\n"
        f"<b>-{AI_WARN_PENALTY} pts</b> deducted.\n\n"
        f"<i>Repeated violations may result in a ban.</i>",
        parse_mode="HTML"
    )

    if warn_num >= 3:
        for admin_id in ADMINS:
            try:
                await bot.send_message(admin_id,
                    f"🚨 <b>Repeated Violation</b>\n"
                    f"User {link} has <b>{warn_num} warnings</b>.\n"
                    f"Consider /ban @{message.from_user.username or uid}",
                    parse_mode="HTML"
                )
            except Exception:
                pass

# ═════════════════════════════════════════════════════════════════════════
def register_ai_handlers(dp: Dispatcher, bot: Bot):

    @dp.message(Command("ask"))
    async def cmd_ask(message: Message):
        if await check_banned(message): return
        await upsert_user(message.from_user)
        user        = await get_user_by_tgid(message.from_user.id)
        is_vip_user = user and user["is_vip"]
        is_adm      = is_admin(message.from_user.id)
        if not is_vip_user and not is_adm:
            return await message.reply(
                f"👑 <b>VIP Only</b>\n\n"
                f"<b>{BOT_NAME}</b> is only available to VIP members.\n"
                f"Buy VIP from /shop to unlock AI chat!",
                parse_mode="HTML"
            )
        args = message.text.split(maxsplit=1)
        if len(args) < 2 or not args[1].strip():
            return await message.reply(
                f"💬 <b>Chat with {BOT_NAME}</b>\n\n"
                f"Usage: /ask &lt;your question&gt;\n"
                f"Example: /ask How do I sync my voice to video?\n\n"
                f"<i>Use /askreset to clear your conversation history.</i>",
                parse_mode="HTML"
            )
        question = args[1].strip()
        uid      = message.from_user.id
        history  = ask_history.get(uid, [])
        history.append({"role": "user", "content": question})
        if len(history) > 12:
            history = history[-12:]
        prompt       = [{"role": "system", "content": BOT_PERSONALITY}] + history
        thinking_msg = await message.reply(f"💭 <i>{BOT_NAME} is thinking...</i>", parse_mode="HTML")
        answer       = await ai_call(prompt, max_tokens=600)
        if answer:
            history.append({"role": "assistant", "content": answer})
            ask_history[uid] = history
        try:
            if not answer:
                await thinking_msg.edit_text("❌ AI is unavailable right now. Try again later.")
            else:
                await thinking_msg.edit_text(f"🤖 <b>{BOT_NAME}</b>\n\n{answer}", parse_mode="HTML")
        except Exception:
            if answer:
                await message.reply(f"🤖 <b>{BOT_NAME}</b>\n\n{answer}", parse_mode="HTML")
            else:
                await message.reply("❌ AI is unavailable right now. Try again later.")

    @dp.message(Command("askreset"))
    async def cmd_askreset(message: Message):
        if await check_banned(message): return
        uid = message.from_user.id
        ask_history.pop(uid, None)
        await message.reply(f"🔄 Conversation with <b>{BOT_NAME}</b> cleared!", parse_mode="HTML")

    @dp.message(Command("toggleai"))
    async def cmd_toggleai(message: Message):
        if not is_admin(message.from_user.id):
            return await message.reply("❌ Admin only.")
        global AI_MODERATION_ENABLED
        # We toggle the module-level variable imported from config
        import handlers.ai as _self
        _self.AI_MODERATION_ENABLED = not _self.AI_MODERATION_ENABLED
        status = "✅ ON" if _self.AI_MODERATION_ENABLED else "⏸ OFF"
        await message.reply(f"🤖 AI Moderation is now <b>{status}</b>", parse_mode="HTML")
