import io
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment
from database import get_db

HEADER_FILL = PatternFill("solid", fgColor="4F81BD")
HEADER_FONT = Font(color="FFFFFF", bold=True)


def _write_header_row(ws, headers: list[str], row: int):
    for col, h in enumerate(headers, 1):
        cell = ws.cell(row, col, h)
        cell.fill = HEADER_FILL
        cell.font = HEADER_FONT
        cell.alignment = Alignment(horizontal="center")


def _autowidth(ws):
    for col in ws.columns:
        try:
            width = max(len(str(cell.value or "")) for cell in col) + 4
            ws.column_dimensions[col[0].column_letter].width = width
        except AttributeError:
            pass

async def excel_city_base(city: str) -> io.BytesIO:
    async with get_db() as db:
        cursor = await db.execute(
            """
            SELECT up.full_name, up.phone, u.username,
                   up.age, up.rating, up.total_shifts, up.confirmed_shifts,
                   up.refused_shifts, up.ignored_shifts,
                   up.consecutive_failures, up.is_active
            FROM user_profiles up
            JOIN users u ON u.telegram_id = up.telegram_id
            WHERE up.city = ?
            ORDER BY up.full_name
            """,
            (city,)
        )
        rows = await cursor.fetchall()

    wb = Workbook()
    ws = wb.active
    ws.title = city

    ws.append([f"База сотрудников — {city}"])
    ws["A1"].font = Font(bold=True, size=14)
    ws.merge_cells("A1:K1")
    ws["A1"].alignment = Alignment(horizontal="center")
    ws.append([])

    headers = [
        "ФИО", "Телефон", "Telegram", "Возраст", "Рейтинг",
        "Всего смен", "Подтверждено", "Отказов", "Игноров",
        "Подряд провалов", "Активен"
    ]
    _write_header_row(ws, headers, 3)

    for r in rows:
        ws.append([
            r[0],
            r[1],
            f"@{r[2]}" if r[2] else "—",
            r[3],
            r[4],
            r[5],
            r[6],
            r[7],
            r[8],
            r[9],
            "✅" if r[10] else "🚫",
        ])

    _autowidth(ws)

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf


async def excel_shift_report(shift_id: int) -> io.BytesIO:
    async with get_db() as db:
        cursor = await db.execute("SELECT * FROM shifts WHERE id = ?", (shift_id,))
        shift = await cursor.fetchone()

        cursor = await db.execute(
            """
            SELECT up.full_name, up.phone, up.rating,
                   sm.member_type, sm.status, sm.position,
                   sr.worked, sr.decline_reason
            FROM shift_members sm
            JOIN user_profiles up ON sm.telegram_id = up.telegram_id
            LEFT JOIN shift_results sr
                ON sr.shift_id = sm.shift_id AND sr.telegram_id = sm.telegram_id
            WHERE sm.shift_id = ?
            ORDER BY sm.member_type, sm.position
            """,
            (shift_id,)
        )
        rows = await cursor.fetchall()

    wb = Workbook()
    ws = wb.active
    ws.title = f"Смена {shift_id}"

    # shift: id=0, city=1, date=2, address=3
    title = f"Смена #{shift_id} | {shift[1]} | {shift[2]} | {shift[3]}"
    ws.append([title])
    ws["A1"].font = Font(bold=True, size=12)
    ws.merge_cells("A1:H1")
    ws["A1"].alignment = Alignment(horizontal="center")
    ws.append([])

    headers = [
        "ФИО", "Телефон", "Рейтинг",
        "Тип", "Статус", "Позиция", "Отработал", "Причина"
    ]
    _write_header_row(ws, headers, 3)

    for r in rows:
        worked = "—"
        if r[6] == 1:
            worked = "Да"
        elif r[6] == 0:
            worked = "Нет"

        ws.append([
            r[0],
            r[1],
            r[2],
            "Основа" if r[3] == "main" else "Резерв",
            r[4],
            r[5],
            worked,
            r[7] or "",
        ])

    _autowidth(ws)

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf