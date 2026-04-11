import os
import datetime
import asyncpg
from config import STARTER_POINTS

_pool: asyncpg.Pool = None

async def get_pool() -> asyncpg.Pool:
    global _pool
    if _pool is None:
        _pool = await asyncpg.create_pool(os.environ["DATABASE_URL"], min_size=1, max_size=5)
    return _pool

def _fix_placeholders(query: str) -> str:
    i = 0
    result = []
    for ch in query:
        if ch == "?":
            i += 1
            result.append(f"${i}")
        else:
            result.append(ch)
    return "".join(result)

async def fetch_one(query: str, params: tuple = ()):
    pool = await get_pool()
    query = _fix_placeholders(query)
    async with pool.acquire() as conn:
        row = await conn.fetchrow(query, *params)
        return dict(row) if row else None

async def fetch_all(query: str, params: tuple = ()):
    pool = await get_pool()
    query = _fix_placeholders(query)
    async with pool.acquire() as conn:
        rows = await conn.fetch(query, *params)
        return [dict(r) for r in rows]

async def execute(query: str, params: tuple = ()):
    pool = await get_pool()
    query = _fix_placeholders(query)
    async with pool.acquire() as conn:
        await conn.execute(query, *params)

async def init_db():
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(f"""
        CREATE TABLE IF NOT EXISTS users (
            id                 SERIAL PRIMARY KEY,
            telegram_id        BIGINT UNIQUE,
            first_name         TEXT,
            username           TEXT,
            speciality         TEXT    DEFAULT 'Not set',
            total_points       INTEGER DEFAULT {STARTER_POINTS},
            remaining_points   INTEGER DEFAULT {STARTER_POINTS},
            artist_points      INTEGER DEFAULT 0,
            projects           INTEGER DEFAULT 0,
            is_vip             INTEGER DEFAULT 0,
            vip_expires_at     TEXT    DEFAULT NULL,
            checkin_streak     INTEGER DEFAULT 0,
            last_checkin       TEXT    DEFAULT NULL,
            penalties_received INTEGER DEFAULT 0,
            items_bought       INTEGER DEFAULT 0,
            items_used         INTEGER DEFAULT 0,
            is_banned          INTEGER DEFAULT 0,
            joined_at          TEXT    DEFAULT NULL,
            tasks_on_time      INTEGER DEFAULT 0,
            last_milestone_3   TEXT    DEFAULT NULL,
            last_milestone_week TEXT   DEFAULT NULL,
            last_milestone_month TEXT  DEFAULT NULL
        )""")
        await conn.execute("""
        CREATE TABLE IF NOT EXISTS works (
            id              SERIAL PRIMARY KEY,
            user_id         INTEGER,
            file_id         TEXT,
            file_type       TEXT    DEFAULT 'video',
            deadline        TEXT,
            max_days        INTEGER DEFAULT 10,
            penalty_days    INTEGER DEFAULT 0,
            last_penalty_at TEXT    DEFAULT NULL,
            submitted       INTEGER DEFAULT 0,
            redub           INTEGER DEFAULT 0
        )""")
        await conn.execute("""
        CREATE TABLE IF NOT EXISTS inventory (
            id          SERIAL PRIMARY KEY,
            user_id     INTEGER,
            item        TEXT,
            obtained_at TEXT,
            expires_at  TEXT DEFAULT NULL
        )""")
        await conn.execute("""
        CREATE TABLE IF NOT EXISTS market (
            id        SERIAL PRIMARY KEY,
            seller_id INTEGER,
            item      TEXT,
            price     INTEGER,
            listed_at TEXT
        )""")
        await conn.execute("""
        CREATE TABLE IF NOT EXISTS bounties (
            id           SERIAL PRIMARY KEY,
            requester_id INTEGER,
            performer_id INTEGER,
            amount       INTEGER,
            status       TEXT DEFAULT 'pending'
        )""")
        await conn.execute("""
        CREATE TABLE IF NOT EXISTS pbounties (
            id               SERIAL PRIMARY KEY,
            requester_id     INTEGER,
            file_id          TEXT,
            file_type        TEXT,
            voice_gender     TEXT,
            voice_type       TEXT,
            emotion          TEXT,
            length           TEXT,
            reward           INTEGER,
            deadline_days    INTEGER,
            deadline_at      TEXT,
            open_expires_at  TEXT,
            performer_id     INTEGER DEFAULT NULL,
            status           TEXT    DEFAULT 'open',
            created_at       TEXT
        )""")
        await conn.execute("""
        CREATE TABLE IF NOT EXISTS clip_approved (
            telegram_id BIGINT PRIMARY KEY,
            approved_at TEXT
        )""")
        await conn.execute("""
        CREATE TABLE IF NOT EXISTS points_history (
            id      SERIAL PRIMARY KEY,
            user_id INTEGER,
            change  INTEGER,
            reason  TEXT,
            ts      TEXT
        )""")
        await conn.execute("""
        CREATE TABLE IF NOT EXISTS warnings (
            id         SERIAL PRIMARY KEY,
            user_id    INTEGER,
            reason     TEXT,
            warned_by  BIGINT,
            warned_at  TEXT
        )""")
        await conn.execute("""
        CREATE TABLE IF NOT EXISTS class_sessions (
            id          SERIAL PRIMARY KEY,
            started_by  BIGINT,
            topic       TEXT,
            started_at  TEXT,
            ended_at    TEXT DEFAULT NULL,
            status      TEXT DEFAULT 'active'
        )""")
        await conn.execute("""
        CREATE TABLE IF NOT EXISTS class_attendance (
            id         SERIAL PRIMARY KEY,
            session_id INTEGER,
            user_id    INTEGER,
            checks     INTEGER DEFAULT 0,
            messaged   INTEGER DEFAULT 0,
            points_given INTEGER DEFAULT 0
        )""")
        await conn.execute("""
        CREATE TABLE IF NOT EXISTS rating_history (
            id          SERIAL PRIMARY KEY,
            user_id     INTEGER,
            rating      TEXT,
            artist_pts  INTEGER,
            bonus_pts   INTEGER,
            reviewed_by BIGINT,
            reviewed_at TEXT
        )""")
        for idx_sql in [
            "CREATE INDEX IF NOT EXISTS idx_users_tid    ON users(telegram_id)",
            "CREATE INDEX IF NOT EXISTS idx_users_uname  ON users(username)",
            "CREATE INDEX IF NOT EXISTS idx_works_uid    ON works(user_id)",
            "CREATE INDEX IF NOT EXISTS idx_inv_uid      ON inventory(user_id)",
            "CREATE INDEX IF NOT EXISTS idx_market_sid   ON market(seller_id)",
            "CREATE INDEX IF NOT EXISTS idx_ph_uid       ON points_history(user_id)",
            "CREATE INDEX IF NOT EXISTS idx_pb_id        ON pbounties(id)",
            "CREATE INDEX IF NOT EXISTS idx_warn_uid     ON warnings(user_id)",
            "CREATE INDEX IF NOT EXISTS idx_rh_uid       ON rating_history(user_id)",
            "CREATE INDEX IF NOT EXISTS idx_ca_sid       ON class_attendance(session_id)",
        ]:
            await conn.execute(idx_sql)
        # Safe migrations
        for col, dfn in [
            ("artist_points",       "INTEGER DEFAULT 0"),
            ("joined_at",           "TEXT DEFAULT NULL"),
            ("tasks_on_time",       "INTEGER DEFAULT 0"),
            ("last_milestone_3",    "TEXT DEFAULT NULL"),
            ("last_milestone_week", "TEXT DEFAULT NULL"),
            ("last_milestone_month","TEXT DEFAULT NULL"),
            ("speciality",          "TEXT DEFAULT 'Not set'"),
            ("is_vip",              "INTEGER DEFAULT 0"),
            ("vip_expires_at",      "TEXT DEFAULT NULL"),
            ("checkin_streak",      "INTEGER DEFAULT 0"),
            ("last_checkin",        "TEXT DEFAULT NULL"),
            ("penalties_received",  "INTEGER DEFAULT 0"),
            ("items_bought",        "INTEGER DEFAULT 0"),
            ("items_used",          "INTEGER DEFAULT 0"),
            ("is_banned",           "INTEGER DEFAULT 0"),
        ]:
            try:
                await conn.execute(f"ALTER TABLE users ADD COLUMN IF NOT EXISTS {col} {dfn}")
            except Exception:
                pass
        for col, dfn in [
            ("max_days",        "INTEGER DEFAULT 10"),
            ("penalty_days",    "INTEGER DEFAULT 0"),
            ("file_type",       "TEXT DEFAULT 'video'"),
            ("submitted",       "INTEGER DEFAULT 0"),
            ("last_penalty_at", "TEXT DEFAULT NULL"),
            ("redub",           "INTEGER DEFAULT 0"),
        ]:
            try:
                await conn.execute(f"ALTER TABLE works ADD COLUMN IF NOT EXISTS {col} {dfn}")
            except Exception:
                pass
        try:
            await conn.execute("ALTER TABLE inventory ADD COLUMN IF NOT EXISTS expires_at TEXT DEFAULT NULL")
            await conn.execute("ALTER TABLE pbounties ADD COLUMN IF NOT EXISTS open_expires_at TEXT DEFAULT NULL")
        except Exception:
            pass
        await conn.execute(
            "UPDATE users SET total_points = remaining_points WHERE total_points < remaining_points"
        )

async def upsert_user(tg_user):
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    await execute(
        f"INSERT INTO users (telegram_id, first_name, username, total_points, remaining_points, joined_at) "
        f"VALUES (?, ?, ?, {STARTER_POINTS}, {STARTER_POINTS}, ?) "
        f"ON CONFLICT(telegram_id) DO UPDATE SET first_name = EXCLUDED.first_name, username = EXCLUDED.username",
        (tg_user.id, tg_user.first_name or "User", tg_user.username, now)
    )

async def get_user_by_tgid(tg_id: int):
    return await fetch_one("SELECT * FROM users WHERE telegram_id = ?", (tg_id,))

async def get_user_by_username(username: str):
    return await fetch_one("SELECT * FROM users WHERE username = ?", (username,))

async def log_points(user_id: int, change: int, reason: str):
    ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    await execute(
        "INSERT INTO points_history (user_id, change, reason, ts) VALUES (?, ?, ?, ?)",
        (user_id, change, reason, ts)
    )

async def add_to_inventory(user_id: int, item: str, expires_at: str = None):
    obtained_at = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    await execute(
        "INSERT INTO inventory (user_id, item, obtained_at, expires_at) VALUES (?, ?, ?, ?)",
        (user_id, item, obtained_at, expires_at)
    )
    await execute("UPDATE users SET items_bought = items_bought + 1 WHERE id = ?", (user_id,))

async def get_fund_balance() -> int:
    from config import ADMINS
    placeholders = ", ".join(f"${i+1}" for i in range(len(ADMINS)))
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            f"SELECT SUM(remaining_points) AS total FROM users WHERE telegram_id NOT IN ({placeholders})",
            *ADMINS
        )
        return row["total"] if row and row["total"] else 0
