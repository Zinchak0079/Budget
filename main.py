import asyncio
from aiogram import Bot, Dispatcher
from aiogram.types import Message
from aiogram.filters import Command

TOKEN = "8274894041:AAGEJSRDxWHbEriVnneByDtZtK_qu-vmflU"

# створюємо бот і диспетчер
bot = Bot(token=TOKEN)
dp = Dispatcher()

# /start
@dp.message(Command("start"))
async def start_cmd(message: Message):
    await message.answer("Привіт! 👋 Бот працює на Aiogram 3.x")

# будь-яке повідомлення
@dp.message()
async def echo(message: Message):
    await message.answer(f"Ти написав: {message.text}")

async def main():
    # запуск поллінгу
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
