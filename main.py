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


# --- Порахувати всі витрати за місяць ---
async def get_month_expenses(year: int, month: int):
    month_str = f"{year}-{month:02d}-%"
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT SUM(expenses) FROM budget WHERE day LIKE ?", (month_str,)) as cursor:
            row = await cursor.fetchone()
            return row[0] if row[0] else 0


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
        yesterday_str = (today - timedelta(days=1)).strftime("%Y-%m-%d")
        year, month = today.year, today.month
        days_in_month = calendar.monthrange(year, month)[1]

        expenses, savings, overspend, month_expenses = await get_day_data(today_str)
        month_limit = DAILY_LIMIT * days_in_month
        month_left = max(0, month_limit - month_expenses - expenses)

        available_budget = DAILY_LIMIT - overspend if overspend > 0 else DAILY_LIMIT
        balance = max(0, available_budget - expenses)

        text = (
            f"📊 <b>Звіт за {today_str}</b>\n\n"
            f"🔴 Витрачено сьогодні: {expenses} грн\n"
            f"📉 Залишок на сьогодні: {balance} грн\n"
            f"💰 Заощадження: {savings} грн\n"
            f"🗓️ Всього за місяць: {month_expenses + expenses} грн\n"
            f"📌 Залишок на місяць: {month_left} грн"
        )
        if overspend > 0:
            text += f"\n⚠️ Перенесено борг: {overspend} грн"

        await message.reply(text, parse_mode="HTML")
        return

    # --- /д: скидання витрат ---
    if text_msg == "/д":
        today = datetime.now(timezone.utc) + timedelta(hours=TIMEZONE_OFFSET)
        today_str = today.strftime("%Y-%m-%d")
        yesterday_str = (today - timedelta(days=1)).strftime("%Y-%m-%d")

        _, yesterday_savings, yesterday_overspend, month_expenses = await get_day_data(yesterday_str)
        await update_day(today_str, 0, yesterday_savings, yesterday_overspend, month_expenses)

        available_budget = DAILY_LIMIT - yesterday_overspend if yesterday_overspend > 0 else DAILY_LIMIT
        text = (
            f"🧹 Витрати за {today_str} скинуто.\n\n"
            f"🔴 Витрачено: 0 грн\n"
            f"📉 Залишок: {available_budget} грн\n"
            f"💰 Заощадження: {yesterday_savings} грн"
        )
        if yesterday_overspend > 0:
            text += f"\n⚠️ Перенесено борг: {yesterday_overspend} грн"

        await message.reply(text)
        return

    # --- /ч: відкат ---
    if text_msg == "/ч":
        if last_amount == 0:
            await message.reply("Немає витрати для скасування.")
            return

        today = datetime.now(timezone.utc) + timedelta(hours=TIMEZONE_OFFSET)
        today_str = today.strftime("%Y-%m-%d")

        expenses, savings, overspend, month_expenses = await get_day_data(today_str)
        expenses = max(0, expenses - last_amount)
        balance = max(0, DAILY_LIMIT - overspend - expenses if overspend > 0 else DAILY_LIMIT - expenses)

        await update_day(today_str, expenses, savings, overspend, month_expenses)
        last_amount = 0
        last_comment = ""

        text = (
            f"❌ Скасовано витрату: {last_amount} грн\n"
            f"🔴 Витрачено: {expenses} грн\n"
            f"📉 Залишок: {balance} грн\n"
            f"💰 Заощадження: {savings} грн"
        )
        if overspend > 0:
            text += f"\n⚠️ Перенесено борг: {overspend} грн"

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
    year, month = today.year, today.month
    days_in_month = calendar.monthrange(year, month)[1]

    expenses, savings, overspend, month_expenses = await get_day_data(today_str)
    expenses += amount

    available_budget = DAILY_LIMIT - overspend if overspend > 0 else DAILY_LIMIT
    balance = max(0, available_budget - expenses)
    month_expenses += amount
    month_limit = DAILY_LIMIT * days_in_month
    month_left = max(0, month_limit - month_expenses)

    await update_day(today_str, expenses, savings, overspend, month_expenses)

    last_amount = amount
    last_comment = comment

    text = f"🔴 Додано витрати: {amount} грн"
    if comment:
        text += f"  <i>({comment})</i>"
    text += (
        f"\n\n🔴 Витрачено: {expenses} грн\n"
        f"📉 Залишок на сьогодні: {balance} грн\n"
        f"💰 Заощадження: {savings} грн\n"
        f"🗓️ Всього за місяць: {month_expenses} грн\n"
        f"📌 Залишок на місяць: {month_left} грн"
    )
    if overspend > 0:
        text += f"\n⚠️ Перенесено борг: {overspend} грн"

    await message.reply(text, parse_mode="HTML")


# --- Автозвіт о 23:00 ---
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
        next_day = (today + timedelta(days=1)).strftime("%Y-%m-%d")

        year, month = today.year, today.month
        days_in_month = calendar.monthrange(year, month)[1]

        expenses, savings, overspend, month_expenses = await get_day_data(today_str)
        available_budget = DAILY_LIMIT - overspend if overspend > 0 else DAILY_LIMIT

        if expenses <= available_budget:
            balance = available_budget - expenses
            savings += balance
            overspend = 0
        else:
            overspend = expenses - available_budget

        # Перевірка нового місяця
        next_date_obj = today + timedelta(days=1)
        if next_date_obj.month != today.month:
            # новий місяць → скидання місячних витрат та заощаджень
            await update_day(next_day, 0, 0, 0, 0)
            month_text = "✅ Місяць завершено. Дані обнулено."
        else:
            await update_day(next_day, 0, savings, overspend, month_expenses)
            month_limit = DAILY_LIMIT * days_in_month
            month_left = max(0, month_limit - month_expenses)
            month_text = f"🗓️ Всього за місяць: {month_expenses} грн\n📌 Залишок на місяць: {month_left} грн"

        text = (
            f"📊 <b>Підсумок дня ({today_str})</b>\n"
            f"🔴 Витрачено: {expenses} грн\n"
            f"📉 Залишок на сьогодні: {max(0, available_budget - expenses)} грн\n"
            f"💰 Заощадження: {savings} грн\n"
            f"{month_text}"
        )
        if overspend > 0:
            text += f"\n⚠️ Перевищення: {overspend} грн (перенесено на завтра)"

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
