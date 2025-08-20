import asyncio
import aiosqlite
from datetime import datetime, timedelta
from aiogram import Bot, Dispatcher
from aiogram.types import Message
from aiogram.fsm.storage.memory import MemoryStorage

# --- Налаштування ---
TOKEN = "8274894041:AAGEJSRDxWHbEriVnneByDtZtK_qu-vmflU"
GROUP_ID = -1003083789411  # твій груп ID
DAILY_BUDGET = 1000

bot = Bot(token=TOKEN)
dp = Dispatcher(storage=MemoryStorage())

DB_PATH = "budget.db"

# --- Глобальна змінна для останньої витрати ---
last_amount = 0

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

# --- Порахувати всі витрати за місяць ---
async def get_month_expenses(year: int, month: int):
    month_str = f"{year}-{month:02d}-%"
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT SUM(expenses) FROM budget WHERE day LIKE ?", (month_str,)) as cursor:
            row = await cursor.fetchone()
            return row[0] if row[0] else 0

# --- Оновити дані дня ---
async def update_day(day: str, expenses: int, savings: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("REPLACE INTO budget (day, expenses, savings) VALUES (?, ?, ?)",
                         (day, expenses, savings))
        await db.commit()

# --- Хендлер повідомлень у групі ---
@dp.message()
async def handle_message(message: Message):
    global last_amount

    if message.chat.id != GROUP_ID:
        return

    # --- Відкат останньої витрати ---
    if message.text.strip() == "/ч":
        if last_amount == 0:
            await message.reply("Немає останньої витрати для скасування.")
            return

        day = datetime.now().strftime("%Y-%m-%d")
        month_name = datetime.now().strftime("%B")
        year = datetime.now().year
        month = datetime.now().month

        expenses, savings = await get_day_data(day)
        expenses -= last_amount
        if expenses < 0:
            expenses = 0

        balance = DAILY_BUDGET - expenses
        if balance < 0:
            balance = 0

        month_expenses = await get_month_expenses(year, month)
        month_expenses -= last_amount
        if month_expenses < 0:
            month_expenses = 0

        await update_day(day, expenses, savings)

        last_amount = 0  # скидаємо останню суму

        text = (
            f"❌ Відкликано останню витрату\n\n"
            f"🔴 Витрачено сьогодні: {expenses} грн\n"
            f"📉 Залишок на сьогодні: {balance} грн\n"
            f"💰 Загальні заощадження на {month_name}: {savings} грн\n"
            f"🗓️ Всього витрачено за місяць: {month_expenses} грн"
        )
        await message.reply(text)
        return

    # --- Додавання витрати ---
    try:
        amount = int(message.text.strip())
    except (ValueError, AttributeError):
        return

    day = datetime.now().strftime("%Y-%m-%d")
    month_name = datetime.now().strftime("%B")
    year = datetime.now().year
    month = datetime.now().month

    expenses, savings = await get_day_data(day)
    expenses += amount
    await update_day(day, expenses, savings)

    balance = DAILY_BUDGET - expenses
    if balance < 0:
        balance = 0

    month_expenses = await get_month_expenses(year, month)
    month_expenses += amount

    last_amount = amount  # запам’ятовуємо останню додану суму

    text = (
        f"🔴 <b>Додано витрати: {amount} грн</b>\n\n"
        f"🔴 Витрачено сьогодні: {expenses} грн\n"
        f"📉 Залишок на сьогодні: {balance} грн\n"
        f"💰 Загальні заощадження на {month_name}: {savings} грн\n"
        f"🗓️ Всього витрачено за місяць: {month_expenses} грн"
    )
    await message.reply(text, parse_mode="HTML")

# --- Автозвіт о 23:00 ---
async def daily_summary():
    global last_amount
    await init_db()
    while True:
        now = datetime.now()
        target = datetime.combine(now.date(), datetime.min.time()) + timedelta(hours=23)
        if now > target:
            target += timedelta(days=1)
        wait_seconds = (target - now).total_seconds()
        await asyncio.sleep(wait_seconds)

        day = (datetime.now() - timedelta(seconds=1)).strftime("%Y-%m-%d")
        month_name = datetime.now().strftime("%B")
        year = datetime.now().year
        month = datetime.now().month

        expenses, savings = await get_day_data(day)
        balance = DAILY_BUDGET - expenses
        if balance > 0:
            savings += balance
        await update_day(day, 0, savings)

        month_expenses = await get_month_expenses(year, month)

        text = (
            f"📊 <b>Підсумок дня ({day})</b>\n"
            f"🔴 Витрачено: {expenses} грн\n"
            f"📉 Залишок на сьогодні: {balance if balance>0 else 0} грн\n"
            f"💰 Загальні заощадження на {month_name}: {savings} грн\n"
            f"🗓️ Всього витрачено за місяць: {month_expenses} грн"
        )
        try:
            await bot.send_message(GROUP_ID, text, parse_mode="HTML")
        except Exception as e:
            print(f"Помилка при відправці повідомлення: {e}")

        last_amount = 0  # <--- скидаємо останню витрату після щоденного підсумку

# --- Головна функція ---
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
