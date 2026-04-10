import asyncio
import datetime
from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton

from config import ADMINS, GROUP_ID, PURCHASES_LOG_ID, RATINGS, STARTER_POINTS, PRICE_MANAGER, STORE, ITEM_EMOJI, REVIEWER_IDS
from core.database import (
    fetch_one, fetch_all, execute,
    upsert_user, get_user_by_tgid, get_user_by_username,
    log_points, get_fund_balance,
)
from core.helpers import (
    user_link, calculate_rank, parse_args, strip_at,
    is_admin, resolve_user, parse_duration, _fmt_duration,
)

# In-memory AI warning counts — shared with handlers/ai.py via import
# We import it there; admins use /resetwarning which calls ai.py's dict
def register_admin_handlers(dp: Dispatcher, bot: Bot):

    @dp.message(Command("speciality"))
    async def cmd_speciality(message: Message):
        if not is_admin(message.from_user.id): return
        args = message.text.split(maxsplit=2)
        user, extra_args = await resolve_user(message, args)
        if not user:
            return await message.reply("❌ User not found. They must have used /start first.")
        speciality = " ".join(extra_args).strip() if extra_args else None
        if not speciality:
            return await message.reply(
                "Usage: /speciality @username &lt;speciality&gt;  or reply to user with /speciality &lt;speciality&gt;",
                parse_mode="HTML"
            )
        await execute("UPDATE users SET speciality = ? WHERE id = ?", (speciality, user["id"]))
        try:
            await bot.send_message(user["telegram_id"],
                f"🎤 Your speciality has been set to: <b>{speciality}</b>", parse_mode="HTML"
            )
        except Exception:
            pass
        await message.reply(
            f"✅ Set speciality of {user_link(user['first_name'], user['telegram_id'], user['username'])} to <b>{speciality}</b>.",
            parse_mode="HTML"
        )

    @dp.message(Command("givework"))
    async def cmd_givework(message: Message):
        if not is_admin(message.from_user.id): return
        if not message.reply_to_message:
            return await message.reply("Reply to a video/audio file and use /givework @user 3d  or  /givework 3d")
        rep  = message.reply_to_message
        args = message.text.split()
        if rep.video or rep.audio:
            file_id   = rep.video.file_id if rep.video else rep.audio.file_id
            file_type = "video" if rep.video else "audio"
            if len(args) < 2:
                return await message.reply("Usage: /givework @user 3d  or  /givework 3d  (reply to file)", parse_mode="HTML")
            duration = parse_duration(args[-1])
            if not duration:
                return await message.reply("❌ Invalid duration. Examples: <code>3d</code>, <code>6h</code>, <code>2d12h</code>", parse_mode="HTML")
            if len(args) == 2:
                user = await get_user_by_tgid(rep.from_user.id)
                if not user:
                    return await message.reply("❌ That user is not registered.")
            else:
                user, _ = await resolve_user(message, args, arg_index=1)
                if not user:
                    return await message.reply("❌ User not found. They must have used /start first.")
        else:
            return await message.reply("Reply to a video or audio file.")
        deadline     = (datetime.datetime.now() + duration).isoformat()
        duration_str = _fmt_duration(duration)
        max_days     = max(1, (duration.days + (1 if duration.seconds > 0 else 0)))
        await execute("DELETE FROM works WHERE user_id = ?", (user["id"],))
        await execute(
            "INSERT INTO works (user_id, file_id, file_type, deadline, max_days, penalty_days) VALUES (?, ?, ?, ?, ?, ?)",
            (user["id"], file_id, file_type, deadline, max(max_days, 10), 0)
        )
        deadline_fmt = datetime.datetime.fromisoformat(deadline).strftime("%Y-%m-%d %H:%M")
        try:
            await bot.send_message(user["telegram_id"],
                f"🎬 You got new work!\n⏳ Deadline: <b>{deadline_fmt}</b> ({duration_str})\nUse /mywork to see it.",
                parse_mode="HTML"
            )
        except Exception:
            pass
        await message.reply(
            f"✅ Work assigned to {user_link(user['first_name'], user['telegram_id'], user['username'])}\n"
            f"⏳ Deadline: <b>{deadline_fmt}</b> ({duration_str})",
            parse_mode="HTML"
        )

    @dp.message(Command("removework"))
    async def cmd_removework(message: Message):
        if not is_admin(message.from_user.id): return
        args = message.text.split()
        user, _ = await resolve_user(message, args)
        if not user:
            return await message.reply("❌ User not found. They must have used /start first.")
        await execute("DELETE FROM works WHERE user_id = ?", (user["id"],))
        try:
            await bot.send_message(user["telegram_id"], "🗑 Your work assignment has been removed.")
        except Exception:
            pass
        await message.reply(
            f"✅ Work removed for {user_link(user['first_name'], user['telegram_id'], user['username'])}.",
            parse_mode="HTML"
        )

    # ── Helper: finalize a review (award pts, remove work, log) ─────────────
    async def _finalize_review(user_id: int, tg_id: int, rating: str, pts: int,
                               submitted_on_time: bool, reviewer_link: str):
        """Award points and close out the work. Submit bonus only if on time AND above poor."""
        bonus = 5 if submitted_on_time and pts > 0 else 0
        total = pts + bonus
        if total > 0:
            await execute(
                "UPDATE users SET total_points = total_points + ?, remaining_points = remaining_points + ?, "
                "projects = projects + 1 WHERE id = ?",
                (total, total, user_id)
            )
        else:
            await execute("UPDATE users SET projects = projects + 1 WHERE id = ?", (user_id,))
        await execute("DELETE FROM works WHERE user_id = ?", (user_id,))
        reason = f"📋 Review: {rating}" + (f" +{bonus} submit bonus" if bonus else "")
        if total > 0:
            await log_points(user_id, total, reason)
        reviewed_link = await _user_link_from_id(user_id)
        bonus_note = f" (+{bonus} submit bonus)" if bonus else ""
        try:
            await bot.send_message(tg_id,
                f"📋 <b>Work Reviewed: {rating.capitalize()}</b>\n"
                f"{'🎉 <b>+' + str(total) + ' pts</b> awarded!' if total > 0 else '❌ No points awarded (poor rating).'}"
                + (f"\n⚡ Includes +{bonus} pts on-time submit bonus!" if bonus else ""),
                parse_mode="HTML"
            )
        except Exception:
            pass
        await bot.send_message(PURCHASES_LOG_ID,
            f"📋 <b>Work Reviewed</b>\n"
            f"👤 {reviewed_link}\n"
            f"⭐ Rating: <b>{rating.capitalize()}</b> → <b>+{total} pts</b>{bonus_note}\n"
            f"👑 Reviewed by: {reviewer_link}\n"
            f"⏰ {datetime.datetime.now().strftime('%Y-%m-%d %H:%M')}",
            parse_mode="HTML"
        )

    async def _user_link_from_id(user_id: int) -> str:
        u = await fetch_one("SELECT first_name, telegram_id, username FROM users WHERE id = ?", (user_id,))
        if u:
            return user_link(u["first_name"], u["telegram_id"], u["username"])
        return "Unknown"

    @dp.message(Command("review"))
    async def cmd_review(message: Message):
        if message.from_user.id not in REVIEWER_IDS:
            return await message.reply("❌ Only designated reviewers can use this command.")
        args = message.text.split()
        user, extra_args = await resolve_user(message, args)
        if not user:
            return await message.reply("❌ User not found. They must have used /start first.")
        rating = extra_args[0].lower() if extra_args else None
        if not rating:
            return await message.reply(
                f"Usage: /review @username &lt;rating&gt;  or reply to user with /review &lt;rating&gt;\n"
                f"Ratings: {', '.join(RATINGS)}",
                parse_mode="HTML"
            )
        if rating not in RATINGS:
            return await message.reply(f"❌ Invalid rating. Choose: {', '.join(RATINGS)}")

        work = await fetch_one("SELECT * FROM works WHERE user_id = ?", (user["id"],))
        if not work:
            return await message.reply(
                f"❌ {user_link(user['first_name'], user['telegram_id'], user['username'])} has no active work assigned.",
                parse_mode="HTML"
            )
        if not work["submitted"]:
            deadline = datetime.datetime.fromisoformat(work["deadline"])
            return await message.reply(
                f"⏳ <b>Not submitted yet.</b>\n"
                f"{user_link(user['first_name'], user['telegram_id'], user['username'])} hasn't submitted their work.\n"
                f"Deadline: <b>{deadline.strftime('%Y-%m-%d %H:%M')}</b>",
                parse_mode="HTML"
            )
        pts  = RATINGS[rating]
        reviewed_link = user_link(user["first_name"], user["telegram_id"], user["username"])
        reviewer_link = user_link(message.from_user.first_name or "Admin", message.from_user.id)

        # ── Non-poor ratings: finalize immediately ──────────────────────────
        if rating != "poor":
            submitted_on_time = False
            if work:
                deadline = datetime.datetime.fromisoformat(work["deadline"])
                # on_time = submitted before or on deadline (submitted flag = 1 means they hit submit)
                submitted_on_time = work["submitted"] == 1 and datetime.datetime.now() <= deadline or \
                                    work["submitted"] == 1 and work["penalty_days"] == 0
            await _finalize_review(user["id"], user["telegram_id"], rating, pts, submitted_on_time, reviewer_link)
            return await message.reply(
                f"✅ Reviewed {reviewed_link} — <b>{rating}</b> (+{pts} pts)", parse_mode="HTML"
            )

        # ── Poor rating: ask reviewer if they want a redub ──────────────────
        keyboard = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(
                text="🔄 Yes, request redub",
                callback_data=f"redub_yes:{user['id']}:{user['telegram_id']}"
            ),
            InlineKeyboardButton(
                text="❌ No, reject",
                callback_data=f"redub_no:{user['id']}:{user['telegram_id']}"
            ),
        ]])
        await message.reply(
            f"⚠️ <b>Poor Rating</b> — {reviewed_link}\n\n"
            f"Do you want to give this user a chance to redub?\n\n"
            f"• <b>Yes</b> — user gets a redub deadline (24h if overdue, original if still valid)\n"
            f"• <b>No</b> — work rejected, 0 pts, work removed",
            parse_mode="HTML",
            reply_markup=keyboard
        )

    # ── Redub: YES ──────────────────────────────────────────────────────────
    @dp.callback_query(F.data.startswith("redub_yes:"))
    async def cb_redub_yes(callback: CallbackQuery):
        _, user_id_str, tg_id_str = callback.data.split(":")
        user_id = int(user_id_str)
        tg_id   = int(tg_id_str)

        if callback.from_user.id not in REVIEWER_IDS:
            return await callback.answer("❌ Not your button.", show_alert=True)

        work = await fetch_one("SELECT * FROM works WHERE user_id = ?", (user_id,))
        if not work:
            await callback.message.edit_text("❌ Work not found — it may have already been removed.")
            return await callback.answer()

        now      = datetime.datetime.now()
        deadline = datetime.datetime.fromisoformat(work["deadline"])

        if now <= deadline:
            # Still has time — keep original deadline, just unsubmit
            new_deadline = work["deadline"]
            deadline_fmt = deadline.strftime("%Y-%m-%d %H:%M")
            time_note    = f"original deadline: <b>{deadline_fmt}</b>"
        else:
            # Past deadline — give 24 hours from now
            new_dl       = now + datetime.timedelta(hours=24)
            new_deadline = new_dl.isoformat()
            deadline_fmt = new_dl.strftime("%Y-%m-%d %H:%M")
            time_note    = f"new 24h deadline: <b>{deadline_fmt}</b>"

        # Unsubmit the work so penalties can resume if they miss it
        await execute(
            "UPDATE works SET submitted = 0, deadline = ?, penalty_days = 0, last_penalty_at = NULL WHERE user_id = ?",
            (new_deadline, user_id)
        )

        reviewed_link = await _user_link_from_id(user_id)
        reviewer_link = user_link(callback.from_user.first_name or "Admin", callback.from_user.id)

        await callback.message.edit_text(
            f"🔄 <b>Redub requested</b> — {reviewed_link}\n{time_note}",
            parse_mode="HTML"
        )
        await callback.answer("Redub requested!")

        try:
            await bot.send_message(tg_id,
                f"🔄 <b>Redub Requested</b>\n\n"
                f"Your work was rated <b>Poor</b> and the reviewer wants a redo.\n"
                f"⏳ Your deadline: <b>{deadline_fmt}</b>\n\n"
                f"Submit your improved version with /submit before the deadline!\n"
                f"<i>Penalties apply if you miss it.</i>",
                parse_mode="HTML"
            )
        except Exception:
            pass

        await bot.send_message(PURCHASES_LOG_ID,
            f"🔄 <b>Redub Requested</b>\n"
            f"👤 {reviewed_link}\n"
            f"⏳ Deadline: <b>{deadline_fmt}</b>\n"
            f"👑 By: {reviewer_link}",
            parse_mode="HTML"
        )

    # ── Redub: NO (full rejection) ──────────────────────────────────────────
    @dp.callback_query(F.data.startswith("redub_no:"))
    async def cb_redub_no(callback: CallbackQuery):
        _, user_id_str, tg_id_str = callback.data.split(":")
        user_id = int(user_id_str)
        tg_id   = int(tg_id_str)

        if callback.from_user.id not in REVIEWER_IDS:
            return await callback.answer("❌ Not your button.", show_alert=True)

        await execute("DELETE FROM works WHERE user_id = ?", (user_id,))
        await execute("UPDATE users SET projects = projects + 1 WHERE id = ?", (user_id,))

        reviewed_link = await _user_link_from_id(user_id)
        reviewer_link = user_link(callback.from_user.first_name or "Admin", callback.from_user.id)

        await callback.message.edit_text(
            f"❌ <b>Rejected</b> — {reviewed_link}\n0 pts awarded. Work removed.",
            parse_mode="HTML"
        )
        await callback.answer("Rejected.")

        try:
            await bot.send_message(tg_id,
                "❌ <b>Work Rejected</b>\n\n"
                "Your submission was rated <b>Poor</b> and has been rejected.\n"
                "No points were awarded.\n\n"
                "<i>Keep practicing and come back stronger! 💪</i>",
                parse_mode="HTML"
            )
        except Exception:
            pass

        await bot.send_message(PURCHASES_LOG_ID,
            f"❌ <b>Work Rejected</b>\n"
            f"👤 {reviewed_link}\n"
            f"⭐ Rating: <b>Poor</b> → 0 pts\n"
            f"👑 By: {reviewer_link}\n"
            f"⏰ {datetime.datetime.now().strftime('%Y-%m-%d %H:%M')}",
            parse_mode="HTML"
        )

    @dp.message(Command("givepoints"))
    async def cmd_givepoints(message: Message):
        if not is_admin(message.from_user.id): return
        args = message.text.split()
        user, extra_args = await resolve_user(message, args)
        if not user:
            return await message.reply("❌ User not found. They must have used /start first.")
        if not extra_args:
            return await message.reply(
                "Usage: /givepoints @username &lt;amount&gt;  or reply to user with /givepoints &lt;amount&gt;",
                parse_mode="HTML"
            )
        try:
            amount = int(extra_args[0])
            if amount <= 0: raise ValueError
        except ValueError:
            return await message.reply("❌ Amount must be a positive integer.")
        await execute(
            "UPDATE users SET total_points = total_points + ?, remaining_points = remaining_points + ? WHERE id = ?",
            (amount, amount, user["id"])
        )
        await log_points(user["id"], amount, "🎁 Admin bonus points")
        try:
            await bot.send_message(user["telegram_id"],
                f"🎉 You received <b>{amount}</b> bonus points!", parse_mode="HTML"
            )
        except Exception:
            pass
        target_link = user_link(user["first_name"], user["telegram_id"], user["username"])
        admin_link  = user_link(message.from_user.first_name or "Admin", message.from_user.id)
        await message.reply(f"✅ Gave {amount} pts to {target_link}.", parse_mode="HTML")
        await bot.send_message(PURCHASES_LOG_ID,
            f"🎁 <b>Points Given</b>\n"
            f"👤 {target_link} → <b>+{amount} pts</b>\n"
            f"👑 By: {admin_link}\n"
            f"⏰ {datetime.datetime.now().strftime('%Y-%m-%d %H:%M')}",
            parse_mode="HTML"
        )

    @dp.message(Command("removepoints"))
    async def cmd_removepoints(message: Message):
        if not is_admin(message.from_user.id): return
        args = message.text.split()
        user, extra_args = await resolve_user(message, args)
        if not user:
            return await message.reply("❌ User not found. They must have used /start first.")
        if not extra_args:
            return await message.reply(
                "Usage: /removepoints @username &lt;amount&gt;  or reply to user with /removepoints &lt;amount&gt;",
                parse_mode="HTML"
            )
        try:
            amount = int(extra_args[0])
            if amount <= 0: raise ValueError
        except ValueError:
            return await message.reply("❌ Amount must be a positive integer.")
        await execute(
            "UPDATE users SET remaining_points = GREATEST(0, remaining_points - ?) WHERE id = ?",
            (amount, user["id"])
        )
        await log_points(user["id"], -amount, "👑 Admin removed points")
        target_link = user_link(user["first_name"], user["telegram_id"], user["username"])
        admin_link  = user_link(message.from_user.first_name or "Admin", message.from_user.id)
        await message.reply(f"✅ Removed {amount} pts from {target_link}.", parse_mode="HTML")
        await bot.send_message(PURCHASES_LOG_ID,
            f"🔻 <b>Points Removed</b>\n"
            f"👤 {target_link} → <b>-{amount} pts</b>\n"
            f"👑 By: {admin_link}\n"
            f"⏰ {datetime.datetime.now().strftime('%Y-%m-%d %H:%M')}",
            parse_mode="HTML"
        )

    @dp.message(Command("ban"))
    async def cmd_ban(message: Message):
        if not is_admin(message.from_user.id): return
        args = message.text.split()
        user, _ = await resolve_user(message, args)
        if not user:
            return await message.reply("❌ User not found. They must have used /start first.")
        if user["telegram_id"] in ADMINS:
            return await message.reply("❌ Cannot ban an admin.")
        if user["is_banned"]:
            return await message.reply("❌ User is already banned.")
        await execute("UPDATE users SET is_banned = 1 WHERE id = ?", (user["id"],))
        link       = user_link(user["first_name"], user["telegram_id"], user["username"])
        admin_link = user_link(message.from_user.first_name or "Admin", message.from_user.id)
        await message.reply(f"🚫 {link} has been banned.", parse_mode="HTML")
        try:
            await bot.send_message(user["telegram_id"],
                "🚫 <b>You have been banned from using this bot.</b>\nContact an admin if you think this is a mistake.",
                parse_mode="HTML"
            )
        except Exception:
            pass
        await bot.send_message(PURCHASES_LOG_ID,
            f"🚫 <b>User Banned</b>\n👤 {link}\n👑 By: {admin_link}\n"
            f"⏰ {datetime.datetime.now().strftime('%Y-%m-%d %H:%M')}",
            parse_mode="HTML"
        )

    @dp.message(Command("unban"))
    async def cmd_unban(message: Message):
        if not is_admin(message.from_user.id): return
        args = message.text.split()
        user, _ = await resolve_user(message, args)
        if not user:
            return await message.reply("❌ User not found. They must have used /start first.")
        if not user["is_banned"]:
            return await message.reply("❌ User is not banned.")
        await execute("UPDATE users SET is_banned = 0 WHERE id = ?", (user["id"],))
        link       = user_link(user["first_name"], user["telegram_id"], user["username"])
        admin_link = user_link(message.from_user.first_name or "Admin", message.from_user.id)
        await message.reply(f"✅ {link} has been unbanned.", parse_mode="HTML")
        try:
            await bot.send_message(user["telegram_id"],
                "✅ <b>Your ban has been lifted. Welcome back!</b>", parse_mode="HTML"
            )
        except Exception:
            pass
        await bot.send_message(PURCHASES_LOG_ID,
            f"✅ <b>User Unbanned</b>\n👤 {link}\n👑 By: {admin_link}\n"
            f"⏰ {datetime.datetime.now().strftime('%Y-%m-%d %H:%M')}",
            parse_mode="HTML"
        )

    @dp.message(Command("announce"))
    async def cmd_announce(message: Message):
        if not is_admin(message.from_user.id): return
        args = message.text.split(maxsplit=1)
        if len(args) < 2:
            return await message.reply("Usage: /announce &lt;message&gt;", parse_mode="HTML")
        text  = args[1].strip()
        users = await fetch_all("SELECT telegram_id FROM users WHERE is_banned = 0")
        sent  = 0; failed = 0
        await message.reply(f"📢 Sending to {len(users)} users...")
        for u in users:
            try:
                await bot.send_message(u["telegram_id"],
                    f"📢 <b>ANNOUNCEMENT</b>\n\n{text}", parse_mode="HTML"
                )
                sent += 1
                await asyncio.sleep(0.05)
            except Exception:
                failed += 1
        await message.reply(f"✅ Sent to <b>{sent}</b> users. Failed: <b>{failed}</b>.", parse_mode="HTML")

    @dp.message(Command("resetstreak"))
    async def cmd_resetstreak(message: Message):
        if not is_admin(message.from_user.id): return
        args = message.text.split()
        user, _ = await resolve_user(message, args)
        if not user:
            return await message.reply("❌ User not found.")
        await execute("UPDATE users SET checkin_streak = 0, last_checkin = NULL WHERE id = ?", (user["id"],))
        link = user_link(user["first_name"], user["telegram_id"], user["username"])
        await message.reply(f"✅ Reset check-in streak for {link}.", parse_mode="HTML")
        try:
            await bot.send_message(user["telegram_id"],
                "🔄 Your check-in streak has been reset by an admin.", parse_mode="HTML"
            )
        except Exception:
            pass

    @dp.message(Command("resetpoints"))
    async def cmd_resetpoints(message: Message):
        if not is_admin(message.from_user.id): return
        args = message.text.split()
        user, _ = await resolve_user(message, args)
        if not user:
            return await message.reply("❌ User not found.")
        await execute(
            "UPDATE users SET remaining_points = ? WHERE id = ?",
            (STARTER_POINTS, user["id"])
        )
        link = user_link(user["first_name"], user["telegram_id"], user["username"])
        await message.reply(f"✅ Reset wallet of {link} to <b>{STARTER_POINTS} pts</b>.", parse_mode="HTML")
        try:
            await bot.send_message(user["telegram_id"],
                f"🔄 Your wallet has been reset to <b>{STARTER_POINTS} pts</b> by an admin.", parse_mode="HTML"
            )
        except Exception:
            pass

    @dp.message(Command("resetwarning"))
    async def cmd_resetwarning(message: Message):
        if not is_admin(message.from_user.id): return
        args = message.text.split()
        user, _ = await resolve_user(message, args)
        if not user:
            return await message.reply("❌ User not found.")
        from handlers.ai import ai_warn_count
        uid       = user["telegram_id"]
        old_count = ai_warn_count.get(uid, 0)
        ai_warn_count[uid] = 0
        link = user_link(user["first_name"], uid, user["username"])
        await message.reply(f"✅ Cleared <b>{old_count}</b> AI warning(s) for {link}.", parse_mode="HTML")
        try:
            await bot.send_message(uid,
                "✅ <b>Your AI moderation warnings have been cleared by an admin.</b>\n"
                "You're starting fresh — keep it respectful!",
                parse_mode="HTML"
            )
        except Exception:
            pass

    @dp.message(Command("remind"))
    async def cmd_remind(message: Message):
        if not is_admin(message.from_user.id): return
        args = message.text.split(maxsplit=2)
        if message.reply_to_message and len(args) >= 1:
            user       = await get_user_by_tgid(message.reply_to_message.from_user.id)
            custom_msg = args[1].strip() if len(args) >= 2 else None
        elif len(args) >= 2:
            user       = await get_user_by_username(strip_at(args[1]))
            custom_msg = args[2].strip() if len(args) == 3 else None
        else:
            return await message.reply("Usage: /remind @username [message]  or reply + /remind [message]")
        if not user:
            return await message.reply("❌ User not found.")
        work = await fetch_one("SELECT deadline FROM works WHERE user_id = ?", (user["id"],))
        if not work and not custom_msg:
            return await message.reply("❌ That user has no active work and no custom message provided.")
        if work:
            deadline = datetime.datetime.fromisoformat(work["deadline"])
            diff     = deadline - datetime.datetime.now()
            if diff.total_seconds() < 0:
                time_str = f"⚠️ {abs(diff.days)}d OVERDUE"
            else:
                hours    = int(diff.total_seconds() // 3600)
                time_str = f"{diff.days}d {hours % 24}h remaining"
            dm_text = (
                f"⏰ <b>Deadline Reminder</b>\n\n"
                f"Your work deadline: <b>{deadline.strftime('%Y-%m-%d %H:%M')}</b>\n"
                f"⏳ {time_str}\n\nSubmit with /submit before the deadline!"
            )
            if custom_msg:
                dm_text += f"\n\n💬 Admin note: {custom_msg}"
        else:
            dm_text = f"📢 <b>Admin Reminder</b>\n\n{custom_msg}"
        try:
            await bot.send_message(user["telegram_id"], dm_text, parse_mode="HTML")
            link = user_link(user["first_name"], user["telegram_id"], user["username"])
            await message.reply(f"✅ Reminder sent to {link}.", parse_mode="HTML")
        except Exception:
            await message.reply("❌ Could not reach that user.")

    @dp.message(Command("report"))
    async def cmd_report(message: Message):
        if not is_admin(message.from_user.id): return
        total_users   = await fetch_one("SELECT COUNT(*) AS c FROM users")
        banned_users  = await fetch_one("SELECT COUNT(*) AS c FROM users WHERE is_banned = 1")
        vip_users     = await fetch_one("SELECT COUNT(*) AS c FROM users WHERE is_vip = 1")
        active_works  = await fetch_one("SELECT COUNT(*) AS c FROM works")
        submitted     = await fetch_one("SELECT COUNT(*) AS c FROM works WHERE submitted = 1")
        open_bounties = await fetch_one("SELECT COUNT(*) AS c FROM bounties WHERE status = 'pending' OR status = 'accepted'")
        open_pb       = await fetch_one("SELECT COUNT(*) AS c FROM pbounties WHERE status = 'open' OR status = 'assigned'")
        market_list   = await fetch_one("SELECT COUNT(*) AS c FROM market")
        fund          = await get_fund_balance()
        top_earner    = await fetch_one(
            f"SELECT first_name, username, telegram_id, total_points FROM users "
            f"WHERE telegram_id NOT IN ({','.join(str(a) for a in ADMINS)}) "
            f"ORDER BY total_points DESC LIMIT 1"
        )
        top_link = user_link(top_earner["first_name"], top_earner["telegram_id"], top_earner["username"]) if top_earner else "N/A"
        await message.reply(
            f"📊 <b>BOT REPORT</b>\n"
            f"━━━━━━━━━━━━━━\n"
            f"👥 Total users: <b>{total_users['c']}</b>\n"
            f"🚫 Banned: <b>{banned_users['c']}</b>\n"
            f"👑 VIP active: <b>{vip_users['c']}</b>\n\n"
            f"🎬 Active works: <b>{active_works['c']}</b>\n"
            f"📤 Awaiting review: <b>{submitted['c']}</b>\n\n"
            f"🎯 Open bounties: <b>{open_bounties['c']}</b>\n"
            f"📢 Open public bounties: <b>{open_pb['c']}</b>\n"
            f"🏪 Market listings: <b>{market_list['c']}</b>\n\n"
            f"🏛 Community fund: <b>{fund} pts</b>\n"
            f"🏆 Top earner: {top_link}",
            parse_mode="HTML"
        )

    @dp.message(Command("pendingworks"))
    async def cmd_pendingworks(message: Message):
        if not is_admin(message.from_user.id): return
        works = await fetch_all(
            "SELECT w.deadline, w.submitted, w.penalty_days, u.first_name, u.username, u.telegram_id "
            "FROM works w JOIN users u ON w.user_id = u.id ORDER BY w.deadline ASC"
        )
        if not works:
            return await message.reply("✅ No active works right now.")
        now   = datetime.datetime.now()
        lines = [f"🎬 <b>ACTIVE WORKS ({len(works)})</b>\n"]
        for w in works:
            deadline = datetime.datetime.fromisoformat(w["deadline"])
            diff     = deadline - now
            link     = user_link(w["first_name"], w["telegram_id"], w["username"])
            if diff.total_seconds() < 0:
                time_str = f"⚠️ {abs(diff.days)}d overdue"
            else:
                hours    = int(diff.total_seconds() // 3600)
                time_str = f"✅ {diff.days}d {hours % 24}h left"
            submitted = " 📤 submitted" if w["submitted"] else ""
            lines.append(f"👤 {link}{submitted}\n⏳ {deadline.strftime('%Y-%m-%d %H:%M')} — {time_str}")
        await message.reply("\n\n".join(lines), parse_mode="HTML")

    @dp.message(Command("pendingreviews"))
    async def cmd_pendingreviews(message: Message):
        if not is_admin(message.from_user.id): return
        works = await fetch_all(
            "SELECT w.deadline, w.file_type, u.first_name, u.username, u.telegram_id "
            "FROM works w JOIN users u ON w.user_id = u.id WHERE w.submitted = 1 ORDER BY w.deadline ASC"
        )
        if not works:
            return await message.reply("✅ No pending reviews right now.")
        lines = [f"📋 <b>PENDING REVIEWS ({len(works)})</b>\n"]
        for w in works:
            deadline = datetime.datetime.fromisoformat(w["deadline"])
            link     = user_link(w["first_name"], w["telegram_id"], w["username"])
            late     = "⚠️ LATE" if datetime.datetime.now() > deadline else "✅ On time"
            lines.append(
                f"👤 {link}\n📁 {w['file_type']} | {late}\nUse: /review (reply) &lt;rating&gt;"
            )
        await message.reply("\n\n".join(lines), parse_mode="HTML")

    @dp.message(Command("activeusers"))
    async def cmd_activeusers(message: Message):
        if not is_admin(message.from_user.id): return
        users = await fetch_all(
            "SELECT telegram_id, first_name, username, speciality, total_points, remaining_points, "
            "is_vip, projects, checkin_streak FROM users ORDER BY total_points DESC"
        )
        if not users:
            return await message.reply("No users registered yet.")
        lines = [f"👥 <b>ALL USERS ({len(users)} total)</b>\n"]
        for i, u in enumerate(users, 1):
            link = user_link(u["first_name"], u["telegram_id"], u["username"])
            vip  = " 👑" if u["is_vip"] else ""
            rank = calculate_rank(u["total_points"])
            spec = u["speciality"] or "Not set"
            lines.append(
                f"{i}. {link}{vip}\n"
                f"   🎤 {spec} | 🏆 {rank}\n"
                f"   ⭐ {u['total_points']} pts (💰 {u['remaining_points']} left) | 🎬 {u['projects']} projects"
            )
        chunk = ""
        for line in lines:
            if len(chunk) + len(line) + 2 > 3800:
                await message.reply(chunk, parse_mode="HTML")
                chunk = line
            else:
                chunk += ("\n\n" if chunk else "") + line
        if chunk:
            await message.reply(chunk, parse_mode="HTML")

    @dp.message(Command("deleteuser"))
    async def cmd_deleteuser(message: Message):
        if not is_admin(message.from_user.id): return
        args = message.text.split()
        user, _ = await resolve_user(message, args)
        if not user:
            return await message.reply("❌ User not found.")
        if user["telegram_id"] in ADMINS:
            return await message.reply("❌ Cannot delete an admin account.")
        uid  = user["id"]
        name = user_link(user["first_name"], user["telegram_id"], user["username"])
        await execute("DELETE FROM users      WHERE id = ?",          (uid,))
        await execute("DELETE FROM works      WHERE user_id = ?",     (uid,))
        await execute("DELETE FROM inventory  WHERE user_id = ?",     (uid,))
        await execute("DELETE FROM market     WHERE seller_id = ?",   (uid,))
        await execute("DELETE FROM bounties   WHERE requester_id = ? OR performer_id = ?", (uid, uid))
        await execute("DELETE FROM pbounties  WHERE requester_id = ? OR performer_id = ?", (uid, uid))
        await message.reply(
            f"🗑 <b>User deleted.</b>\nAll data for {name} has been removed.",
            parse_mode="HTML"
        )

    @dp.message(Command("setprice"))
    async def cmd_setprice(message: Message):
        if message.from_user.username != PRICE_MANAGER:
            return await message.reply("❌ Only @tg_zomooroo can change shop prices.")
        args = parse_args(message.text, 3)
        if not args:
            return await message.reply("Usage: /setprice &lt;item&gt; &lt;price&gt;", parse_mode="HTML")
        item = args[1].lower()
        if item not in STORE:
            return await message.reply("❌ Unknown item.")
        try:
            price = int(args[2])
            if price <= 0: raise ValueError
        except ValueError:
            return await message.reply("❌ Price must be a positive integer.")
        old = STORE[item]
        STORE[item] = price
        await message.reply(
            f"✅ {ITEM_EMOJI.get(item,'📦')} <b>{item}</b>: {old} → <b>{price}</b> pts", parse_mode="HTML"
        )

    @dp.message(Command("bounty_success"))
    async def cmd_bounty_success(message: Message):
        if not is_admin(message.from_user.id): return
        args = parse_args(message.text, 2)
        if not args:
            return await message.reply("Usage: /bounty_success &lt;bounty_id&gt;", parse_mode="HTML")
        try:
            bounty_id = int(args[1])
        except ValueError:
            return await message.reply("❌ Invalid bounty ID.")
        bounty = await fetch_one(
            "SELECT * FROM bounties WHERE id = ? AND status = 'accepted'", (bounty_id,)
        )
        if not bounty:
            return await message.reply("❌ Bounty not found or not in accepted state.")
        requester = await fetch_one("SELECT * FROM users WHERE id = ?", (bounty["requester_id"],))
        performer = await fetch_one("SELECT * FROM users WHERE id = ?", (bounty["performer_id"],))
        amount    = bounty["amount"]
        if not is_admin(requester["telegram_id"]) and requester["remaining_points"] < amount:
            return await message.reply(
                f"❌ Requester doesn't have enough points ({requester['remaining_points']} < {amount})."
            )
        if not is_admin(requester["telegram_id"]):
            await execute(
                "UPDATE users SET remaining_points = GREATEST(0, remaining_points - ?) WHERE id = ?",
                (amount, requester["id"])
            )
        await execute(
            "UPDATE users SET remaining_points = remaining_points + ?, total_points = total_points + ? WHERE id = ?",
            (amount, amount, performer["id"])
        )
        await log_points(requester["id"], -amount, f"🎯 Bounty #{bounty_id} paid")
        await log_points(performer["id"], amount,  f"🎯 Bounty #{bounty_id} earned")
        await execute("UPDATE bounties SET status = 'completed' WHERE id = ?", (bounty_id,))
        req_link  = user_link(requester["first_name"], requester["telegram_id"], requester["username"])
        perf_link = user_link(performer["first_name"], performer["telegram_id"], performer["username"])
        await message.reply(
            f"✅ <b>Bounty #{bounty_id} Completed!</b>\n\n"
            f"💸 {req_link} → <b>-{amount} pts</b>\n"
            f"💰 {perf_link} → <b>+{amount} pts</b>",
            parse_mode="HTML"
        )
        try:
            await bot.send_message(requester["telegram_id"],
                f"🎯 <b>Bounty #{bounty_id} completed!</b>\n<b>-{amount} pts</b> deducted.", parse_mode="HTML"
            )
            await bot.send_message(performer["telegram_id"],
                f"🎉 <b>Bounty #{bounty_id} completed!</b>\n<b>+{amount} pts</b> added!", parse_mode="HTML"
            )
        except Exception:
            pass

    @dp.message(Command("pbounty_success"))
    async def cmd_pbounty_success(message: Message):
        if not is_admin(message.from_user.id): return
        args = parse_args(message.text, 2)
        if not args:
            return await message.reply("Usage: /pbounty_success &lt;bounty_id&gt;", parse_mode="HTML")
        try:
            bounty_id = int(args[1])
        except ValueError:
            return await message.reply("❌ Invalid bounty ID.")
        bounty = await fetch_one(
            "SELECT * FROM pbounties WHERE id = ? AND status = 'assigned'", (bounty_id,)
        )
        if not bounty:
            return await message.reply("❌ Bounty not found or not in assigned state.")
        requester = await fetch_one("SELECT * FROM users WHERE id = ?", (bounty["requester_id"],))
        performer = await fetch_one("SELECT * FROM users WHERE id = ?", (bounty["performer_id"],))
        reward    = bounty["reward"]
        await execute(
            "UPDATE users SET remaining_points = remaining_points + ?, total_points = total_points + ? WHERE id = ?",
            (reward, reward, performer["id"])
        )
        await execute("UPDATE pbounties SET status = 'completed' WHERE id = ?", (bounty_id,))
        req_link  = user_link(requester["first_name"], requester["telegram_id"], requester["username"])
        perf_link = user_link(performer["first_name"], performer["telegram_id"], performer["username"])
        await message.reply(
            f"✅ <b>Public Bounty #{bounty_id} Completed!</b>\n\n"
            f"💰 {perf_link} received <b>+{reward} pts</b>\n"
            f"Points were reserved from {req_link} at creation.",
            parse_mode="HTML"
        )
        try:
            await bot.send_message(performer["telegram_id"],
                f"🎉 <b>Bounty #{bounty_id} completed!</b>\n<b>+{reward} pts</b> added!", parse_mode="HTML"
            )
            await bot.send_message(requester["telegram_id"],
                f"✅ <b>Your public bounty #{bounty_id} was completed!</b>\n{reward} pts paid out.", parse_mode="HTML"
            )
        except Exception:
            pass

    @dp.message(Command("pbounty_cancel"))
    async def cmd_pbounty_cancel(message: Message):
        if not is_admin(message.from_user.id): return
        args = parse_args(message.text, 2)
        if not args:
            return await message.reply("Usage: /pbounty_cancel &lt;bounty_id&gt;", parse_mode="HTML")
        try:
            bounty_id = int(args[1])
        except ValueError:
            return await message.reply("❌ Invalid bounty ID.")
        bounty = await fetch_one(
            "SELECT * FROM pbounties WHERE id = ? AND status NOT IN ('completed', 'cancelled')",
            (bounty_id,)
        )
        if not bounty:
            return await message.reply("❌ Bounty not found or already completed/cancelled.")
        requester = await fetch_one("SELECT * FROM users WHERE id = ?", (bounty["requester_id"],))
        await execute(
            "UPDATE users SET remaining_points = remaining_points + ? WHERE id = ?",
            (bounty["reward"], requester["id"])
        )
        await execute("UPDATE pbounties SET status = 'cancelled' WHERE id = ?", (bounty_id,))
        req_link = user_link(requester["first_name"], requester["telegram_id"], requester["username"])
        await message.reply(
            f"✅ Bounty #{bounty_id} cancelled. <b>{bounty['reward']} pts</b> refunded to {req_link}.",
            parse_mode="HTML"
        )
        try:
            await bot.send_message(requester["telegram_id"],
                f"ℹ️ Public Bounty #{bounty_id} was cancelled by an admin.\n"
                f"<b>{bounty['reward']} pts</b> returned to your balance.",
                parse_mode="HTML"
            )
        except Exception:
            pass
        if bounty["performer_id"]:
            performer = await fetch_one("SELECT * FROM users WHERE id = ?", (bounty["performer_id"],))
            if performer:
                try:
                    await bot.send_message(performer["telegram_id"],
                        f"ℹ️ Public Bounty #{bounty_id} was cancelled by an admin. No penalty applied."
                    )
                except Exception:
                    pass
