import aiosqlite
from config import DATABASE_URL

DB_PATH = DATABASE_URL if DATABASE_URL.endswith(".db") else "cleaning_bot.db"


async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                telegram_id INTEGER PRIMARY KEY,
                username TEXT,
                registered_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                is_active INTEGER DEFAULT 1
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS user_profiles (
                telegram_id INTEGER PRIMARY KEY,
                city TEXT,
                full_name TEXT,
                age INTEGER,
                phone TEXT,
                rating REAL DEFAULT 5.0,
                total_shifts INTEGER DEFAULT 0,
                confirmed_shifts INTEGER DEFAULT 0,
                refused_shifts INTEGER DEFAULT 0,
                ignored_shifts INTEGER DEFAULT 0,
                is_active INTEGER DEFAULT 1,
                FOREIGN KEY (telegram_id) REFERENCES users(telegram_id)
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS shifts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
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
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS shift_members (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                shift_id INTEGER NOT NULL,
                telegram_id INTEGER NOT NULL,
                member_type TEXT NOT NULL,
                position INTEGER,
                status TEXT DEFAULT 'registered',
                reminder_sent_at TIMESTAMP,
                morning_reminder_sent_at TIMESTAMP,
                joined_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (shift_id) REFERENCES shifts(id),
                FOREIGN KEY (telegram_id) REFERENCES users(telegram_id)
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS shift_results (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                shift_id INTEGER NOT NULL,
                telegram_id INTEGER NOT NULL,
                worked INTEGER,
                decline_reason TEXT,
                FOREIGN KEY (shift_id) REFERENCES shifts(id),
                UNIQUE(shift_id, telegram_id)
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS unblock_requests (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                telegram_id INTEGER NOT NULL,
                city TEXT,
                message TEXT,
                status TEXT DEFAULT 'pending',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # Миграция: добавляем колонки если обновляемся со старой БД
        for table, col, definition in [
            ("shifts",        "morning_reminder_time",      "TEXT"),
            ("shift_members", "reminder_sent_at",           "TIMESTAMP"),
            ("shift_members", "morning_reminder_sent_at",   "TIMESTAMP"),
            ("user_profiles", "consecutive_failures",       "INTEGER DEFAULT 0"),  # Блок 7
        ]:
            try:
                await db.execute(f"ALTER TABLE {table} ADD COLUMN {col} {definition}")
            except Exception:
                pass

        # Миграция: уникальный индекс для shift_results (для существующих БД)
        try:
            await db.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS uq_shift_result "
                "ON shift_results(shift_id, telegram_id)"
            )
        except Exception:
            pass

        await db.commit()


# ─── Users ────────────────────────────────────────────────────────────────────

async def get_user(telegram_id: int) -> dict | None:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM users WHERE telegram_id = ?", (telegram_id,)) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None


async def create_user(telegram_id: int, username: str | None):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT OR IGNORE INTO users (telegram_id, username) VALUES (?, ?)",
            (telegram_id, username),
        )
        await db.commit()


async def get_profile(telegram_id: int) -> dict | None:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM user_profiles WHERE telegram_id = ?", (telegram_id,)) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None


async def upsert_profile(telegram_id: int, **fields):
    async with aiosqlite.connect(DB_PATH) as db:
        existing = await db.execute(
            "SELECT 1 FROM user_profiles WHERE telegram_id = ?", (telegram_id,)
        )
        if await existing.fetchone():
            set_clause = ", ".join(f"{k} = ?" for k in fields)
            await db.execute(
                f"UPDATE user_profiles SET {set_clause} WHERE telegram_id = ?",
                (*fields.values(), telegram_id),
            )
        else:
            cols = "telegram_id, " + ", ".join(fields.keys())
            placeholders = "?, " + ", ".join("?" for _ in fields)
            await db.execute(
                f"INSERT INTO user_profiles ({cols}) VALUES ({placeholders})",
                (telegram_id, *fields.values()),
            )
        await db.commit()


async def get_users_by_city(city: str) -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """SELECT u.telegram_id, up.full_name, up.phone, up.rating
               FROM users u
               JOIN user_profiles up ON u.telegram_id = up.telegram_id
               WHERE up.city = ? AND up.is_active = 1 AND u.is_active = 1""",
            (city,),
        ) as cur:
            return [dict(r) for r in await cur.fetchall()]


async def increment_stat(telegram_id: int, field: str):
    allowed = {"confirmed_shifts", "refused_shifts", "ignored_shifts", "total_shifts"}
    if field not in allowed:
        return
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            f"UPDATE user_profiles SET {field} = {field} + 1 WHERE telegram_id = ?",
            (telegram_id,),
        )
        await db.commit()


# ─── Shifts ───────────────────────────────────────────────────────────────────

async def create_shift(
    city: str, date: str, address: str, payment: str, conditions: str,
    main_slots: int, reserve_slots: int,
    reminder_time: str, morning_reminder_time: str,
) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            """INSERT INTO shifts
               (city, date, address, payment, conditions, main_slots, reserve_slots,
                reminder_time, morning_reminder_time)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (city, date, address, payment, conditions, main_slots, reserve_slots,
             reminder_time, morning_reminder_time),
        )
        await db.commit()
        return cur.lastrowid


async def get_shift(shift_id: int) -> dict | None:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM shifts WHERE id = ?", (shift_id,)) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None


async def get_active_shift_by_city(city: str) -> dict | None:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM shifts WHERE city = ? AND status = 'active' ORDER BY created_at DESC LIMIT 1",
            (city,),
        ) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None


async def get_active_shift_by_id(shift_id: int) -> dict | None:
    """
    Возвращает смену по ID независимо от статуса —
    нужно для отчётов, где смена уже 'completed'.
    """
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM shifts WHERE id = ?", (shift_id,)
        ) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None


async def get_all_active_shifts() -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM shifts WHERE status = 'active'") as cur:
            return [dict(r) for r in await cur.fetchall()]


async def update_shift_status(shift_id: int, status: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE shifts SET status = ? WHERE id = ?", (status, shift_id))
        await db.commit()


# ─── Shift members ────────────────────────────────────────────────────────────

async def get_shift_members(shift_id: int) -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """SELECT sm.*, up.full_name, up.phone
               FROM shift_members sm
               LEFT JOIN user_profiles up ON sm.telegram_id = up.telegram_id
               WHERE sm.shift_id = ?
               ORDER BY sm.member_type, sm.position""",
            (shift_id,),
        ) as cur:
            return [dict(r) for r in await cur.fetchall()]


async def get_member_count(shift_id: int, member_type: str) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            """SELECT COUNT(*) FROM shift_members
               WHERE shift_id = ? AND member_type = ? AND status NOT IN ('refused','removed')""",
            (shift_id, member_type),
        ) as cur:
            row = await cur.fetchone()
            return row[0] if row else 0


async def add_shift_member(shift_id: int, telegram_id: int, member_type: str, position: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO shift_members (shift_id, telegram_id, member_type, position) VALUES (?, ?, ?, ?)",
            (shift_id, telegram_id, member_type, position),
        )
        await db.commit()


async def get_user_shift_membership(shift_id: int, telegram_id: int) -> dict | None:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM shift_members WHERE shift_id = ? AND telegram_id = ?",
            (shift_id, telegram_id),
        ) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None


async def update_member_status(shift_id: int, telegram_id: int, status: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE shift_members SET status = ? WHERE shift_id = ? AND telegram_id = ?",
            (status, shift_id, telegram_id),
        )
        await db.commit()


async def set_reminder_sent_at(shift_id: int, telegram_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """UPDATE shift_members SET reminder_sent_at = CURRENT_TIMESTAMP
               WHERE shift_id = ? AND telegram_id = ?""",
            (shift_id, telegram_id),
        )
        await db.commit()


async def set_morning_reminder_sent_at(shift_id: int, telegram_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """UPDATE shift_members SET morning_reminder_sent_at = CURRENT_TIMESTAMP
               WHERE shift_id = ? AND telegram_id = ?""",
            (shift_id, telegram_id),
        )
        await db.commit()


async def get_members_to_ignore_check(shift_id: int) -> list[dict]:
    """Только ОСНОВА: напоминание отправлено 30+ мин назад, не ответили."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """SELECT * FROM shift_members
               WHERE shift_id = ? AND status = 'registered'
               AND member_type = 'main'
               AND reminder_sent_at IS NOT NULL
               AND (julianday('now') - julianday(reminder_sent_at)) * 1440 >= 30""",
            (shift_id,),
        ) as cur:
            return [dict(r) for r in await cur.fetchall()]


async def get_members_to_morning_ignore_check(shift_id: int) -> list[dict]:
    """Только ОСНОВА: утреннее напоминание отправлено 10+ мин назад, не ответили."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """SELECT * FROM shift_members
               WHERE shift_id = ? AND member_type = 'main' AND status = 'registered'
               AND morning_reminder_sent_at IS NOT NULL
               AND (julianday('now') - julianday(morning_reminder_sent_at)) * 1440 >= 10""",
            (shift_id,),
        ) as cur:
            return [dict(r) for r in await cur.fetchall()]


async def get_first_reserve(shift_id: int) -> dict | None:
    """Первый в резерве со статусом registered или confirmed."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """SELECT * FROM shift_members
               WHERE shift_id = ? AND member_type = 'reserve'
               AND status IN ('registered', 'confirmed')
               ORDER BY position ASC LIMIT 1""",
            (shift_id,),
        ) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None


async def promote_to_main(shift_id: int, telegram_id: int, new_position: int):
    """Перевести из резерва в основной состав."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """UPDATE shift_members
               SET member_type = 'main', position = ?, status = 'registered',
                   reminder_sent_at = NULL, morning_reminder_sent_at = NULL
               WHERE shift_id = ? AND telegram_id = ?""",
            (new_position, shift_id, telegram_id),
        )
        await db.commit()


# ─── Блок 6: shift_results ────────────────────────────────────────────────────

async def save_shift_result(
    shift_id: int,
    telegram_id: int,
    worked: bool,
    decline_reason: str = None,
):
    """
    Сохраняет результат смены.
    Если запись уже есть (ON CONFLICT) — обновляет.
    Также обновляет статус в shift_members и счётчики в user_profiles.
    """
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """INSERT INTO shift_results (shift_id, telegram_id, worked, decline_reason)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(shift_id, telegram_id) DO UPDATE SET
                   worked = excluded.worked,
                   decline_reason = excluded.decline_reason""",
            (shift_id, telegram_id, 1 if worked else 0, decline_reason),
        )

        # Статус участника
        new_status = "worked" if worked else "removed"
        await db.execute(
            "UPDATE shift_members SET status = ? WHERE shift_id = ? AND telegram_id = ?",
            (new_status, shift_id, telegram_id),
        )

        # Счётчики профиля
        if worked:
            await db.execute(
                """UPDATE user_profiles
                   SET total_shifts = total_shifts + 1,
                       confirmed_shifts = confirmed_shifts + 1
                   WHERE telegram_id = ?""",
                (telegram_id,),
            )

        await db.commit()


async def get_shift_result(shift_id: int, telegram_id: int) -> dict | None:
    """Проверяет, уже ли сотрудник отправил отчёт по смене."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM shift_results WHERE shift_id = ? AND telegram_id = ?",
            (shift_id, telegram_id),
        ) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None


async def get_shift_members_for_report(shift_id: int) -> list[dict]:
    """
    Участники смены, которым нужно отправить форму отчёта.
    Исключаем тех, кто refused или removed ДО завершения смены.
    """
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """SELECT sm.telegram_id, sm.member_type, up.full_name
               FROM shift_members sm
               JOIN user_profiles up ON sm.telegram_id = up.telegram_id
               WHERE sm.shift_id = ?
                 AND sm.status NOT IN ('refused', 'removed')""",
            (shift_id,),
        ) as cur:
            return [dict(r) for r in await cur.fetchall()]


async def get_shift_results_full(shift_id: int) -> tuple[list, list, list]:
    """
    Полный итоговый отчёт по смене для админа.
    Возвращает: (отработали, не вышли, не ответили)
    """
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row

        async with db.execute(
            """SELECT sr.telegram_id, up.full_name, up.phone, sm.member_type
               FROM shift_results sr
               JOIN user_profiles up ON sr.telegram_id = up.telegram_id
               JOIN shift_members sm
                 ON sm.shift_id = sr.shift_id AND sm.telegram_id = sr.telegram_id
               WHERE sr.shift_id = ? AND sr.worked = 1""",
            (shift_id,),
        ) as cur:
            worked = [dict(r) for r in await cur.fetchall()]

        async with db.execute(
            """SELECT sr.telegram_id, up.full_name, up.phone,
                      sr.decline_reason, sm.member_type
               FROM shift_results sr
               JOIN user_profiles up ON sr.telegram_id = up.telegram_id
               JOIN shift_members sm
                 ON sm.shift_id = sr.shift_id AND sm.telegram_id = sr.telegram_id
               WHERE sr.shift_id = ? AND sr.worked = 0""",
            (shift_id,),
        ) as cur:
            not_worked = [dict(r) for r in await cur.fetchall()]

        # Кто в shift_members есть, но в shift_results ещё нет
        async with db.execute(
            """SELECT sm.telegram_id, up.full_name, up.phone, sm.member_type
               FROM shift_members sm
               JOIN user_profiles up ON sm.telegram_id = up.telegram_id
               WHERE sm.shift_id = ?
                 AND sm.status NOT IN ('removed', 'refused')
                 AND sm.telegram_id NOT IN (
                     SELECT telegram_id FROM shift_results WHERE shift_id = ?
                 )""",
            (shift_id, shift_id),
        ) as cur:
            no_response = [dict(r) for r in await cur.fetchall()]

    return worked, not_worked, no_response


# ─── Блок 7: рейтинг, блокировка, разблокировка ──────────────────────────────

async def increment_consecutive_failures(telegram_id: int):
    """Отказ или игнор — +1 к счётчику подряд идущих провалов."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """UPDATE user_profiles
               SET consecutive_failures = consecutive_failures + 1
               WHERE telegram_id = ?""",
            (telegram_id,),
        )
        await db.commit()


async def reset_consecutive_failures(telegram_id: int):
    """Подтверждение или отработал — сбрасываем счётчик."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE user_profiles SET consecutive_failures = 0 WHERE telegram_id = ?",
            (telegram_id,),
        )
        await db.commit()


async def check_and_block_if_needed(telegram_id: int) -> bool:
    """
    Проверяет consecutive_failures.
    Если >= 4 — блокирует пользователя (is_active = 0 в обеих таблицах).
    Возвращает True если заблокировал.
    """
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT consecutive_failures FROM user_profiles WHERE telegram_id = ?",
            (telegram_id,),
        ) as cur:
            row = await cur.fetchone()

        if not row or row["consecutive_failures"] < 4:
            return False

        await db.execute(
            "UPDATE user_profiles SET is_active = 0 WHERE telegram_id = ?",
            (telegram_id,),
        )
        await db.execute(
            "UPDATE users SET is_active = 0 WHERE telegram_id = ?",
            (telegram_id,),
        )
        await db.commit()
        return True


async def unblock_user(telegram_id: int):
    """Разблокировка — сброс is_active и счётчика."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """UPDATE user_profiles
               SET is_active = 1, consecutive_failures = 0
               WHERE telegram_id = ?""",
            (telegram_id,),
        )
        await db.execute(
            "UPDATE users SET is_active = 1 WHERE telegram_id = ?",
            (telegram_id,),
        )
        await db.commit()


async def create_unblock_request(telegram_id: int, city: str, message: str) -> bool:
    """
    Создаёт запрос на разблокировку.
    Возвращает False если pending-запрос уже есть.
    """
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT id FROM unblock_requests WHERE telegram_id = ? AND status = 'pending'",
            (telegram_id,),
        ) as cur:
            if await cur.fetchone():
                return False

        await db.execute(
            "INSERT INTO unblock_requests (telegram_id, city, message) VALUES (?, ?, ?)",
            (telegram_id, city, message),
        )
        await db.commit()
        return True


async def get_pending_unblock_requests() -> list[dict]:
    """Все pending-запросы на разблокировку с данными профиля."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """SELECT ur.*, up.full_name, up.phone, up.refused_shifts,
                      up.ignored_shifts, up.consecutive_failures
               FROM unblock_requests ur
               LEFT JOIN user_profiles up ON ur.telegram_id = up.telegram_id
               WHERE ur.status = 'pending'
               ORDER BY ur.created_at ASC""",
        ) as cur:
            return [dict(r) for r in await cur.fetchall()]


async def resolve_unblock_request(request_id: int, status: str):
    """status = 'approved' | 'denied'"""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE unblock_requests SET status = ? WHERE id = ?",
            (status, request_id),
        )
        await db.commit()


async def get_unblock_request(request_id: int) -> dict | None:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM unblock_requests WHERE id = ?", (request_id,)
        ) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None
from contextlib import asynccontextmanager

@asynccontextmanager
async def get_db():
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        yield db