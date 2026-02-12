#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Telegram Bot для продажи лицензий Timecyc Editor
Работает с PHP API на Reg.ru + локальная SQLite база
ЗАЩИЩЕННАЯ ВЕРСИЯ - с API ключом
"""

import os
import sqlite3
import secrets
import asyncio
import logging
import requests
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
# НАСТРОЙКИ
# ============================================================================

BOT_TOKEN        = os.getenv("BOT_TOKEN", "YOUR_BOT_TOKEN_HERE")
ADMIN_IDS_STR    = os.getenv("ADMIN_IDS", "")
ADMIN_IDS        = [int(x.strip()) for x in ADMIN_IDS_STR.split(",") if x.strip()] if ADMIN_IDS_STR else []
SELLER_USERNAME  = os.getenv("SELLER_USERNAME", "your_telegram")
API_URL          = os.getenv("API_URL", "https://pweper.ru/api.php")

# ============================================================================
# 🔐 СЕКРЕТНЫЙ API КЛЮЧ - ОБЯЗАТЕЛЬНО ИЗМЕНИТЕ!
# ============================================================================
# Это должен быть тот же ключ, что и в api.php!
# Сгенерируйте случайный ключ, например:
# import secrets; print(secrets.token_hex(32))

API_SECRET_KEY = os.getenv("API_SECRET_KEY", "ЗАМЕНИТЕ_ЭТОТ_КЛЮЧ_НА_СЛУЧАЙНЫЙ_ОЧЕНЬ_ДЛИННЫЙ_СЕКРЕТНЫЙ_КОД_12345")

# ============================================================================

DB_FILE          = "licenses.db"

PRICES = {
    "1month":   {"stars": 50,  "days": 30,    "name": "1 месяц"},
    "3months":  {"stars": 120, "days": 90,    "name": "3 месяца"},
    "lifetime": {"stars": 250, "days": 36500, "name": "Навсегда"},
}

# ============================================================================
# ЛОГИРОВАНИЕ
# ============================================================================

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

# ============================================================================
# БОТ
# ============================================================================

bot     = Bot(token=BOT_TOKEN)
storage = MemoryStorage()
dp      = Dispatcher(storage=storage)

# ============================================================================
# БАЗА ДАННЫХ SQLite
# ============================================================================

def init_db():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()

    c.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id   INTEGER PRIMARY KEY,
            username  TEXT,
            first_name TEXT,
            total_spent_stars INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS license_keys (
            key            TEXT PRIMARY KEY,
            user_id        INTEGER NOT NULL,
            plan           TEXT NOT NULL,
            created_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            expires_at     TIMESTAMP NOT NULL,
            payment_method TEXT NOT NULL,
            activated      INTEGER DEFAULT 0,
            hwid           TEXT,
            activated_at   TIMESTAMP
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS transactions (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id    INTEGER NOT NULL,
            plan       TEXT NOT NULL,
            amount     INTEGER NOT NULL,
            method     TEXT NOT NULL,
            license_key TEXT NOT NULL,
            timestamp  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    conn.commit()
    conn.close()
    logger.info("Database initialized")


def _gen_key() -> str:
    """Генерация уникального ключа в формате PWEPER-XXXXXXXX-XXXXXXXX-XXXXXXXX"""
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    while True:
        key = (
            f"PWEPER"
            f"-{secrets.token_hex(4).upper()}"
            f"-{secrets.token_hex(4).upper()}"
            f"-{secrets.token_hex(4).upper()}"
        )
        c.execute("SELECT key FROM license_keys WHERE key = ?", (key,))
        if c.fetchone() is None:
            conn.close()
            return key


# ============================================================================
# 🔐 ЗАЩИЩЕННАЯ ФУНКЦИЯ - СИНХРОНИЗАЦИЯ С СЕРВЕРОМ
# ============================================================================

def sync_key_to_server(key: str, plan: str, expires_at: str) -> bool:
    """
    Отправляет созданный ключ на сервер Reg.ru с API ключом
    
    Args:
        key: Ключ активации (например PWEPER-XXXXXXXX-XXXXXXXX-XXXXXXXX)
        plan: Тариф (1month, 3months, lifetime)
        expires_at: Дата истечения в ISO формате
    
    Returns:
        bool: True если ключ успешно добавлен на сервер, False если ошибка
    """
    try:
        # URL endpoint для добавления ключа
        url = f"{API_URL.rstrip('/api.php')}/api.php/add_key"
        
        payload = {
            "secret": API_SECRET_KEY,
            "key": key,
            "plan": plan,
            "expires_at": expires_at
        }
        
        # 🔐 Добавляем секретный ключ в заголовки
        headers = {
            "Content-Type": "application/json",
            "secret": API_SECRET_KEY
        }
        
        logger.info(f"📤 Отправка ключа на сервер: {key}")
        logger.info(f"   URL: {url}")
        logger.info(f"   Данные: {payload}")
        logger.info(f"   API ключ: {API_SECRET_KEY[:10]}...")
        
        response = requests.post(url, json=payload, headers=headers, timeout=10)
        
        if response.status_code == 200:
            data = response.json()
            if data.get("success"):
                logger.info(f"✅ Ключ {key} успешно добавлен на сервер")
                return True
            else:
                logger.error(f"❌ Сервер вернул ошибку: {data.get('error', 'Unknown error')}")
                return False
        elif response.status_code == 401:
            logger.error(f"❌ API ключ отсутствует! Проверьте настройки.")
            return False
        elif response.status_code == 403:
            logger.error(f"❌ Неверный API ключ! Убедитесь что в bot.py и api.php одинаковые ключи.")
            return False
        else:
            logger.error(f"❌ Сервер вернул код {response.status_code}")
            logger.error(f"   Ответ: {response.text}")
            return False
            
    except requests.exceptions.Timeout:
        logger.error(f"⏱️ Таймаут при отправке ключа на сервер")
        return False
    except Exception as e:
        logger.error(f"❌ Ошибка синхронизации с сервером: {e}")
        return False


def create_license(user_id: int, plan: str, method: str,
                   username: str = None, first_name: str = None) -> str:
    """Создать лицензию в SQLite И на сервере, вернуть ключ"""
    key = _gen_key()
    expires_at = datetime.now() + timedelta(days=PRICES[plan]["days"])
    expires_at_str = expires_at.isoformat()

    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()

    # Сохраняем локально
    c.execute("""
        INSERT INTO users (user_id, username, first_name)
        VALUES (?, ?, ?)
        ON CONFLICT(user_id) DO UPDATE SET
            username   = excluded.username,
            first_name = excluded.first_name
    """, (user_id, username, first_name))

    c.execute("""
        INSERT INTO license_keys (key, user_id, plan, expires_at, payment_method)
        VALUES (?, ?, ?, ?, ?)
    """, (key, user_id, plan, expires_at_str, method))

    if method != "admin_gift":
        c.execute("""
            UPDATE users SET total_spent_stars = total_spent_stars + ?
            WHERE user_id = ?
        """, (PRICES[plan]["stars"], user_id))

    conn.commit()
    conn.close()

    logger.info(f"License created locally: {key} | user={user_id} | plan={plan} | method={method}")
    
    # 🔐 Синхронизируем с сервером (с API ключом)
    sync_success = sync_key_to_server(key, plan, expires_at_str)
    if sync_success:
        logger.info(f"✅ Ключ {key} синхронизирован с сервером")
    else:
        logger.warning(f"⚠️ Ключ {key} создан локально, но НЕ синхронизирован с сервером!")
        logger.warning(f"   Проверьте: API_SECRET_KEY в bot.py и api.php должны совпадать!")
    
    return key


def get_user_licenses(user_id: int) -> List[Dict]:
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute("SELECT * FROM license_keys WHERE user_id = ? ORDER BY created_at DESC", (user_id,))
    rows = c.fetchall()
    conn.close()

    result = []
    for row in rows:
        expires_at = datetime.fromisoformat(row["expires_at"])
        days_left  = (expires_at - datetime.now()).days
        result.append({
            "key":       row["key"],
            "plan":      row["plan"],
            "activated": bool(row["activated"]),
            "expires_at":row["expires_at"],
            "days_left": max(0, days_left),
            "expired":   days_left < 0,
        })
    return result


def add_transaction(user_id: int, plan: str, amount: int, method: str, key: str):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("""
        INSERT INTO transactions (user_id, plan, amount, method, license_key)
        VALUES (?, ?, ?, ?, ?)
    """, (user_id, plan, amount, method, key))
    conn.commit()
    conn.close()


def get_stats() -> Dict:
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()

    c.execute("SELECT COUNT(*) as n FROM users")
    total_users = c.fetchone()["n"]

    c.execute("SELECT COUNT(*) as n FROM license_keys")
    total_keys = c.fetchone()["n"]

    c.execute("SELECT COUNT(*) as n FROM license_keys WHERE datetime(expires_at) > datetime('now')")
    active_keys = c.fetchone()["n"]

    c.execute("SELECT COUNT(*) as n FROM transactions")
    total_tx = c.fetchone()["n"]

    c.execute("SELECT SUM(amount) as s FROM transactions")
    total_stars = c.fetchone()["s"] or 0

    conn.close()
    return {
        "total_users":  total_users,
        "total_keys":   total_keys,
        "active_keys":  active_keys,
        "total_tx":     total_tx,
        "total_stars":  total_stars,
    }


# ============================================================================
# FSM СОСТОЯНИЯ
# ============================================================================

class AdminStates(StatesGroup):
    waiting_user_id = State()
    waiting_plan    = State()


# ============================================================================
# КЛАВИАТУРЫ
# ============================================================================

def main_menu_kb(is_admin: bool = False) -> InlineKeyboardMarkup:
    rows = []
    if is_admin:
        rows.append([InlineKeyboardButton(text="⚙️ Админ-панель", callback_data="admin_panel")])
    rows += [
        [InlineKeyboardButton(text="💎 Купить лицензию", callback_data="buy")],
        [InlineKeyboardButton(text="🔑 Мои лицензии", callback_data="my_licenses")],
        [InlineKeyboardButton(text="❓ Помощь", callback_data="help")]
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def buy_kb() -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text="⭐ Оплатить звёздами", callback_data="payment_stars")],
        [InlineKeyboardButton(text=f"💬 Написать @{SELLER_USERNAME}", url=f"https://t.me/{SELLER_USERNAME}")],
        [InlineKeyboardButton(text="🔙 Назад", callback_data="main")]
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def plan_kb() -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(
            text=f"{PRICES['1month']['name']} — {PRICES['1month']['stars']}⭐",
            callback_data="plan_1month"
        )],
        [InlineKeyboardButton(
            text=f"{PRICES['3months']['name']} — {PRICES['3months']['stars']}⭐",
            callback_data="plan_3months"
        )],
        [InlineKeyboardButton(
            text=f"{PRICES['lifetime']['name']} — {PRICES['lifetime']['stars']}⭐",
            callback_data="plan_lifetime"
        )],
        [InlineKeyboardButton(text="🔙 Назад", callback_data="buy")]
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def back_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔙 Назад", callback_data="main")]
    ])


def admin_menu_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🎁 Выдать ключ", callback_data="admin_give_key")],
        [InlineKeyboardButton(text="📊 Статистика", callback_data="admin_stats")],
        [InlineKeyboardButton(text="🔧 Тест API", callback_data="admin_test_api")],
        [InlineKeyboardButton(text="🔙 Назад", callback_data="main")]
    ])


def admin_plan_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=PRICES["1month"]["name"], callback_data="admin_plan_1month")],
        [InlineKeyboardButton(text=PRICES["3months"]["name"], callback_data="admin_plan_3months")],
        [InlineKeyboardButton(text=PRICES["lifetime"]["name"], callback_data="admin_plan_lifetime")],
        [InlineKeyboardButton(text="❌ Отмена", callback_data="admin_panel")]
    ])


# ============================================================================
# КОМАНДЫ
# ============================================================================

@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    user_id = message.from_user.id
    is_admin = user_id in ADMIN_IDS
    
    text = (
        f"👋 <b>Добро пожаловать в Timecyc Editor!</b>\n\n"
        f"Здесь вы можете приобрести лицензию на редактор timecyc для GTA San Andreas.\n\n"
        f"💎 <b>Доступные тарифы:</b>\n"
        f"• {PRICES['1month']['name']}: {PRICES['1month']['stars']}⭐\n"
        f"• {PRICES['3months']['name']}: {PRICES['3months']['stars']}⭐\n"
        f"• {PRICES['lifetime']['name']}: {PRICES['lifetime']['stars']}⭐\n\n"
        f"🔑 После оплаты вы получите ключ активации."
    )
    
    await message.answer(text, reply_markup=main_menu_kb(is_admin), parse_mode="HTML")


@dp.callback_query(F.data == "main")
async def cb_main(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    is_admin = user_id in ADMIN_IDS
    
    text = (
        f"👋 <b>Главное меню</b>\n\n"
        f"💎 <b>Доступные тарифы:</b>\n"
        f"• {PRICES['1month']['name']}: {PRICES['1month']['stars']}⭐\n"
        f"• {PRICES['3months']['name']}: {PRICES['3months']['stars']}⭐\n"
        f"• {PRICES['lifetime']['name']}: {PRICES['lifetime']['stars']}⭐"
    )
    
    await callback.message.edit_text(text, reply_markup=main_menu_kb(is_admin), parse_mode="HTML")
    await callback.answer()


# ============================================================================
# ПОКУПКА
# ============================================================================

@dp.callback_query(F.data == "buy")
async def cb_buy(callback: types.CallbackQuery):
    text = (
        "💳 <b>Выберите способ оплаты:</b>\n\n"
        "⭐ <b>Telegram Stars</b> — моментальная активация\n"
        f"💬 <b>Написать продавцу</b> — @{SELLER_USERNAME}"
    )
    await callback.message.edit_text(text, reply_markup=buy_kb(), parse_mode="HTML")
    await callback.answer()


@dp.callback_query(F.data == "payment_stars")
async def cb_payment_stars(callback: types.CallbackQuery):
    text = "📦 <b>Выберите тариф:</b>"
    await callback.message.edit_text(text, reply_markup=plan_kb(), parse_mode="HTML")
    await callback.answer()


@dp.callback_query(F.data.startswith("plan_"))
async def cb_plan(callback: types.CallbackQuery):
    plan = callback.data.replace("plan_", "")
    price = PRICES[plan]
    
    await callback.answer("Создаю счёт...", show_alert=False)
    
    try:
        prices = [LabeledPrice(label=price["name"], amount=price["stars"])]
        
        await bot.send_invoice(
            chat_id=callback.from_user.id,
            title=f"Timecyc Editor — {price['name']}",
            description=f"Лицензия на {price['days']} дней",
            payload=f"{plan}",
            currency="XTR",
            prices=prices
        )
        
        await callback.message.edit_text(
            "💳 <b>Счёт отправлен!</b>\n\nОплатите его, чтобы получить ключ.",
            reply_markup=back_kb(),
            parse_mode="HTML"
        )
    except Exception as e:
        logger.error(f"Invoice error: {e}")
        await callback.message.edit_text(
            f"❌ <b>Ошибка создания счёта</b>\n\n{str(e)}",
            reply_markup=back_kb(),
            parse_mode="HTML"
        )


@dp.pre_checkout_query()
async def process_pre_checkout(pre_checkout_query: PreCheckoutQuery):
    await bot.answer_pre_checkout_query(pre_checkout_query.id, ok=True)


@dp.message(F.successful_payment)
async def process_successful_payment(message: types.Message):
    user_id = message.from_user.id
    plan = message.successful_payment.invoice_payload
    
    key = create_license(
        user_id,
        plan,
        "telegram_stars",
        message.from_user.username,
        message.from_user.first_name
    )
    
    add_transaction(user_id, plan, PRICES[plan]["stars"], "telegram_stars", key)
    
    text = (
        f"✅ <b>Оплата прошла успешно!</b>\n\n"
        f"🔑 Ваш ключ активации:\n"
        f"<code>{key}</code>\n\n"
        f"📦 Тариф: {PRICES[plan]['name']}\n"
        f"⏱ Срок действия: {PRICES[plan]['days']} дней\n\n"
        f"<b>Как активировать:</b>\n"
        f"1. Запустите Timecyc Editor\n"
        f"2. Введите ключ при первом запуске\n"
        f"3. Ключ привяжется к вашему компьютеру\n\n"
        f"💾 Ключ также сохранён в разделе «Мои лицензии»"
    )
    
    await message.answer(text, parse_mode="HTML")


# ============================================================================
# МОИ ЛИЦЕНЗИИ
# ============================================================================

@dp.callback_query(F.data == "my_licenses")
async def cb_my_licenses(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    licenses = get_user_licenses(user_id)
    
    if not licenses:
        text = "У вас пока нет лицензий.\n\nНажмите «Купить лицензию», чтобы приобрести."
    else:
        text = "🔑 <b>Ваши лицензии:</b>\n\n"
        for lic in licenses:
            status = "✅ Активна" if not lic["expired"] else "❌ Истекла"
            activated = "🔗 Привязана" if lic["activated"] else "⚠️ Не активирована"
            
            text += (
                f"<code>{lic['key']}</code>\n"
                f"📦 План: {PRICES.get(lic['plan'], {}).get('name', lic['plan'])}\n"
                f"📅 Осталось дней: {lic['days_left']}\n"
                f"{status} | {activated}\n"
                f"━━━━━━━━━━━━━━━\n"
            )

    await callback.message.edit_text(text, reply_markup=back_kb(), parse_mode="HTML")
    await callback.answer()


# ============================================================================
# ПОМОЩЬ
# ============================================================================

@dp.callback_query(F.data == "help")
async def cb_help(callback: types.CallbackQuery):
    text = (
        "❓ <b>Помощь</b>\n\n"
        "<b>Как купить лицензию:</b>\n"
        "1. Нажмите «Купить лицензию»\n"
        "2. Выберите способ оплаты:\n"
        "   • Telegram Stars — мгновенно\n"
        f"   • Написать @{SELLER_USERNAME} — вручную\n"
        "3. Оплатите счёт\n"
        "4. Получите ключ активации\n\n"
        "<b>Как активировать:</b>\n"
        "1. Запустите Timecyc Editor\n"
        "2. Введите ключ при первом запуске\n"
        "3. Ключ привяжется к компьютеру\n\n"
        "<b>Важно:</b>\n"
        "• Один ключ — один компьютер\n\n"
        f"📧 Поддержка: @{SELLER_USERNAME}"
    )
    await callback.message.edit_text(text, reply_markup=back_kb(), parse_mode="HTML")
    await callback.answer()


# ============================================================================
# АДМИН-ПАНЕЛЬ
# ============================================================================

@dp.message(Command("admin"))
async def cmd_admin(message: types.Message):
    if message.from_user.id not in ADMIN_IDS:
        await message.answer("❌ Нет доступа")
        return
    stats = get_stats()
    text = (
        "⚙️ <b>Админ-панель</b>\n\n"
        f"👥 Пользователей: {stats['total_users']}\n"
        f"🔑 Всего ключей: {stats['total_keys']}\n"
        f"✅ Активных: {stats['active_keys']}\n"
        f"💰 Транзакций: {stats['total_tx']}\n"
        f"⭐ Заработано: {stats['total_stars']} звёзд"
    )
    await message.answer(text, reply_markup=admin_menu_kb(), parse_mode="HTML")


@dp.callback_query(F.data == "admin_panel")
async def cb_admin_panel(callback: types.CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer("❌ Нет доступа", show_alert=True)
        return
    stats = get_stats()
    text = (
        "⚙️ <b>Админ-панель</b>\n\n"
        f"👥 Пользователей: {stats['total_users']}\n"
        f"🔑 Всего ключей: {stats['total_keys']}\n"
        f"✅ Активных: {stats['active_keys']}\n"
        f"💰 Транзакций: {stats['total_tx']}\n"
        f"⭐ Заработано: {stats['total_stars']} звёзд"
    )
    await callback.message.edit_text(text, reply_markup=admin_menu_kb(), parse_mode="HTML")
    await callback.answer()


# ─── Выдать ключ ───────────────────────────────────────────────────────────────

@dp.callback_query(F.data == "admin_give_key")
async def cb_admin_give_key(callback: types.CallbackQuery, state: FSMContext):
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer("❌ Нет доступа", show_alert=True)
        return
    await callback.message.edit_text(
        "👤 Введите <b>User ID</b> пользователя:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="❌ Отмена", callback_data="admin_panel")]
        ]),
        parse_mode="HTML",
    )
    await state.set_state(AdminStates.waiting_user_id)
    await callback.answer()


@dp.message(AdminStates.waiting_user_id)
async def admin_get_user_id(message: types.Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS:
        return
    try:
        user_id = int(message.text.strip())
    except ValueError:
        await message.answer("❌ Неверный формат. Введите число.")
        return
    await state.update_data(user_id=user_id)
    await message.answer(
        f"👤 User ID: <code>{user_id}</code>\n\n📦 Выберите план:",
        reply_markup=admin_plan_kb(),
        parse_mode="HTML",
    )
    await state.set_state(AdminStates.waiting_plan)


@dp.callback_query(F.data.startswith("admin_plan_"), AdminStates.waiting_plan)
async def admin_create_key(callback: types.CallbackQuery, state: FSMContext):
    if callback.from_user.id not in ADMIN_IDS:
        return
    plan    = callback.data.replace("admin_plan_", "")
    data    = await state.get_data()
    user_id = data["user_id"]

    key = create_license(user_id, plan, "admin_gift")

    try:
        await bot.send_message(
            user_id,
            f"🎁 <b>Вам выдан ключ активации!</b>\n\n"
            f"🔑 <code>{key}</code>\n\n"
            f"📦 План: {PRICES[plan]['name']}\n"
            f"⏱ Срок: {PRICES[plan]['days']} дней",
            parse_mode="HTML",
        )
        delivery = "✅ Ключ отправлен пользователю"
    except Exception:
        delivery = "⚠️ Не удалось отправить пользователю"

    await callback.message.edit_text(
        f"✅ <b>Ключ создан!</b>\n\n"
        f"🔑 <code>{key}</code>\n"
        f"👤 User ID: {user_id}\n"
        f"📦 План: {PRICES[plan]['name']}\n\n"
        f"{delivery}",
        reply_markup=admin_menu_kb(),
        parse_mode="HTML",
    )
    await state.clear()


# ─── Статистика ────────────────────────────────────────────────────────────────

@dp.callback_query(F.data == "admin_stats")
async def cb_admin_stats(callback: types.CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer("❌ Нет доступа", show_alert=True)
        return
    stats = get_stats()
    text = (
        "📊 <b>Детальная статистика</b>\n\n"
        f"👥 Всего пользователей: {stats['total_users']}\n"
        f"🔑 Всего ключей: {stats['total_keys']}\n"
        f"✅ Активных ключей: {stats['active_keys']}\n"
        f"💰 Всего транзакций: {stats['total_tx']}\n"
        f"⭐ Заработано звёзд: {stats['total_stars']}"
    )
    await callback.message.edit_text(text, reply_markup=admin_menu_kb(), parse_mode="HTML")
    await callback.answer()


# ─── Тест API ──────────────────────────────────────────────────────────────────

@dp.callback_query(F.data == "admin_test_api")
async def cb_test_api(callback: types.CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer("❌ Нет доступа", show_alert=True)
        return

    await callback.answer("🔄 Тестирую API...", show_alert=False)

    try:
        resp = requests.get(f"{API_URL.rstrip('/api.php')}/api.php/health", timeout=10)
        if resp.status_code == 200:
            d = resp.json()
            security_status = "🔐 Включена" if d.get('security') == 'enabled' else "⚠️ Не включена"
            text = (
                f"✅ <b>API работает!</b>\n\n"
                f"🌐 URL: {API_URL}\n"
                f"📡 Статус: {d.get('status', '—')}\n"
                f"💾 База: {d.get('database', '—')}\n"
                f"🐘 PHP: {d.get('php_version', '—')}\n"
                f"🔐 Безопасность: {security_status}\n"
                f"🕐 Время: {d.get('timestamp', '—')}"
            )
        else:
            text = (
                f"⚠️ <b>API ответил с ошибкой</b>\n\n"
                f"Код: {resp.status_code}\n"
                f"URL: {API_URL}"
            )
    except requests.exceptions.Timeout:
        text = (
            f"⏱️ <b>Тайм-аут подключения</b>\n\n"
            f"API не отвечает.\n"
            f"URL: {API_URL}"
        )
    except Exception as e:
        text = (
            f"❌ <b>Ошибка подключения к API</b>\n\n"
            f"URL: {API_URL}\n"
            f"Ошибка: {e}"
        )

    await callback.message.edit_text(text, reply_markup=admin_menu_kb(), parse_mode="HTML")


# ============================================================================
# ЗАПУСК
# ============================================================================

async def main():
    logger.info("=" * 50)
    logger.info("Timecyc Editor License Bot — Starting (SECURED)")
    logger.info("=" * 50)

    if not BOT_TOKEN or BOT_TOKEN == "YOUR_BOT_TOKEN_HERE":
        logger.error("BOT_TOKEN не задан!")
        return
    
    if API_SECRET_KEY == "ЗАМЕНИТЕ_ЭТОТ_КЛЮЧ_НА_СЛУЧАЙНЫЙ_ОЧЕНЬ_ДЛИННЫЙ_СЕКРЕТНЫЙ_КОД_12345":
        logger.warning("⚠️ ВНИМАНИЕ! API_SECRET_KEY не изменен!")
        logger.warning("⚠️ Обязательно установите уникальный секретный ключ!")
        logger.warning("⚠️ Сгенерируйте ключ: python -c 'import secrets; print(secrets.token_hex(32))'")

    init_db()

    if ADMIN_IDS:
        logger.info(f"Admin IDs: {ADMIN_IDS}")
    else:
        logger.warning("ADMIN_IDS не заданы — админ-панель недоступна")

    logger.info(f"API URL: {API_URL}")
    logger.info(f"API Key: {API_SECRET_KEY[:10]}... (первые 10 символов)")
    logger.info(f"Seller: @{SELLER_USERNAME}")
    logger.info("=" * 50)

    try:
        await dp.start_polling(bot, skip_updates=True)
    except Exception as e:
        logger.error(f"Ошибка: {e}")
    finally:
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
