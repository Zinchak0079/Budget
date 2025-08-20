import asyncio
import aiosqlite
from datetime import datetime, timedelta
from aiogram import Bot, Dispatcher
from aiogram.types import Message

TOKEN = "8274894041:AAGEJSRDxWHbEriVnneByDtZtK_qu-vmflU"
CHANNEL_ID = -1001234567890  # заміни на свій канал
DAILY_BUDGET = 1000

bot = Bot(token=TOKEN)
dp = Dispatcher()

DB_PATH = "budget.db"

# --- Ініціалізація БД ---
async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS budget (
                day TEXT PRIMARY KEY,
                expenses INTEGER DEFAULT 0,
                savings INTEGER DEFAULT 0
            )
        """)
        await db.commit()

# --- Отримати дані дня ---
async def get_day_data(day: str):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT expenses, savings FROM budget WHERE day=?", (day,)) as cursor:
            row = await cursor.fetchone()
            if row:
                return row[0], row[1]
            else:
                await db.execute("INSERT INTO budget (day, expenses, savings) VALUES (?, 0, 0)", (day,))
                await db.commit()
                return 0, 0

# --- Оновити дані дня ---
async def update_day(day: str, expenses: int, savings: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("REPLACE INTO budget (day, expenses, savings) VALUES (?, ?, ?)",
                         (day, expenses, savings))
        await db.commit()

# --- Хендлер повідомлень у каналі ---
@dp.channel_post()
async def handle_channel_post(message: Message):
    day = datetime.now().strftime("%Y-%m-%d")
    expenses, savings = await get_day_data(day)

    try:
        amount = int(message.text)  # припускаємо, що у каналі пишуть тільки цифри (витрати)
    except ValueError:
        return  # якщо не число, ігноруємо

    expenses += amount
    await update_day(day, expenses, savings)

# --- Автозвіт о 23:00 ---
async def daily_summary():
    await init_db()
    while True:
        now = datetime.now()
        # розрахунок часу до 23:00
        target = datetime.combine(now.date(), datetime.min.time()) + timedelta(hours=23)
        if now > target:
            target += timedelta(days=1)
        wait_seconds = (target - now).total_seconds()
        await asyncio.sleep(wait_seconds)

        day = (datetime.now() - timedelta(seconds=1)).strftime("%Y-%m-%d")
        expenses, savings = await get_day_data(day)
        balance = DAILY_BUDGET - expenses
        if balance > 0:
            savings += balance
        await update_day(day, 0, savings)  # новий день, витрати обнуляються

        text = (f"📊 Підсумок дня ({day}):\n"
                f"🔴 Витрачено: {expenses} грн\n"
                f"📉 Залишок на сьогодні: {balance if balance>0 else 0} грн\n"
                f"💰 Загальні заощадження: {savings} грн")

        await bot.send_message(CHANNEL_ID, text)

# --- Головна функція ---
async def main():
    await init_db()
    asyncio.create_task(daily_summary())
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())

