import asyncio
import datetime
from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton

from config import ADMINS, GROUP_ID, PURCHASES_LOG_ID
from core.database import (
    fetch_one, fetch_all, execute,
    upsert_user, get_user_by_tgid, get_user_by_username,
    log_points,
)
from core.helpers import user_link, is_admin, check_banned, parse_args, strip_at

# ── In-memory pbounty form sessions ──────────────────────────────────────
pbounty_sessions: dict[int, dict] = {}

PBOUNTY_STEPS = ["voice_gender", "voice_type", "emotion", "length", "reward", "deadline_days"]

PBOUNTY_QUESTIONS = {
    "voice_gender":  "🎙 <b>Step 1/6 — Voice Gender</b>\n\nWhat voice gender do you need?\nReply with: <code>Male</code>, <code>Female</code>, or <code>Any</code>",
    "voice_type":    "🎭 <b>Step 2/6 — Voice Type</b>\n\nWhat type of voice? (e.g. deep, soft, narrator, anime, heroic...)\nType your answer:",
    "emotion":       "😤 <b>Step 3/6 — Emotion</b>\n\nWhat emotion should the voice convey? (e.g. angry, sad, happy, calm...)\nType your answer:",
    "length":        "⏱ <b>Step 4/6 — Video Length</b>\n\nHow long is the clip? (e.g. 30 seconds, 1 minute...)\nType your answer:",
    "reward":        "💰 <b>Step 5/6 — Reward Points</b>\n\nHow many points will you offer as reward?\nType a number (you must have enough points):",
    "deadline_days": "📅 <b>Step 6/6 — Deadline</b>\n\nHow many days will you give for the job? (1–30)\nType a number:",
}

def pbounty_preview(data: dict) -> str:
    return (
        f"📋 <b>PUBLIC BOUNTY PREVIEW</b>\n\n"
        f"🎙 Voice Gender: <b>{data['voice_gender']}</b>\n"
        f"🎭 Voice Type: <b>{data['voice_type']}</b>\n"
        f"😤 Emotion: <b>{data['emotion']}</b>\n"
        f"⏱ Length: <b>{data['length']}</b>\n"
        f"💰 Reward: <b>{data['reward']} pts</b>\n"
        f"📅 Deadline: <b>{data['deadline_days']} day(s)</b>\n\nIs this correct?"
    )

def pbounty_public_text(bounty_id: int, requester_name: str, requester_id: int, data: dict) -> str:
    link = user_link(requester_name, requester_id)
    return (
        f"🎯 <b>NEW PUBLIC BOUNTY #{bounty_id}</b>\n\n"
        f"👤 Requester: {link}\n\n"
        f"🎙 Voice Gender: <b>{data['voice_gender']}</b>\n"
        f"🎭 Voice Type: <b>{data['voice_type']}</b>\n"
        f"😤 Emotion: <b>{data['emotion']}</b>\n"
        f"⏱ Length: <b>{data['length']}</b>\n\n"
        f"💰 Reward: <b>{data['reward']} pts</b>\n"
        f"📅 Deadline: <b>{data['deadline_days']} day(s)</b>"
    )

def bounty_accept_keyboard(bounty_id: int, performer_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Accept",  callback_data=f"bounty_accept:{bounty_id}:{performer_id}"),
        InlineKeyboardButton(text="❌ Decline", callback_data=f"bounty_decline:{bounty_id}:{performer_id}"),
    ]])

def pbounty_confirm_keyboard(session_key: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Confirm", callback_data=f"pb_confirm:{session_key}"),
        InlineKeyboardButton(text="❌ Cancel",  callback_data=f"pb_cancel:{session_key}"),
    ]])

def pbounty_apply_keyboard(bounty_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="🎤 Send Voice Sample", callback_data=f"pb_apply:{bounty_id}"),
    ]])

def pbounty_sample_keyboard(bounty_id: int, applicant_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Accept", callback_data=f"pb_accept:{bounty_id}:{applicant_id}"),
        InlineKeyboardButton(text="❌ Ignore", callback_data=f"pb_ignore:{bounty_id}:{applicant_id}"),
    ]])

# ═════════════════════════════════════════════════════════════════════════
def register_bounty_handlers(dp: Dispatcher, bot: Bot):

    # ── Private bounty ────────────────────────────────────────────────────
    @dp.message(Command("bounty"))
    async def cmd_bounty(message: Message):
        if await check_banned(message): return
        await upsert_user(message.from_user)
        requester = await get_user_by_tgid(message.from_user.id)
        args = message.text.split(maxsplit=2)

        if message.reply_to_message and len(args) == 2:
            try:
                amount = int(args[1])
                if amount <= 0: raise ValueError
            except ValueError:
                return await message.reply("❌ Amount must be a positive integer.")
            performer = await get_user_by_tgid(message.reply_to_message.from_user.id)
            if not performer:
                return await message.reply("❌ That user is not registered.")
            if message.reply_to_message.from_user.id == message.from_user.id:
                return await message.reply("❌ You can't bounty yourself.")
        elif len(args) == 3:
            try:
                amount = int(args[2])
                if amount <= 0: raise ValueError
            except ValueError:
                return await message.reply("❌ Amount must be a positive integer.")
            if strip_at(args[1]) == message.from_user.username:
                return await message.reply("❌ You can't bounty yourself.")
            performer = await get_user_by_username(strip_at(args[1]))
            if not performer:
                return await message.reply("❌ User not found.")
        else:
            return await message.reply(
                "Usage: /bounty @username &lt;amount&gt;  or reply to user with /bounty &lt;amount&gt;",
                parse_mode="HTML"
            )

        if not is_admin(message.from_user.id) and requester["remaining_points"] < amount:
            return await message.reply(
                f"❌ Not enough points. You have <b>{requester['remaining_points']}</b>, need <b>{amount}</b>.",
                parse_mode="HTML"
            )

        await execute(
            "INSERT INTO bounties (requester_id, performer_id, amount, status) VALUES (?, ?, ?, 'pending')",
            (requester["id"], performer["id"], amount)
        )
        bounty = await fetch_one(
            "SELECT id FROM bounties WHERE requester_id=? AND performer_id=? ORDER BY id DESC LIMIT 1",
            (requester["id"], performer["id"])
        )
        bounty_id = bounty["id"]
        req_link  = user_link(message.from_user.first_name or "User", message.from_user.id)
        perf_link = user_link(performer["first_name"], performer["telegram_id"], performer["username"])

        try:
            await bot.send_message(performer["telegram_id"],
                f"🎯 <b>Bounty Request!</b>\n\n"
                f"👤 {req_link} wants to hire you!\n"
                f"💰 Reward: <b>{amount} pts</b>\n"
                f"🆔 Bounty ID: <b>#{bounty_id}</b>\n\n"
                f"Do you want to accept this bounty?",
                parse_mode="HTML",
                reply_markup=bounty_accept_keyboard(bounty_id, performer["id"])
            )
        except Exception:
            pass

        await message.reply(
            f"🎯 <b>Bounty Posted!</b>\n\nTarget: {perf_link}\n"
            f"💰 Reward: <b>{amount} pts</b>\n🆔 Bounty ID: <b>#{bounty_id}</b>\n\nWaiting for them to accept...",
            parse_mode="HTML"
        )

    @dp.callback_query(F.data.startswith("bounty_accept:"))
    async def cb_bounty_accept(callback: CallbackQuery):
        _, bounty_id_str, performer_id_str = callback.data.split(":")
        bounty_id    = int(bounty_id_str)
        performer_id = int(performer_id_str)
        user = await get_user_by_tgid(callback.from_user.id)
        if not user or user["id"] != performer_id:
            return await callback.answer("❌ This button is not for you.", show_alert=True)
        bounty = await fetch_one(
            "SELECT * FROM bounties WHERE id = ? AND status = 'pending'", (bounty_id,)
        )
        if not bounty:
            return await callback.answer("❌ Bounty not found or already handled.", show_alert=True)
        await execute("UPDATE bounties SET status = 'accepted' WHERE id = ?", (bounty_id,))
        requester = await fetch_one("SELECT * FROM users WHERE id = ?", (bounty["requester_id"],))
        perf_link = user_link(callback.from_user.first_name or "User", callback.from_user.id)
        req_link  = user_link(requester["first_name"], requester["telegram_id"], requester["username"])
        admin_tags = " ".join(f'<a href="tg://user?id={aid}">Admin</a>' for aid in ADMINS)
        await bot.send_message(GROUP_ID,
            f"🎯 <b>Bounty Accepted!</b>\n\n"
            f"📋 Requester: {req_link}\n🎤 Performer: {perf_link}\n"
            f"💰 Amount: <b>{bounty['amount']} pts</b>\n🆔 Bounty ID: <b>#{bounty_id}</b>\n\n"
            f"👑 {admin_tags} — when done: <code>/bounty_success {bounty_id}</code>",
            parse_mode="HTML"
        )
        await callback.message.edit_text(
            f"✅ <b>Bounty #{bounty_id} Accepted!</b>\n\n"
            f"💰 Reward: <b>{bounty['amount']} pts</b>\n"
            f"Complete the task and an admin will confirm with /bounty_success {bounty_id}",
            parse_mode="HTML"
        )
        await callback.answer("✅ Bounty accepted!")
        try:
            await bot.send_message(requester["telegram_id"],
                f"✅ <b>Your bounty was accepted!</b>\n\n"
                f"🎤 {perf_link} accepted bounty <b>#{bounty_id}</b>.\nAn admin will confirm completion.",
                parse_mode="HTML"
            )
        except Exception:
            pass

    @dp.callback_query(F.data.startswith("bounty_decline:"))
    async def cb_bounty_decline(callback: CallbackQuery):
        _, bounty_id_str, performer_id_str = callback.data.split(":")
        bounty_id    = int(bounty_id_str)
        performer_id = int(performer_id_str)
        user = await get_user_by_tgid(callback.from_user.id)
        if not user or user["id"] != performer_id:
            return await callback.answer("❌ This button is not for you.", show_alert=True)
        bounty = await fetch_one(
            "SELECT * FROM bounties WHERE id = ? AND status = 'pending'", (bounty_id,)
        )
        if not bounty:
            return await callback.answer("❌ Bounty not found or already handled.", show_alert=True)
        await execute("UPDATE bounties SET status = 'declined' WHERE id = ?", (bounty_id,))
        requester = await fetch_one("SELECT * FROM users WHERE id = ?", (bounty["requester_id"],))
        perf_link = user_link(callback.from_user.first_name or "User", callback.from_user.id)
        await callback.message.edit_text(f"❌ <b>Bounty #{bounty_id} Declined.</b>", parse_mode="HTML")
        await callback.answer("Bounty declined.")
        try:
            await bot.send_message(requester["telegram_id"],
                f"❌ <b>Bounty #{bounty_id} was declined.</b>\n\n"
                f"{perf_link} declined your bounty request.\nYour points were not deducted.",
                parse_mode="HTML"
            )
        except Exception:
            pass

    # ── Public bounty ─────────────────────────────────────────────────────
    @dp.message(Command("pbounty"))
    async def cmd_pbounty(message: Message):
        if await check_banned(message): return
        if not message.reply_to_message:
            return await message.reply(
                "Reply to the <b>video clip</b> you need dubbed, then use /pbounty.", parse_mode="HTML"
            )
        rep = message.reply_to_message
        if rep.video:
            file_id, file_type = rep.video.file_id, "video"
        elif rep.audio:
            file_id, file_type = rep.audio.file_id, "audio"
        else:
            return await message.reply("❌ The replied message must contain a video or audio file.")
        await upsert_user(message.from_user)
        user = await get_user_by_tgid(message.from_user.id)
        pbounty_sessions[message.from_user.id] = {
            "step":      PBOUNTY_STEPS[0],
            "data":      {},
            "file_id":   file_id,
            "file_type": file_type,
            "user_id":   user["id"],
        }
        try:
            await bot.send_message(message.from_user.id,
                "🎯 <b>Public Bounty Form</b>\n\n"
                "I'll ask you 6 quick questions.\n\n"
                + PBOUNTY_QUESTIONS["voice_gender"],
                parse_mode="HTML"
            )
            await message.reply("📩 Check your DMs — I've started the bounty form!")
        except Exception:
            del pbounty_sessions[message.from_user.id]
            await message.reply("❌ I couldn't DM you. Please start a private chat with the bot first.")

    @dp.callback_query(F.data.startswith("pb_confirm:"))
    async def cb_pbounty_confirm(callback: CallbackQuery):
        uid = int(callback.data.split(":")[1])
        if callback.from_user.id != uid:
            return await callback.answer("❌ Not your form.", show_alert=True)
        session = pbounty_sessions.get(uid)
        if not session or session.get("step") != "awaiting_confirm":
            return await callback.answer("❌ Session expired. Use /pbounty again.", show_alert=True)
        data          = session["data"]
        user          = await get_user_by_tgid(uid)
        reward        = data["reward"]
        deadline_days = data["deadline_days"]
        if not is_admin(uid):
            if user["remaining_points"] < reward:
                await callback.message.edit_text("❌ Not enough points anymore. Bounty cancelled.")
                del pbounty_sessions[uid]
                return await callback.answer()
            await execute(
                "UPDATE users SET remaining_points = remaining_points - ? WHERE id = ?",
                (reward, user["id"])
            )
        deadline_at     = (datetime.datetime.now() + datetime.timedelta(days=deadline_days)).isoformat()
        open_expires_at = (datetime.datetime.now() + datetime.timedelta(hours=24)).isoformat()
        created_at      = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
        await execute(
            """INSERT INTO pbounties
               (requester_id, file_id, file_type, voice_gender, voice_type, emotion,
                length, reward, deadline_days, deadline_at, open_expires_at, status, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'open', ?)""",
            (user["id"], session["file_id"], session["file_type"],
             data["voice_gender"], data["voice_type"], data["emotion"],
             data["length"], reward, deadline_days, deadline_at, open_expires_at, created_at)
        )
        bounty    = await fetch_one(
            "SELECT id FROM pbounties WHERE requester_id = ? ORDER BY id DESC LIMIT 1", (user["id"],)
        )
        bounty_id = bounty["id"]
        del pbounty_sessions[uid]
        await callback.message.edit_text(
            f"✅ <b>Public Bounty #{bounty_id} is live!</b>\n\n"
            f"💰 <b>{reward} pts</b> reserved from your balance.\n"
            f"All users will be notified. Good luck!",
            parse_mode="HTML"
        )
        await callback.answer("Bounty posted!")
        pub_text  = pbounty_public_text(bounty_id, callback.from_user.first_name or "User", uid, data)
        keyboard  = pbounty_apply_keyboard(bounty_id)
        all_users = await fetch_all("SELECT telegram_id FROM users WHERE telegram_id != ?", (uid,))
        for row in all_users:
            try:
                await bot.send_message(row["telegram_id"], pub_text, parse_mode="HTML", reply_markup=keyboard)
                await asyncio.sleep(0.05)
            except Exception:
                pass

    @dp.callback_query(F.data.startswith("pb_cancel:"))
    async def cb_pbounty_cancel_form(callback: CallbackQuery):
        uid = int(callback.data.split(":")[1])
        if callback.from_user.id != uid:
            return await callback.answer("❌ Not your form.", show_alert=True)
        pbounty_sessions.pop(uid, None)
        await callback.message.edit_text("❌ Bounty cancelled.")
        await callback.answer()

    @dp.callback_query(F.data.startswith("pb_apply:"))
    async def cb_pbounty_apply(callback: CallbackQuery):
        bounty_id = int(callback.data.split(":")[1])
        uid       = callback.from_user.id
        bounty = await fetch_one(
            "SELECT * FROM pbounties WHERE id = ? AND status = 'open'", (bounty_id,)
        )
        if not bounty:
            return await callback.answer("❌ This bounty is no longer accepting applications.", show_alert=True)
        requester = await fetch_one("SELECT * FROM users WHERE id = ?", (bounty["requester_id"],))
        if requester["telegram_id"] == uid:
            return await callback.answer("❌ You can't apply to your own bounty.", show_alert=True)
        applicant = await get_user_by_tgid(uid)
        if not applicant:
            return await callback.answer("❌ You are not registered. Use /start first.", show_alert=True)
        await callback.answer()
        pbounty_sessions[uid] = {
            "step":      "awaiting_voice_sample",
            "bounty_id": bounty_id,
            "user_id":   applicant["id"],
        }
        try:
            await bot.send_message(uid,
                f"🎤 <b>Apply for Bounty #{bounty_id}</b>\n\n"
                f"Send a <b>voice message</b> that matches the style below:\n\n"
                f"🎙 Gender: <b>{bounty['voice_gender']}</b>\n"
                f"🎭 Type: <b>{bounty['voice_type']}</b>\n"
                f"😤 Emotion: <b>{bounty['emotion']}</b>\n\n"
                f"Record and send your sample now!",
                parse_mode="HTML"
            )
        except Exception:
            pbounty_sessions.pop(uid, None)

    @dp.callback_query(F.data.startswith("pb_accept:"))
    async def cb_pbounty_accept_performer(callback: CallbackQuery):
        _, bounty_id_str, applicant_id_str = callback.data.split(":")
        bounty_id    = int(bounty_id_str)
        applicant_id = int(applicant_id_str)
        bounty = await fetch_one(
            "SELECT * FROM pbounties WHERE id = ? AND status = 'open'", (bounty_id,)
        )
        if not bounty:
            return await callback.answer("❌ Bounty no longer open.", show_alert=True)
        requester = await fetch_one("SELECT * FROM users WHERE id = ?", (bounty["requester_id"],))
        if requester["telegram_id"] != callback.from_user.id:
            return await callback.answer("❌ Only the requester can accept.", show_alert=True)
        performer   = await fetch_one("SELECT * FROM users WHERE id = ?", (applicant_id,))
        deadline_at = (datetime.datetime.now() + datetime.timedelta(days=bounty["deadline_days"])).isoformat()
        await execute(
            "UPDATE pbounties SET status = 'assigned', performer_id = ?, deadline_at = ? WHERE id = ?",
            (applicant_id, deadline_at, bounty_id)
        )
        perf_link  = user_link(performer["first_name"], performer["telegram_id"], performer["username"])
        req_link   = user_link(callback.from_user.first_name or "User", callback.from_user.id)
        admin_tags = " ".join(f'<a href="tg://user?id={aid}">Admin</a>' for aid in ADMINS)
        await callback.message.edit_caption(
            caption=f"✅ <b>Accepted!</b> Bounty #{bounty_id} assigned to {perf_link}.\nWaiting for admin to confirm.",
            parse_mode="HTML"
        )
        await callback.answer("Performer accepted!")
        try:
            send_fn = bot.send_video if bounty["file_type"] == "video" else bot.send_audio
            await send_fn(performer["telegram_id"], bounty["file_id"],
                caption=(
                    f"🎬 <b>Bounty Assigned! #{bounty_id}</b>\n\n"
                    f"💰 Reward: <b>{bounty['reward']} pts</b>\n"
                    f"📅 Deadline: <b>{bounty['deadline_days']} day(s)</b>\n\n"
                    f"Submit your finished dub to: {admin_tags}"
                ),
                parse_mode="HTML"
            )
        except Exception:
            pass
        await bot.send_message(GROUP_ID,
            f"🎯 <b>Public Bounty #{bounty_id} Assigned!</b>\n\n"
            f"👤 Requester: {req_link}\n🎤 Performer: {perf_link}\n"
            f"💰 Reward: <b>{bounty['reward']} pts</b>\n\n"
            f"{admin_tags} — confirm with <code>/pbounty_success {bounty_id}</code> when done.",
            parse_mode="HTML"
        )

    @dp.callback_query(F.data.startswith("pb_ignore:"))
    async def cb_pbounty_ignore_performer(callback: CallbackQuery):
        _, bounty_id_str, applicant_id_str = callback.data.split(":")
        bounty_id    = int(bounty_id_str)
        applicant_id = int(applicant_id_str)
        bounty = await fetch_one("SELECT * FROM pbounties WHERE id = ?", (bounty_id,))
        if not bounty:
            return await callback.answer("❌ Bounty not found.", show_alert=True)
        requester = await fetch_one("SELECT * FROM users WHERE id = ?", (bounty["requester_id"],))
        if requester["telegram_id"] != callback.from_user.id:
            return await callback.answer("❌ Only the requester can ignore.", show_alert=True)
        performer = await fetch_one("SELECT * FROM users WHERE id = ?", (applicant_id,))
        await callback.message.edit_caption(caption="❌ Sample ignored.", parse_mode="HTML")
        await callback.answer("Ignored.")
        try:
            await bot.send_message(performer["telegram_id"],
                f"😔 The requester passed on your sample for Bounty #{bounty_id}.\nBetter luck next time!"
            )
        except Exception:
            pass

    # ── DM voice sample catch ─────────────────────────────────────────────
    @dp.message(F.chat.type == "private", F.voice)
    async def pbounty_voice_sample_handler(message: Message):
        uid     = message.from_user.id
        session = pbounty_sessions.get(uid)
        if not session or session.get("step") != "awaiting_voice_sample":
            return
        bounty_id = session["bounty_id"]
        bounty    = await fetch_one(
            "SELECT * FROM pbounties WHERE id = ? AND status = 'open'", (bounty_id,)
        )
        if not bounty:
            del pbounty_sessions[uid]
            return await message.answer("❌ That bounty is no longer open.")
        del pbounty_sessions[uid]
        requester      = await fetch_one("SELECT * FROM users WHERE id = ?", (bounty["requester_id"],))
        applicant_link = user_link(message.from_user.first_name or "User", uid)
        try:
            await bot.send_voice(requester["telegram_id"], message.voice.file_id,
                caption=(
                    f"🎙 <b>Bounty Sample — #{bounty_id}</b>\n\n"
                    f"From: {applicant_link}\n\nAccept this voice for your bounty?"
                ),
                parse_mode="HTML",
                reply_markup=pbounty_sample_keyboard(bounty_id, session["user_id"])
            )
            await message.answer(f"✅ Your sample was sent for Bounty #{bounty_id}!\nWait for their decision.")
        except Exception:
            await message.answer("❌ Couldn't reach the requester. They may have blocked the bot.")

    # ── DM form step handler (MUST be last in private chat) ──────────────
    @dp.message(F.chat.type == "private")
    async def pbounty_form_handler(message: Message):
        if message.text and message.text.startswith("/"):
            return
        uid = message.from_user.id
        if uid not in pbounty_sessions:
            return
        session = pbounty_sessions[uid]
        step    = session["step"]
        if step in ("awaiting_confirm", "awaiting_voice_sample"):
            return
        text = (message.text or "").strip()
        if not text:
            await message.answer("Please send a text reply.")
            return

        if step == "voice_gender":
            if text.lower() not in ("male", "female", "any"):
                await message.answer("❌ Reply with: <code>Male</code>, <code>Female</code>, or <code>Any</code>", parse_mode="HTML")
                return
            session["data"]["voice_gender"] = text.capitalize()
        elif step == "voice_type":
            session["data"]["voice_type"] = text
        elif step == "emotion":
            session["data"]["emotion"] = text
        elif step == "length":
            session["data"]["length"] = text
        elif step == "reward":
            try:
                reward = int(text)
                if reward <= 0: raise ValueError
            except ValueError:
                await message.answer("❌ Please enter a positive number.")
                return
            user = await get_user_by_tgid(uid)
            if not is_admin(uid) and user["remaining_points"] < reward:
                await message.answer(
                    f"❌ Not enough points. You have <b>{user['remaining_points']}</b>, need <b>{reward}</b>.",
                    parse_mode="HTML"
                )
                return
            session["data"]["reward"] = reward
        elif step == "deadline_days":
            try:
                days = int(text)
                if not 1 <= days <= 30: raise ValueError
            except ValueError:
                await message.answer("❌ Please enter a number between 1 and 30.")
                return
            session["data"]["deadline_days"] = days
            await message.answer(
                pbounty_preview(session["data"]),
                parse_mode="HTML",
                reply_markup=pbounty_confirm_keyboard(uid)
            )
            session["step"] = "awaiting_confirm"
            return

        current_idx     = PBOUNTY_STEPS.index(step)
        next_step       = PBOUNTY_STEPS[current_idx + 1]
        session["step"] = next_step
        await message.answer(PBOUNTY_QUESTIONS[next_step], parse_mode="HTML")
