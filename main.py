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


# --- Ініціалізація БД ---
async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        # основна таблиця
        await db.execute("""
            CREATE TABLE IF NOT EXISTS budget (
                day TEXT PRIMARY KEY,
                expenses INTEGER DEFAULT 0,
                savings INTEGER DEFAULT 0,
                overspend INTEGER DEFAULT 0,
                month_expenses INTEGER DEFAULT 0
            )
        """)
        # лог витрат
        await db.execute("""
            CREATE TABLE IF NOT EXISTS expenses_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                day TEXT,
                amount INTEGER,
                comment TEXT
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


# --- /ч: відкат останньої витрати ---
async def cancel_last_expense(today_str: str):
    async with aiosqlite.connect(DB_PATH) as db:
        # беремо останню витрату з логів
        cursor = await db.execute("SELECT id, amount FROM expenses_log WHERE day=? ORDER BY id DESC LIMIT 1", (today_str,))
        row = await cursor.fetchone()
        if not row:
            return None
        exp_id, amount = row
        # видаляємо з логів
        await db.execute("DELETE FROM expenses_log WHERE id=?", (exp_id,))
        # оновлюємо таблицю budget
        expenses, savings, overspend, month_expenses = await get_day_data(today_str)
        expenses = max(0, expenses - amount)
        month_expenses = max(0, month_expenses - amount)
        await update_day(today_str, expenses, savings, overspend, month_expenses)
        await db.commit()
        return amount, expenses, savings, overspend


# --- Перевірка бюджетного місяця ---
def get_budget_month(today: datetime):
    """Повертає рік і місяць для бюджетного обліку (рахунок з 5 числа)"""
    if today.day >= 5:
        return today.year, today.month
    else:
        prev_month = today.month - 1 or 12
        prev_year = today.year - 1 if prev_month == 12 else today.year
        return prev_year, prev_month


# --- Хендлер повідомлень ---
@dp.message()
async def handle_message(message: Message):
    if message.chat.id != GROUP_ID:
        return

    text_msg = message.text.strip()

    # --- /ч ---
    if text_msg == "/ч":
        today = datetime.now(timezone.utc) + timedelta(hours=TIMEZONE_OFFSET)
        today_str = today.strftime("%Y-%m-%d")
        result = await cancel_last_expense(today_str)
        if not result:
            await message.reply("❌ Немає витрат для скасування.")
            return
        amount, expenses, savings, overspend = result
        balance = max(0, DAILY_LIMIT - overspend - expenses if overspend > 0 else DAILY_LIMIT - expenses)
        text = (
            f"❌ Скасовано витрату: {amount} грн\n"
            f"🔴 Витрачено: {expenses} грн\n"
            f"📉 Залишок: {balance} грн\n"
            f"💰 Заощадження: {savings} грн"
        )
        if overspend > 0:
            text += f"\n⚠️ Перенесено борг: {overspend} грн"
        await message.reply(text)
        return

    # --- Додавання витрат ---
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
    year, month = get_budget_month(today)
    days_in_month = calendar.monthrange(year, month)[1]

    expenses, savings, overspend, month_expenses = await get_day_data(today_str)
    expenses += amount
    month_expenses += amount

    # лог витрати
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("INSERT INTO expenses_log (day, amount, comment) VALUES (?, ?, ?)", (today_str, amount, comment))
        await db.commit()

    available_budget = DAILY_LIMIT - overspend if overspend > 0 else DAILY_LIMIT
    balance = max(0, available_budget - expenses)
    month_limit = DAILY_LIMIT * days_in_month
    month_left = max(0, month_limit - month_expenses)

    await update_day(today_str, expenses, savings, overspend, month_expenses)

    text = f"🔴 Додано витрати: {amount} грн"
    if comment:
        text += f"  <i>({comment})</i>"
    text += (
        f"\n\n🔴 Витрачено: {expenses} грн\n"
        f"📉 Залишок на сьогодні: {balance} грн\n"
        f"💰 Заощадження: {savings} грн\n"
        f"🗓️ Всього за бюджетний місяць: {month_expenses} грн\n"
        f"📌 Залишок на місяць: {month_left} грн"
    )
    if overspend > 0:
        text += f"\n⚠️ Перенесено борг: {overspend} грн"

    await message.reply(text, parse_mode="HTML")
