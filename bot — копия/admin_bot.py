# admin_bot.py

import logging
import asyncio
import datetime
import gspread_asyncio
from oauth2client.service_account import ServiceAccountCredentials
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram import Router
from aiogram.types import Message
from aiogram.filters.command import CommandObject
from dotenv import load_dotenv
import os

# Загрузка переменных окружения из .env файла
load_dotenv()

# Настройка логирования
logging.basicConfig(level=logging.INFO)

# Получение токена и админ ID из переменных окружения
ADMIN_BOT_TOKEN = os.getenv("ADMIN_BOT_TOKEN")
ADMIN_IDS = os.getenv("ADMIN_IDS", "")
# Преобразуем строку с ID в список целых чисел
try:
    ADMIN_IDS = [int(id_.strip()) for id_ in ADMIN_IDS.split(",") if id_.strip().isdigit()]
except ValueError:
    ADMIN_IDS = []
    logging.error("Ошибка при парсинге ADMIN_IDS. Убедитесь, что они являются числами и разделены запятыми.")

# Проверка наличия хотя бы одного админа
if not ADMIN_IDS:
    logging.warning("Список ADMIN_IDS пуст. Бот не будет доступен никому.")

SPREADSHEET_NAME = os.getenv("SPREADSHEET_NAME", "OrdersForCakes")
ORDERS_SHEET_NAME = os.getenv("ORDERS_SHEET_NAME", "orders")
CAKES_SHEET_NAME = os.getenv("CAKES_SHEET_NAME", "cakes")

# Определение Scopes
scope = [
    "https://spreadsheets.google.com/feeds",
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive.file",
    "https://www.googleapis.com/auth/drive"
]

# Асинхронная инициализация клиента Google Sheets
async def get_gspread_client():
    agcm = gspread_asyncio.AsyncioGspreadClientManager(
        lambda: ServiceAccountCredentials.from_json_keyfile_name("credentials.json", scope)
    )
    return await agcm.authorize()

# Инициализация бота и диспетчера
bot = Bot(token=ADMIN_BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())
router = Router()
dp.include_router(router)

# Определение состояний для FSM (если потребуется расширение функционала)
class AdminStates(StatesGroup):
    ViewingOrders = State()
    UpdatingOrderStatus = State()

# Глобальная переменная для клиента Google Sheets
gc = None

# Вспомогательные функции
async def get_all_orders():
    """Возвращает все заказы из листа 'orders'."""
    try:
        sh = await gc.open(SPREADSHEET_NAME)
        orders_sheet = await sh.worksheet(ORDERS_SHEET_NAME)
        all_orders = await orders_sheet.get_all_records()
        logging.info(f"Fetched {len(all_orders)} orders from Google Sheets.")
        return all_orders
    except Exception as e:
        logging.error(f"Ошибка при получении всех заказов: {e}")
        return []

async def update_order_status(order_id, new_status):
    """Обновляет статус заказа по его OrderID."""
    try:
        sh = await gc.open(SPREADSHEET_NAME)
        orders_sheet = await sh.worksheet(ORDERS_SHEET_NAME)
        all_orders = await orders_sheet.get_all_records()
        
        # Получение заголовков столбцов
        headers = await orders_sheet.row_values(1)
        if 'status' not in headers:
            logging.error("Столбец 'status' не найден в листе.")
            return False
        status_col = headers.index('status') + 1  # +1, т.к. индексы начинаются с 0, а столбцы — с 1
        
        # Поиск строки с нужным OrderID
        for idx, order in enumerate(all_orders, start=2):  # start=2, т.к. 1 строка — заголовки
            if str(order.get('OrderID')) == str(order_id):
                # Обновление статуса в конкретной ячейке
                await orders_sheet.update_cell(idx, status_col, new_status)
                logging.info(f"Updated OrderID {order_id} status to '{new_status}'.")
                return True
        logging.warning(f"OrderID {order_id} не найден.")
        return False
    except Exception as e:
        logging.error(f"Ошибка при обновлении статуса заказа {order_id}: {e}")
        return False

# Проверка, является ли пользователь администратором
def is_admin(user_id):
    return user_id in ADMIN_IDS

# Обработчик команд, доступных только администраторам
@router.message(Command("start"))
async def admin_cmd_start(message: Message, state: FSMContext):
    user_id = message.from_user.id
    if not is_admin(user_id):
        await message.answer("У вас нет доступа к этому боту.")
        return
    await message.answer(
        "Привет, Администратор!\n"
        "Доступные команды:\n"
        "/view_orders - Просмотр всех заказов\n"
        "/update_status - Обновление статуса заказа\n"
        "/help - Список доступных команд"
    )
    await state.clear()

@router.message(Command("help"))
async def admin_cmd_help(message: Message, state: FSMContext):
    user_id = message.from_user.id
    if not is_admin(user_id):
        await message.answer("У вас нет доступа к этому боту.")
        return
    await message.answer(
        "Доступные команды:\n"
        "/view_orders - Просмотр всех заказов\n"
        "/update_status - Обновление статуса заказа\n"
        "/help - Список доступных команд"
    )
    await state.clear()

@router.message(Command("view_orders"))
async def admin_cmd_view_orders(message: Message, state: FSMContext):
    user_id = message.from_user.id
    if not is_admin(user_id):
        await message.answer("У вас нет доступа к этому боту.")
        return
    
    all_orders = await get_all_orders()
    
    if not all_orders:
        await message.answer("Нет доступных заказов.")
        return
    
    # Сортировка заказов по дате от новых к старым
    try:
        sorted_orders = sorted(
            all_orders,
            key=lambda x: datetime.datetime.strptime(x['date'], "%Y-%m-%d %H:%M:%S"),
            reverse=True
        )
    except Exception as e:
        logging.error(f"Ошибка при сортировке заказов: {e}")
        sorted_orders = all_orders  # В случае ошибки используем неотсортированный список
    
    # Пагинация: 10 заказов в одном сообщении
    page_size = 10
    total_orders = len(sorted_orders)
    pages = (total_orders + page_size - 1) // page_size  # Количество страниц
    
    for page in range(pages):
        start = page * page_size
        end = start + page_size
        orders_slice = sorted_orders[start:end]
        
        orders_text = "<b>Все заказы:</b>\n\n"
        for order in orders_slice:
            orders_text += (
                f"№ {order['OrderID']}\n"
                f"Пользователь: @{order['user_name']} (ID: {order['user_id']})\n"
                f"Торт: {order['cake_name']}\n"
                f"Цена: {order['price']} руб.\n"
                f"Вкус: {order['taste']}\n"
                f"Размер: {order['size']} персон\n"
                f"Декор: {order['decor']}\n"
                f"Статус: {order['status']}\n"
                f"Дата: {order['date']}\n"
                "-----------------------\n"
            )
        
        await message.answer(orders_text, parse_mode='HTML')

@router.message(Command("update_status"))
async def admin_cmd_update_status(message: Message, state: FSMContext):
    user_id = message.from_user.id
    if not is_admin(user_id):
        await message.answer("У вас нет доступа к этому боту.")
        return
    
    await message.answer("Введите OrderID и новый статус через пробел.\nНапример: `1 Выполнен`")
    await state.set_state(AdminStates.UpdatingOrderStatus)

@router.message(AdminStates.UpdatingOrderStatus)
async def process_update_status(message: Message, state: FSMContext):
    user_id = message.from_user.id
    if not is_admin(user_id):
        await message.answer("У вас нет доступа к этому боту.")
        return
    
    input_text = message.text.strip()
    if not input_text:
        await message.answer("Пожалуйста, введите OrderID и новый статус через пробел.\nНапример: `1 Выполнен`")
        return
    
    parts = input_text.split(maxsplit=1)
    if len(parts) != 2:
        await message.answer("Неверный формат. Введите OrderID и новый статус через пробел.\nНапример: `1 Выполнен`")
        return
    
    order_id, new_status = parts
    if not order_id.isdigit():
        await message.answer("OrderID должен быть числом.")
        return
    
    success = await update_order_status(order_id, new_status)
    if success:
        await message.answer(f"Статус заказа №{order_id} успешно обновлён на '{new_status}'.")
    else:
        await message.answer(f"Не удалось обновить статус заказа №{order_id}. Убедитесь, что OrderID верный.")
    
    await state.clear()

# Дополнительные функции и команды можно добавлять по мере необходимости

# ========= MAIN LAUNCH ==========
async def main():
    global gc
    # Инициализация клиента Google Sheets
    gc = await get_gspread_client()
    
    # Запуск поллинга
    await dp.start_polling(bot, skip_updates=True)

if __name__ == "__main__":
    asyncio.run(main())
