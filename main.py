import logging
import asyncio
import sqlite3
from aiogram import Bot, Dispatcher, executor, types
from datetime import datetime, timedelta
import os

logging.basicConfig(level=logging.INFO)

BOT_TOKEN = os.getenv("BOT_TOKEN")
bot = Bot(token=BOT_TOKEN, parse_mode=types.ParseMode.HTML)
dp = Dispatcher(bot)

DAILY_BUDGET = 1000

# --- БАЗА ДАНИХ ---
conn = sqlite3.connect("budget.db", check_same_thread=False)
cursor = conn.cursor()

cursor.execute("""
CREATE TABLE IF NOT EXISTS budget (
    day TEXT PRIMARY KEY,
    expenses INTEGER DEFAULT 0,
    savings INTEGER DEFAULT 0
)
""")
conn.commit()


def get_today():
    return datetime.now().strftime("%Y-%m-%d")


def get_day_data():
    today = get_today()
    cursor.execute("SELECT expenses, savings FROM budget WHERE day=?", (today,))
    row = cursor.fetchone()
    if row:
        return row[0], row[1]
    else:
        cursor.execute("INSERT INTO budget (day, expenses, savings) VALUES (?, 0, 0)", (today,))
        conn.commit()
        return 0, 0


def update_day(expenses, savings):
    today = get_today()
    cursor.execute("REPLACE INTO budget (day, expenses, savings) VALUES (?, ?, ?)", (today, expenses, savings))
    conn.commit()


def carry_over_savings():
    today = get_today()
    cursor.execute("SELECT day, expenses, savings FROM budget ORDER BY day DESC LIMIT 1")
    row = cursor.fetchone()

    if row and row[0] != today:
        # Переносимо залишок в savings
        old_expenses, old_savings = row[1], row[2]
        balance = DAILY_BUDGET - old_expenses
        if balance > 0:
            old_savings += balance
        # Створюємо новий день
        cursor.execute("INSERT OR REPLACE INTO budget (day, expenses, savings) VALUES (?, ?, ?)", (today, 0, old_savings))
        conn.commit()


# --- БОТ --- 
def format_balance(expenses, savings):
    balance = DAILY_BUDGET - expenses
    color_balance = "🟢" if balance >= 0 else "🔴"
    return balance, color_balance, savings


@dp.message_handler(commands=["start"])
async def start_cmd(message: types.Message):
    await message.answer("Привіт! Я бот для підрахунку витрат.\n"
                         "Використовуй команду:\n"
                         "/витрата <сума> <опис>")


@dp.message_handler(commands=["витрата"])
async def add_expense(message: types.Message):
    carry_over_savings()
    expenses, savings = get_day_data()

    try:
        args = message.text.split(maxsplit=2)
        amount = int(args[1])
        category = args[2] if len(args) > 2 else "без категорії"
    except (IndexError, ValueError):
        await message.reply("❌ Формат: /витрата <сума> <опис>")
        return

    expenses += amount
    update_day(expenses, savings)

    balance, color_balance, savings = format_balance(expenses, savings)
    text = (f"✅ Витрата: 🔴{amount} грн ({category})\n"
            f"📉 Залишок на сьогодні: {color_balance}{balance} грн\n"
            f"💰 Загальні заощадження: 🟢{savings} грн")
    await message.answer(text)


@dp.message_handler(commands=["статус"])
async def status_cmd(message: types.Message):
    carry_over_savings()
    expenses, savings = get_day_data()
    balance, color_balance, savings = format_balance(expenses, savings)
    await message.answer(f"📊 Статус дня:\n"
                         f"🔴 Витрачено: {expenses} грн\n"
                         f"📉 Залишок: {color_balance}{balance} грн\n"
                         f"💰 Заощадження: 🟢{savings} грн")


# --- АВТО ЗВІТ ---
async def daily_summary():
    while True:
        now = datetime.now()
        target = datetime.combine(now.date(), datetime.min.time()) + timedelta(days=1, minutes=-1)
        wait_seconds = (target - now).total_seconds()
        await asyncio.sleep(wait_seconds)

        expenses, savings = get_day_data()
        balance = DAILY_BUDGET - expenses
        if balance > 0:
            savings += balance
        update_day(0, savings)  # новий день, витрати обнуляються

        text = (f"📊 Підсумок дня: витрачено 🔴{expenses} грн, "
                f"залишок {'🟢' if balance >= 0 else '🔴'}{balance} грн\n"f"💰 Загальні заощадження: 🟢{savings} грн")

        # ⚠️ Треба вказати ID групи вручну або отримати автоматично
        CHAT_ID = os.getenv("CHAT_ID")
        if CHAT_ID:
            try:
                await bot.send_message(chat_id=CHAT_ID, text=text)
            except:
                pass


if name == "__main__":
    loop = asyncio.get_event_loop()
    loop.create_task(daily_summary())
    executor.start_polling(dp, skip_updates=True)
