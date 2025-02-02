import logging
import asyncio
import datetime
import gspread_asyncio
from oauth2client.service_account import ServiceAccountCredentials
from aiogram import Bot, Dispatcher, types
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove
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
ADMIN_IDS = os.getenv("ADMIN_IDS", "")
# Преобразуем строку с ID в список целых чисел
try:
    ADMIN_IDS = [int(id_.strip()) for id_ in ADMIN_IDS.split(",") if id_.strip().isdigit()]
except ValueError:
    ADMIN_IDS = []
    logging.error("Ошибка при парсинге ADMIN_IDS. Убедитесь, что они являются числами и разделены запятыми.")

if not ADMIN_IDS:
    logging.warning("Список ADMIN_IDS пуст. Административные функции будут недоступны.")

SPREADSHEET_NAME = os.getenv("SPREADSHEET_NAME", "OrdersForCakes")
ORDERS_SHEET_NAME = os.getenv("ORDERS_SHEET_NAME", "orders")
CAKES_SHEET_NAME = os.getenv("CAKES_SHEET_NAME", "cakes")

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

# Состояния
class OrderStates(StatesGroup):
    ChoosingCake = State()
    ChoosingTaste = State()
    ChoosingSize = State()
    ChoosingDecor = State()
    Confirming = State()

class AdminStates(StatesGroup):
    ViewingOrders = State()
    UpdatingOrderStatus = State()

# Глобальная переменная для Google Sheets
gc = None

# Проверка на администратора
def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS

# Клавиатуры для меню
# Пользовательское меню
user_menu = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="Сделать заказ")],
        [KeyboardButton(text="Статус заказов")],
        [KeyboardButton(text="Отмена")]
    ],
    resize_keyboard=True
)

# Администраторское меню
admin_menu = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="Просмотреть заказы")],
        [KeyboardButton(text="Обновить статус заказа")],
        [KeyboardButton(text="Отмена")]
    ],
    resize_keyboard=True
)

# Вспомогательные функции работы с Google Sheets
async def get_catalog_of_cakes():
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
    try:
        sh = await gc.open(SPREADSHEET_NAME)
        orders_sheet = await sh.worksheet(ORDERS_SHEET_NAME)
        all_orders = await orders_sheet.get_all_records()
        logging.info(f"Fetched {len(all_orders)} orders from Google Sheets.")
        user_orders = [
            o for o in all_orders
            if str(o.get('user_id', '')).strip() == str(user_id).strip()
        ]
        logging.info(f"User {user_id} has {len(user_orders)} orders.")
        return user_orders
    except Exception as e:
        logging.error(f"Ошибка при получении заказов пользователя {user_id}: {e}")
        return []

async def create_new_order(user_id, user_name, cake, taste, size, decor):
    try:
        sh = await gc.open(SPREADSHEET_NAME)
        orders_sheet = await sh.worksheet(ORDERS_SHEET_NAME)
        all_values = await orders_sheet.get_all_values()
        if len(all_values) < 2:
            order_id = 1
        else:
            last_row = all_values[-1]
            order_id = int(last_row[0]) + 1
        status = "ожидается подтверждение администратора"
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
            current_date
        ])
        logging.info(f"Created new order {order_id} for user {user_id}.")
        return order_id
    except Exception as e:
        logging.error(f"Ошибка при создании заказа: {e}")
        return None

async def update_order_status(order_id, new_status):
    try:
        sh = await gc.open(SPREADSHEET_NAME)
        orders_sheet = await sh.worksheet(ORDERS_SHEET_NAME)
        all_orders = await orders_sheet.get_all_records()

        headers = await orders_sheet.row_values(1)
        if 'status' not in headers:
            logging.error("Столбец 'status' не найден в листе.")
            return False
        status_col = headers.index('status') + 1

        for idx, order in enumerate(all_orders, start=2):
            if str(order.get('OrderID')) == str(order_id):
                await orders_sheet.update_cell(idx, status_col, new_status)
                logging.info(f"Updated OrderID {order_id} status to '{new_status}'.")
                return True
        logging.warning(f"OrderID {order_id} не найден.")
        return False
    except Exception as e:
        logging.error(f"Ошибка при обновлении статуса заказа {order_id}: {e}")
        return False

async def send_status_update(user_id, order_id, new_status):
    try:
        await bot.send_message(
            chat_id=user_id,
            text=f"✅ Ваш заказ №{order_id} был обновлён.\nНовый статус: <b>{new_status}</b>",
            parse_mode='HTML'
        )
        logging.info(f"Sent status update to user {user_id} for order {order_id}.")
    except Exception as e:
        logging.error(f"Не удалось отправить уведомление пользователю {user_id}: {e}")

# === Обработчики ===

@router.message(Command("start"))
async def handle_start(message: Message, state: FSMContext):
    """Запуск бота. Показываем меню в зависимости от ролей."""
    await state.clear()
    user_id = message.from_user.id
    if is_admin(user_id):
        await message.answer(
            "Привет, Администратор!",
            reply_markup=admin_menu
        )
    else:
        await message.answer(
            "Привет! Я бот для оформления заказов на торты.",
            reply_markup=user_menu
        )

@router.message(lambda m: m.text == "Отмена")
async def handle_cancel(message: Message, state: FSMContext):
    """Кнопка «Отмена» для возврата в главное меню."""
    await state.clear()
    user_id = message.from_user.id
    if is_admin(user_id):
        await message.answer("Возврат в админ-меню.", reply_markup=admin_menu)
    else:
        await message.answer("Возврат в пользовательское меню.", reply_markup=user_menu)

# === Пользовательское меню ===
@router.message(lambda m: m.text == "Сделать заказ")
async def user_make_order(message: Message, state: FSMContext):
    """Начало оформления заказа."""
    user_id = message.from_user.id
    if is_admin(user_id):
        await message.answer("Администратор не может использовать этот функционал.")
        #return

    catalog = await get_catalog_of_cakes()
    if not catalog:
        await message.answer("Каталог тортов пока пуст.")
        return

    # Выводим список тортов
    for cake in catalog:
        text = f"<b>{cake['name']}</b>\nЦена: {cake['price']} руб.\n{cake['description']}"
        photo = cake.get('photo')
        if photo:
            await message.answer_photo(photo=photo, caption=text, parse_mode='HTML')
        else:
            await message.answer(text, parse_mode='HTML')

    await message.answer(
        "Введите название торта:",
        reply_markup=ReplyKeyboardMarkup(
            keyboard=[
                [KeyboardButton(text="Отмена")]
            ],
            resize_keyboard=True
        )
    )
    await state.set_state(OrderStates.ChoosingCake)

@router.message(lambda m: m.text == "Статус заказов")
async def user_check_status(message: Message, state: FSMContext):
    """Проверка статусов заказов пользователя."""
    user_id = message.from_user.id
    if is_admin(user_id):
        await message.answer("Администратор не может использовать этот функционал.")
        #return

    orders = await get_all_orders_by_user(user_id)
    if not orders:
        await message.answer("У вас ещё нет заказов.", reply_markup=user_menu)
        return

    # Сортировка по дате
    try:
        sorted_orders = sorted(
            orders,
            key=lambda x: datetime.datetime.strptime(x['date'], "%Y-%m-%d %H:%M:%S"),
            reverse=True
        )
    except Exception as e:
        logging.error(f"Ошибка при сортировке заказов: {e}")
        sorted_orders = orders

    # Вывод
    page_size = 5
    total = len(sorted_orders)
    pages = (total + page_size - 1) // page_size

    for page in range(pages):
        start = page * page_size
        end = start + page_size
        chunk = sorted_orders[start:end]

        text = "<b>Ваши заказы:</b>\n\n"
        for o in chunk:
            text += (
                f"№ {o['OrderID']}\n"
                f"Торт: {o['cake_name']}\n"
                f"Цена: {o['price']} руб.\n"
                f"Вкус: {o['taste']}\n"
                f"Размер: {o['size']} персон\n"
                f"Декор: {o['decor']}\n"
                f"Статус: {o['status']}\n"
                f"Дата: {o['date']}\n"
                "-----------------------\n"
            )

        await message.answer(text, parse_mode='HTML', reply_markup=user_menu)

# === Обработчики состояний оформления заказа (OrderStates) ===
@router.message(OrderStates.ChoosingCake)
async def user_choosing_cake(message: Message, state: FSMContext):
    if message.text == "Отмена":
        await handle_cancel(message, state)
        return

    user_id = message.from_user.id
    if is_admin(user_id):
        await message.answer("Администратор не может использовать этот функционал.")
        #return

    chosen_cake_name = message.text.strip()
    catalog = await get_catalog_of_cakes()
    chosen_cake = next((c for c in catalog if c['name'].lower() == chosen_cake_name.lower()), None)
    if not chosen_cake:
        await message.answer("Такого торта нет в каталоге. Попробуйте ещё раз или нажмите Отмена.")
        return

    await state.update_data(chosen_cake=chosen_cake)
    await message.answer(
        "Какой вкус вы предпочитаете?",
        reply_markup=ReplyKeyboardMarkup(
            keyboard=[
                [KeyboardButton(text="Отмена")]
            ],
            resize_keyboard=True
        )
    )
    await state.set_state(OrderStates.ChoosingTaste)

@router.message(OrderStates.ChoosingTaste)
async def user_choosing_taste(message: Message, state: FSMContext):
    if message.text == "Отмена":
        await handle_cancel(message, state)
        return

    await state.update_data(taste=message.text.strip())
    await message.answer(
        "На сколько персон?",
        reply_markup=ReplyKeyboardMarkup(
            keyboard=[
                [KeyboardButton(text="Отмена")]
            ],
            resize_keyboard=True
        )
    )
    await state.set_state(OrderStates.ChoosingSize)

@router.message(OrderStates.ChoosingSize)
async def user_choosing_size(message: Message, state: FSMContext):
    if message.text == "Отмена":
        await handle_cancel(message, state)
        return

    size = message.text.strip()
    if not size.isdigit():
        await message.answer("Число, пожалуйста. Или нажмите Отмена.")
        return
    await state.update_data(size=size)
    await message.answer(
        "Какой декор? (например: ягоды, фигурки...)",
        reply_markup=ReplyKeyboardMarkup(
            keyboard=[
                [KeyboardButton(text="Отмена")]
            ],
            resize_keyboard=True
        )
    )
    await state.set_state(OrderStates.ChoosingDecor)

@router.message(OrderStates.ChoosingDecor)
async def user_choosing_decor(message: Message, state: FSMContext):
    if message.text == "Отмена":
        await handle_cancel(message, state)
        return

    await state.update_data(decor=message.text.strip())
    data = await state.get_data()
    cake = data['chosen_cake']
    taste = data['taste']
    size = data['size']
    decor = data['decor']

    confirmation_text = (
        f"Пожалуйста, подтвердите заказ:\n\n"
        f"Торт: <b>{cake['name']}</b>\n"
        f"Вкус: {taste}\n"
        f"Размер: {size} персон\n"
        f"Декор: {decor}\n\n"
        "Отправьте «Да» для подтверждения или «Нет» для отмены."
    )
    await message.answer(
        confirmation_text,
        parse_mode='HTML',
        reply_markup=ReplyKeyboardMarkup(
            keyboard=[
                [KeyboardButton(text="Да"), KeyboardButton(text="Нет")]
            ],
            resize_keyboard=True
        )
    )
    await state.set_state(OrderStates.Confirming)

@router.message(OrderStates.Confirming)
async def user_confirming_order(message: Message, state: FSMContext):
    response = message.text.lower()
    if response == "отмена":
        await handle_cancel(message, state)
        return

    data = await state.get_data()
    user_id = message.from_user.id
    user_name = message.from_user.username or message.from_user.full_name

    if response == "да":
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
                f"Спасибо! Заказ #{order_id} оформлен.\n"
                "Ожидается подтверждение администратора.",
                reply_markup=user_menu
            )
            # Уведомление администраторов
            current_date = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            for admin_id in ADMIN_IDS:
                try:
                    await bot.send_message(
                        chat_id=admin_id,
                        text=(
                            f"📦 <b>Новый заказ</b>\n\n"
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
            await message.answer("Произошла ошибка при оформлении заказа.", reply_markup=user_menu)
        await state.clear()

    elif response == "нет":
        await message.answer("Заказ отменён.", reply_markup=user_menu)
        await state.clear()
    else:
        await message.answer("Введите «Да» или «Нет».")

@router.message(lambda m: m.text == "Просмотреть заказы")
async def admin_view_orders_menu(message: Message, state: FSMContext):
    """Обработчик нажатия на кнопку «Просмотреть заказы»."""
    user_id = message.from_user.id
    if not is_admin(user_id):
        await message.answer("У вас нет доступа к этому боту.")
        return

    all_orders = await get_all_orders()
    if not all_orders:
        await message.answer("Нет доступных заказов.", reply_markup=admin_menu)
        return

    # Фильтрация: Статус != "Доставлен"
    filtered_orders = [o for o in all_orders if o.get('status') != "Доставлен"]
    if not filtered_orders:
        await message.answer("Нет заказов, ожидающих подтверждения.", reply_markup=admin_menu)
        return

    try:
        sorted_orders = sorted(
            filtered_orders,
            key=lambda x: datetime.datetime.strptime(x['date'], "%Y-%m-%d %H:%M:%S"),
            reverse=True
        )
    except Exception as e:
        logging.error(f"Ошибка при сортировке заказов: {e}")
        sorted_orders = filtered_orders

    page_size = 10
    total_orders = len(sorted_orders)
    pages = (total_orders + page_size - 1) // page_size

    for page in range(pages):
        start = page * page_size
        end = start + page_size
        chunk = sorted_orders[start:end]

        text = "<b>Заказы:</b>\n\n"
        for o in chunk:
            text += (
                f"№ {o['OrderID']}\n"
                f"Пользователь: @{o['user_name']} (ID: {o['user_id']})\n"
                f"Торт: {o['cake_name']}\n"
                f"Цена: {o['price']} руб.\n"
                f"Вкус: {o['taste']}\n"
                f"Размер: {o['size']} персон\n"
                f"Декор: {o['decor']}\n"
                f"Статус: {o['status']}\n"
                f"Дата: {o['date']}\n"
                "-----------------------\n"
            )
        await message.answer(text, parse_mode='HTML', reply_markup=admin_menu)

@router.message(lambda m: m.text == "Обновить статус заказа")
async def admin_update_status_menu(message: Message, state: FSMContext):
    """Начинаем процесс обновления статуса: просим ввести OrderID и новый статус."""
    user_id = message.from_user.id
    if not is_admin(user_id):
        await message.answer("У вас нет доступа к этому боту.")
        return

    await message.answer(
        "Введите OrderID и новый статус через пробел.\nНапример: `1 Доставлен`",
        reply_markup=ReplyKeyboardMarkup(
            keyboard=[
                [KeyboardButton(text="Отмена")]
            ],
            resize_keyboard=True
        )
    )
    await state.set_state(AdminStates.UpdatingOrderStatus)

@router.message(AdminStates.UpdatingOrderStatus)
async def admin_process_update_status(message: Message, state: FSMContext):
    """Собственно обновляем статус."""
    if message.text == "Отмена":
        await handle_cancel(message, state)
        return

    user_id = message.from_user.id
    if not is_admin(user_id):
        await message.answer("У вас нет доступа к этому боту.")
        return

    parts = message.text.strip().split(maxsplit=1)
    if len(parts) != 2:
        await message.answer(
            "Неверный формат. Введите OrderID и новый статус через пробел.\nНапример: `1 Доставлен`",
            reply_markup=ReplyKeyboardMarkup(
                keyboard=[
                    [KeyboardButton(text="Отмена")]
                ],
                resize_keyboard=True
            )
        )
        return

    order_id, new_status = parts
    if not order_id.isdigit():
        await message.answer(
            "OrderID должен быть числом.",
            reply_markup=ReplyKeyboardMarkup(
                keyboard=[
                    [KeyboardButton(text="Отмена")]
                ],
                resize_keyboard=True
            )
        )
        return

    success = await update_order_status(order_id, new_status)
    if success:
        # Отправляем уведомление пользователю
        all_orders = await get_all_orders()
        order = next((o for o in all_orders if str(o['OrderID']) == str(order_id)), None)
        if order:
            user_id_to_notify = int(order['user_id'])
            await send_status_update(user_id_to_notify, order_id, new_status)
            await message.answer(
                f"Статус заказа №{order_id} обновлён на '{new_status}'. Уведомление пользователю отправлено.",
                reply_markup=admin_menu
            )
        else:
            await message.answer(
                f"Статус заказа №{order_id} обновлён, но не удалось найти заказ для уведомления пользователя.",
                reply_markup=admin_menu
            )
    else:
        await message.answer(
            "Не удалось обновить статус. Проверьте OrderID.",
            reply_markup=admin_menu
        )

    await state.clear()

# ===== Запуск бота =====
async def main():
    global gc
    gc = await get_gspread_client()
    await dp.start_polling(bot, skip_updates=True)

if __name__ == "__main__":
    asyncio.run(main())
