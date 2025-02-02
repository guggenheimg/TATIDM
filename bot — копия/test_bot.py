
from aiogram import Bot, Dispatcher, Router
from aiogram.filters import Command
from aiogram.types import Message, ReplyKeyboardMarkup, KeyboardButton
import asyncio

BOT_TOKEN = "7939439290:AAF0aZO0zDTPrIxV6zTyIkSFLs8fccYvY4g"  # Временно пропишите сюда, чисто для теста

router = Router()
dp = Dispatcher()

dp.include_router(router)
bot = Bot(BOT_TOKEN)

@router.message(Command("start"))
async def cmd_start_test(message: Message):
    await message.answer("Привет! Тестовая клавиатура:")

async def main():
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
