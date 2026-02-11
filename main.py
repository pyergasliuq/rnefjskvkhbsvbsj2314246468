#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Telegram Bot для продажи лицензий Timecyc Editor
ВСЕ В ОДНОМ ФАЙЛЕ - для удобного запуска на хостингах

Работает с PHP API на Reg.ru (или любом другом хостинге)
"""

import os
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
# НАСТРОЙКИ - ИЗМЕНИТЕ ЭТИ ЗНАЧЕНИЯ
# ============================================================================

# Токен бота (получить у @BotFather)
BOT_TOKEN = os.getenv("BOT_TOKEN", "YOUR_BOT_TOKEN_HERE")

# ID администраторов (через запятую)
ADMIN_IDS_STR = os.getenv("ADMIN_IDS", "")
ADMIN_IDS = [int(x.strip()) for x in ADMIN_IDS_STR.split(",") if x.strip()] if ADMIN_IDS_STR else []

# Ваш Telegram username для покупок (БЕЗ @)
SELLER_USERNAME = os.getenv("SELLER_USERNAME", "your_telegram")

API_URL = os.getenv("API_URL", "https://pweper.ru")

# Цены в звездах Telegram
PRICES = {
    "1month": {"stars": 50, "days": 30, "name": "1 месяц"},
    "3months": {"stars": 120, "days": 90, "name": "3 месяца"},
    "lifetime": {"stars": 250, "days": 36500, "name": "Навсегда"}
}

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
# РАБОТА С API (создание ключей)
# ============================================================================

def create_key_in_api(user_id: int, plan: str, payment_method: str) -> str:
    """
    Создать ключ через API
    
    ВАЖНО: Так как PHP API не имеет endpoint для создания ключей,
    мы генерируем ключ локально и отправляем его в API через /verify
    с пустым HWID для первой регистрации.
    
    Альтернатива: Можно добавить endpoint /create в api.php
    """
    # Генерируем ключ
    key = f"PWEPER-{secrets.token_hex(4).upper()}-{secrets.token_hex(4).upper()}-{secrets.token_hex(4).upper()}"
    
    # Вычисляем срок действия
    plan_info = PRICES.get(plan, PRICES["1month"])
    expires_at = datetime.now() + timedelta(days=plan_info["days"])
    
    logger.info(f"Generated key: {key} for user {user_id}, plan: {plan}")
    
    # Сохраняем в локальную "базу" (в памяти)
    # В реальности нужно либо:
    # 1. Добавить endpoint /create в api.php
    # 2. Или использовать прямой доступ к MySQL из бота
    # 3. Или хранить ключи в отдельном месте
    
    # Для демонстрации - логируем
    logger.warning(f"⚠️ ВАЖНО: Ключ {key} создан, но не сохранен в API!")
    logger.warning(f"   Вам нужно вручную добавить его в БД MySQL на Reg.ru:")
    logger.warning(f"   INSERT INTO license_keys (`key`, user_id, plan, expires_at, payment_method)")
    logger.warning(f"   VALUES ('{key}', {user_id}, '{plan}', '{expires_at.strftime('%Y-%m-%d %H:%M:%S')}', '{payment_method}');")
    
    return key


# ============================================================================
# СОСТОЯНИЯ FSM
# ============================================================================

class AdminStates(StatesGroup):
    waiting_for_user_id = State()
    waiting_for_plan = State()

# ============================================================================
# ОБРАБОТЧИКИ КОМАНД
# ============================================================================

@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    """Команда /start"""
    user_id = message.from_user.id
    username = message.from_user.username or "пользователь"
    
    logger.info(f"User {user_id} ({username}) started bot")
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💳 Купить лицензию", callback_data="buy")],
        [InlineKeyboardButton(text="ℹ️ Информация", callback_data="info")],
    ])
    
    if user_id in ADMIN_IDS:
        keyboard.inline_keyboard.append([
            InlineKeyboardButton(text="👑 Админ-панель", callback_data="admin")
        ])
    
    await message.answer(
        f"👋 Привет, {username}!\n\n"
        f"🎮 <b>Timecyc Editor License Bot</b>\n\n"
        f"Я помогу тебе приобрести лицензию для работы с Timecyc Editor.\n\n"
        f"Выбери действие:",
        reply_markup=keyboard,
        parse_mode="HTML"
    )


@dp.callback_query(F.data == "buy")
async def process_buy(callback: types.CallbackQuery):
    """Покупка лицензии"""
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text=f"⭐ {info['name']} - {info['stars']} звезд",
            callback_data=f"plan_{plan}"
        )]
        for plan, info in PRICES.items()
    ] + [[InlineKeyboardButton(text="◀️ Назад", callback_data="back_to_menu")]])
    
    await callback.message.edit_text(
        "💎 <b>Выберите тариф:</b>\n\n"
        "⭐ 1 месяц - 50 звезд\n"
        "⭐ 3 месяца - 120 звезд\n"
        "⭐ Навсегда - 250 звезд\n\n"
        "<i>Оплата производится звездами Telegram</i>",
        reply_markup=keyboard,
        parse_mode="HTML"
    )


@dp.callback_query(F.data.startswith("plan_"))
async def process_plan_selection(callback: types.CallbackQuery):
    """Выбран тариф"""
    plan = callback.data.replace("plan_", "")
    plan_info = PRICES[plan]
    
    # Создаем счет на оплату в звездах
    await bot.send_invoice(
        chat_id=callback.from_user.id,
        title=f"Timecyc Editor - {plan_info['name']}",
        description=f"Лицензия на использование Timecyc Editor ({plan_info['name']})",
        payload=f"{plan}_{callback.from_user.id}",
        currency="XTR",  # Звезды Telegram
        prices=[LabeledPrice(label=plan_info['name'], amount=plan_info['stars'])]
    )
    
    await callback.answer("✅ Счет создан! Проверьте сообщение выше.")


@dp.pre_checkout_query()
async def process_pre_checkout(pre_checkout_query: PreCheckoutQuery):
    """Обработка pre-checkout запроса"""
    logger.info(f"Pre-checkout from user {pre_checkout_query.from_user.id}")
    await bot.answer_pre_checkout_query(pre_checkout_query.id, ok=True)


@dp.message(F.successful_payment)
async def process_successful_payment(message: types.Message):
    """Успешная оплата"""
    payment = message.successful_payment
    payload_parts = payment.invoice_payload.split("_")
    plan = payload_parts[0]
    user_id = int(payload_parts[1])
    
    logger.info(f"Successful payment from user {user_id}, plan: {plan}, stars: {payment.total_amount}")
    
    # Создаем ключ
    key = create_key_in_api(user_id, plan, "stars")
    
    plan_info = PRICES[plan]
    
    await message.answer(
        f"✅ <b>Оплата прошла успешно!</b>\n\n"
        f"🔑 <b>Ваш лицензионный ключ:</b>\n"
        f"<code>{key}</code>\n\n"
        f"📋 <b>Тариф:</b> {plan_info['name']}\n"
        f"⏳ <b>Срок действия:</b> {plan_info['days']} дней\n\n"
        f"<b>Как активировать:</b>\n"
        f"1. Запустите Timecyc Editor\n"
        f"2. Введите этот ключ в окне активации\n"
        f"3. Ключ привяжется к вашему компьютеру\n\n"
        f"<i>Сохраните этот ключ в надежном месте!</i>",
        parse_mode="HTML"
    )
    
    # Уведомляем админов
    for admin_id in ADMIN_IDS:
        try:
            await bot.send_message(
                admin_id,
                f"💰 <b>Новая продажа!</b>\n\n"
                f"👤 User ID: {user_id}\n"
                f"📦 Тариф: {plan_info['name']}\n"
                f"⭐ Сумма: {payment.total_amount} stars\n"
                f"🔑 Ключ: <code>{key}</code>",
                parse_mode="HTML"
            )
        except:
            pass


@dp.callback_query(F.data == "info")
async def process_info(callback: types.CallbackQuery):
    """Информация о боте"""
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="◀️ Назад", callback_data="back_to_menu")]
    ])
    
    await callback.message.edit_text(
        "ℹ️ <b>Информация о Timecyc Editor</b>\n\n"
        "🎨 Timecyc Editor - это профессиональный редактор для настройки визуального "
        "оформления игрового мира.\n\n"
        "<b>Возможности:</b>\n"
        "• Редактирование цветов неба, солнца, облаков\n"
        "• Настройка погодных эффектов\n"
        "• Предпросмотр в реальном времени\n"
        "• Поддержка всех типов погоды\n\n"
        "<b>Тарифы:</b>\n"
        "⭐ 1 месяц - 50 звезд\n"
        "⭐ 3 месяца - 120 звезд (экономия 30 звезд!)\n"
        "⭐ Навсегда - 250 звезд\n\n"
        f"<b>Поддержка:</b> @{SELLER_USERNAME}",
        reply_markup=keyboard,
        parse_mode="HTML"
    )


@dp.callback_query(F.data == "admin")
async def process_admin_panel(callback: types.CallbackQuery):
    """Админ-панель"""
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer("⛔ У вас нет доступа к админ-панели", show_alert=True)
        return
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔑 Создать ключ", callback_data="admin_create_key")],
        [InlineKeyboardButton(text="📊 Статистика", callback_data="admin_stats")],
        [InlineKeyboardButton(text="🔧 Тест API", callback_data="admin_test_api")],
        [InlineKeyboardButton(text="◀️ Назад", callback_data="back_to_menu")]
    ])
    
    await callback.message.edit_text(
        "👑 <b>Админ-панель</b>\n\n"
        "Выберите действие:",
        reply_markup=keyboard,
        parse_mode="HTML"
    )


@dp.callback_query(F.data == "admin_create_key")
async def process_admin_create_key(callback: types.CallbackQuery, state: FSMContext):
    """Создать ключ вручную"""
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer("⛔ Нет доступа", show_alert=True)
        return
    
    await callback.message.edit_text(
        "🔑 <b>Создание ключа</b>\n\n"
        "Введите User ID пользователя (или 0 для тестового ключа):",
        parse_mode="HTML"
    )
    await state.set_state(AdminStates.waiting_for_user_id)


@dp.message(AdminStates.waiting_for_user_id)
async def process_user_id_input(message: types.Message, state: FSMContext):
    """Получен User ID"""
    if message.from_user.id not in ADMIN_IDS:
        return
    
    try:
        user_id = int(message.text.strip())
    except:
        await message.answer("❌ Неверный формат. Введите число.")
        return
    
    await state.update_data(user_id=user_id)
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=info['name'], callback_data=f"admin_plan_{plan}")]
        for plan, info in PRICES.items()
    ])
    
    await message.answer(
        f"👤 User ID: {user_id}\n\n"
        "📦 Выберите тариф:",
        reply_markup=keyboard
    )
    await state.set_state(AdminStates.waiting_for_plan)


@dp.callback_query(F.data.startswith("admin_plan_"), AdminStates.waiting_for_plan)
async def process_plan_input(callback: types.CallbackQuery, state: FSMContext):
    """Выбран тариф"""
    if callback.from_user.id not in ADMIN_IDS:
        return
    
    plan = callback.data.replace("admin_plan_", "")
    data = await state.get_data()
    user_id = data.get("user_id", 0)
    
    # Создаем ключ
    key = create_key_in_api(user_id, plan, "admin")
    
    plan_info = PRICES[plan]
    
    await callback.message.edit_text(
        f"✅ <b>Ключ создан!</b>\n\n"
        f"🔑 <code>{key}</code>\n\n"
        f"👤 User ID: {user_id}\n"
        f"📦 Тариф: {plan_info['name']}\n"
        f"⏳ Срок: {plan_info['days']} дней\n\n"
        f"⚠️ <b>ВАЖНО!</b> Вам нужно вручную добавить этот ключ в БД MySQL:\n\n"
        f"<code>INSERT INTO license_keys (`key`, user_id, plan, expires_at, payment_method)\n"
        f"VALUES ('{key}', {user_id}, '{plan}', "
        f"DATE_ADD(NOW(), INTERVAL {plan_info['days']} DAY), 'admin');</code>\n\n"
        f"Выполните этот SQL запрос в phpMyAdmin на Reg.ru",
        parse_mode="HTML"
    )
    
    await state.clear()


@dp.callback_query(F.data == "admin_test_api")
async def process_test_api(callback: types.CallbackQuery):
    """Тест подключения к API"""
    if callback.from_user.id not in ADMIN_IDS:
        return
    
    await callback.answer("🔄 Тестирую API...", show_alert=False)
    
    try:
        # Тестируем health endpoint
        response = requests.get(f"{API_URL}/api.php/health", timeout=10)
        
        if response.status_code == 200:
            data = response.json()
            
            await callback.message.edit_text(
                f"✅ <b>API работает!</b>\n\n"
                f"🌐 URL: {API_URL}\n"
                f"📡 Статус: {data.get('status', 'unknown')}\n"
                f"💾 База: {data.get('database', 'unknown')}\n"
                f"🐘 PHP: {data.get('php_version', 'unknown')}\n"
                f"🕐 Время: {data.get('timestamp', 'unknown')}\n\n"
                f"<i>API подключен и работает корректно!</i>",
                parse_mode="HTML"
            )
        else:
            await callback.message.edit_text(
                f"⚠️ <b>API ответил с ошибкой</b>\n\n"
                f"Код: {response.status_code}\n"
                f"URL: {API_URL}\n\n"
                f"Проверьте настройки API на Reg.ru",
                parse_mode="HTML"
            )
    except requests.exceptions.Timeout:
        await callback.message.edit_text(
            f"⏱️ <b>Тайм-аут подключения</b>\n\n"
            f"API не отвечает. Проверьте:\n"
            f"1. Правильность URL: {API_URL}\n"
            f"2. API файл загружен на сервер\n"
            f"3. Сервер доступен",
            parse_mode="HTML"
        )
    except Exception as e:
        await callback.message.edit_text(
            f"❌ <b>Ошибка подключения к API</b>\n\n"
            f"URL: {API_URL}\n"
            f"Ошибка: {str(e)}\n\n"
            f"Проверьте настройку API_URL в переменных окружения",
            parse_mode="HTML"
        )


@dp.callback_query(F.data == "admin_stats")
async def process_admin_stats(callback: types.CallbackQuery):
    """Статистика (заглушка, так как нет прямого доступа к БД)"""
    if callback.from_user.id not in ADMIN_IDS:
        return
    
    await callback.message.edit_text(
        "📊 <b>Статистика</b>\n\n"
        "⚠️ Для просмотра статистики используйте phpMyAdmin на Reg.ru\n\n"
        "Или добавьте endpoint /stats в api.php для получения данных через API",
        parse_mode="HTML"
    )


@dp.callback_query(F.data == "back_to_menu")
async def process_back(callback: types.CallbackQuery):
    """Вернуться в главное меню"""
    user_id = callback.from_user.id
    username = callback.from_user.username or "пользователь"
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💳 Купить лицензию", callback_data="buy")],
        [InlineKeyboardButton(text="ℹ️ Информация", callback_data="info")],
    ])
    
    if user_id in ADMIN_IDS:
        keyboard.inline_keyboard.append([
            InlineKeyboardButton(text="👑 Админ-панель", callback_data="admin")
        ])
    
    await callback.message.edit_text(
        f"👋 Привет, {username}!\n\n"
        f"🎮 <b>Timecyc Editor License Bot</b>\n\n"
        f"Выбери действие:",
        reply_markup=keyboard,
        parse_mode="HTML"
    )


# ============================================================================
# ГЛАВНАЯ ФУНКЦИЯ
# ============================================================================

async def main():
    """Главная функция"""
    logger.info("=" * 60)
    logger.info("Timecyc Editor License Bot - Starting...")
    logger.info("=" * 60)
    
    # Проверка настроек
    if not BOT_TOKEN or BOT_TOKEN == "YOUR_BOT_TOKEN_HERE":
        logger.error("❌ BOT_TOKEN not set!")
        logger.error("Установите переменную окружения BOT_TOKEN")
        return
    
    if not ADMIN_IDS:
        logger.warning("⚠️ ADMIN_IDS not set! Admin panel will be unavailable")
    else:
        logger.info(f"✅ Admin IDs: {ADMIN_IDS}")
    
    logger.info(f"✅ Bot token: {BOT_TOKEN[:10]}...")
    logger.info(f"✅ API URL: {API_URL}")
    logger.info(f"✅ Seller: @{SELLER_USERNAME}")
    
    logger.info("=" * 60)
    logger.info("Starting bot polling...")
    logger.info("=" * 60)
    
    # Запускаем бота
    try:
        await dp.start_polling(bot)
    except KeyboardInterrupt:
        logger.info("Bot stopped by user")
    except Exception as e:
        logger.error(f"Bot error: {e}")
        raise


# ============================================================================
# ТОЧКА ВХОДА
# ============================================================================

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Stopped by user")
    except Exception as e:
        logger.error(f"Fatal error: {e}")
