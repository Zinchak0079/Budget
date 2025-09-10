import asyncio
import aiosqlite
import re
import calendar
from datetime import datetime, timedelta, timezone
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
        await db.commit()


# --- Отримати дані дня ---
async def get_day_data(day: str):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT expenses, savings, overspend, month_expenses FROM budget WHERE day=?", (day,)) as cursor:
            row = await cursor.fetchone()
            if row:
                return row[0], row[1], row[2], row[3]
            else:
                await db.execute("INSERT INTO budget (day, expenses, savings, overspend, month_expenses) VALUES (?, 0, 0, 0, 0)", (day,))
                await db.commit()
                return 0, 0, 0, 0


# --- Оновити дані дня ---
async def update_day(day: str, expenses: int, savings: int, overspend: int, month_expenses: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            INSERT INTO budget (day, expenses, savings, overspend, month_expenses)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(day) DO UPDATE SET
                expenses = excluded.expenses,
                savings = excluded.savings,
                overspend = excluded.overspend,
                month_expenses = excluded.month_expenses
        """, (day, expenses, savings, overspend, month_expenses))
        await db.commit()


# --- Отримати період бюджетного місяця (з 5 числа до 4 числа) ---
def get_budget_month(today: datetime):
    if today.day >= 5:
        start = today.replace(day=5)
        # кінець = 4 число наступного місяця
        if today.month == 12:
            end = today.replace(year=today.year + 1, month=1, day=4)
        else:
            end = today.replace(month=today.month + 1, day=4)
    else:
        # якщо сьогодні до 4 → це ще попередній місяць
        if today.month == 1:
            start = today.replace(year=today.year - 1, month=12, day=5)
        else:
            start = today.replace(month=today.month - 1, day=5)
        end = today.replace(day=4)
    return start, end


# --- Порахувати всі витрати за бюджетний місяць ---
async def get_month_expenses(today: datetime):
    start, end = get_budget_month(today)
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT SUM(expenses) FROM budget WHERE day BETWEEN ? AND ?", 
                              (start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d"))) as cursor:
            row = await cursor.fetchone()
            return row[0] if row[0] else 0, start, end


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
            "🔄 /ч – скасувати <u>останню витрату</u> (з урахуванням боргу).\n"
            "🧹 /д – повернути стан <u>на вчора</u>.\n"
            "📊 /звіт – показати актуальний стан.\n"
            "ℹ️ /інфо – список команд."
        )
        await message.reply(help_text, parse_mode="HTML")
        return

    # --- /звіт ---
    if text_msg == "/звіт":
        today = datetime.now(timezone.utc) + timedelta(hours=TIMEZONE_OFFSET)
        today_str = today.strftime("%Y-%m-%d")

        expenses, savings, overspend, month_expenses = await get_day_data(today_str)
        total_month, start, end = await get_month_expenses(today)
        days_in_month = (end - start).days + 1
        month_limit = DAILY_LIMIT * days_in_month
        month_left = max(0, month_limit - total_month)

        available_budget = DAILY_LIMIT - overspend if overspend > 0 else DAILY_LIMIT
        balance = max(0, available_budget - expenses)

        text = (
            f"📊 <b>Звіт за {today_str}</b>\n\n"
            f"🔴 Витрачено сьогодні: {expenses} грн\n"
            f"📉 Залишок на сьогодні: {balance} грн\n"
            f"💰 Заощадження: {savings} грн\n"
            f"🗓️ Всього за період {start.strftime('%d.%m')} - {end.strftime('%d.%m')}: {total_month} грн\n"
            f"📌 Залишок на період: {month_left} грн"
        )
        if overspend > 0:
            text += f"\n⚠️ Перенесено борг: {overspend} грн"

        await message.reply(text, parse_mode="HTML")
        return

    # --- /д: відкат до вчора ---
    if text_msg == "/д":
        today = datetime.now(timezone.utc) + timedelta(hours=TIMEZONE_OFFSET)
        today_str = today.strftime("%Y-%m-%d")
        yesterday_str = (today - timedelta(days=1)).strftime("%Y-%m-%d")

        y_exp, y_sav, y_over, y_month = await get_day_data(yesterday_str)
        await update_day(today_str, y_exp, y_sav, y_over, y_month)

        available_budget = DAILY_LIMIT - y_over if y_over > 0 else DAILY_LIMIT
        balance = max(0, available_budget - y_exp)

        text = (
            f"🧹 Повернуто стан на {yesterday_str}\n\n"
            f"🔴 Витрачено: {y_exp} грн\n"
            f"📉 Залишок: {balance} грн\n"
            f"💰 Заощадження: {y_sav} грн"
        )
        if y_over > 0:
            text += f"\n⚠️ Перенесено борг: {y_over} грн"

        await message.reply(text)
        return

    # --- /ч: відкат останньої витрати ---
    if text_msg == "/ч":
        if last_amount == 0:
            await message.reply("Немає витрати для скасування.")
            return

        today = datetime.now(timezone.utc) + timedelta(hours=TIMEZONE_OFFSET)
        today_str = today.strftime("%Y-%m-%d")

        expenses, savings, overspend, month_expenses = await get_day_data(today_str)
        expenses = max(0, expenses - last_amount)

        available_budget = DAILY_LIMIT - overspend if overspend > 0 else DAILY_LIMIT
        balance = max(0, available_budget - expenses)

        await update_day(today_str, expenses, savings, overspend, month_expenses)
        text = (
            f"❌ Скасовано витрату: {last_amount} грн\n"
            f"🔴 Витрачено: {expenses} грн\n"
            f"📉 Залишок: {balance} грн\n"
            f"💰 Заощадження: {savings} грн"
        )
        if overspend > 0:
            text += f"\n⚠️ Перенесено борг: {overspend} грн"

        last_amount = 0
        last_comment = ""

        await message.reply(text)
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

    today = datetime.now(timezone.utc) + timedelta(hours=TIMEZONE_OFFSET)
    today_str = today.strftime("%Y-%m-%d")

    expenses, savings, overspend, month_expenses = await get_day_data(today_str)
    expenses += amount

    available_budget = DAILY_LIMIT - overspend if overspend > 0 else DAILY_LIMIT
    balance = max(0, available_budget - expenses)

    total_month, start, end = await get_month_expenses(today)
    total_month += amount
    days_in_month = (end - start).days + 1
    month_limit = DAILY_LIMIT * days_in_month
    month_left = max(0, month_limit - total_month)

    await update_day(today_str, expenses, savings, overspend, total_month)

    last_amount = amount
    last_comment = comment

    text = f"🔴 Додано витрати: {amount} грн"
    if comment:
        text += f"  <i>({comment})</i>"
    text += (
        f"\n\n🔴 Витрачено: {expenses} грн\n"
        f"📉 Залишок на сьогодні: {balance} грн\n"
        f"💰 Заощадження: {savings} грн\n"
        f"🗓️ Всього за період {start.strftime('%d.%m')} - {end.strftime('%d.%m')}: {total_month} грн\n"
        f"📌 Залишок на період: {month_left} грн"
    )
    if overspend > 0:
        text += f"\n⚠️ Перенесено борг: {overspend} грн"

    await message.reply(text, parse_mode="HTML")
