import re
import datetime
from aiogram.types import Message
from config import ADMINS, RANKS, STARTER_POINTS
from core.database import fetch_one, execute, get_user_by_tgid, get_user_by_username

def is_admin(user_id: int) -> bool:
    return user_id in ADMINS

def calculate_rank(points: int) -> str:
    for threshold, rank in RANKS:
        if points >= threshold:
            return rank
    return "Beginner"

def user_link(first_name: str, telegram_id: int, username: str = None) -> str:
    if first_name and first_name.strip():
        display = first_name.strip()
    elif username:
        display = f"@{username}"
    else:
        display = "User"
    safe = display.replace("<", "&lt;").replace(">", "&gt;")
    return f'<a href="tg://user?id={telegram_id}">{safe}</a>'

def parse_args(text: str, expected: int):
    parts = text.split()
    return parts if len(parts) == expected else None

def strip_at(s: str) -> str:
    return s.lstrip("@")

def parse_duration(s: str):
    s = s.lower().strip()
    d_match = re.search(r'(\d+)d', s)
    h_match = re.search(r'(\d+)h', s)
    if not d_match and not h_match:
        return None
    days  = int(d_match.group(1)) if d_match else 0
    hours = int(h_match.group(1)) if h_match else 0
    if days == 0 and hours == 0:
        return None
    return datetime.timedelta(days=days, hours=hours)

def _fmt_duration(td: datetime.timedelta) -> str:
    total_seconds = int(td.total_seconds())
    d = total_seconds // 86400
    h = (total_seconds % 86400) // 3600
    parts = []
    if d: parts.append(f"{d}d")
    if h: parts.append(f"{h}h")
    return " ".join(parts) if parts else "0h"

async def check_banned(message: Message) -> bool:
    user = await get_user_by_tgid(message.from_user.id)
    if user and user["is_banned"] and message.from_user.id not in ADMINS:
        await message.reply("🚫 You are banned from using this bot.")
        return True
    return False

async def resolve_user(message: Message, args: list, arg_index: int = 1):
    if message.entities:
        for entity in message.entities:
            if entity.type == "text_mention" and entity.user:
                user = await get_user_by_tgid(entity.user.id)
                if not user:
                    await execute(
                        f"INSERT INTO users (telegram_id, first_name, username, remaining_points) "
                        f"VALUES (?, ?, ?, {STARTER_POINTS}) ON CONFLICT(telegram_id) DO NOTHING",
                        (entity.user.id, entity.user.first_name or "User", entity.user.username)
                    )
                    user = await get_user_by_tgid(entity.user.id)
                remaining = args[arg_index + 1:] if len(args) > arg_index else []
                return user, remaining
            elif entity.type == "mention" and len(args) > arg_index:
                uname = strip_at(args[arg_index])
                user  = await get_user_by_username(uname)
                return user, args[arg_index + 1:]

    if len(args) > arg_index and args[arg_index].startswith("@"):
        uname = strip_at(args[arg_index])
        user  = await get_user_by_username(uname)
        return user, args[arg_index + 1:]

    if message.reply_to_message:
        user = await get_user_by_tgid(message.reply_to_message.from_user.id)
        return user, args[arg_index:]

    if len(args) > arg_index and not args[arg_index].startswith("/"):
        candidate = args[arg_index]
        if not candidate[0].isdigit():
            user = await fetch_one(
                "SELECT * FROM users WHERE first_name ILIKE ? LIMIT 1",
                (f"%{candidate}%",)
            )
            return user, args[arg_index + 1:]

    return None, None

async def build_inv_text(display: str, items: list, own: bool = True) -> str:
    from config import ITEM_EMOJI
    if not items:
        return f"🎒 <b>{display}'s Inventory</b>\n\nInventory is empty."
    counts: dict = {}
    for row in items:
        counts[row["item"]] = counts.get(row["item"], 0) + 1
    lines = [
        f"{ITEM_EMOJI.get(k,'📦')} <code>{k}</code>{f' x{v}' if v > 1 else ''}"
        for k, v in counts.items()
    ]
    hint = "\n\nUse /use &lt;item&gt; • /market list &lt;item&gt; &lt;price&gt; to sell" if own else ""
    return f"🎒 <b>{display}'s Inventory</b>\n\n" + "\n".join(lines) + hint
