import logging
import asyncio
import datetime
from typing import List, Optional, Dict, Any
import os

import gspread_asyncio
from oauth2client.service_account import ServiceAccountCredentials
from aiogram import Bot, Dispatcher, types, Router
from aiogram.types import (
    ReplyKeyboardMarkup, KeyboardButton, Message,
    InlineKeyboardMarkup, InlineKeyboardButton
)
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from aiogram.fsm.storage.memory import MemoryStorage
from dotenv import load_dotenv

# Загрузка переменных окружения
load_dotenv()

# Настройка логирования
logging.basicConfig(level=logging.INFO)

# Получение токена и админов
BOT_TOKEN: str = os.getenv("BOT_TOKEN")
ADMIN_IDS_STR: str = os.getenv("ADMIN_IDS", "")
try:
    ADMIN_IDS: List[int] = [int(id_.strip()) for id_ in ADMIN_IDS_STR.split(",") if id_.strip().isdigit()]
except ValueError:
    ADMIN_IDS = []
    logging.error("Ошибка при парсинге ADMIN_IDS.")

if not ADMIN_IDS:
    logging.warning("Список ADMIN_IDS пуст. Административные функции будут недоступны.")

SPREADSHEET_NAME: str = os.getenv("SPREADSHEET_NAME", "OrdersForCakes")
ORDERS_SHEET_NAME: str = os.getenv("ORDERS_SHEET_NAME", "orders")
CAKES_SHEET_NAME: str = os.getenv("CAKES_SHEET_NAME", "cakes")

SCOPE: List[str] = [
    "https://spreadsheets.google.com/feeds",
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive.file",
    "https://www.googleapis.com/auth/drive"
]

# Константы для кнопок
CANCEL_TEXT = "Отмена"
CANCEL_BUTTON = KeyboardButton(text=CANCEL_TEXT)

def get_cancel_markup() -> ReplyKeyboardMarkup:
    """Возвращает клавиатуру с кнопкой 'Отмена'."""
    return ReplyKeyboardMarkup(keyboard=[[CANCEL_BUTTON]], resize_keyboard=True)

# Асинхронная инициализация клиента Google Sheets
async def get_gspread_client() -> gspread_asyncio.AsyncioGspreadClient:
    agcm = gspread_asyncio.AsyncioGspreadClientManager(
        lambda: ServiceAccountCredentials.from_json_keyfile_name("credentials.json", SCOPE)
    )
    return await agcm.authorize()

# Инициализация бота, диспетчера и роутера
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())
router = Router()
dp.include_router(router)

# Состояния для оформления заказов
class OrderStates(StatesGroup):
    ChoosingCake = State()
    ChoosingTaste = State()
    ChoosingSize = State()
    ChoosingDecor = State()
    Confirming = State()

# Состояния для административного функционала
class AdminStates(StatesGroup):
    ViewingOrders = State()
    UpdatingOrderStatus = State()

# Глобальная переменная для клиента Google Sheets
gc: Optional[gspread_asyncio.AsyncioGspreadClient] = None

def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS

# Основные клавиатуры для пользователей и администраторов
user_menu = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="Сделать заказ")],
        [KeyboardButton(text="Статус заказов")],
        [KeyboardButton(text=CANCEL_TEXT)]
    ],
    resize_keyboard=True
)

admin_menu = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="Просмотреть заказы")],
        [KeyboardButton(text="Обновить статус заказа")],
        [KeyboardButton(text=CANCEL_TEXT)]
    ],
    resize_keyboard=True
)

async def handle_cancel(message: Message, state: FSMContext) -> None:
    """Обработка команды 'Отмена' – сброс состояний и возврат в меню."""
    await state.clear()
    if is_admin(message.from_user.id):
        await message.answer("Возврат в админ-меню.", reply_markup=admin_menu)
    else:
        await message.answer("Возврат в пользовательское меню.", reply_markup=user_menu)

# Функции для работы с Google Sheets
async def get_catalog_of_cakes() -> List[Dict[str, Any]]:
    try:
        sh = await gc.open(SPREADSHEET_NAME)
        cakes_sheet = await sh.worksheet(CAKES_SHEET_NAME)
        data = await cakes_sheet.get_all_records()
        logging.info(f"Fetched {len(data)} cakes from catalog.")
        return data
    except Exception as e:
        logging.exception(f"Ошибка при получении каталога тортов: {e}")
        return []

async def get_all_orders() -> List[Dict[str, Any]]:
    try:
        sh = await gc.open(SPREADSHEET_NAME)
        orders_sheet = await sh.worksheet(ORDERS_SHEET_NAME)
        all_orders = await orders_sheet.get_all_records()
        logging.info(f"Fetched {len(all_orders)} orders.")
        return all_orders
    except Exception as e:
        logging.exception(f"Ошибка при получении всех заказов: {e}")
        return []

async def get_all_orders_by_user(user_id: int) -> List[Dict[str, Any]]:
    try:
        sh = await gc.open(SPREADSHEET_NAME)
        orders_sheet = await sh.worksheet(ORDERS_SHEET_NAME)
        all_orders = await orders_sheet.get_all_records()
        user_orders = [o for o in all_orders if str(o.get('user_id', '')).strip() == str(user_id)]
        logging.info(f"User {user_id} has {len(user_orders)} orders.")
        return user_orders
    except Exception as e:
        logging.exception(f"Ошибка при получении заказов пользователя {user_id}: {e}")
        return []

async def create_new_order(user_id: int, user_name: str, cake: Dict[str, Any],
                           taste: str, size: str, decor: str) -> Optional[int]:
    try:
        sh = await gc.open(SPREADSHEET_NAME)
        orders_sheet = await sh.worksheet(ORDERS_SHEET_NAME)
        all_values = await orders_sheet.get_all_values()
        order_id = 1 if len(all_values) < 2 else int(all_values[-1][0]) + 1
        status = "ожидается подтверждение администратора"
        current_date = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        await orders_sheet.append_row([
            order_id,
            str(user_id),
            user_name.strip(),
            cake.get('name', '').strip(),
            str(cake.get('price', '')).strip(),
            taste.strip(),
            size.strip(),
            decor.strip(),
            status,
            current_date
        ])
        logging.info(f"Created new order {order_id} for user {user_id}.")
        return order_id
    except Exception as e:
        logging.exception(f"Ошибка при создании заказа: {e}")
        return None

async def update_order_status(order_id: str, new_status: str) -> bool:
    try:
        sh = await gc.open(SPREADSHEET_NAME)
        orders_sheet = await sh.worksheet(ORDERS_SHEET_NAME)
        all_orders = await orders_sheet.get_all_records()
        headers = await orders_sheet.row_values(1)
        if 'status' not in headers:
            logging.error("Столбец 'status' не найден.")
            return False
        status_col = headers.index('status') + 1
        for idx, order in enumerate(all_orders, start=2):
            if str(order.get('OrderID')) == str(order_id):
                await orders_sheet.update_cell(idx, status_col, new_status)
                logging.info(f"Updated OrderID {order_id} to '{new_status}'.")
                return True
        logging.warning(f"OrderID {order_id} не найден.")
        return False
    except Exception as e:
        logging.exception(f"Ошибка при обновлении статуса заказа {order_id}: {e}")
        return False

async def send_status_update(user_id: int, order_id: str, new_status: str) -> None:
    try:
        await bot.send_message(
            chat_id=user_id,
            text=f"✅ Ваш заказ №{order_id} обновлён.\nНовый статус: <b>{new_status}</b>",
            parse_mode='HTML'
        )
        logging.info(f"Sent status update to user {user_id} for order {order_id}.")
    except Exception as e:
        logging.exception(f"Не удалось отправить уведомление пользователю {user_id}: {e}")

# Функция формирования текста и инлайн-клавиатуры для пагинации заказов
def get_orders_page_text_and_markup(orders: List[Dict[str, Any]], page: int, page_size: int = 5):
    total = len(orders)
    pages = (total + page_size - 1) // page_size
    start = page * page_size
    end = start + page_size
    chunk = orders[start:end]
    text = "<b>Ваши заказы:</b>\n\n"
    for o in chunk:
        text += (
            f"№ {o.get('OrderID')}\n"
            f"Торт: {o.get('cake_name')}\n"
            f"Цена: {o.get('price')} руб.\n"
            f"Вкус: {o.get('taste')}\n"
            f"Размер: {o.get('size')} персон\n"
            f"Декор: {o.get('decor')}\n"
            f"Статус: {o.get('status')}\n"
            f"Дата: {o.get('date')}\n"
            "-----------------------\n"
        )
    buttons = []
    if page > 0:
        buttons.append(InlineKeyboardButton("Назад", callback_data=f"orders_page:{page-1}"))
    if page < pages - 1:
        buttons.append(InlineKeyboardButton("Вперёд", callback_data=f"orders_page:{page+1}"))
    markup = InlineKeyboardMarkup(row_width=2)
    if buttons:
        markup.add(*buttons)
    return text, markup

# Обработчики бота

@router.message(Command("start"))
async def handle_start(message: Message, state: FSMContext):
    """Запуск бота. Отправка приветственного сообщения в зависимости от роли пользователя."""
    await state.clear()
    user_id = message.from_user.id
    if is_admin(user_id):
        await message.answer("Привет, Администратор!", reply_markup=admin_menu)
    else:
        await message.answer("Привет! Я бот для оформления заказов на торты.", reply_markup=user_menu)

@router.message(lambda m: m.text == CANCEL_TEXT)
async def handle_cancel_command(message: Message, state: FSMContext):
    """Обработка команды 'Отмена'."""
    await handle_cancel(message, state)

@router.message(lambda m: m.text == "Сделать заказ")
async def user_make_order(message: Message, state: FSMContext):
    """Начало оформления заказа – вывод каталога тортов."""
    user_id = message.from_user.id
    if is_admin(user_id):
        await message.answer("Администратор не может использовать этот функционал.")
        return

    catalog = await get_catalog_of_cakes()
    if not catalog:
        await message.answer("Каталог тортов пока пуст.")
        return

    for cake in catalog:
        text = (
            f"<b>{cake.get('name', '')}</b>\n"
            f"Цена: {cake.get('price', '')} руб.\n"
            f"{cake.get('description', '')}"
        )
        photo = cake.get('photo')
        if photo:
            await message.answer_photo(photo=photo, caption=text, parse_mode='HTML')
        else:
            await message.answer(text, parse_mode='HTML')

    await message.answer("Введите название торта:", reply_markup=get_cancel_markup())
    await state.set_state(OrderStates.ChoosingCake)

@router.message(lambda m: m.text == "Статус заказов")
async def user_check_status(message: Message, state: FSMContext):
    """
    Получение заказов пользователя и вывод информации с пагинацией с использованием инлайн-кнопок.
    """
    user_id = message.from_user.id
    if is_admin(user_id):
        await message.answer("Администратор не может использовать этот функционал.")
        return

    orders = await get_all_orders_by_user(user_id)
    if not orders:
        await message.answer("У вас ещё нет заказов.", reply_markup=user_menu)
        return

    try:
        sorted_orders = sorted(
            orders,
            key=lambda x: datetime.datetime.strptime(x.get('date', ''), "%Y-%m-%d %H:%M:%S"),
            reverse=True
        )
    except Exception as e:
        logging.exception(f"Ошибка при сортировке заказов: {e}")
        sorted_orders = orders

    page = 0
    page_size = 1 #5
    text, markup = get_orders_page_text_and_markup(sorted_orders, page, page_size)
    await message.answer(text, reply_markup=markup, parse_mode="HTML")

@router.callback_query(lambda c: c.data and c.data.startswith("orders_page:"))
async def orders_pagination_callback(callback_query: types.CallbackQuery, state: FSMContext):
    """Обработчик навигации по страницам заказов."""
    try:
        page = int(callback_query.data.split(":")[1])
    except ValueError:
        await callback_query.answer("Неверный номер страницы.")
        return

    orders = await get_all_orders_by_user(callback_query.from_user.id)
    if not orders:
        await callback_query.message.edit_text("У вас ещё нет заказов.", reply_markup=user_menu)
        await callback_query.answer()
        return

    try:
        sorted_orders = sorted(
            orders,
            key=lambda x: datetime.datetime.strptime(x.get('date', ''), "%Y-%m-%d %H:%M:%S"),
            reverse=True
        )
    except Exception as e:
        logging.exception(f"Ошибка при сортировке заказов: {e}")
        sorted_orders = orders

    page_size = 1 #5
    text, markup = get_orders_page_text_and_markup(sorted_orders, page, page_size)
    await callback_query.message.edit_text(text, reply_markup=markup, parse_mode="HTML")
    await callback_query.answer()

@router.message(OrderStates.ChoosingCake)
async def user_choosing_cake(message: Message, state: FSMContext):
    """Выбор торта пользователем."""
    if message.text.strip().lower() == CANCEL_TEXT.lower():
        await handle_cancel(message, state)
        return

    user_id = message.from_user.id
    if is_admin(user_id):
        await message.answer("Администратор не может использовать этот функционал.")
        return

    chosen_cake_name = message.text.strip()
    catalog = await get_catalog_of_cakes()
    chosen_cake = next((c for c in catalog if c.get('name', '').lower() == chosen_cake_name.lower()), None)
    if not chosen_cake:
        await message.answer("Такого торта нет в каталоге. Попробуйте ещё раз или нажмите Отмена.")
        return

    await state.update_data(chosen_cake=chosen_cake)
    await message.answer("Какой вкус вы предпочитаете?", reply_markup=get_cancel_markup())
    await state.set_state(OrderStates.ChoosingTaste)

@router.message(OrderStates.ChoosingTaste)
async def user_choosing_taste(message: Message, state: FSMContext):
    """Выбор вкуса."""
    if message.text.strip().lower() == CANCEL_TEXT.lower():
        await handle_cancel(message, state)
        return

    await state.update_data(taste=message.text.strip())
    await message.answer("На сколько персон?", reply_markup=get_cancel_markup())
    await state.set_state(OrderStates.ChoosingSize)

@router.message(OrderStates.ChoosingSize)
async def user_choosing_size(message: Message, state: FSMContext):
    """Выбор количества персон."""
    if message.text.strip().lower() == CANCEL_TEXT.lower():
        await handle_cancel(message, state)
        return

    size = message.text.strip()
    if not size.isdigit():
        await message.answer("Пожалуйста, введите число или нажмите Отмена.")
        return
    await state.update_data(size=size)
    await message.answer("Какой декор? (например: ягоды, фигурки...)", reply_markup=get_cancel_markup())
    await state.set_state(OrderStates.ChoosingDecor)

@router.message(OrderStates.ChoosingDecor)
async def user_choosing_decor(message: Message, state: FSMContext):
    """Выбор декора."""
    if message.text.strip().lower() == CANCEL_TEXT.lower():
        await handle_cancel(message, state)
        return

    await state.update_data(decor=message.text.strip())
    data = await state.get_data()
    cake = data.get('chosen_cake', {})
    taste = data.get('taste', '')
    size = data.get('size', '')
    decor = data.get('decor', '')

    confirmation_text = (
        f"Пожалуйста, подтвердите заказ:\n\n"
        f"Торт: <b>{cake.get('name', '')}</b>\n"
        f"Вкус: {taste}\n"
        f"Размер: {size} персон\n"
        f"Декор: {decor}\n\n"
        "Отправьте «Да» для подтверждения или «Нет» для отмены."
    )
    # Здесь используется инлайн-клавиатура для подтверждения заказа
    await message.answer(
        confirmation_text,
        parse_mode='HTML',
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="Да", callback_data="confirm_order:yes"),
                 InlineKeyboardButton(text="Нет", callback_data="confirm_order:no")]
            ]
        )
    )
    await state.set_state(OrderStates.Confirming)

@router.callback_query(lambda c: c.data and c.data.startswith("confirm_order:"))
async def order_confirmation_callback(callback_query: types.CallbackQuery, state: FSMContext):
    """Обработка подтверждения заказа через инлайн-кнопки."""
    response = callback_query.data.split(":")[1].lower()
    if response == "no":
        await callback_query.message.edit_text("Заказ отменён.", reply_markup=user_menu)
        await state.clear()
        await callback_query.answer()
        return

    data = await state.get_data()
    user_id = callback_query.from_user.id
    user_name = callback_query.from_user.username or callback_query.from_user.full_name

    order_id = await create_new_order(
        user_id=user_id,
        user_name=user_name,
        cake=data.get('chosen_cake', {}),
        taste=data.get('taste', ''),
        size=data.get('size', ''),
        decor=data.get('decor', '')
    )
    if order_id is not None:
        await callback_query.message.edit_text(
            f"Спасибо! Заказ #{order_id} оформлен.\nОжидается подтверждение администратора.",
            reply_markup=user_menu,
            parse_mode="HTML"
        )
        current_date = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        for admin_id in ADMIN_IDS:
            try:
                await bot.send_message(
                    chat_id=admin_id,
                    text=(
                        f"📦 <b>Новый заказ</b>\n\n"
                        f"№ {order_id}\n"
                        f"Пользователь: @{user_name} (ID: {user_id})\n"
                        f"Торт: {data.get('chosen_cake', {}).get('name', '')}\n"
                        f"Вкус: {data.get('taste', '')}\n"
                        f"Размер: {data.get('size', '')} персон\n"
                        f"Декор: {data.get('decor', '')}\n"
                        f"Статус: ожидается подтверждение администратора\n"
                        f"Дата: {current_date}"
                    ),
                    parse_mode='HTML'
                )
            except Exception as e:
                logging.exception(f"Не удалось отправить уведомление администратору {admin_id}: {e}")
    else:
        await callback_query.message.edit_text("Произошла ошибка при оформлении заказа.", reply_markup=user_menu)
    await state.clear()
    await callback_query.answer()

@router.message(lambda m: m.text == "Просмотреть заказы")
async def admin_view_orders_menu(message: Message, state: FSMContext):
    """Вывод заказов для администратора."""
    user_id = message.from_user.id
    if not is_admin(user_id):
        await message.answer("У вас нет доступа к этому боту.")
        return

    all_orders = await get_all_orders()
    if not all_orders:
        await message.answer("Нет доступных заказов.", reply_markup=admin_menu)
        return

    filtered_orders = [o for o in all_orders if o.get('status') != "Доставлен"]
    if not filtered_orders:
        await message.answer("Нет заказов, ожидающих подтверждения.", reply_markup=admin_menu)
        return

    try:
        sorted_orders = sorted(
            filtered_orders,
            key=lambda x: datetime.datetime.strptime(x.get('date', ''), "%Y-%m-%d %H:%M:%S"),
            reverse=True
        )
    except Exception as e:
        logging.exception(f"Ошибка при сортировке заказов: {e}")
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
                f"№ {o.get('OrderID')}\n"
                f"Пользователь: @{o.get('user_name')} (ID: {o.get('user_id')})\n"
                f"Торт: {o.get('cake_name')}\n"
                f"Цена: {o.get('price')} руб.\n"
                f"Вкус: {o.get('taste')}\n"
                f"Размер: {o.get('size')} персон\n"
                f"Декор: {o.get('decor')}\n"
                f"Статус: {o.get('status')}\n"
                f"Дата: {o.get('date')}\n"
                "-----------------------\n"
            )
        await message.answer(text, parse_mode='HTML', reply_markup=admin_menu)

@router.message(lambda m: m.text == "Обновить статус заказа")
async def admin_update_status_menu(message: Message, state: FSMContext):
    """Запрос на обновление статуса заказа."""
    user_id = message.from_user.id
    if not is_admin(user_id):
        await message.answer("У вас нет доступа к этому боту.")
        return

    await message.answer(
        "Введите OrderID и новый статус через пробел.\nНапример: `1 Доставлен`",
        reply_markup=get_cancel_markup()
    )
    await state.set_state(AdminStates.UpdatingOrderStatus)

@router.message(AdminStates.UpdatingOrderStatus)
async def admin_process_update_status(message: Message, state: FSMContext):
    if message.text.strip().lower() == CANCEL_TEXT.lower():
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
            reply_markup=get_cancel_markup()
        )
        return

    order_id, new_status = parts
    if not order_id.isdigit():
        await message.answer("OrderID должен быть числом.", reply_markup=get_cancel_markup())
        return

    success = await update_order_status(order_id, new_status)
    if success:
        all_orders = await get_all_orders()
        order = next((o for o in all_orders if str(o.get('OrderID')) == order_id), None)
        if order:
            user_id_to_notify = int(order.get('user_id', 0))
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
        await message.answer("Не удалось обновить статус. Проверьте OrderID.", reply_markup=admin_menu)

    await state.clear()

# Запуск бота
async def main():
    global gc
    gc = await get_gspread_client()
    await dp.start_polling(bot, skip_updates=True)

if __name__ == "__main__":
    asyncio.run(main())
