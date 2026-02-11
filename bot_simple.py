import os
import sqlite3
import secrets
import asyncio
import logging
from datetime import datetime, timedelta
from typing import Dict, List

from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    InlineKeyboardMarkup, InlineKeyboardButton,
    LabeledPrice, PreCheckoutQuery
)

# ============================================================================
# НАСТРОЙКИ - ИЗМЕНИТЕ ЭТИ ЗНАЧЕНИЯ
# ============================================================================

BOT_TOKEN = os.getenv("BOT_TOKEN", "YOUR_BOT_TOKEN_HERE")
ADMIN_IDS_STR = os.getenv("ADMIN_IDS", "")

# Парсим ADMIN_IDS в список чисел
if ADMIN_IDS_STR:
    ADMIN_IDS = [int(x.strip()) for x in ADMIN_IDS_STR.split(",") if x.strip()]
else:
    ADMIN_IDS = []

# Ваш Telegram username для покупок (БЕЗ @)
SELLER_USERNAME = os.getenv("SELLER_USERNAME", "your_telegram")

# Цены (только в звездах, так как СБП убран)
PRICES = {
    "1month": {"stars": 50, "days": 30, "name": "1 месяц"},
    "3months": {"stars": 120, "days": 90, "name": "3 месяца"},
    "lifetime": {"stars": 250, "days": 36500, "name": "Навсегда"}
}

# База данных
DB_FILE = "licenses.db"

# ============================================================================
# ЛОГИРОВАНИЕ
# ============================================================================

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# ============================================================================
# ИНИЦИАЛИЗАЦИЯ БОТА
# ============================================================================

bot = Bot(token=BOT_TOKEN)
storage = MemoryStorage()
dp = Dispatcher(storage=storage)

# ============================================================================
# БАЗА ДАННЫХ SQLite
# ============================================================================

def init_database():
    """Инициализация базы данных"""
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    
    # Таблица пользователей
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            first_name TEXT,
            total_spent_stars INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    
    # Таблица ключей
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS license_keys (
            key TEXT PRIMARY KEY,
            user_id INTEGER NOT NULL,
            plan TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            expires_at TIMESTAMP NOT NULL,
            payment_method TEXT NOT NULL,
            activated INTEGER DEFAULT 0,
            hwid TEXT,
            activated_at TIMESTAMP
        )
    """)
    
    # Таблица транзакций
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS transactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            plan TEXT NOT NULL,
            amount INTEGER NOT NULL,
            method TEXT NOT NULL,
            license_key TEXT NOT NULL,
            timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    
    conn.commit()
    conn.close()
    logger.info("Database initialized")


def generate_key() -> str:
    """Генерация уникального ключа"""
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    
    while True:
        key = f"PWEPER-{secrets.token_hex(8).upper()}"
        cursor.execute("SELECT key FROM license_keys WHERE key = ?", (key,))
        if cursor.fetchone() is None:
            conn.close()
            return key


def create_license(user_id: int, plan: str, payment_method: str, 
                  username: str = None, first_name: str = None) -> str:
    """Создание новой лицензии"""
    key = generate_key()
    expires_at = datetime.now() + timedelta(days=PRICES[plan]["days"])
    
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    
    # Создаем/обновляем пользователя
    cursor.execute("""
        INSERT INTO users (user_id, username, first_name)
        VALUES (?, ?, ?)
        ON CONFLICT(user_id) DO UPDATE SET
            username = excluded.username,
            first_name = excluded.first_name
    """, (user_id, username, first_name))
    
    # Создаем ключ
    cursor.execute("""
        INSERT INTO license_keys 
        (key, user_id, plan, expires_at, payment_method)
        VALUES (?, ?, ?, ?, ?)
    """, (key, user_id, plan, expires_at.isoformat(), payment_method))
    
    # Обновляем статистику
    cursor.execute("""
        UPDATE users 
        SET total_spent_stars = total_spent_stars + ?
        WHERE user_id = ?
    """, (PRICES[plan]["stars"], user_id))
    
    conn.commit()
    conn.close()
    
    logger.info(f"License created: {key} for user {user_id}")
    return key


def get_user_licenses(user_id: int) -> List[Dict]:
    """Получить все лицензии пользователя"""
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    cursor.execute("""
        SELECT * FROM license_keys 
        WHERE user_id = ?
        ORDER BY created_at DESC
    """, (user_id,))
    
    rows = cursor.fetchall()
    conn.close()
    
    result = []
    for row in rows:
        expires_at = datetime.fromisoformat(row["expires_at"])
        days_left = (expires_at - datetime.now()).days
        
        result.append({
            "key": row["key"],
            "plan": row["plan"],
            "activated": bool(row["activated"]),
            "expires_at": row["expires_at"],
            "days_left": max(0, days_left),
            "expired": days_left < 0
        })
    
    return result


def add_transaction(user_id: int, plan: str, amount: int, method: str, key: str):
    """Добавить транзакцию"""
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    
    cursor.execute("""
        INSERT INTO transactions 
        (user_id, plan, amount, method, license_key)
        VALUES (?, ?, ?, ?, ?)
    """, (user_id, plan, amount, method, key))
    
    conn.commit()
    conn.close()


def get_statistics() -> Dict:
    """Получить статистику"""
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    # Общие данные
    cursor.execute("SELECT COUNT(*) as count FROM users")
    total_users = cursor.fetchone()["count"]
    
    cursor.execute("SELECT COUNT(*) as count FROM license_keys")
    total_keys = cursor.fetchone()["count"]
    
    cursor.execute("SELECT COUNT(*) as count FROM transactions")
    total_transactions = cursor.fetchone()["count"]
    
    # Активные ключи
    cursor.execute("""
        SELECT COUNT(*) as count FROM license_keys 
        WHERE datetime(expires_at) > datetime('now')
    """)
    active_keys = cursor.fetchone()["count"]
    
    # Всего звезд
    cursor.execute("SELECT SUM(amount) as total FROM transactions")
    total_stars = cursor.fetchone()["total"] or 0
    
    conn.close()
    
    return {
        "total_users": total_users,
        "total_keys": total_keys,
        "total_transactions": total_transactions,
        "active_keys": active_keys,
        "total_stars": total_stars
    }


# ============================================================================
# FSM СОСТОЯНИЯ
# ============================================================================

class AdminStates(StatesGroup):
    waiting_user_id = State()
    waiting_plan = State()


# ============================================================================
# КЛАВИАТУРЫ
# ============================================================================

def main_menu_kb(is_admin: bool = False) -> InlineKeyboardMarkup:
    """Главное меню"""
    buttons = []
    
    if is_admin:
        buttons.append([InlineKeyboardButton(text="⚙️ Админ-панель", callback_data="admin_panel")])
    
    buttons.extend([
        [InlineKeyboardButton(text="💎 Купить лицензию", callback_data="buy")],
        [InlineKeyboardButton(text="🔑 Мои лицензии", callback_data="my_licenses")],
        [InlineKeyboardButton(text="❓ Помощь", callback_data="help")]
    ])
    
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def admin_menu_kb() -> InlineKeyboardMarkup:
    """Админ меню"""
    buttons = [
        [InlineKeyboardButton(text="➕ Выдать ключ", callback_data="admin_give_key")],
        [InlineKeyboardButton(text="📊 Статистика", callback_data="admin_stats")],
        [InlineKeyboardButton(text="◀️ Назад", callback_data="start")]
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def payment_method_kb() -> InlineKeyboardMarkup:
    """Выбор способа оплаты"""
    buttons = [
        [InlineKeyboardButton(text="⭐ Telegram Stars", callback_data="payment_stars")],
        [InlineKeyboardButton(text="💬 Написать продавцу", url=f"https://t.me/{SELLER_USERNAME}")],
        [InlineKeyboardButton(text="◀️ Назад", callback_data="start")]
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def plans_kb() -> InlineKeyboardMarkup:
    """Выбор плана"""
    buttons = [
        [InlineKeyboardButton(
            text=f"{PRICES['1month']['name']} - {PRICES['1month']['stars']} ⭐",
            callback_data="plan_1month"
        )],
        [InlineKeyboardButton(
            text=f"{PRICES['3months']['name']} - {PRICES['3months']['stars']} ⭐",
            callback_data="plan_3months"
        )],
        [InlineKeyboardButton(
            text=f"{PRICES['lifetime']['name']} - {PRICES['lifetime']['stars']} ⭐",
            callback_data="plan_lifetime"
        )],
        [InlineKeyboardButton(text="◀️ Назад", callback_data="buy")]
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def back_to_menu_kb() -> InlineKeyboardMarkup:
    """Кнопка возврата в меню"""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="◀️ Главное меню", callback_data="start")]
    ])


# ============================================================================
# ОБРАБОТЧИКИ КОМАНД
# ============================================================================

@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    """Команда /start"""
    user_name = message.from_user.first_name
    is_admin = message.from_user.id in ADMIN_IDS
    
    text = (
        f"👋 Привет, {user_name}!\n\n"
        f"🎨 <b>Timecyc Editor by Pweper</b>\n"
        f"Профессиональный редактор timecyc для GTA.\n\n"
        f"✨ <b>Возможности:</b>\n"
        f"• Визуальное редактирование неба и погоды\n"
        f"• Поддержка всех параметров timecyc\n"
        f"• Предпросмотр в реальном времени\n"
        f"• Экспорт в JSON\n\n"
        f"💎 Выберите действие ниже:"
    )
    
    await message.answer(text, reply_markup=main_menu_kb(is_admin), parse_mode="HTML")


@dp.message(Command("admin"))
async def cmd_admin(message: types.Message):
    """Команда /admin"""
    if message.from_user.id not in ADMIN_IDS:
        await message.answer("❌ У вас нет прав администратора")
        return
    
    stats = get_statistics()
    
    text = (
        "⚙️ <b>Админ-панель</b>\n\n"
        f"👥 Пользователей: {stats['total_users']}\n"
        f"🔑 Всего ключей: {stats['total_keys']}\n"
        f"✅ Активных: {stats['active_keys']}\n"
        f"💰 Транзакций: {stats['total_transactions']}\n"
        f"⭐ Заработано: {stats['total_stars']} звезд"
    )
    
    await message.answer(text, reply_markup=admin_menu_kb(), parse_mode="HTML")


# ============================================================================
# ОБРАБОТЧИКИ CALLBACK
# ============================================================================

@dp.callback_query(F.data == "start")
async def cb_start(callback: types.CallbackQuery):
    """Возврат в главное меню"""
    user_name = callback.from_user.first_name
    is_admin = callback.from_user.id in ADMIN_IDS
    
    text = (
        f"👋 Привет, {user_name}!\n\n"
        f"🎨 <b>Timecyc Editor by Pweper</b>\n\n"
        f"💎 Выберите действие:"
    )
    
    await callback.message.edit_text(text, reply_markup=main_menu_kb(is_admin), parse_mode="HTML")
    await callback.answer()


@dp.callback_query(F.data == "buy")
async def cb_buy(callback: types.CallbackQuery):
    """Покупка лицензии"""
    text = (
        "💳 <b>Выберите способ оплаты:</b>\n\n"
        "⭐ <b>Telegram Stars</b> - мгновенная оплата\n"
        f"💬 <b>Написать продавцу</b> - @{SELLER_USERNAME}\n\n"
        "<i>При оплате продавцу напрямую ключ будет выдан вручную</i>"
    )
    
    await callback.message.edit_text(text, reply_markup=payment_method_kb(), parse_mode="HTML")
    await callback.answer()


@dp.callback_query(F.data == "payment_stars")
async def cb_payment_stars(callback: types.CallbackQuery):
    """Выбор плана для Stars"""
    text = (
        "📦 <b>Выберите план подписки:</b>\n\n"
        "1️⃣ <b>1 месяц</b> - базовая лицензия\n"
        "3️⃣ <b>3 месяца</b> - выгодное предложение\n"
        "♾️ <b>Навсегда</b> - безлимитный доступ\n\n"
        "После покупки вы получите уникальный ключ активации."
    )
    
    await callback.message.edit_text(text, reply_markup=plans_kb(), parse_mode="HTML")
    await callback.answer()


@dp.callback_query(F.data.startswith("plan_"))
async def cb_plan_selected(callback: types.CallbackQuery):
    """Выбран план - создание счета"""
    plan = callback.data.replace("plan_", "")
    price = PRICES[plan]["stars"]
    
    # Создаем счет на оплату Stars
    prices = [LabeledPrice(label=f"Timecyc Editor - {PRICES[plan]['name']}", amount=price)]
    
    await bot.send_invoice(
        chat_id=callback.from_user.id,
        title=f"Timecyc Editor ({PRICES[plan]['name']})",
        description=f"Лицензия на {PRICES[plan]['name']}",
        payload=f"{plan}_stars_{callback.from_user.id}",
        provider_token="",
        currency="XTR",
        prices=prices
    )
    
    await callback.answer("Счет создан! Оплатите его для активации.")


@dp.pre_checkout_query()
async def pre_checkout_handler(pre_checkout_query: PreCheckoutQuery):
    """Обработка pre-checkout для Stars"""
    await bot.answer_pre_checkout_query(pre_checkout_query.id, ok=True)


@dp.message(F.successful_payment)
async def successful_payment_handler(message: types.Message):
    """Успешная оплата через Stars"""
    payment = message.successful_payment
    payload = payment.invoice_payload
    
    parts = payload.split("_")
    plan = parts[0]
    user_id = int(parts[2])
    
    # Создаем лицензию
    key = create_license(
        user_id, plan, "stars",
        username=message.from_user.username,
        first_name=message.from_user.first_name
    )
    
    # Записываем транзакцию
    add_transaction(user_id, plan, PRICES[plan]["stars"], "stars", key)
    
    # Отправляем ключ пользователю
    text = (
        f"✅ <b>Оплата успешна!</b>\n\n"
        f"🔑 Ваш ключ активации:\n"
        f"<code>{key}</code>\n\n"
        f"📱 <b>Как использовать:</b>\n"
        f"1. Запустите Timecyc Editor\n"
        f"2. Введите этот ключ при первом запуске\n"
        f"3. Программа автоматически активируется\n\n"
        f"⏱ Срок действия: {PRICES[plan]['days']} дней\n"
        f"💾 Сохраните ключ в надежном месте!"
    )
    
    await message.answer(text, reply_markup=back_to_menu_kb(), parse_mode="HTML")
    
    # Уведомление админам
    for admin_id in ADMIN_IDS:
        try:
            await bot.send_message(
                admin_id,
                f"💰 Новая покупка!\n\n"
                f"Пользователь: {message.from_user.id} (@{message.from_user.username})\n"
                f"План: {plan}\n"
                f"Сумма: {PRICES[plan]['stars']} ⭐\n"
                f"Ключ: {key}"
            )
        except:
            pass


@dp.callback_query(F.data == "my_licenses")
async def cb_my_licenses(callback: types.CallbackQuery):
    """Мои лицензии"""
    licenses = get_user_licenses(callback.from_user.id)
    
    if not licenses:
        text = (
            "🔑 <b>У вас пока нет лицензий</b>\n\n"
            "Приобретите лицензию, чтобы начать использовать Timecyc Editor!"
        )
    else:
        text = "🔑 <b>Ваши лицензии:</b>\n\n"
        
        for lic in licenses:
            status = "❌ Истекла" if lic["expired"] else f"✅ Активна ({lic['days_left']} дней)"
            activated = "✓ Привязана" if lic["activated"] else "✗ Не активирована"
            
            text += (
                f"<code>{lic['key']}</code>\n"
                f"Статус: {status}\n"
                f"Привязка: {activated}\n"
                f"План: {lic['plan']}\n\n"
            )
    
    await callback.message.edit_text(text, reply_markup=back_to_menu_kb(), parse_mode="HTML")
    await callback.answer()


@dp.callback_query(F.data == "help")
async def cb_help(callback: types.CallbackQuery):
    """Помощь"""
    text = (
        "❓ <b>Помощь</b>\n\n"
        "<b>Как купить лицензию:</b>\n"
        "1. Нажмите 'Купить лицензию'\n"
        "2. Выберите способ оплаты:\n"
        "   • Telegram Stars - мгновенно\n"
        f"   • Написать @{SELLER_USERNAME} - вручную\n"
        "3. Оплатите счет\n"
        "4. Получите ключ активации\n\n"
        "<b>Как активировать:</b>\n"
        "1. Запустите Timecyc Editor\n"
        "2. При первом запуске введите ключ\n"
        "3. Программа автоматически активируется\n\n"
        "<b>Важно:</b>\n"
        "• Ключ привязывается к компьютеру\n"
        "• Один ключ = один компьютер\n\n"
        f"📧 Поддержка: @{SELLER_USERNAME}"
    )
    
    await callback.message.edit_text(text, reply_markup=back_to_menu_kb(), parse_mode="HTML")
    await callback.answer()


# ============================================================================
# АДМИН-ПАНЕЛЬ
# ============================================================================

@dp.callback_query(F.data == "admin_panel")
async def cb_admin_panel(callback: types.CallbackQuery):
    """Админ панель"""
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer("❌ Нет доступа", show_alert=True)
        return
    
    stats = get_statistics()
    
    text = (
        "⚙️ <b>Админ-панель</b>\n\n"
        f"👥 Пользователей: {stats['total_users']}\n"
        f"🔑 Всего ключей: {stats['total_keys']}\n"
        f"✅ Активных: {stats['active_keys']}\n"
        f"💰 Транзакций: {stats['total_transactions']}\n"
        f"⭐ Заработано: {stats['total_stars']} звезд"
    )
    
    await callback.message.edit_text(text, reply_markup=admin_menu_kb(), parse_mode="HTML")
    await callback.answer()


@dp.callback_query(F.data == "admin_give_key")
async def cb_admin_give_key(callback: types.CallbackQuery, state: FSMContext):
    """Выдать ключ вручную"""
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer("❌ Нет доступа", show_alert=True)
        return
    
    await callback.message.edit_text(
        "👤 Введите ID пользователя:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="❌ Отмена", callback_data="admin_panel")]
        ])
    )
    await state.set_state(AdminStates.waiting_user_id)
    await callback.answer()


@dp.message(AdminStates.waiting_user_id)
async def admin_get_user_id(message: types.Message, state: FSMContext):
    """Получен ID пользователя"""
    try:
        user_id = int(message.text)
        await state.update_data(user_id=user_id)
        
        text = (
            "📦 Выберите план для выдачи:\n\n"
            "Отправьте команду:\n"
            "/plan_1month - 1 месяц\n"
            "/plan_3months - 3 месяца\n"
            "/plan_lifetime - навсегда"
        )
        
        await message.answer(text)
        await state.set_state(AdminStates.waiting_plan)
    except ValueError:
        await message.answer("❌ Неверный формат ID. Введите число.")


@dp.message(AdminStates.waiting_plan, F.text.startswith("/plan_"))
async def admin_create_key(message: types.Message, state: FSMContext):
    """Создание ключа админом"""
    plan = message.text.replace("/plan_", "")
    
    if plan not in PRICES:
        await message.answer("❌ Неверный план")
        return
    
    data = await state.get_data()
    user_id = data["user_id"]
    
    # Создаем ключ
    key = create_license(user_id, plan, "admin_gift")
    
    # Отправляем пользователю
    try:
        await bot.send_message(
            user_id,
            f"🎁 Вам выдан ключ активации!\n\n"
            f"🔑 <code>{key}</code>\n\n"
            f"План: {PRICES[plan]['name']}\n"
            f"Срок: {PRICES[plan]['days']} дней",
            parse_mode="HTML"
        )
        status = "✅ Ключ отправлен пользователю"
    except:
        status = "⚠️ Не удалось отправить пользователю"
    
    await message.answer(
        f"✅ Ключ создан!\n\n"
        f"🔑 <code>{key}</code>\n"
        f"👤 User ID: {user_id}\n"
        f"📦 План: {plan}\n\n"
        f"{status}",
        parse_mode="HTML"
    )
    
    await state.clear()


@dp.callback_query(F.data == "admin_stats")
async def cb_admin_stats(callback: types.CallbackQuery):
    """Детальная статистика"""
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer("❌ Нет доступа", show_alert=True)
        return
    
    stats = get_statistics()
    
    text = (
        "📊 <b>Детальная статистика</b>\n\n"
        f"👥 Всего пользователей: {stats['total_users']}\n"
        f"🔑 Всего ключей: {stats['total_keys']}\n"
        f"✅ Активных ключей: {stats['active_keys']}\n"
        f"💰 Всего транзакций: {stats['total_transactions']}\n"
        f"⭐ Заработано звезд: {stats['total_stars']}"
    )
    
    await callback.message.edit_text(text, reply_markup=admin_menu_kb(), parse_mode="HTML")
    await callback.answer()


# ============================================================================
# ЗАПУСК БОТА
# ============================================================================

async def main():
    """Запуск бота"""
    logger.info("Starting bot...")
    
    # Инициализация базы данных
    init_database()
    
    # Проверка токена
    if BOT_TOKEN == "YOUR_BOT_TOKEN_HERE":
        logger.error("Please set BOT_TOKEN in environment variables!")
        return
    
    if not ADMIN_IDS:
        logger.warning("No ADMIN_IDS set. Admin panel will be disabled.")
    else:
        logger.info(f"Admin IDs: {ADMIN_IDS}")
    
    try:
        await dp.start_polling(bot, skip_updates=True)
    except Exception as e:
        logger.error(f"Error: {e}")
    finally:
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
