import asyncpg
from contextlib import asynccontextmanager
from config import DATABASE_URL

_pool: asyncpg.Pool | None = None


async def init_db():
    """
    Создаёт пул и таблицы в Postgres.
    Структура таблиц сохранена максимально близко к SQLite-версии.
    """
    global _pool
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL is not set. Add it in Render Environment variables.")

    if _pool is None:
        _pool = await asyncpg.create_pool(DATABASE_URL, min_size=1, max_size=5)

    async with _pool.acquire() as conn:
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                telegram_id BIGINT PRIMARY KEY,
                username TEXT,
                registered_at TIMESTAMPTZ DEFAULT NOW(),
                is_active INTEGER DEFAULT 1
            );
        """)

        await conn.execute("""
            CREATE TABLE IF NOT EXISTS user_profiles (
                telegram_id BIGINT PRIMARY KEY,
                city TEXT,
                full_name TEXT,
                age INTEGER,
                phone TEXT,
                rating DOUBLE PRECISION DEFAULT 5.0,
                total_shifts INTEGER DEFAULT 0,
                confirmed_shifts INTEGER DEFAULT 0,
                refused_shifts INTEGER DEFAULT 0,
                ignored_shifts INTEGER DEFAULT 0,
                consecutive_failures INTEGER DEFAULT 0,
                is_active INTEGER DEFAULT 1,
                CONSTRAINT fk_user_profiles_users
                    FOREIGN KEY (telegram_id) REFERENCES users(telegram_id)
                    ON DELETE CASCADE
            );
        """)

        await conn.execute("""
            CREATE TABLE IF NOT EXISTS shifts (
                id BIGSERIAL PRIMARY KEY,
                city TEXT NOT NULL,
                date TEXT NOT NULL,
                address TEXT NOT NULL,
                payment TEXT NOT NULL,
                conditions TEXT,
                main_slots INTEGER NOT NULL,
                reserve_slots INTEGER NOT NULL,
                reminder_time TEXT,
                morning_reminder_time TEXT,
                status TEXT DEFAULT 'active',
                created_at TIMESTAMPTZ DEFAULT NOW()
            );
        """)

        await conn.execute("""
            CREATE TABLE IF NOT EXISTS shift_members (
                id BIGSERIAL PRIMARY KEY,
                shift_id BIGINT NOT NULL,
                telegram_id BIGINT NOT NULL,
                member_type TEXT NOT NULL,
                position INTEGER,
                status TEXT DEFAULT 'registered',
                reminder_sent_at TIMESTAMPTZ,
                morning_reminder_sent_at TIMESTAMPTZ,
                joined_at TIMESTAMPTZ DEFAULT NOW(),
                CONSTRAINT fk_shift_members_shifts
                    FOREIGN KEY (shift_id) REFERENCES shifts(id)
                    ON DELETE CASCADE,
                CONSTRAINT fk_shift_members_users
                    FOREIGN KEY (telegram_id) REFERENCES users(telegram_id)
                    ON DELETE CASCADE
            );
        """)

        await conn.execute("""
            CREATE TABLE IF NOT EXISTS shift_results (
                id BIGSERIAL PRIMARY KEY,
                shift_id BIGINT NOT NULL,
                telegram_id BIGINT NOT NULL,
                worked INTEGER,
                decline_reason TEXT,
                CONSTRAINT fk_shift_results_shifts
                    FOREIGN KEY (shift_id) REFERENCES shifts(id)
                    ON DELETE CASCADE,
                CONSTRAINT fk_shift_results_users
                    FOREIGN KEY (telegram_id) REFERENCES users(telegram_id)
                    ON DELETE CASCADE,
                CONSTRAINT uq_shift_results UNIQUE (shift_id, telegram_id)
            );
        """)

        await conn.execute("""
            CREATE TABLE IF NOT EXISTS unblock_requests (
                id BIGSERIAL PRIMARY KEY,
                telegram_id BIGINT NOT NULL,
                city TEXT,
                message TEXT,
                status TEXT DEFAULT 'pending',
                created_at TIMESTAMPTZ DEFAULT NOW()
            );
        """)


def _rec_to_dict(r: asyncpg.Record | None) -> dict | None:
    return dict(r) if r else None


# ─── Users ────────────────────────────────────────────────────────────────────

async def get_user(telegram_id: int) -> dict | None:
    async with _pool.acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM users WHERE telegram_id = $1", telegram_id)
        return _rec_to_dict(row)


async def create_user(telegram_id: int, username: str | None):
    async with _pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO users (telegram_id, username) VALUES ($1, $2) "
            "ON CONFLICT (telegram_id) DO NOTHING",
            telegram_id, username
        )


async def get_profile(telegram_id: int) -> dict | None:
    async with _pool.acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM user_profiles WHERE telegram_id = $1", telegram_id)
        return _rec_to_dict(row)


async def upsert_profile(telegram_id: int, **fields):
    if not fields:
        return

    cols = list(fields.keys())
    values = list(fields.values())

    async with _pool.acquire() as conn:
        insert_cols = ", ".join(["telegram_id"] + cols)
        insert_vals = ", ".join([f"${i}" for i in range(1, len(values) + 2)])
        updates = ", ".join([f"{c} = EXCLUDED.{c}" for c in cols])

        await conn.execute(
            f"INSERT INTO user_profiles ({insert_cols}) VALUES ({insert_vals}) "
            f"ON CONFLICT (telegram_id) DO UPDATE SET {updates}",
            telegram_id, *values
        )


async def get_users_by_city(city: str) -> list[dict]:
    async with _pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT u.telegram_id, up.full_name, up.phone, up.rating
               FROM users u
               JOIN user_profiles up ON u.telegram_id = up.telegram_id
               WHERE up.city = $1 AND up.is_active = 1 AND u.is_active = 1""",
            city
        )
        return [dict(r) for r in rows]


async def increment_stat(telegram_id: int, field: str):
    allowed = {"confirmed_shifts", "refused_shifts", "ignored_shifts", "total_shifts"}
    if field not in allowed:
        return
    async with _pool.acquire() as conn:
        await conn.execute(
            f"UPDATE user_profiles SET {field} = {field} + 1 WHERE telegram_id = $1",
            telegram_id
        )


# ─── Shifts ───────────────────────────────────────────────────────────────────

async def create_shift(
    city: str, date: str, address: str, payment: str, conditions: str,
    main_slots: int, reserve_slots: int,
    reminder_time: str, morning_reminder_time: str,
) -> int:
    async with _pool.acquire() as conn:
        row = await conn.fetchrow(
            """INSERT INTO shifts
               (city, date, address, payment, conditions, main_slots, reserve_slots,
                reminder_time, morning_reminder_time)
               VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9)
               RETURNING id""",
            city, date, address, payment, conditions, main_slots, reserve_slots,
            reminder_time, morning_reminder_time
        )
        return int(row["id"])


async def get_shift(shift_id: int) -> dict | None:
    async with _pool.acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM shifts WHERE id = $1", shift_id)
        return _rec_to_dict(row)


async def get_active_shift_by_city(city: str) -> dict | None:
    async with _pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM shifts WHERE city = $1 AND status = 'active' "
            "ORDER BY created_at DESC LIMIT 1",
            city
        )
        return _rec_to_dict(row)


async def get_active_shift_by_id(shift_id: int) -> dict | None:
    async with _pool.acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM shifts WHERE id = $1", shift_id)
        return _rec_to_dict(row)


async def get_all_active_shifts() -> list[dict]:
    async with _pool.acquire() as conn:
        rows = await conn.fetch("SELECT * FROM shifts WHERE status = 'active'")
        return [dict(r) for r in rows]


async def update_shift_status(shift_id: int, status: str):
    async with _pool.acquire() as conn:
        await conn.execute("UPDATE shifts SET status = $1 WHERE id = $2", status, shift_id)


# ─── Shift members ────────────────────────────────────────────────────────────

async def get_shift_members(shift_id: int) -> list[dict]:
    async with _pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT sm.*, up.full_name, up.phone
               FROM shift_members sm
               LEFT JOIN user_profiles up ON sm.telegram_id = up.telegram_id
               WHERE sm.shift_id = $1
               ORDER BY sm.member_type, sm.position""",
            shift_id
        )
        return [dict(r) for r in rows]


async def get_member_count(shift_id: int, member_type: str) -> int:
    async with _pool.acquire() as conn:
        row = await conn.fetchrow(
            """SELECT COUNT(*) AS c FROM shift_members
               WHERE shift_id = $1 AND member_type = $2
                 AND status NOT IN ('refused','removed')""",
            shift_id, member_type
        )
        return int(row["c"])


async def add_shift_member(shift_id: int, telegram_id: int, member_type: str, position: int):
    async with _pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO shift_members (shift_id, telegram_id, member_type, position) "
            "VALUES ($1,$2,$3,$4)",
            shift_id, telegram_id, member_type, position
        )


async def get_user_shift_membership(shift_id: int, telegram_id: int) -> dict | None:
    async with _pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM shift_members WHERE shift_id = $1 AND telegram_id = $2",
            shift_id, telegram_id
        )
        return _rec_to_dict(row)


async def update_member_status(shift_id: int, telegram_id: int, status: str):
    async with _pool.acquire() as conn:
        await conn.execute(
            "UPDATE shift_members SET status = $1 WHERE shift_id = $2 AND telegram_id = $3",
            status, shift_id, telegram_id
        )


async def set_reminder_sent_at(shift_id: int, telegram_id: int):
    async with _pool.acquire() as conn:
        await conn.execute(
            "UPDATE shift_members SET reminder_sent_at = NOW() "
            "WHERE shift_id = $1 AND telegram_id = $2",
            shift_id, telegram_id
        )


async def set_morning_reminder_sent_at(shift_id: int, telegram_id: int):
    async with _pool.acquire() as conn:
        await conn.execute(
            "UPDATE shift_members SET morning_reminder_sent_at = NOW() "
            "WHERE shift_id = $1 AND telegram_id = $2",
            shift_id, telegram_id
        )


async def get_members_to_ignore_check(shift_id: int) -> list[dict]:
    async with _pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT * FROM shift_members
               WHERE shift_id = $1 AND status = 'registered'
                 AND member_type = 'main'
                 AND reminder_sent_at IS NOT NULL
                 AND (NOW() - reminder_sent_at) >= INTERVAL '30 minutes'""",
            shift_id
        )
        return [dict(r) for r in rows]


async def get_members_to_morning_ignore_check(shift_id: int) -> list[dict]:
    async with _pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT * FROM shift_members
               WHERE shift_id = $1 AND member_type = 'main' AND status = 'registered'
                 AND morning_reminder_sent_at IS NOT NULL
                 AND (NOW() - morning_reminder_sent_at) >= INTERVAL '10 minutes'""",
            shift_id
        )
        return [dict(r) for r in rows]


async def get_first_reserve(shift_id: int) -> dict | None:
    async with _pool.acquire() as conn:
        row = await conn.fetchrow(
            """SELECT * FROM shift_members
               WHERE shift_id = $1 AND member_type = 'reserve'
                 AND status IN ('registered', 'confirmed')
               ORDER BY position ASC LIMIT 1""",
            shift_id
        )
        return _rec_to_dict(row)


async def promote_to_main(shift_id: int, telegram_id: int, new_position: int):
    async with _pool.acquire() as conn:
        await conn.execute(
            """UPDATE shift_members
               SET member_type = 'main', position = $1, status = 'registered',
                   reminder_sent_at = NULL, morning_reminder_sent_at = NULL
               WHERE shift_id = $2 AND telegram_id = $3""",
            new_position, shift_id, telegram_id
        )


# ─── shift_results ────────────────────────────────────────────────────────────

async def save_shift_result(
    shift_id: int,
    telegram_id: int,
    worked: bool,
    decline_reason: str = None,
):
    async with _pool.acquire() as conn:
        await conn.execute(
            """INSERT INTO shift_results (shift_id, telegram_id, worked, decline_reason)
               VALUES ($1,$2,$3,$4)
               ON CONFLICT (shift_id, telegram_id) DO UPDATE SET
                   worked = EXCLUDED.worked,
                   decline_reason = EXCLUDED.decline_reason""",
            shift_id, telegram_id, 1 if worked else 0, decline_reason
        )

        new_status = "worked" if worked else "removed"
        await conn.execute(
            "UPDATE shift_members SET status = $1 WHERE shift_id = $2 AND telegram_id = $3",
            new_status, shift_id, telegram_id
        )

        if worked:
            await conn.execute(
                """UPDATE user_profiles
                   SET total_shifts = total_shifts + 1,
                       confirmed_shifts = confirmed_shifts + 1
                   WHERE telegram_id = $1""",
                telegram_id
            )


async def get_shift_result(shift_id: int, telegram_id: int) -> dict | None:
    async with _pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM shift_results WHERE shift_id = $1 AND telegram_id = $2",
            shift_id, telegram_id
        )
        return _rec_to_dict(row)


async def get_shift_members_for_report(shift_id: int) -> list[dict]:
    async with _pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT sm.telegram_id, sm.member_type, up.full_name
               FROM shift_members sm
               JOIN user_profiles up ON sm.telegram_id = up.telegram_id
               WHERE sm.shift_id = $1
                 AND sm.status NOT IN ('refused', 'removed')""",
            shift_id
        )
        return [dict(r) for r in rows]


async def get_shift_results_full(shift_id: int) -> tuple[list, list, list]:
    async with _pool.acquire() as conn:
        worked = await conn.fetch(
            """SELECT sr.telegram_id, up.full_name, up.phone, sm.member_type
               FROM shift_results sr
               JOIN user_profiles up ON sr.telegram_id = up.telegram_id
               JOIN shift_members sm
                 ON sm.shift_id = sr.shift_id AND sm.telegram_id = sr.telegram_id
               WHERE sr.shift_id = $1 AND sr.worked = 1""",
            shift_id
        )

        not_worked = await conn.fetch(
            """SELECT sr.telegram_id, up.full_name, up.phone,
                      sr.decline_reason, sm.member_type
               FROM shift_results sr
               JOIN user_profiles up ON sr.telegram_id = up.telegram_id
               JOIN shift_members sm
                 ON sm.shift_id = sr.shift_id AND sm.telegram_id = sr.telegram_id
               WHERE sr.shift_id = $1 AND sr.worked = 0""",
            shift_id
        )

        no_response = await conn.fetch(
            """SELECT sm.telegram_id, up.full_name, up.phone, sm.member_type
               FROM shift_members sm
               JOIN user_profiles up ON sm.telegram_id = up.telegram_id
               WHERE sm.shift_id = $1
                 AND sm.status NOT IN ('removed', 'refused')
                 AND sm.telegram_id NOT IN (
                     SELECT telegram_id FROM shift_results WHERE shift_id = $1
                 )""",
            shift_id
        )

    return [dict(r) for r in worked], [dict(r) for r in not_worked], [dict(r) for r in no_response]


# ─── Блок 7 ───────────────────────────────────────────────────────────────────

async def increment_consecutive_failures(telegram_id: int):
    async with _pool.acquire() as conn:
        await conn.execute(
            """UPDATE user_profiles
               SET consecutive_failures = consecutive_failures + 1
               WHERE telegram_id = $1""",
            telegram_id
        )


async def reset_consecutive_failures(telegram_id: int):
    async with _pool.acquire() as conn:
        await conn.execute(
            "UPDATE user_profiles SET consecutive_failures = 0 WHERE telegram_id = $1",
            telegram_id
        )


async def check_and_block_if_needed(telegram_id: int) -> bool:
    async with _pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT consecutive_failures FROM user_profiles WHERE telegram_id = $1",
            telegram_id
        )
        if not row or int(row["consecutive_failures"]) < 4:
            return False

        await conn.execute("UPDATE user_profiles SET is_active = 0 WHERE telegram_id = $1", telegram_id)
        await conn.execute("UPDATE users SET is_active = 0 WHERE telegram_id = $1", telegram_id)
        return True


async def unblock_user(telegram_id: int):
    async with _pool.acquire() as conn:
        await conn.execute(
            "UPDATE user_profiles SET is_active = 1, consecutive_failures = 0 WHERE telegram_id = $1",
            telegram_id
        )
        await conn.execute("UPDATE users SET is_active = 1 WHERE telegram_id = $1", telegram_id)


async def create_unblock_request(telegram_id: int, city: str, message: str) -> bool:
    async with _pool.acquire() as conn:
        existing = await conn.fetchrow(
            "SELECT id FROM unblock_requests WHERE telegram_id = $1 AND status = 'pending'",
            telegram_id
        )
        if existing:
            return False

        await conn.execute(
            "INSERT INTO unblock_requests (telegram_id, city, message) VALUES ($1,$2,$3)",
            telegram_id, city, message
        )
        return True


async def get_pending_unblock_requests() -> list[dict]:
    async with _pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT ur.*, up.full_name, up.phone, up.refused_shifts,
                      up.ignored_shifts, up.consecutive_failures
               FROM unblock_requests ur
               LEFT JOIN user_profiles up ON ur.telegram_id = up.telegram_id
               WHERE ur.status = 'pending'
               ORDER BY ur.created_at ASC"""
        )
        return [dict(r) for r in rows]


async def resolve_unblock_request(request_id: int, status: str):
    async with _pool.acquire() as conn:
        await conn.execute(
            "UPDATE unblock_requests SET status = $1 WHERE id = $2",
            status, request_id
        )


async def get_unblock_request(request_id: int) -> dict | None:
    async with _pool.acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM unblock_requests WHERE id = $1", request_id)
        return _rec_to_dict(row)


@asynccontextmanager
async def get_db():
    async with _pool.acquire() as conn:
        yield conn