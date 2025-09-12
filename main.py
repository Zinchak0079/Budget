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
        # основна таблиця (стан дня)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS budget (
                day TEXT PRIMARY KEY,
                expenses INTEGER DEFAULT 0,
                savings INTEGER DEFAULT 0,
                overspend INTEGER DEFAULT 0,
                month_expenses INTEGER DEFAULT 0
            )
        """)
        # лог усіх витрат (щоб /ч міг відміняти по черзі)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS expenses_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                day TEXT,
                amount INTEGER,
                comment TEXT,
                created_at TEXT
            )
        """)
        # snapshot стану на початок дня (те, що записує daily_summary для next_day)
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


# --- Допоміжні: період бюджетного місяця (з 5-го по 4-е) ---
def get_budget_period(dt: date):
    """Повертає (start_date, end_date) бюджетного періоду (з 5-го по 4-е)."""
    if dt.day >= 5:
        start = date(dt.year, dt.month, 5)
        # кінець = 4 число наступного місяця
        if dt.month == 12:
            end = date(dt.year + 1, 1, 4)
        else:
            end = date(dt.year, dt.month + 1, 4)
    else:
        # період починався 5-го попереднього місяця
        if dt.month == 1:
            start = date(dt.year - 1, 12, 5)
        else:
            start = date(dt.year, dt.month - 1, 5)
        end = date(dt.year, dt.month, 4)
    return start, end


# --- Отримати дані дня ---
async def get_day_data(day: str):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT expenses, savings, overspend, month_expenses FROM budget WHERE day=?",
            (day,),
        ) as cursor:
            row = await cursor.fetchone()
            if row:
                return row[0], row[1], row[2], row[3]
            else:
                # створюємо порожній запис (потрібно, щоб записи завжди були)
                await db.execute(
                    "INSERT INTO budget (day, expenses, savings, overspend, month_expenses) VALUES (?, 0, 0, 0, 0)",
                    (day,),
                )
                await db.commit()
                return 0, 0, 0, 0


# --- Оновити дані дня ---
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


# --- Порахувати всі витрати за бюджетний місяць (з 5-го по 4-е) ---
async def get_month_expenses_for_date(dt: date):
    start, end = get_budget_period(dt)
    start_s = start.strftime("%Y-%m-%d")
    end_s = (end - timedelta(days=0)).strftime("%Y-%m-%d")  # end is inclusive
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT SUM(expenses) FROM budget WHERE day BETWEEN ? AND ?", (start_s, end_s)) as cur:
            row = await cur.fetchone()
            return row[0] if row and row[0] else 0, start, end


# --- Лог витрат (додавання/відображення) ---
async def log_expense(day: str, amount: int, comment: str):
    created_at = datetime.utcnow().isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("INSERT INTO expenses_log (day, amount, comment, created_at) VALUES (?, ?, ?, ?)",
                         (day, amount, comment, created_at))
        await db.commit()


# --- Скасувати останню витрату (видаляє останній рядок з expenses_log для дня) ---
async def cancel_last_expense(today_str: str):
    async with aiosqlite.connect(DB_PATH) as db:
        # беремо останню витрату для today
        async with db.execute("SELECT id, amount FROM expenses_log WHERE day=? ORDER BY id DESC LIMIT 1", (today_str,)) as cur:
            row = await cur.fetchone()
            if not row:
                return None
            exp_id, amount = row

        # видаляємо з логів
        await db.execute("DELETE FROM expenses_log WHERE id=?", (exp_id,))

        # оновлюємо таблицю budget (зменшуємо expenses та month_expenses)
        expenses, savings, overspend, month_expenses = await get_day_data(today_str)
        new_expenses = max(0, expenses - amount)
        new_month_expenses = max(0, month_expenses - amount)
        await update_day(today_str, new_expenses, savings, overspend, new_month_expenses)
        await db.commit()
        return amount, new_expenses, savings, overspend, new_month_expenses


# --- Відмінити всі витрати сьогодні (повернути стан, встановлений вночі) ---
async def revert_to_snapshot_or_yesterday(today_str: str):
    """
    Повертає сьогоднішній день до snapshot (якщо є) — тобто до стану, який записав daily_summary.
    Якщо snapshot відсутній — повертає стан з yesterday_row (фолбек).
    Повертає кортеж (restored_expenses, restored_savings, restored_overspend, restored_month_expenses).
    """
    # 1) спроба snapshot
    snap = await get_snapshot(today_str)
    if snap:
        s_expenses, s_savings, s_overspend, s_month = snap
        # видаляємо всі today's expenses_log
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("DELETE FROM expenses_log WHERE day=?", (today_str,))
            await db.commit()
        # записуємо snapshot в budget
        await update_day(today_str, s_expenses, s_savings, s_overspend, s_month)
        return s_expenses, s_savings, s_overspend, s_month

    # 2) фолбек — беремо вчорашній рядок
    today_dt = datetime.strptime(today_str, "%Y-%m-%d").date()
    yesterday_dt = today_dt - timedelta(days=1)
    y_str = yesterday_dt.strftime("%Y-%m-%d")
    y_expenses, y_savings, y_overspend, y_month = await get_day_data(y_str)
    # видаляємо today's logs
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM expenses_log WHERE day=?", (today_str,))
        await db.commit()
    # записуємо в сьогоднішній рядок стан вчора
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
            "🔄 /ч – скасувати <u>останню витрату</u> (по черзі).\n"
            "🧹 /д – повернути стан <u>на початок дня</u> (стан після вчорашнього підсумку).\n"
            "📊 /звіт – показати актуальний стан (з урахуванням перенесених витрат, бюджетний період з 5 по 4).\n"
            "ℹ️ /інфо – список команд."
        )
        asyncio.create_task(message.reply(help_text, parse_mode="HTML"))
        return

    # --- /звіт ---
    if text_msg == "/звіт":
        now = datetime.now(timezone.utc) + timedelta(hours=TIMEZONE_OFFSET)
        today_str = now.strftime("%Y-%m-%d")
        expenses, savings, overspend, month_expenses = await get_day_data(today_str)

        total_month, start, end = await get_month_expenses_for_date(now.date())
        days_in_period = (end - start).days + 1
        month_limit = DAILY_LIMIT * days_in_period
        month_left = max(0, month_limit - total_month - 0)  # total_month already sums stored month_expenses across days

        # effective available budget for today — беремо overspend з today-record (це перенесення з вчора)
        effective_overspend = overspend
        available_budget = DAILY_LIMIT - effective_overspend if effective_overspend > 0 else DAILY_LIMIT
        balance = max(0, available_budget - expenses)

        text = (
            f"📊 <b>Звіт за {today_str}</b>\n\n"
            f"🔴 Витрачено сьогодні: {expenses} грн\n"
            f"📉 Залишок на сьогодні: {balance} грн (з {available_budget})\n"
            f"💰 Заощадження: {savings} грн\n"
            f"🗓️ Всього за період {start.strftime('%d.%m')} - {(end - timedelta(days=0)).strftime('%d.%m')}: {total_month} грн\n"
            f"📌 Залишок на період: {month_left} грн"
        )
        if effective_overspend > 0:
            text += f"\n⚠️ Перенесено борг: {effective_overspend} грн"

        await message.reply(text, parse_mode="HTML")
        return

    # --- /д: повернути стан на початок дня (snapshot або вчора) ---
    if text_msg == "/д":
        now = datetime.now(timezone.utc) + timedelta(hours=TIMEZONE_OFFSET)
        today_str = now.strftime("%Y-%m-%d")

        restored_exp, restored_sav, restored_over, restored_month = await revert_to_snapshot_or_yesterday(today_str)

        available_budget = DAILY_LIMIT - restored_over if restored_over > 0 else DAILY_LIMIT
        balance = max(0, available_budget - restored_exp)

        text = (
            f"🧹 Повернуто стан на початок дня ({today_str}).\n\n"
            f"🔴 Витрачено: {restored_exp} грн\n"
            f"📉 Залишок: {balance} грн (з {available_budget})\n"
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
            await message.reply("Немає витрат для скасування.")
            return
        amount, new_expenses, savings, overspend, new_month_expenses = res

        # effective overspend: беремо з сьогоднішнього запису (це перенесення з учора),
        # якщо 0, пробуємо snapshot або вчора як фолбек
        effective_overspend = overspend
        if effective_overspend == 0:
            snap = await get_snapshot(today_str)
            if snap:
                effective_overspend = snap[2]
            else:
                # фолбек вчора
                yesterday_str = (now - timedelta(days=1)).strftime("%Y-%m-%d")
                _, _, y_overs, _ = await get_day_data(yesterday_str)
                effective_overspend = y_overs

        available_budget = DAILY_LIMIT - effective_overspend if effective_overspend > 0 else DAILY_LIMIT
        balance = max(0, available_budget - new_expenses)

        text = (
            f"❌ Скасовано витрату: {amount} грн\n"
            f"🔴 Витрачено: {new_expenses} грн\n"
            f"📉 Залишок: {balance} грн (з {available_budget})\n"
            f"💰 Заощадження: {savings} грн\n"
        )
        if effective_overspend > 0:
            text += f"⚠️ Перенесено борг: {effective_overspend} грн\n"

        await message.reply(text, parse_mode="HTML")
        return

    # --- Додавання витрати (логування + оновлення budget) ---
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

    # беремо поточний стан дня
    expenses, savings, overspend, month_expenses = await get_day_data(today_str)

    # лог витрати (щоб потім /ч міг її видалити)
    await log_expense(today_str, amount, comment)

    # додаємо витрату
    expenses += amount
    month_expenses += amount

    # effective overspend — беремо зі сьогоднішнього запису (це перенесено з учора)
    effective_overspend = overspend
    if effective_overspend == 0:
        # якщо сьогоднішній overspend ==0 — можливо не було записано snapshot (стартап)
        snap = await get_snapshot(today_str)
        if snap:
            effective_overspend = snap[2]
        else:
            # фолбек: дивимось вчора
            yesterday_str = (now - timedelta(days=1)).strftime("%Y-%m-%d")
            _, _, y_overs, _ = await get_day_data(yesterday_str)
            effective_overspend = y_overs

    available_budget = DAILY_LIMIT - effective_overspend if effective_overspend > 0 else DAILY_LIMIT
    balance = max(0, available_budget - expenses)

    # оновлюємо таблицю
    await update_day(today_str, expenses, savings, effective_overspend, month_expenses)

    # зберігаємо last_amount для бек-совместності (але /ч працює з логом)
    last_amount = amount
    last_comment = comment

    # місячні дані (за бюджетним періодом)
    total_month, start, end = await get_month_expenses_for_date(now.date())
    days_in_period = (end - start).days + 1
    month_limit = DAILY_LIMIT * days_in_period
    month_left = max(0, month_limit - total_month)

    text = f"🔴 Додано витрати: {amount} грн"
    if comment:
        text += f"  <i>({comment})</i>"
    text += (
        f"\n\n🔴 Витрачено: {expenses} грн\n"
        f"📉 Залишок на сьогодні: {balance} грн (з {available_budget})\n"
        f"💰 Заощадження: {savings} грн\n"
        f"🗓️ Всього за бюджетний період {start.strftime('%d.%m')} - {(end - timedelta(days=0)).strftime('%d.%m')}: {total_month} грн\n"
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

        # поточний стан
        expenses, savings, overspend, month_expenses = await get_day_data(today_str)

        # effective overspend — це вже значення перенесення, яке було на початку дня
        available_budget = DAILY_LIMIT - overspend if overspend > 0 else DAILY_LIMIT

        if expenses <= available_budget:
            balance = available_budget - expenses
            savings += balance
            new_overspend = 0
        else:
            new_overspend = expenses - available_budget

        # нові місячні витрати на завтра
        new_month_expenses = month_expenses  # month_expenses зберігає накопичення; ми маємо переконатися, що воно включає сьогодні
        # Якщо month_expenses в нашій структурі не включає today's expenses, то new_month_expenses = month_expenses + expenses.
        # У нашій логіці ми зберігаємо month_expenses вже як накопичення; але надійно додамо:
        new_month_expenses = max(0, new_month_expenses + 0)  # безпечна операція (впевненість)
        # У попередніх місцях month_expenses вже збільшувався під час додавання витрат,
        # тому тут залишаємо як є.

        # Перевірка: чи починається новий бюджетний період наступного дня (5-е число)
        if next_date.day == 5:
            # новий бюджетний місяць починається — скидаємо savings і month_expenses
            await update_day(next_day, 0, 0, 0, 0)
            # зберігаємо snapshot теж
            await save_snapshot(next_day, 0, 0, 0, 0)
            month_text = "✅ Бюджетний період починається завтра (дані скинуто)."
        else:
            # записуємо стан на початок наступного дня (те, що користувач побачить вранці)
            await update_day(next_day, 0, savings, new_overspend, new_month_expenses)
            # зберігаємо snapshot для можливого /д
            await save_snapshot(next_day, 0, savings, new_overspend, new_month_expenses)
            # обчислення для повідомлення
            total_month, start, end = await get_month_expenses_for_date(today.date())
            days_in_period = (end - start).days + 1
            month_limit = DAILY_LIMIT * days_in_period
            month_left = max(0, month_limit - total_month)
            month_text = f"🗓️ Всього за період {start.strftime('%d.%m')} - {(end - timedelta(days=0)).strftime('%d.%m')}: {total_month} грн\n📌 Залишок на період: {month_left} грн"

        # Формуємо повідомлення
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
