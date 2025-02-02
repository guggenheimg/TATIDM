# combined_bot.py

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
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_IDS = os.getenv("ADMIN_IDS")
# Преобразуем строку с ID в список целых чисел
try:
    ADMIN_IDS = [int(id_.strip()) for id_ in ADMIN_IDS.split(",") if id_.strip().isdigit()]
except ValueError:
    ADMIN_IDS = []
    logging.error("Ошибка при парсинге ADMIN_IDS. Убедитесь, что они являются числами и разделены запятыми.")

# Проверка наличия хотя бы одного админа
if not ADMIN_IDS:
    logging.warning("Список ADMIN_IDS пуст. Административные команды будут недоступны.")

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
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())
router = Router()
dp.include_router(router)

# Определение состояний для FSM
class OrderStates(StatesGroup):
    ChoosingCake = State()
    ChoosingTaste = State()
    ChoosingSize = State()
    ChoosingDecor = State()
    Confirming = State()

class AdminStates(StatesGroup):
    ViewingOrders = State()
    UpdatingOrderStatus = State()

# Глобальная переменная для клиента Google Sheets
gc = None

# Вспомогательные функции
async def get_catalog_of_cakes():
    """Считывает каталог тортов из листа 'cakes'."""
    try:
        sh = await gc.open(SPREADSHEET_NAME)
        cakes_sheet = await sh.worksheet(CAKES_SHEET_NAME)
        data = await cakes_sheet.get_all_records()
        logging.info(f"Fetched {len(data)} cakes from catalog.")
        return data
    except Exception as e:
        logging.error(f"Ошибка при получении каталога тортов: {e}")
        return []

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

async def get_all_orders_by_user(user_id):
    """Возвращает все заказы, оформленные на заданный user_id."""
    try:
        sh = await gc.open(SPREADSHEET_NAME)
        orders_sheet = await sh.worksheet(ORDERS_SHEET_NAME)
        all_orders = await orders_sheet.get_all_records()
        logging.info(f"Fetched {len(all_orders)} orders from Google Sheets.")
        for order in all_orders:
            logging.info(f"OrderID: {order.get('OrderID')}, user_id: {order.get('user_id')}")
        # Приводим оба user_id к строке и убираем пробелы
        user_orders = [
            order for order in all_orders 
            if str(order.get('user_id', '')).strip() == str(user_id).strip()
        ]
        logging.info(f"User {user_id} has {len(user_orders)} orders.")
        return user_orders
    except Exception as e:
        logging.error(f"Ошибка при получении заказов пользователя {user_id}: {e}")
        return []

async def create_new_order(user_id, user_name, cake, taste, size, decor):
    """Создаёт новый заказ в листе 'orders'."""
    try:
        sh = await gc.open(SPREADSHEET_NAME)
        orders_sheet = await sh.worksheet(ORDERS_SHEET_NAME)
        # Получение последнего OrderID и инкремент
        all_values = await orders_sheet.get_all_values()
        if len(all_values) < 2:
            order_id = 1
        else:
            last_order = all_values[-1]
            order_id = int(last_order[0]) + 1
        status = "ожидается подтверждение администратора"  # Установлен новый статус
        current_date = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        await orders_sheet.append_row([
            order_id,
            str(user_id).strip(),
            user_name.strip(),
            cake['name'].strip(),
            str(cake['price']).strip(),
            taste.strip(),
            str(size).strip(),
            decor.strip(),
            status,
            current_date  # Новое поле даты
        ])
        logging.info(f"Created new order {order_id} for user {user_id}.")
        return order_id
    except Exception as e:
        logging.error(f"Ошибка при создании заказа: {e}")
        return None

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

async def send_status_update(user_id, order_id, new_status):
    """Отправляет уведомление пользователю о смене статуса заказа."""
    try:
        await bot.send_message(
            chat_id=user_id,
            text=f"✅ Ваш заказ №{order_id} был обновлён.\nНовый статус: <b>{new_status}</b>",
            parse_mode='HTML'
        )
        logging.info(f"Sent status update to user {user_id} for order {order_id}.")
    except Exception as e:
        logging.error(f"Не удалось отправить уведомление пользователю {user_id}: {e}")

# Проверка, является ли пользователь администратором
def is_admin(user_id):
    return user_id in ADMIN_IDS

# Обработчики команд и сообщений

# Пользовательские команды
@router.message(Command("start"))
async def cmd_start(message: Message, state: FSMContext):
    if is_admin(message.from_user.id):
        await message.answer(
            "Привет, Администратор!\n"
            "Используй команды /help для списка доступных команд."
        )
    else:
        await message.answer(
            "Привет! Я бот для оформления заказов на торты.\n"
            "Используй команду /menu, чтобы посмотреть каталог тортов."
        )
    await state.clear()

@router.message(Command("menu"))
async def cmd_menu(message: Message, state: FSMContext):
    if is_admin(message.from_user.id):
        await message.answer("Администратор не может использовать эту команду.")
        #return
    
    catalog = await get_catalog_of_cakes()
    if not catalog:
        await message.answer("Каталог тортов пока пуст.")
        return
    
    for cake in catalog:
        text = f"<b>{cake['name']}</b>\nЦена: {cake['price']} руб.\n{cake['description']}"
        photo = cake.get('photo')  # Предполагается, что ссылка на фото хранится в поле 'photo'
        if photo:
            # Отправляем фото и описание напрямую по URL
            await message.answer_photo(photo=photo, caption=text, parse_mode='HTML')
        else:
            await message.answer(text, parse_mode='HTML')
    
    await message.answer("Выберите торт, введя его название:")
    await state.set_state(OrderStates.ChoosingCake)

@router.message(OrderStates.ChoosingCake)
async def process_choosing_cake(message: Message, state: FSMContext):
    if is_admin(message.from_user.id):
        await message.answer("Администратор не может использовать этот функционал.")
        #return
    
    chosen_cake_name = message.text.strip()
    catalog = await get_catalog_of_cakes()
    chosen_cake = next(
        (cake for cake in catalog if cake['name'].lower() == chosen_cake_name.lower()), 
        None
    )
    
    if not chosen_cake:
        await message.answer("Такого торта нет в каталоге. Попробуйте ещё раз или введите /menu.")
        return
    
    await state.update_data(chosen_cake=chosen_cake)
    await message.answer(f"Вы выбрали торт <b>{chosen_cake['name']}</b>.\n"
                         "Какой вкус вы предпочитаете?", parse_mode='HTML')
    await state.set_state(OrderStates.ChoosingTaste)

@router.message(OrderStates.ChoosingTaste)
async def process_choosing_taste(message: Message, state: FSMContext):
    if is_admin(message.from_user.id):
        await message.answer("Администратор не может использовать этот функционал.")
        #return
    
    taste = message.text.strip()
    await state.update_data(taste=taste)
    await message.answer("На сколько персон вам нужен торт?")
    await state.set_state(OrderStates.ChoosingSize)

@router.message(OrderStates.ChoosingSize)
async def process_choosing_size(message: Message, state: FSMContext):
    if is_admin(message.from_user.id):
        await message.answer("Администратор не может использовать этот функционал.")
        #return
    
    size = message.text.strip()
    if not size.isdigit():
        await message.answer("Пожалуйста, введите числовое значение для количества персон.")
        return
    await state.update_data(size=size)
    await message.answer("Какой декор вы бы хотели? (например, ягоды, фигурки, надпись или без декора)")
    await state.set_state(OrderStates.ChoosingDecor)

@router.message(OrderStates.ChoosingDecor)
async def process_choosing_decor(message: Message, state: FSMContext):
    if is_admin(message.from_user.id):
        await message.answer("Администратор не может использовать этот функционал.")
        #return
    
    decor = message.text.strip()
    await state.update_data(decor=decor)
    
    data = await state.get_data()
    cake = data['chosen_cake']
    taste = data['taste']
    size = data['size']
    decor = data['decor']
    
    confirmation_text = (
        f"Пожалуйста, подтвердите ваш заказ:\n\n"
        f"Торт: <b>{cake['name']}</b>\n"
        f"Вкус: {taste}\n"
        f"Размер: {size} персон\n"
        f"Декор: {decor}\n\n"
        f"Если всё верно, напишите 'Да'. Для отмены напишите 'Нет'."
    )
    
    await message.answer(confirmation_text, parse_mode='HTML')
    await state.set_state(OrderStates.Confirming)

@router.message(OrderStates.Confirming)
async def process_confirming(message: Message, state: FSMContext):
    if is_admin(message.from_user.id):
        await message.answer("Администратор не может использовать этот функционал.")
        #return
    
    response = message.text.strip().lower()
    if response == "да":
        data = await state.get_data()
        user_id = message.from_user.id
        user_name = message.from_user.username or message.from_user.full_name
        order_id = await create_new_order(
            user_id=user_id,
            user_name=user_name,
            cake=data['chosen_cake'],
            taste=data['taste'],
            size=data['size'],
            decor=data['decor']
        )
        if order_id is not None:
            await message.answer(
                f"Спасибо! Ваш заказ принят.\n"
                f"Номер заказа: <b>{order_id}</b>\n"
                "Ожидайте подтверждения администратора."
            )
            
            # Уведомление администраторов о новом заказе
            current_date = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            for admin_id in ADMIN_IDS:
                try:
                    await bot.send_message(
                        chat_id=admin_id,
                        text=(
                            f"📦 <b>Новый заказ</b> 📦\n\n"
                            f"№ {order_id}\n"
                            f"Пользователь: @{user_name} (ID: {user_id})\n"
                            f"Торт: {data['chosen_cake']['name']}\n"
                            f"Вкус: {data['taste']}\n"
                            f"Размер: {data['size']} персон\n"
                            f"Декор: {data['decor']}\n"
                            f"Статус: ожидается подтверждение администратора\n"
                            f"Дата: {current_date}"
                        ),
                        parse_mode='HTML'
                    )
                except Exception as e:
                    logging.error(f"Не удалось отправить уведомление администратору {admin_id}: {e}")
        else:
            await message.answer("Произошла ошибка при оформлении заказа. Пожалуйста, попробуйте позже.")
        await state.clear()
    elif response == "нет":
        await message.answer("Заказ отменён. Если хотите оформить новый заказ, используйте команду /menu.")
        await state.clear()
    else:
        await message.answer("Пожалуйста, ответьте 'Да' или 'Нет' для подтверждения заказа.")

@router.message(Command("status"))
async def cmd_status(message: Message, command: CommandObject):
    if is_admin(message.from_user.id):
        await message.answer("Администратор не может использовать этот функционал.")
        #return
    
    # Получение Telegram ID пользователя
    user_id = message.from_user.id
    logging.info(f"User {user_id} invoked /status command.")
    
    user_orders = await get_all_orders_by_user(user_id)
    
    if not user_orders:
        await message.answer("У вас ещё нет заказов.")
        return
    
    logging.info(f"User {user_id} has {len(user_orders)} orders. Preparing to send them.")
    
    # Сортировка заказов от самых новых к самым старым по дате
    try:
        sorted_orders = sorted(
            user_orders, 
            key=lambda x: datetime.datetime.strptime(x['date'], "%Y-%m-%d %H:%M:%S"), 
            reverse=True
        )
    except Exception as e:
        logging.error(f"Ошибка при сортировке заказов: {e}")
        sorted_orders = user_orders  # В случае ошибки используем неотсортированный список
    
    # Пагинация: 5 заказов в одном сообщении
    page_size = 5
    total_orders = len(sorted_orders)
    pages = (total_orders + page_size - 1) // page_size  # Количество страниц
    
    logging.info(f"Total orders: {total_orders}, Pages: {pages}")
    
    for page in range(pages):
        start = page * page_size
        end = start + page_size
        orders_slice = sorted_orders[start:end]
        
        orders_text = "<b>Ваши заказы:</b>\n\n"
        for order in orders_slice:
            orders_text += (
                f"№ {order['OrderID']}\n"
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

# Административные команды
@router.message(Command("help"))
async def admin_cmd_help(message: Message, state: FSMContext):
    user_id = message.from_user.id
    if not is_admin(user_id):
        await message.answer("У вас нет доступа к этому боту.")
        return
    await message.answer(
        "Доступные команды:\n"
        "/view_orders - Просмотр заказов, ожидающих подтверждения\n"
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
    
    # Фильтрация заказов, которые не имеют статус "Доставлен"
    filtered_orders = [order for order in all_orders if order.get('status') != "Доставлен"]
    
    if not filtered_orders:
        await message.answer("Нет заказов, ожидающих подтверждения.")
        return
    
    # Сортировка заказов по дате от новых к старым
    try:
        sorted_orders = sorted(
            filtered_orders,
            key=lambda x: datetime.datetime.strptime(x['date'], "%Y-%m-%d %H:%M:%S"),
            reverse=True
        )
    except Exception as e:
        logging.error(f"Ошибка при сортировке заказов: {e}")
        sorted_orders = filtered_orders  # В случае ошибки используем неотсортированный список
    
    # Пагинация: 10 заказов в одном сообщении
    page_size = 10
    total_orders = len(sorted_orders)
    pages = (total_orders + page_size - 1) // page_size  # Количество страниц
    
    for page in range(pages):
        start = page * page_size
        end = start + page_size
        orders_slice = sorted_orders[start:end]
        
        orders_text = "<b>Заказы, ожидающие подтверждения:</b>\n\n"
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
    
    await message.answer("Введите OrderID и новый статус через пробел.\nНапример: `1 Доставлен`")
    await state.set_state(AdminStates.UpdatingOrderStatus)

@router.message(AdminStates.UpdatingOrderStatus)
async def process_update_status(message: Message, state: FSMContext):
    user_id = message.from_user.id
    if not is_admin(user_id):
        await message.answer("У вас нет доступа к этому боту.")
        return
    
    input_text = message.text.strip()
    if not input_text:
        await message.answer("Пожалуйста, введите OrderID и новый статус через пробел.\nНапример: `1 Доставлен`")
        return
    
    parts = input_text.split(maxsplit=1)
    if len(parts) != 2:
        await message.answer("Неверный формат. Введите OrderID и новый статус через пробел.\nНапример: `1 Доставлен`")
        return
    
    order_id, new_status = parts
    if not order_id.isdigit():
        await message.answer("OrderID должен быть числом.")
        return
    
    success = await update_order_status(order_id, new_status)
    if success:
        # Получение информации о заказе для отправки уведомления
        all_orders = await get_all_orders()
        order = next((o for o in all_orders if str(o['OrderID']) == str(order_id)), None)
        if order:
            user_id_to_notify = int(order['user_id'])
            await send_status_update(user_id_to_notify, order_id, new_status)
            await message.answer(f"Статус заказа №{order_id} успешно обновлён на '{new_status}'. Уведомление пользователю отправлено.")
        else:
            await message.answer(f"Статус заказа №{order_id} обновлён на '{new_status}', но не удалось найти информацию о заказе для уведомления пользователя.")
    else:
        await message.answer(f"Не удалось обновить статус заказа №{order_id}. Убедитесь, что OrderID верный.")
    
    await state.clear()

# ========= MAIN LAUNCH ==========
async def main():
    global gc
    # Инициализация клиента Google Sheets
    gc = await get_gspread_client()
    
    # Запуск поллинга
    await dp.start_polling(bot, skip_updates=True)

if __name__ == "__main__":
    asyncio.run(main())
