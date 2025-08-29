import asyncio 
import aiosqlite
import re
from datetime import datetime, timedelta, timezone
from aiogram import Bot, Dispatcher
from aiogram.types import Message
from aiogram.fsm.storage.memory import MemoryStorage

# --- Налаштування ---
TOKEN = "8274894041:AAGEJSRDxWHbEriVnneByDtZtK_qu-vmflU"
GROUP_ID = -1003083789411  # твій груп ID
DAILY_BUDGET = 1000
TIMEZONE_OFFSET = 2  # твій пояс UTC+2

bot = Bot(token=TOKEN)
dp = Dispatcher(storage=MemoryStorage())

DB_PATH = "budget.db"

# --- Глобальні змінні ---
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
                carried_over INTEGER DEFAULT 0
            )
        """)
        await db.commit()

# --- Отримати дані дня ---
async def get_day_data(day: str):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT expenses, savings, carried_over FROM budget WHERE day=?", (day,)) as cursor:
            row = await cursor.fetchone()
            if row:
                return row[0], row[1], row[2]
            else:
                await db.execute("INSERT INTO budget (day, expenses, savings, carried_over) VALUES (?, 0, 0, 0)", (day,))
                await db.commit()
                return 0, 0, 0

# --- Порахувати всі витрати за місяць ---
async def get_month_expenses(year: int, month: int):
    month_str = f"{year}-{month:02d}-%"
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT SUM(expenses) FROM budget WHERE day LIKE ?", (month_str,)) as cursor:
            row = await cursor.fetchone()
            return row[0] if row[0] else 0

# --- Оновити дані дня ---
async def update_day(day: str, expenses: int, savings: int, carried_over: int = 0):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("REPLACE INTO budget (day, expenses, savings, carried_over) VALUES (?, ?, ?, ?)",
                         (day, expenses, savings, carried_over))
        await db.commit()

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
            "🔄 /ч – скасувати <u>останню витрату</u>.\n"
            "🧹 /д – видалити <u>усі витрати за сьогодні</u>, повернути стан після 23:00 учора.\n"
            "📊 /звіт – показати актуальний стан (з урахуванням перенесених витрат).\n"
            "ℹ️ /інфо – список команд."
        )
        await message.reply(help_text, parse_mode="HTML")
        return

    # --- /звіт ---
    if text_msg == "/звіт":
        today = datetime.now(timezone.utc) + timedelta(hours=TIMEZONE_OFFSET)
        today_str = today.strftime("%Y-%m-%d")
        yesterday = (today - timedelta(days=1)).strftime("%Y-%m-%d")
        month_name = today.strftime("%B")
        year, month = today.year, today.month

        expenses, savings, carried_over = await get_day_data(today_str)
        yesterday_expenses, _, _ = await get_day_data(yesterday)

        # Актуальний бюджет
        available_budget = DAILY_BUDGET - carried_over if carried_over > 0 else DAILY_BUDGET
        balance = max(0, available_budget - expenses)
        month_expenses = await get_month_expenses(year, month)

        text = (
            f"📊 <b>Звіт за {today_str}</b>\n\n"
            f"🔴 Витрачено сьогодні: {expenses} грн\n"
            f"📉 Залишок на сьогодні: {balance} грн\n"
            f"💰 Заощадження: {savings} грн\n"
            f"🗓️ Всього за місяць: {month_expenses} грн"
        )
        if carried_over > 0:
            text += f"\n⚠️ Перенесено з минулого дня: {carried_over} грн"

        await message.reply(text, parse_mode="HTML")
        return

    # --- /д: скидання витрат ---
    if text_msg == "/д":
        today = datetime.now(timezone.utc) + timedelta(hours=TIMEZONE_OFFSET)
        yesterday = (today - timedelta(days=1)).strftime("%Y-%m-%d")
        today_str = today.strftime("%Y-%m-%d")
        month_name = today.strftime("%B")
        year, month = today.year, today.month

        _, yesterday_savings, yesterday_carried_over = await get_day_data(yesterday)
        await update_day(today_str, 0, yesterday_savings, yesterday_carried_over)

        available_budget = DAILY_BUDGET - yesterday_carried_over if yesterday_carried_over > 0 else DAILY_BUDGET
        month_expenses = await get_month_expenses(year, month)

        text = (
            f"🧹 Витрати за {today_str} скинуто.\n\n"
            f"🔴 Витрачено: 0 грн\n"
            f"📉 Залишок: {available_budget} грн\n"
            f"💰 Заощадження: {yesterday_savings} грн\n"
            f"🗓️ Всього за місяць: {month_expenses} грн"
        )
        if yesterday_carried_over > 0:
            text += f"\n⚠️ Перенесено борг: {yesterday_carried_over} грн"

        await message.reply(text)
        return

    # --- /ч: відкат ---
    if text_msg == "/ч":
        if last_amount == 0:
            await message.reply("Немає витрати для скасування.")
            return

        today = datetime.now(timezone.utc) + timedelta(hours=TIMEZONE_OFFSET)
        today_str = today.strftime("%Y-%m-%d")
        month_name = today.strftime("%B")
        year, month = today.year, today.month

        expenses, savings, carried_over = await get_day_data(today_str)
        expenses = max(0, expenses - last_amount)

        available_budget = DAILY_BUDGET - carried_over if carried_over > 0 else DAILY_BUDGET
        balance = max(0, available_budget - expenses)

        await update_day(today_str, expenses, savings, carried_over)
        month_expenses = await get_month_expenses(year, month)

        text = (
            f"❌ Скасовано витрату: {last_amount} грн\n\n"
            f"🔴 Витрачено: {expenses} грн\n"
            f"📉 Залишок: {balance} грн\n"
            f"💰 Заощадження: {savings} грн\n"
            f"🗓️ Всього за місяць: {month_expenses} грн"
        )
        if carried_over > 0:
            text += f"\n⚠️ Перенесено борг: {carried_over} грн"

        last_amount = 0
        last_comment = ""
        await message.reply(text)
        return

    # --- Додавання витрати ---
    try:
        match = re.match(r"^([\d\.,]+)(?:\s*(.*))?$", text_msg)
        if not match:
            return
        raw_amount = match.group(1).replace(",", ".")
        amount = int(float(raw_amount))
        comment = match.group(2) if match.group(2) else ""
    except Exception:
        return

    today = datetime.now(timezone.utc) + timedelta(hours=TIMEZONE_OFFSET)
    today_str = today.strftime("%Y-%m-%d")
    month_name = today.strftime("%B")
    year, month = today.year, today.month

    expenses, savings, carried_over = await get_day_data(today_str)
    expenses += amount

    available_budget = DAILY_BUDGET - carried_over if carried_over > 0 else DAILY_BUDGET
    balance = max(0, available_budget - expenses)

    await update_day(today_str, expenses, savings, carried_over)

    last_amount = amount
    last_comment = comment
    month_expenses = await get_month_expenses(year, month)

    text = f"🔴 Додано витрати: {amount} грн"
    if comment:
        text += f"  <i>({comment})</i>"
    text += (
        f"\n\n🔴 Витрачено: {expenses} грн\n"
        f"📉 Залишок: {balance} грн\n"
        f"💰 Заощадження: {savings} грн\n"
        f"🗓️ Всього за місяць: {month_expenses} грн"
    )
    if carried_over > 0:
        text += f"\n⚠️ Перенесено борг: {carried_over} грн"

    await message.reply(text, parse_mode="HTML")

# --- Автозвіт о 23:00 ---
async def daily_summary():
    global last_amount
    await init_db()
    local_tz = timezone(timedelta(hours=TIMEZONE_OFFSET))

    while True:
        now = datetime.now(local_tz)
        target = datetime.combine(now.date(), datetime.min.time(), tzinfo=local_tz) + timedelta(hours=23)
        if now > target:
            target += timedelta(days=1)
        wait_seconds = (target - now).total_seconds()
        await asyncio.sleep(wait_seconds)

        today = datetime.now(local_tz)
        day = (today - timedelta(seconds=1)).strftime("%Y-%m-%d")
        next_day = today.strftime("%Y-%m-%d")
        month_name = today.strftime("%B")
        year, month = today.year, today.month

        expenses, savings, carried_over = await get_day_data(day)
        available_budget = DAILY_BUDGET - carried_over if carried_over > 0 else DAILY_BUDGET

        if expenses <= available_budget:
            balance = available_budget - expenses
            savings += balance
            await update_day(next_day, 0, savings, 0)
        else:
            overspend = expenses - available_budget
            await update_day(next_day, 0, savings, overspend)

        month_expenses = await get_month_expenses(year, month)

        text = (
            f"📊 <b>Підсумок дня ({day})</b>\n"
            f"🔴 Витрачено: {expenses} грн\n"
            f"📉 Залишок: {max(0, available_budget - expenses)} грн\n"
            f"💰 Заощадження: {savings} грн\n"
            f"🗓️ Всього за місяць: {month_expenses} грн"
        )
        if expenses > available_budget:
            text += f"\n⚠️ Перевищення: {expenses - available_budget} грн (перенесено на завтра)"

        try:
            await bot.send_message(GROUP_ID, text, parse_mode="HTML")
        except Exception as e:
            print(f"Помилка надсилання: {e}")

        last_amount = 0
        last_comment = ""

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
