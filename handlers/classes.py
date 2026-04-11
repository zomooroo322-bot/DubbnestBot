import asyncio
import datetime
from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton

from config import ADMINS, GROUP_ID, PURCHASES_LOG_ID
from core.database import fetch_one, fetch_all, execute, log_points, get_user_by_tgid, upsert_user
from core.helpers import is_admin, user_link, check_banned

# Active class session tracker
_active_session: dict = {}   # {session_id: {bot, task}}

CLASS_POINTS_FULL    = 6   # all checks attended
CLASS_POINTS_PARTIAL = 4   # 50–99%
CLASS_POINTS_MIN     = 1   # attended but low

def attendance_keyboard(session_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(
            text="✋ Mark Attendance",
            callback_data=f"class_attend:{session_id}"
        )
    ]])

def register_class_handlers(dp: Dispatcher, bot: Bot):

    @dp.message(Command("classstart"))
    async def cmd_classstart(message: Message):
        if not is_admin(message.from_user.id): return
        if _active_session:
            return await message.reply("❌ A class is already active. Use /classend first.")
        args  = message.text.split(maxsplit=1)
        topic = args[1].strip() if len(args) > 1 else "General Session"
        now   = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
        await execute(
            "INSERT INTO class_sessions (started_by, topic, started_at, status) VALUES (?, ?, ?, 'active')",
            (message.from_user.id, topic, now)
        )
        session = await fetch_one(
            "SELECT id FROM class_sessions ORDER BY id DESC LIMIT 1"
        )
        session_id = session["id"]
        _active_session["id"]         = session_id
        _active_session["check_num"]  = 0
        _active_session["topic"]      = topic
        _active_session["started_by"] = message.from_user.id

        # Send first attendance button to group
        await bot.send_message(GROUP_ID,
            f"🎓 <b>CLASS STARTED!</b>\n\n"
            f"📚 Topic: <b>{topic}</b>\n"
            f"⏰ Started: <b>{now}</b>\n\n"
            f"Click below to mark your attendance!\n"
            f"<i>Attendance will be checked every 10 minutes.</i>",
            parse_mode="HTML",
            reply_markup=attendance_keyboard(session_id)
        )
        await message.reply(f"✅ Class started! Topic: <b>{topic}</b>\nID: #{session_id}", parse_mode="HTML")

        # Start 10-min check loop
        task = asyncio.create_task(_check_loop(bot, session_id, message.from_user.id))
        _active_session["task"] = task

    async def _check_loop(bot: Bot, session_id: int, admin_id: int):
        """Send attendance check every 10 minutes while class is active."""
        while _active_session.get("id") == session_id:
            await asyncio.sleep(600)
            if _active_session.get("id") != session_id:
                break
            _active_session["check_num"] = _active_session.get("check_num", 0) + 1
            check_num = _active_session["check_num"]
            await bot.send_message(GROUP_ID,
                f"⏰ <b>Attendance Check #{check_num}</b>\n\n"
                f"Still here? Click below to stay marked active!",
                parse_mode="HTML",
                reply_markup=attendance_keyboard(session_id)
            )

    @dp.callback_query(F.data.startswith("class_attend:"))
    async def cb_class_attend(callback: CallbackQuery):
        session_id = int(callback.data.split(":")[1])
        if _active_session.get("id") != session_id:
            return await callback.answer("⏰ This attendance button has expired.", show_alert=True)
        uid = callback.from_user.id
        await upsert_user(callback.from_user)
        user = await get_user_by_tgid(uid)
        if not user:
            return await callback.answer("❌ Please use /start first.", show_alert=True)
        # Check if already marked for this check
        existing = await fetch_one(
            "SELECT id, checks, messaged FROM class_attendance WHERE session_id = ? AND user_id = ?",
            (session_id, user["id"])
        )
        check_num = _active_session.get("check_num", 0)
        if existing:
            if existing["checks"] >= check_num + 1:
                return await callback.answer("✅ Already marked for this check!", show_alert=False)
            await execute(
                "UPDATE class_attendance SET checks = ?, messaged = messaged + ? WHERE id = ?",
                (check_num + 1, 1 if check_num > 0 else 0, existing["id"])
            )
        else:
            await execute(
                "INSERT INTO class_attendance (session_id, user_id, checks, messaged) VALUES (?, ?, 1, 0)",
                (session_id, user["id"])
            )
        await callback.answer("✅ Attendance marked!", show_alert=False)

    @dp.message(Command("classend"))
    async def cmd_classend(message: Message):
        if not is_admin(message.from_user.id): return
        if not _active_session:
            return await message.reply("❌ No active class session.")
        session_id = _active_session["id"]
        topic      = _active_session.get("topic", "Class")
        total_checks = _active_session.get("check_num", 0) + 1  # +1 for initial

        # Cancel loop
        task = _active_session.get("task")
        if task:
            task.cancel()
        _active_session.clear()

        # Mark session ended
        now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
        await execute(
            "UPDATE class_sessions SET ended_at = ?, status = 'ended' WHERE id = ?",
            (now, session_id)
        )

        # Send final attendance button
        await bot.send_message(GROUP_ID,
            f"🏁 <b>CLASS ENDED!</b>\n\n"
            f"📚 Topic: <b>{topic}</b>\n\n"
            f"Last chance to mark attendance! (expires in 10 min)",
            parse_mode="HTML",
            reply_markup=attendance_keyboard(session_id)
        )

        # Give 10 min for final marks then calculate points
        await asyncio.sleep(600)
        await _award_class_points(bot, session_id, total_checks, topic, message.from_user.id)
        await message.reply(f"✅ Class ended. Points are being awarded!", parse_mode="HTML")

    async def _award_class_points(bot, session_id, total_checks, topic, admin_id):
        attendees = await fetch_all(
            "SELECT ca.user_id, ca.checks, ca.messaged, "
            "u.telegram_id, u.first_name, u.username "
            "FROM class_attendance ca JOIN users u ON ca.user_id = u.id "
            "WHERE ca.session_id = ? AND ca.points_given = 0",
            (session_id,)
        )
        if not attendees:
            return

        summary_lines = [f"🎓 <b>Class Points Awarded — {topic}</b>\n"]
        for att in attendees:
            ratio = att["checks"] / max(total_checks, 1)
            if ratio >= 0.70:
                pts = CLASS_POINTS_FULL
            elif ratio >= 0.50:
                pts = CLASS_POINTS_PARTIAL
            else:
                pts = CLASS_POINTS_MIN

            # Give to both artist_points and remaining_points
            await execute(
                "UPDATE users SET artist_points = artist_points + ?, "
                "remaining_points = remaining_points + ?, "
                "total_points = total_points + ? WHERE id = ?",
                (pts, pts, pts, att["user_id"])
            )
            await log_points(att["user_id"], pts, f"🎓 Class attendance: {topic}")
            await execute(
                "UPDATE class_attendance SET points_given = ? WHERE session_id = ? AND user_id = ?",
                (pts, session_id, att["user_id"])
            )
            link = user_link(att["first_name"], att["telegram_id"], att["username"])
            summary_lines.append(f"✅ {link} — +{pts} pts ({att['checks']}/{total_checks} checks)")
            try:
                await bot.send_message(att["telegram_id"],
                    f"🎓 <b>Class Points Awarded!</b>\n\n"
                    f"📚 Topic: <b>{topic}</b>\n"
                    f"✅ Checks: <b>{att['checks']}/{total_checks}</b>\n"
                    f"🎨 Points: <b>+{pts}</b> (artist + remaining)",
                    parse_mode="HTML"
                )
            except Exception:
                pass

        await bot.send_message(PURCHASES_LOG_ID,
            "\n".join(summary_lines), parse_mode="HTML"
        )
