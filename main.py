import asyncio
import aiosqlite
import re
import calendar
from datetime import datetime, date, timedelta, timezone
from aiogram import Bot, Dispatcher
from aiogram.types import Message
from aiogram.fsm.storage.memory import MemoryStorage

# --- Налаштування ---
TOKEN = "8274894041:AAGEJSRDxWHbEriVnneByDtZtK_qu-vmflU"
GROUP_ID = -1003083789411
DAILY_LIMIT = 1000
TIMEZONE_OFFSET = 2  # UTC+2

bot = Bot(token=TOKEN)
dp = Dispatcher(storage=MemoryStorage())

DB_PATH = "budget.db"

last_amount = 0
last_comment = ""


# --- Ініціалізація БД ---
async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS budget (
                day TEXT PRIMARY KEY,
                expenses INTEGER DEFAULT 0,
                savings INTEGER DEFAULT 0,
                overspend INTEGER DEFAULT 0,
                month_expenses INTEGER DEFAULT 0
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS expenses_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                day TEXT,
                amount INTEGER,
                comment TEXT,
                created_at TEXT
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS snapshots (
                day TEXT PRIMARY KEY,
                expenses INTEGER,
                savings INTEGER,
                overspend INTEGER,
                month_expenses INTEGER
            )
        """)
        await db.commit()


# --- Допоміжні: бюджетний період (з 5-го по 4-е) ---
def get_budget_period(dt: date):
    """Повертає (start_date, end_date) бюджетного періоду (з 5-го по 4-е включно)."""
    if dt.day >= 5:
        start = date(dt.year, dt.month, 5)
        if dt.month == 12:
            end = date(dt.year + 1, 1, 4)
        else:
            end = date(dt.year, dt.month + 1, 4)
    else:
        if dt.month == 1:
            start = date(dt.year - 1, 12, 5)
        else:
            start = date(dt.year, dt.month - 1, 5)
        end = date(dt.year, dt.month, 4)
    return start, end


# --- Отримати всі дані дня ---
async def get_day_data(day: str):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT expenses, savings, overspend, month_expenses FROM budget WHERE day=?", (day,)
        ) as cur:
            row = await cur.fetchone()
            if row:
                return row[0], row[1], row[2], row[3]
            else:
                # створюємо пустий рядок
                await db.execute(
                    "INSERT INTO budget (day, expenses, savings, overspend, month_expenses) VALUES (?, 0, 0, 0, 0)",
                    (day,),
                )
                await db.commit()
                return 0, 0, 0, 0


# --- Оновити запис дня ---
async def update_day(day: str, expenses: int, savings: int, overspend: int, month_expenses: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            INSERT INTO budget (day, expenses, savings, overspend, month_expenses)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(day) DO UPDATE SET
                expenses = excluded.expenses,
                savings = excluded.savings,
                overspend = excluded.overspend,
                month_expenses = excluded.month_expenses
            """,
            (day, expenses, savings, overspend, month_expenses),
        )
        await db.commit()


# --- Snapshot helpers ---
async def save_snapshot(day: str, expenses: int, savings: int, overspend: int, month_expenses: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            INSERT INTO snapshots (day, expenses, savings, overspend, month_expenses)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(day) DO UPDATE SET
                expenses=excluded.expenses,
                savings=excluded.savings,
                overspend=excluded.overspend,
                month_expenses=excluded.month_expenses
            """,
            (day, expenses, savings, overspend, month_expenses),
        )
        await db.commit()


async def get_snapshot(day: str):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT expenses, savings, overspend, month_expenses FROM snapshots WHERE day=?", (day,)) as cur:
            r = await cur.fetchone()
            return r if r else None


# --- Порахувати всі витрати за бюджетний період (з 5-го по 4-е) ---
async def get_month_expenses_for_date(dt: date):
    start, end = get_budget_period(dt)
    start_s = start.strftime("%Y-%m-%d")
    end_s = (end - timedelta(days=0)).strftime("%Y-%m-%d")  # end має бути включно
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT SUM(expenses) FROM budget WHERE day BETWEEN ? AND ?", (start_s, end_s)) as cur:
            row = await cur.fetchone()
            return row[0] if row and row[0] else 0, start, end


# --- Логи витрат ---
async def log_expense(day: str, amount: int, comment: str):
    created_at = datetime.utcnow().isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("INSERT INTO expenses_log (day, amount, comment, created_at) VALUES (?, ?, ?, ?)",
                         (day, amount, comment, created_at))
        await db.commit()


async def sum_expenses_log_for_day(day: str):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT SUM(amount) FROM expenses_log WHERE day=?", (day,)) as cur:
            row = await cur.fetchone()
            return row[0] if row and row[0] else 0


# --- Скасувати останню витрату сьогодні (по черзі) ---
async def cancel_last_expense(today_str: str):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT id, amount FROM expenses_log WHERE day=? ORDER BY id DESC LIMIT 1", (today_str,)) as cur:
            row = await cur.fetchone()
            if not row:
                return None
            exp_id, amount = row

        # видаляємо лог
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("DELETE FROM expenses_log WHERE id=?", (exp_id,))
            await db.commit()

    # після видалення перераховуємо expenses для сьогодні
    new_expenses = await sum_expenses_log_for_day(today_str)

    # беремо поточні savings і overspend з budget (якщо overspend == 0 — звернемося до snapshot або вчора)
    expenses_before, savings, overspend, month_expenses = await get_day_data(today_str)

    # effective overspend — якщо в budget сьогодні 0, беремо snapshot або вчора як фолбек
    effective_overspend = overspend
    if effective_overspend == 0:
        snap = await get_snapshot(today_str)
        if snap:
            effective_overspend = snap[2]
        else:
            # фолбек — вчора
            dt = datetime.strptime(today_str, "%Y-%m-%d").date()
            y_str = (dt - timedelta(days=1)).strftime("%Y-%m-%d")
            _, _, y_overs, _ = await get_day_data(y_str)
            effective_overspend = y_overs

    # оновлюємо month_expenses заново (перерахунок по бюджету в періоді)
    today_dt = datetime.strptime(today_str, "%Y-%m-%d").date()
    # тимчасово оновлюємо today.expenses, щоб при сумуванні в budget врахувався новий today
    await update_day(today_str, new_expenses, savings, effective_overspend, 0)
    total_month, start, end = await get_month_expenses_for_date(today_dt)
    # записуємо коректний month_expenses
    await update_day(today_str, new_expenses, savings, effective_overspend, total_month)

    return amount, new_expenses, savings, effective_overspend, total_month


# --- Відновити стан на початку дня (snapshot) або взяти вчорашній стан ---
async def revert_to_snapshot_or_yesterday(today_str: str):
    snap = await get_snapshot(today_str)
    if snap:
        s_expenses, s_savings, s_overspend, s_month = snap
        # видаляємо сьогоднішні логи
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("DELETE FROM expenses_log WHERE day=?", (today_str,))
            await db.commit()
        # записуємо snapshot в budget
        await update_day(today_str, s_expenses, s_savings, s_overspend, s_month)
        return s_expenses, s_savings, s_overspend, s_month

    # фолбек: беремо вчорашній рядок
    today_dt = datetime.strptime(today_str, "%Y-%m-%d").date()
    yesterday_dt = today_dt - timedelta(days=1)
    y_str = yesterday_dt.strftime("%Y-%m-%d")
    y_expenses, y_savings, y_overspend, y_month = await get_day_data(y_str)

    # видаляємо сьогоднішні логи
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM expenses_log WHERE day=?", (today_str,))
        await db.commit()
    # встановлюємо сьогодні як вчорашній стан
    await update_day(today_str, y_expenses, y_savings, y_overspend, y_month)
    return y_expenses, y_savings, y_overspend, y_month


# --- Хендлер повідомлень ---
@dp.message()
async def handle_message(message: Message):
    global last_amount, last_comment

    if message.chat.id != GROUP_ID:
        return

    text_msg = message.text.strip()

    # --- /інфо ---
    if text_msg == "/інфо":
        help_text = (
            "📌 <b>Команди:</b>\n\n"
            "🔄 /ч – скасувати <u>останню витрату</u> (по черзі, тільки сьогодні).\n"
            "🧹 /д – повернути стан <u>на початок дня</u> (snapshot після вчорашнього підсумку).\n"
            "📊 /звіт – показати актуальний стан (з урахуванням перенесених витрат, бюджетний період з 5 по 4).\n"
            "ℹ️ /інфо – список команд."
        )
        await message.reply(help_text, parse_mode="HTML")
        return

    # --- /звіт ---
    if text_msg == "/звіт":
        now = datetime.now(timezone.utc) + timedelta(hours=TIMEZONE_OFFSET)
        today_str = now.strftime("%Y-%m-%d")
        expenses, savings, overspend, month_expenses = await get_day_data(today_str)

        total_month, start, end = await get_month_expenses_for_date(now.date())
        days_in_period = (end - start).days + 1
        month_limit = DAILY_LIMIT * days_in_period
        month_left = max(0, month_limit - total_month)

        effective_overspend = overspend
        if effective_overspend == 0:
            snap = await get_snapshot(today_str)
            if snap:
                effective_overspend = snap[2]
            else:
                yesterday_str = (now - timedelta(days=1)).strftime("%Y-%m-%d")
                _, _, y_overs, _ = await get_day_data(yesterday_str)
                effective_overspend = y_overs

        available_budget = DAILY_LIMIT - effective_overspend if effective_overspend > 0 else DAILY_LIMIT
        balance = max(0, available_budget - expenses)

        text = (
            f"📊 <b>Звіт за {today_str}</b>\n\n"
            f"🔴 Витрачено сьогодні: {expenses} грн\n"
            f"📉 Залишок на сьогодні: {balance} грн (з {available_budget} грн)\n"
            f"💰 Заощадження: {savings} грн\n"
            f"🗓️ Всього за період {start.strftime('%d.%m')} - {(end - timedelta(days=0)).strftime('%d.%m')}: {total_month} грн\n"
            f"📌 Залишок на період: {month_left} грн"
        )
        if effective_overspend > 0:
            text += f"\n⚠️ Перенесено борг: {effective_overspend} грн"

        await message.reply(text, parse_mode="HTML")
        return

    # --- /д: повернути стан на початок дня ---
    if text_msg == "/д":
        now = datetime.now(timezone.utc) + timedelta(hours=TIMEZONE_OFFSET)
        today_str = now.strftime("%Y-%m-%d")

        restored_exp, restored_sav, restored_over, restored_month = await revert_to_snapshot_or_yesterday(today_str)

        available_budget = DAILY_LIMIT - restored_over if restored_over > 0 else DAILY_LIMIT
        balance = max(0, available_budget - restored_exp)

        text = (
            f"🧹 Повернуто стан на початок дня ({today_str}).\n\n"
            f"🔴 Витрачено: {restored_exp} грн\n"
            f"📉 Залишок: {balance} грн (з {available_budget} грн)\n"
            f"💰 Заощадження: {restored_sav} грн"
        )
        if restored_over > 0:
            text += f"\n⚠️ Перенесено борг: {restored_over} грн"

        await message.reply(text, parse_mode="HTML")
        return

    # --- /ч: відмінити останню витрату (по черзі) ---
    if text_msg == "/ч":
        now = datetime.now(timezone.utc) + timedelta(hours=TIMEZONE_OFFSET)
        today_str = now.strftime("%Y-%m-%d")

        res = await cancel_last_expense(today_str)
        if not res:
            await message.reply("Немає витрат для скасування (сьогодні).")
            return

        amount, new_expenses, savings, effective_overspend, total_month = res

        available_budget = DAILY_LIMIT - effective_overspend if effective_overspend > 0 else DAILY_LIMIT
        balance = max(0, available_budget - new_expenses)

        text = (
            f"❌ Скасовано витрату: {amount} грн\n"
            f"🔴 Витрачено: {new_expenses} грн\n"
            f"📉 Залишок: {balance} грн (з {available_budget} грн)\n"
            f"💰 Заощадження: {savings} грн\n"
            f"🗓️ Всього за період: {total_month} грн"
        )
        if effective_overspend > 0:
            text += f"\n⚠️ Перенесено борг: {effective_overspend} грн"

        await message.reply(text, parse_mode="HTML")
        return

    # --- Додавання витрати ---
    match = re.match(r"^([\d\.,]+)(?:\s*(.*))?$", text_msg)
    if not match:
        return

    raw_amount = match.group(1).replace(",", ".")
    try:
        amount = int(float(raw_amount))
    except ValueError:
        return
    comment = match.group(2) if match.group(2) else ""

    now = datetime.now(timezone.utc) + timedelta(hours=TIMEZONE_OFFSET)
    today_str = now.strftime("%Y-%m-%d")

    # лог витрати
    await log_expense(today_str, amount, comment)

    # перерахунок today's expenses з логів (щоб уникнути розбіжностей)
    new_expenses = await sum_expenses_log_for_day(today_str)

    # беремо поточний savings та overspend з budget; якщо overspend == 0, беремо snapshot або вчора
    _, savings, overspend, _ = await get_day_data(today_str)
    effective_overspend = overspend
    if effective_overspend == 0:
        snap = await get_snapshot(today_str)
        if snap:
            effective_overspend = snap[2]
        else:
            yesterday_str = (now - timedelta(days=1)).strftime("%Y-%m-%d")
            _, _, y_overs, _ = await get_day_data(yesterday_str)
            effective_overspend = y_overs

    available_budget = DAILY_LIMIT - effective_overspend if effective_overspend > 0 else DAILY_LIMIT
    balance = max(0, available_budget - new_expenses)

    # оновлюємо month_expenses (перерахунок по бюджетному періоду)
    # спочатку тимчасово оновимо today's expenses, щоб сумування врахувало їх
    await update_day(today_str, new_expenses, savings, effective_overspend, 0)
    total_month, start, end = await get_month_expenses_for_date(now.date())
    await update_day(today_str, new_expenses, savings, effective_overspend, total_month)

    # зберігаємо last_amount для сумісності (але основою для /ч є лог)
    last_amount = amount
    last_comment = comment

    days_in_period = (end - start).days + 1
    month_limit = DAILY_LIMIT * days_in_period
    month_left = max(0, month_limit - total_month)

    text = f"🔴 Додано витрати: {amount} грн"
    if comment:
        text += f"  <i>({comment})</i>"
    text += (
        f"\n\n🔴 Витрачено: {new_expenses} грн\n"
        f"📉 Залишок на сьогодні: {balance} грн (з {available_budget} грн)\n"
        f"💰 Заощадження: {savings} грн\n"
        f"🗓️ Всього за період {start.strftime('%d.%m')} - {(end - timedelta(days=0)).strftime('%d.%m')}: {total_month} грн\n"
        f"📌 Залишок на період: {month_left} грн"
    )
    if effective_overspend > 0:
        text += f"\n⚠️ Перенесено борг: {effective_overspend} грн"

    await message.reply(text, parse_mode="HTML")


# --- Автозвіт о 23:00 (збереження snapshot для next_day) ---
async def daily_summary():
    local_tz = timezone(timedelta(hours=TIMEZONE_OFFSET))
    await init_db()

    while True:
        now = datetime.now(local_tz)
        target = datetime.combine(now.date(), datetime.min.time(), tzinfo=local_tz) + timedelta(hours=23)
        if now > target:
            target += timedelta(days=1)
        wait_seconds = (target - now).total_seconds()
        await asyncio.sleep(wait_seconds)

        today = datetime.now(local_tz)
        today_str = today.strftime("%Y-%m-%d")
        next_date = today + timedelta(days=1)
        next_day = next_date.strftime("%Y-%m-%d")

        # today's expenses перерахунок з логів (точніше ніж з budget)
        expenses = await sum_expenses_log_for_day(today_str)
        # отримуємо поточні savings та overspend
        _, savings, overspend, month_expenses = await get_day_data(today_str)

        # effective overspend — це перенесення з початку дня (overspend в budget)
        effective_overspend = overspend
        if effective_overspend == 0:
            snap = await get_snapshot(today_str)
            if snap:
                effective_overspend = snap[2]
            else:
                # фолбек - вчорашній
                yesterday_str = (today - timedelta(days=1)).strftime("%Y-%m-%d")
                _, _, y_overs, _ = await get_day_data(yesterday_str)
                effective_overspend = y_overs

        available_budget = DAILY_LIMIT - effective_overspend if effective_overspend > 0 else DAILY_LIMIT

        if expenses <= available_budget:
            balance = available_budget - expenses
            savings += balance
            new_overspend = 0
        else:
            new_overspend = expenses - available_budget

        # перерахунок month_expenses (включно із сьогодні)
        total_month, start, end = await get_month_expenses_for_date(today.date())

        # Перевірка: якщо завтра починається новий бюджетний період (5-е число) — скинути savings і month_expenses
        if next_date.day == 5:
            # новий бюджетний період
            await update_day(next_day, 0, 0, 0, 0)
            await save_snapshot(next_day, 0, 0, 0, 0)
            month_text = "✅ Бюджетний період починається завтра — дані скинуто."
        else:
            # записуємо стан на початок наступного дня
            await update_day(next_day, 0, savings, new_overspend, total_month)
            await save_snapshot(next_day, 0, savings, new_overspend, total_month)
            days_in_period = (end - start).days + 1
            month_limit = DAILY_LIMIT * days_in_period
            month_left = max(0, month_limit - total_month)
            month_text = f"🗓️ Всього за період {start.strftime('%d.%m')} - {(end - timedelta(days=0)).strftime('%d.%m')}: {total_month} грн\n📌 Залишок на період: {month_left} грн"

        # формуємо повідомлення
        text = (
            f"📊 <b>Підсумок дня ({today_str})</b>\n"
            f"🔴 Витрачено: {expenses} грн\n"
            f"📉 Залишок на сьогодні: {max(0, available_budget - expenses)} грн\n"
            f"💰 Заощадження: {savings} грн\n"
            f"{month_text}"
        )
        if new_overspend > 0:
            text += f"\n⚠️ Перевитрата: {new_overspend} грн (перенесено на завтра)"

        try:
            await bot.send_message(GROUP_ID, text, parse_mode="HTML")
        except Exception as e:
            print(f"Помилка надсилання: {e}")


# --- Головна ---
async def main():
    await init_db()
    asyncio.create_task(daily_summary())
    while True:
        try:
            await dp.start_polling(bot)
        except Exception as e:
            print(f"Помилка polling: {e}")
            await asyncio.sleep(5)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        print("Зупинка контейнера...")
