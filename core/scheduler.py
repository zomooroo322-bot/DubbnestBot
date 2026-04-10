import asyncio
import math
import datetime
from aiogram import Bot
from config import ADMINS, GROUP_ID, PURCHASES_LOG_ID
from core.database import fetch_all, execute, log_points
from core.helpers import user_link

async def start_scheduler(bot: Bot):
    while True:
        await asyncio.sleep(300)
        now     = datetime.datetime.now()
        now_str = now.strftime("%Y-%m-%d %H:%M:%S")

        # ── Expire VIP ──
        expired_vips = await fetch_all(
            "SELECT id, telegram_id, first_name FROM users "
            "WHERE is_vip = 1 AND vip_expires_at IS NOT NULL AND vip_expires_at <= ?",
            (now_str,)
        )
        for u in expired_vips:
            await execute("UPDATE users SET is_vip = 0, vip_expires_at = NULL WHERE id = ?", (u["id"],))
            try:
                await bot.send_message(u["telegram_id"],
                    "👑 <b>VIP Expired</b>\n\n"
                    "Your 7-day VIP has ended. Steal protection is no longer active.\n"
                    "Buy a new VIP from /shop to renew!",
                    parse_mode="HTML"
                )
            except Exception:
                pass

        # ── Expire protection items ──
        expired_protections = await fetch_all(
            "SELECT inv.id, inv.user_id, u.telegram_id "
            "FROM inventory inv JOIN users u ON inv.user_id = u.id "
            "WHERE inv.item = 'protection' AND inv.expires_at IS NOT NULL AND inv.expires_at <= ?",
            (now_str,)
        )
        for row in expired_protections:
            await execute("DELETE FROM inventory WHERE id = ?", (row["id"],))
            try:
                await bot.send_message(row["telegram_id"],
                    "🛡 <b>Protection Expired</b>\n\nYour 3-day shield has run out.\n"
                    "Buy a new one from /shop to stay protected!",
                    parse_mode="HTML"
                )
            except Exception:
                pass

        # ── Work deadline penalties ──
        overdue = await fetch_all(
            "SELECT w.id, w.user_id, w.deadline, w.max_days, w.penalty_days, w.last_penalty_at, "
            "u.telegram_id, u.first_name, u.username, u.remaining_points "
            "FROM works w JOIN users u ON w.user_id = u.id WHERE w.deadline IS NOT NULL AND w.submitted = 0"
        )
        for work in overdue:
            deadline = datetime.datetime.fromisoformat(work["deadline"])
            if now <= deadline:
                continue

            already_penalised = work["penalty_days"]
            penalty_cap       = work["max_days"]
            elapsed_hours     = (now - deadline).total_seconds() / 3600
            ticks_due         = min(math.ceil(elapsed_hours / 24), penalty_cap)

            if already_penalised >= penalty_cap:
                await execute("DELETE FROM works WHERE id = ?", (work["id"],))
                try:
                    await bot.send_message(work["telegram_id"],
                        "❌ <b>Work Removed</b> — max overdue period exceeded.", parse_mode="HTML"
                    )
                except Exception:
                    pass
                continue

            new_ticks = ticks_due - already_penalised
            if new_ticks <= 0:
                continue

            if already_penalised == 0:
                overdue_hours = round((now - deadline).total_seconds() / 3600, 1)
                try:
                    await bot.send_message(work["telegram_id"],
                        f"⏰ <b>Deadline Missed!</b>\n\n"
                        f"Your deadline passed <b>{overdue_hours}h</b> ago.\n"
                        f"You will receive <b>-15 pts per day</b> until you submit or 10 days pass.\n\n"
                        f"Submit ASAP with /submit to stop penalties!",
                        parse_mode="HTML"
                    )
                except Exception:
                    pass

            for _ in range(new_ticks):
                if already_penalised >= penalty_cap:
                    break
                already_penalised += 1
                await execute(
                    "UPDATE users SET remaining_points = GREATEST(0, remaining_points - 15), "
                    "penalties_received = penalties_received + 1 WHERE id = ?",
                    (work["user_id"],)
                )
                await log_points(work["user_id"], -15, f"⏰ Late penalty day {already_penalised}")
                await execute(
                    "UPDATE works SET penalty_days = ?, last_penalty_at = ? WHERE id = ?",
                    (already_penalised, now_str, work["id"])
                )
                days_remaining = penalty_cap - already_penalised
                user_link_str  = user_link(work["first_name"], work["telegram_id"], work["username"])
                try:
                    await bot.send_message(work["telegram_id"],
                        f"⚠️ <b>Late Penalty — Day {already_penalised}</b>\n"
                        f"-15 pts deducted. "
                        f"{'Submit ASAP with /submit!' if days_remaining > 0 else 'Work will be removed!'}\n"
                        f"Days until force-removal: {days_remaining}",
                        parse_mode="HTML"
                    )
                except Exception:
                    pass
                admin_tags    = " ".join(f'<a href="tg://user?id={aid}">Admin</a>' for aid in ADMINS)
                penalty_report = (
                    f"🚨 <b>Late Penalty Report</b>\n\n"
                    f"👤 {user_link_str}\n"
                    f"📅 Penalty Day: <b>{already_penalised}</b>\n"
                    f"💸 Deducted: <b>-15 pts</b>\n"
                    f"⏳ Days until force-removal: <b>{days_remaining}</b>\n\n"
                    f"{admin_tags}"
                )
                for _chat in (PURCHASES_LOG_ID,):
                    try:
                        await bot.send_message(_chat, penalty_report, parse_mode="HTML")
                    except Exception:
                        pass
                if already_penalised >= penalty_cap:
                    await execute("DELETE FROM works WHERE id = ?", (work["id"],))
                    try:
                        await bot.send_message(work["telegram_id"],
                            "❌ <b>Work Force-Removed</b> — 10 day overdue limit reached.", parse_mode="HTML"
                        )
                    except Exception:
                        pass
                    try:
                        await bot.send_message(PURCHASES_LOG_ID,
                            f"🗑 <b>Work Force-Removed</b>\n"
                            f"👤 {user_link_str}\n"
                            f"📅 Reached 10-day overdue limit.\n"
                            f"⏰ {datetime.datetime.now().strftime('%Y-%m-%d %H:%M')}",
                            parse_mode="HTML"
                        )
                    except Exception:
                        pass
                    break

        # ── Public bounty open expiry (24h no applicants) ──
        expired_open = await fetch_all(
            "SELECT pb.*, u.telegram_id AS req_tg "
            "FROM pbounties pb JOIN users u ON pb.requester_id = u.id "
            "WHERE pb.status = 'open' AND pb.open_expires_at IS NOT NULL AND pb.open_expires_at <= ?",
            (now_str,)
        )
        for pb in expired_open:
            await execute(
                "UPDATE users SET remaining_points = remaining_points + ? WHERE id = ?",
                (pb["reward"], pb["requester_id"])
            )
            await execute("UPDATE pbounties SET status = 'expired' WHERE id = ?", (pb["id"],))
            try:
                await bot.send_message(pb["req_tg"],
                    f"⏰ <b>Public Bounty #{pb['id']} Expired</b>\n\n"
                    f"No one applied within 24 hours.\n"
                    f"<b>{pb['reward']} pts</b> have been refunded to your balance.\n\n"
                    f"You can repost with /pbounty anytime!",
                    parse_mode="HTML"
                )
            except Exception:
                pass

        # ── Public bounty deadline auto-penalty ──
        overdue_pbounties = await fetch_all(
            "SELECT pb.*, "
            "u_req.telegram_id AS req_tg, "
            "u_perf.telegram_id AS perf_tg "
            "FROM pbounties pb "
            "JOIN users u_req  ON pb.requester_id = u_req.id "
            "LEFT JOIN users u_perf ON pb.performer_id = u_perf.id "
            "WHERE pb.status = 'assigned' AND pb.deadline_at IS NOT NULL AND pb.deadline_at <= ?",
            (now_str,)
        )
        for pb in overdue_pbounties:
            if pb["performer_id"]:
                await execute(
                    "UPDATE users SET remaining_points = GREATEST(0, remaining_points - 20), "
                    "penalties_received = penalties_received + 1 WHERE id = ?",
                    (pb["performer_id"],)
                )
                try:
                    await bot.send_message(pb["perf_tg"],
                        f"⚠️ <b>Public Bounty #{pb['id']} — Deadline Missed!</b>\n\n"
                        f"You did not submit before the deadline.\n"
                        f"<b>-20 pts</b> deducted as penalty.\n"
                        f"The bounty has been cancelled.",
                        parse_mode="HTML"
                    )
                except Exception:
                    pass
            await execute(
                "UPDATE users SET remaining_points = remaining_points + ? WHERE id = ?",
                (pb["reward"], pb["requester_id"])
            )
            await execute("UPDATE pbounties SET status = 'failed' WHERE id = ?", (pb["id"],))
            try:
                await bot.send_message(pb["req_tg"],
                    f"❌ <b>Public Bounty #{pb['id']} Failed</b>\n\n"
                    f"The performer missed the deadline.\n"
                    f"<b>{pb['reward']} pts</b> have been refunded to your balance.",
                    parse_mode="HTML"
                )
            except Exception:
                pass
