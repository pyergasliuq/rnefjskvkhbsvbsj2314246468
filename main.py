#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Telegram Bot для продажи лицензий Timecyc Editor
Работает с PHP API на Reg.ru + локальная SQLite база
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
API_URL          = os.getenv("API_URL", "https://pweper.ru")
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


def create_license(user_id: int, plan: str, method: str,
                   username: str = None, first_name: str = None) -> str:
    """Создать лицензию в SQLite, вернуть ключ"""
    key = _gen_key()
    expires_at = datetime.now() + timedelta(days=PRICES[plan]["days"])

    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()

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
    """, (key, user_id, plan, expires_at.isoformat(), method))

    if method != "admin_gift":
        c.execute("""
            UPDATE users SET total_spent_stars = total_spent_stars + ?
            WHERE user_id = ?
        """, (PRICES[plan]["stars"], user_id))

    conn.commit()
    conn.close()

    logger.info(f"License created: {key} | user={user_id} | plan={plan} | method={method}")
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
        [InlineKeyboardButton(text="💎 Купить лицензию",  callback_data="buy")],
        [InlineKeyboardButton(text="🔑 Мои лицензии",     callback_data="my_licenses")],
        [InlineKeyboardButton(text="❓ Помощь",           callback_data="help")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def payment_method_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⭐ Telegram Stars",      callback_data="payment_stars")],
        [InlineKeyboardButton(text="💬 Написать продавцу",  url=f"https://t.me/{SELLER_USERNAME}")],
        [InlineKeyboardButton(text="◀️ Назад",               callback_data="start")],
    ])


def plans_kb(back_cb: str = "buy") -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(
            text=f"{info['name']} — {info['stars']} ⭐",
            callback_data=f"plan_{key}"
        )]
        for key, info in PRICES.items()
    ]
    rows.append([InlineKeyboardButton(text="◀️ Назад", callback_data=back_cb)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def admin_menu_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ Выдать ключ",    callback_data="admin_give_key")],
        [InlineKeyboardButton(text="📊 Статистика",    callback_data="admin_stats")],
        [InlineKeyboardButton(text="🔧 Тест API",      callback_data="admin_test_api")],
        [InlineKeyboardButton(text="◀️ Назад",          callback_data="start")],
    ])


def back_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="◀️ Главное меню", callback_data="start")]
    ])


def admin_plan_kb() -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text=info["name"], callback_data=f"admin_plan_{key}")]
        for key, info in PRICES.items()
    ]
    rows.append([InlineKeyboardButton(text="❌ Отмена", callback_data="admin_panel")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


# ============================================================================
# /start и возврат в меню
# ============================================================================

@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    is_admin = message.from_user.id in ADMIN_IDS
    name = message.from_user.first_name

    text = (
        f"👋 Привет, {name}!\n\n"
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


@dp.callback_query(F.data == "start")
async def cb_start(callback: types.CallbackQuery):
    is_admin = callback.from_user.id in ADMIN_IDS
    name = callback.from_user.first_name

    text = (
        f"👋 Привет, {name}!\n\n"
        f"🎨 <b>Timecyc Editor by Pweper</b>\n\n"
        f"💎 Выберите действие:"
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
        "⭐ <b>Telegram Stars</b> — мгновенная оплата\n"
        f"💬 <b>Написать продавцу</b> — @{SELLER_USERNAME}\n\n"
        "<i>При оплате продавцу напрямую ключ выдаётся вручную</i>"
    )
    await callback.message.edit_text(text, reply_markup=payment_method_kb(), parse_mode="HTML")
    await callback.answer()


@dp.callback_query(F.data == "payment_stars")
async def cb_payment_stars(callback: types.CallbackQuery):
    text = (
        "📦 <b>Выберите план подписки:</b>\n\n"
        "1️⃣ <b>1 месяц</b> — базовая лицензия\n"
        "3️⃣ <b>3 месяца</b> — выгодное предложение\n"
        "♾️ <b>Навсегда</b> — безлимитный доступ\n\n"
        "После покупки вы получите уникальный ключ активации."
    )
    await callback.message.edit_text(text, reply_markup=plans_kb(back_cb="buy"), parse_mode="HTML")
    await callback.answer()


@dp.callback_query(F.data.startswith("plan_"))
async def cb_plan_selected(callback: types.CallbackQuery):
    plan  = callback.data.replace("plan_", "")
    info  = PRICES[plan]
    price = [LabeledPrice(label=f"Timecyc Editor — {info['name']}", amount=info["stars"])]

    await bot.send_invoice(
        chat_id=callback.from_user.id,
        title=f"Timecyc Editor ({info['name']})",
        description=f"Лицензия на {info['name']}",
        payload=f"{plan}_stars_{callback.from_user.id}",
        provider_token="",
        currency="XTR",
        prices=price,
    )
    await callback.answer("Счёт создан! Оплатите его для активации.")


@dp.pre_checkout_query()
async def pre_checkout(pcq: PreCheckoutQuery):
    await bot.answer_pre_checkout_query(pcq.id, ok=True)


@dp.message(F.successful_payment)
async def successful_payment(message: types.Message):
    payment = message.successful_payment
    parts   = payment.invoice_payload.split("_")
    plan    = parts[0]
    user_id = int(parts[2])

    key = create_license(
        user_id, plan, "stars",
        username=message.from_user.username,
        first_name=message.from_user.first_name,
    )
    add_transaction(user_id, plan, PRICES[plan]["stars"], "stars", key)

    text = (
        f"✅ <b>Оплата успешна!</b>\n\n"
        f"🔑 Ваш ключ активации:\n"
        f"<code>{key}</code>\n\n"
        f"📱 <b>Как использовать:</b>\n"
        f"1. Запустите Timecyc Editor\n"
        f"2. Введите этот ключ при первом запуске\n"
        f"3. Ключ привяжется к вашему компьютеру\n\n"
        f"⏱ Срок действия: {PRICES[plan]['days']} дней\n"
        f"💾 Сохраните ключ в надёжном месте!"
    )
    await message.answer(text, reply_markup=back_kb(), parse_mode="HTML")

    for admin_id in ADMIN_IDS:
        try:
            await bot.send_message(
                admin_id,
                f"💰 <b>Новая покупка!</b>\n\n"
                f"👤 {message.from_user.id} (@{message.from_user.username})\n"
                f"📦 План: {plan}\n"
                f"⭐ Сумма: {PRICES[plan]['stars']}\n"
                f"🔑 Ключ: <code>{key}</code>",
                parse_mode="HTML",
            )
        except Exception:
            pass


# ============================================================================
# МОИ ЛИЦЕНЗИИ
# ============================================================================

@dp.callback_query(F.data == "my_licenses")
async def cb_my_licenses(callback: types.CallbackQuery):
    licenses = get_user_licenses(callback.from_user.id)

    if not licenses:
        text = (
            "🔑 <b>У вас пока нет лицензий</b>\n\n"
            "Приобретите лицензию, чтобы начать использовать Timecyc Editor!"
        )
    else:
        text = "🔑 <b>Ваши лицензии:</b>\n\n"
        for lic in licenses:
            status    = "❌ Истекла" if lic["expired"] else f"✅ Активна ({lic['days_left']} дней)"
            activated = "✓ Привязана" if lic["activated"] else "✗ Не активирована"
            text += (
                f"<code>{lic['key']}</code>\n"
                f"Статус: {status}\n"
                f"Привязка: {activated}\n"
                f"План: {lic['plan']}\n\n"
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
        resp = requests.get(f"{API_URL}/api.php/health", timeout=10)
        if resp.status_code == 200:
            d = resp.json()
            text = (
                f"✅ <b>API работает!</b>\n\n"
                f"🌐 URL: {API_URL}\n"
                f"📡 Статус: {d.get('status', '—')}\n"
                f"💾 База: {d.get('database', '—')}\n"
                f"🐘 PHP: {d.get('php_version', '—')}\n"
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
    logger.info("Timecyc Editor License Bot — Starting")
    logger.info("=" * 50)

    if not BOT_TOKEN or BOT_TOKEN == "YOUR_BOT_TOKEN_HERE":
        logger.error("BOT_TOKEN не задан!")
        return

    init_db()

    if ADMIN_IDS:
        logger.info(f"Admin IDs: {ADMIN_IDS}")
    else:
        logger.warning("ADMIN_IDS не заданы — админ-панель недоступна")

    logger.info(f"API URL: {API_URL}")
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
