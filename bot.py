#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Telegram Bot для продажи лицензий Timecyc Editor
Поддержка: СБП, Telegram Stars
Админ-панель для управления ключами
"""

import os
import json
import hashlib
import secrets
import asyncio
import logging
from datetime import datetime, timedelta
from typing import Optional, Dict, List

from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    InlineKeyboardMarkup, InlineKeyboardButton,
    LabeledPrice, PreCheckoutQuery
)

# Настройки
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_IDS = os.getenv("ADMIN_IDS")

# Цены (в рублях для СБП, в звездах для Stars)
PRICES = {
    "1month": {"rub": 299, "stars": 50, "days": 30},
    "3months": {"rub": 699, "stars": 120, "days": 90},
    "lifetime": {"rub": 1499, "stars": 250, "days": 36500}
}

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Инициализация бота
bot = Bot(token=BOT_TOKEN)
storage = MemoryStorage()
dp = Dispatcher(storage=storage)


class LicenseManager:
    """Менеджер лицензий"""
    
    def __init__(self, db_file: str = "licenses.json"):
        self.db_file = db_file
        self.licenses: Dict = self.load_db()
    
    def load_db(self) -> Dict:
        """Загрузка базы данных"""
        if os.path.exists(self.db_file):
            try:
                with open(self.db_file, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except Exception as e:
                logger.error(f"Error loading database: {e}")
        return {"users": {}, "keys": {}, "transactions": []}
    
    def save_db(self):
        """Сохранение базы данных"""
        try:
            with open(self.db_file, 'w', encoding='utf-8') as f:
                json.dump(self.licenses, f, indent=2, ensure_ascii=False)
        except Exception as e:
            logger.error(f"Error saving database: {e}")
    
    def generate_key(self) -> str:
        """Генерация уникального ключа"""
        while True:
            key = f"TC-{secrets.token_hex(8).upper()}"
            if key not in self.licenses["keys"]:
                return key
    
    def create_license(self, user_id: int, plan: str, payment_method: str) -> str:
        """Создание новой лицензии"""
        key = self.generate_key()
        expires_at = datetime.now() + timedelta(days=PRICES[plan]["days"])
        
        self.licenses["keys"][key] = {
            "user_id": user_id,
            "plan": plan,
            "created_at": datetime.now().isoformat(),
            "expires_at": expires_at.isoformat(),
            "payment_method": payment_method,
            "activated": False,
            "hwid": None
        }
        
        # Обновляем информацию о пользователе
        if str(user_id) not in self.licenses["users"]:
            self.licenses["users"][str(user_id)] = {
                "keys": [],
                "total_spent_rub": 0,
                "total_spent_stars": 0
            }
        
        self.licenses["users"][str(user_id)]["keys"].append(key)
        
        # Обновляем статистику трат
        if payment_method == "stars":
            self.licenses["users"][str(user_id)]["total_spent_stars"] += PRICES[plan]["stars"]
        else:
            self.licenses["users"][str(user_id)]["total_spent_rub"] += PRICES[plan]["rub"]
        
        self.save_db()
        return key
    
    def verify_key(self, key: str, hwid: str) -> Dict:
        """Проверка ключа"""
        if key not in self.licenses["keys"]:
            return {"valid": False, "reason": "Ключ не найден"}
        
        lic = self.licenses["keys"][key]
        
        # Проверка срока действия
        expires_at = datetime.fromisoformat(lic["expires_at"])
        if datetime.now() > expires_at:
            return {"valid": False, "reason": "Лицензия истекла"}
        
        # Проверка HWID
        if lic["activated"]:
            if lic["hwid"] != hwid:
                return {"valid": False, "reason": "Ключ привязан к другому устройству"}
        else:
            # Первая активация - привязываем к HWID
            lic["hwid"] = hwid
            lic["activated"] = True
            lic["activated_at"] = datetime.now().isoformat()
            self.save_db()
        
        days_left = (expires_at - datetime.now()).days
        return {
            "valid": True,
            "plan": lic["plan"],
            "expires_at": lic["expires_at"],
            "days_left": days_left
        }
    
    def get_user_licenses(self, user_id: int) -> List[Dict]:
        """Получить все лицензии пользователя"""
        user_data = self.licenses["users"].get(str(user_id))
        if not user_data:
            return []
        
        result = []
        for key in user_data["keys"]:
            lic = self.licenses["keys"][key]
            expires_at = datetime.fromisoformat(lic["expires_at"])
            days_left = (expires_at - datetime.now()).days
            
            result.append({
                "key": key,
                "plan": lic["plan"],
                "activated": lic["activated"],
                "expires_at": lic["expires_at"],
                "days_left": max(0, days_left),
                "expired": days_left < 0
            })
        
        return result
    
    def add_transaction(self, user_id: int, plan: str, amount: float, method: str, key: str):
        """Добавить транзакцию"""
        self.licenses["transactions"].append({
            "user_id": user_id,
            "plan": plan,
            "amount": amount,
            "method": method,
            "key": key,
            "timestamp": datetime.now().isoformat()
        })
        self.save_db()


# Глобальный менеджер лицензий
license_manager = LicenseManager()


# FSM состояния
class AdminStates(StatesGroup):
    waiting_user_id = State()
    waiting_plan = State()


# Клавиатуры
def main_menu_kb() -> InlineKeyboardMarkup:
    """Главное меню"""
    buttons = [
        [InlineKeyboardButton(text="💎 Купить лицензию", callback_data="buy")],
        [InlineKeyboardButton(text="🔑 Мои лицензии", callback_data="my_licenses")],
        [InlineKeyboardButton(text="❓ Помощь", callback_data="help")]
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def admin_menu_kb() -> InlineKeyboardMarkup:
    """Админ меню"""
    buttons = [
        [InlineKeyboardButton(text="➕ Выдать ключ", callback_data="admin_give_key")],
        [InlineKeyboardButton(text="📊 Статистика", callback_data="admin_stats")],
        [InlineKeyboardButton(text="🔍 Поиск пользователя", callback_data="admin_find_user")],
        [InlineKeyboardButton(text="◀️ Назад", callback_data="start")]
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def plans_kb(payment_method: str) -> InlineKeyboardMarkup:
    """Выбор плана"""
    currency = "⭐" if payment_method == "stars" else "₽"
    price_key = "stars" if payment_method == "stars" else "rub"
    
    buttons = [
        [InlineKeyboardButton(
            text=f"1 месяц - {PRICES['1month'][price_key]} {currency}",
            callback_data=f"plan_1month_{payment_method}"
        )],
        [InlineKeyboardButton(
            text=f"3 месяца - {PRICES['3months'][price_key]} {currency}",
            callback_data=f"plan_3months_{payment_method}"
        )],
        [InlineKeyboardButton(
            text=f"Навсегда - {PRICES['lifetime'][price_key]} {currency}",
            callback_data=f"plan_lifetime_{payment_method}"
        )],
        [InlineKeyboardButton(text="◀️ Назад", callback_data="buy")]
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def payment_method_kb() -> InlineKeyboardMarkup:
    """Выбор способа оплаты"""
    buttons = [
        [InlineKeyboardButton(text="⭐ Telegram Stars", callback_data="payment_stars")],
        [InlineKeyboardButton(text="💳 СБП (Банковская карта)", callback_data="payment_sbp")],
        [InlineKeyboardButton(text="◀️ Назад", callback_data="start")]
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def back_to_menu_kb() -> InlineKeyboardMarkup:
    """Кнопка возврата в меню"""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="◀️ Главное меню", callback_data="start")]
    ])


# Обработчики команд
@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    """Команда /start"""
    user_name = message.from_user.first_name
    
    text = (
        f"👋 Привет, {user_name}!\n\n"
        f"🎨 <b>Timecyc Editor</b> - профессиональный редактор timecyc для GTA.\n\n"
        f"✨ <b>Возможности:</b>\n"
        f"• Визуальное редактирование неба и погоды\n"
        f"• Поддержка всех параметров timecyc\n"
        f"• Предпросмотр в реальном времени\n"
        f"• Экспорт в JSON\n\n"
        f"💎 Выберите действие ниже:"
    )
    
    kb = main_menu_kb()
    
    # Добавляем админ кнопку для администраторов
    if message.from_user.id in ADMIN_IDS:
        kb.inline_keyboard.insert(0, [
            InlineKeyboardButton(text="⚙️ Админ-панель", callback_data="admin_panel")
        ])
    
    await message.answer(text, reply_markup=kb, parse_mode="HTML")


@dp.message(Command("admin"))
async def cmd_admin(message: types.Message):
    """Команда /admin"""
    if message.from_user.id not in ADMIN_IDS:
        await message.answer("❌ У вас нет прав администратора")
        return
    
    await show_admin_panel(message)


# Обработчики callback
@dp.callback_query(F.data == "start")
async def cb_start(callback: types.CallbackQuery):
    """Возврат в главное меню"""
    user_name = callback.from_user.first_name
    
    text = (
        f"👋 Привет, {user_name}!\n\n"
        f"🎨 <b>Timecyc Editor</b> - профессиональный редактор timecyc для GTA.\n\n"
        f"💎 Выберите действие ниже:"
    )
    
    kb = main_menu_kb()
    
    if callback.from_user.id in ADMIN_IDS:
        kb.inline_keyboard.insert(0, [
            InlineKeyboardButton(text="⚙️ Админ-панель", callback_data="admin_panel")
        ])
    
    await callback.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
    await callback.answer()


@dp.callback_query(F.data == "buy")
async def cb_buy(callback: types.CallbackQuery):
    """Покупка лицензии"""
    text = (
        "💳 <b>Выберите способ оплаты:</b>\n\n"
        "⭐ <b>Telegram Stars</b> - мгновенная оплата через Telegram\n"
        "💳 <b>СБП</b> - оплата банковской картой (РФ)"
    )
    
    await callback.message.edit_text(text, reply_markup=payment_method_kb(), parse_mode="HTML")
    await callback.answer()


@dp.callback_query(F.data.startswith("payment_"))
async def cb_payment_method(callback: types.CallbackQuery):
    """Выбор метода оплаты"""
    method = callback.data.replace("payment_", "")
    
    text = (
        "📦 <b>Выберите план подписки:</b>\n\n"
        "1️⃣ <b>1 месяц</b> - базовая лицензия\n"
        "3️⃣ <b>3 месяца</b> - выгодное предложение\n"
        "♾️ <b>Навсегда</b> - безлимитный доступ\n\n"
        "После покупки вы получите уникальный ключ активации."
    )
    
    await callback.message.edit_text(text, reply_markup=plans_kb(method), parse_mode="HTML")
    await callback.answer()


@dp.callback_query(F.data.startswith("plan_"))
async def cb_plan_selected(callback: types.CallbackQuery):
    """Выбран план - создание счета"""
    parts = callback.data.replace("plan_", "").split("_")
    plan = parts[0]
    payment_method = parts[1]
    
    if payment_method == "stars":
        # Оплата через Telegram Stars
        await process_stars_payment(callback, plan)
    else:
        # Оплата через СБП (YooKassa)
        await process_sbp_payment(callback, plan)


async def process_stars_payment(callback: types.CallbackQuery, plan: str):
    """Обработка оплаты через Stars"""
    price = PRICES[plan]["stars"]
    
    # Создаем счет на оплату Stars
    prices = [LabeledPrice(label=f"Timecyc Editor - {plan}", amount=price)]
    
    plan_names = {
        "1month": "1 месяц",
        "3months": "3 месяца",
        "lifetime": "навсегда"
    }
    
    await bot.send_invoice(
        chat_id=callback.from_user.id,
        title=f"Timecyc Editor ({plan_names[plan]})",
        description=f"Лицензия на {plan_names[plan]}",
        payload=f"{plan}_stars_{callback.from_user.id}",
        provider_token="",  # Пустой для Stars
        currency="XTR",  # Telegram Stars
        prices=prices
    )
    
    await callback.answer("Счет создан! Оплатите его для активации.")


async def process_sbp_payment(callback: types.CallbackQuery, plan: str):
    price = PRICES[plan]["rub"]
    
    text = (
        f"💳 <b>Оплата через СБП</b>\n\n"
        f"Сумма: {price} ₽\n"
        f"План: {plan}\n\n"
        f"📱 <b>Инструкция:</b>\n"
        f"1. Переведите {price} ₽ на номер 2202208811419895\n"
        f"2. Отправьте скриншот оплаты администратору @keedboy016\n"
        f"3. Ключ будет выдан после проверки платежа"
    )
    
    await callback.message.edit_text(text, reply_markup=back_to_menu_kb(), parse_mode="HTML")
    await callback.answer()


@dp.pre_checkout_query()
async def pre_checkout_handler(pre_checkout_query: PreCheckoutQuery):
    """Обработка pre-checkout для Stars"""
    await bot.answer_pre_checkout_query(pre_checkout_query.id, ok=True)


@dp.message(F.successful_payment)
async def successful_payment_handler(message: types.Message):
    """Успешная оплата через Stars"""
    payment = message.successful_payment
    payload = payment.invoice_payload
    
    # Парсим payload
    parts = payload.split("_")
    plan = parts[0]
    user_id = int(parts[2])
    
    # Создаем лицензию
    key = license_manager.create_license(user_id, plan, "stars")
    
    # Записываем транзакцию
    license_manager.add_transaction(
        user_id, plan, PRICES[plan]["stars"], "stars", key
    )
    
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
                f"Пользователь: {message.from_user.id}\n"
                f"План: {plan}\n"
                f"Метод: Telegram Stars\n"
                f"Ключ: {key}"
            )
        except:
            pass


@dp.callback_query(F.data == "my_licenses")
async def cb_my_licenses(callback: types.CallbackQuery):
    """Мои лицензии"""
    licenses = license_manager.get_user_licenses(callback.from_user.id)
    
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
        "2. Выберите способ оплаты\n"
        "3. Выберите план подписки\n"
        "4. Оплатите счет\n"
        "5. Получите ключ активации\n\n"
        "<b>Как активировать:</b>\n"
        "1. Запустите Timecyc Editor\n"
        "2. При первом запуске введите ключ\n"
        "3. Программа автоматически активируется\n\n"
        "<b>Важно:</b>\n"
        "• Ключ привязывается к вашему компьютеру\n"
        "• Переносить на другие ПК нельзя\n"
        "• Один ключ = один компьютер\n\n"
        "📧 Поддержка: @keedboy016"
    )
    
    await callback.message.edit_text(text, reply_markup=back_to_menu_kb(), parse_mode="HTML")
    await callback.answer()


# Админ-панель
@dp.callback_query(F.data == "admin_panel")
async def cb_admin_panel(callback: types.CallbackQuery):
    """Админ панель"""
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer("❌ Нет доступа", show_alert=True)
        return
    
    await show_admin_panel(callback.message)
    await callback.answer()


async def show_admin_panel(message):
    """Показать админ панель"""
    total_users = len(license_manager.licenses["users"])
    total_keys = len(license_manager.licenses["keys"])
    total_transactions = len(license_manager.licenses["transactions"])
    
    # Считаем активные ключи
    active_keys = sum(
        1 for lic in license_manager.licenses["keys"].values()
        if datetime.now() < datetime.fromisoformat(lic["expires_at"])
    )
    
    text = (
        "⚙️ <b>Админ-панель</b>\n\n"
        f"👥 Пользователей: {total_users}\n"
        f"🔑 Всего ключей: {total_keys}\n"
        f"✅ Активных: {active_keys}\n"
        f"💰 Транзакций: {total_transactions}\n"
    )
    
    await message.edit_text(text, reply_markup=admin_menu_kb(), parse_mode="HTML")


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
            "1️⃣ /plan_1month - 1 месяц\n"
            "3️⃣ /plan_3months - 3 месяца\n"
            "♾️ /plan_lifetime - навсегда"
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
    key = license_manager.create_license(user_id, plan, "admin_gift")
    
    # Отправляем пользователю
    try:
        await bot.send_message(
            user_id,
            f"🎁 Вам выдан ключ активации!\n\n"
            f"🔑 <code>{key}</code>\n\n"
            f"План: {plan}\n"
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
    """Статистика"""
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer("❌ Нет доступа", show_alert=True)
        return
    
    transactions = license_manager.licenses["transactions"]
    
    total_rub = sum(t["amount"] for t in transactions if t["method"] in ["sbp", "card"])
    total_stars = sum(t["amount"] for t in transactions if t["method"] == "stars")
    
    # Группировка по планам
    plans_count = {}
    for t in transactions:
        plan = t["plan"]
        plans_count[plan] = plans_count.get(plan, 0) + 1
    
    text = (
        "📊 <b>Статистика</b>\n\n"
        f"💰 Заработано:\n"
        f"  • {total_rub} ₽\n"
        f"  • {total_stars} ⭐\n\n"
        f"📦 Продано планов:\n"
    )
    
    for plan, count in plans_count.items():
        text += f"  • {plan}: {count} шт\n"
    
    await callback.message.edit_text(text, reply_markup=admin_menu_kb(), parse_mode="HTML")
    await callback.answer()


async def main():
    """Запуск бота"""
    logger.info("Starting bot...")
    
    # Проверка токена
    if BOT_TOKEN == "YOUR_BOT_TOKEN_HERE":
        logger.error("Please set BOT_TOKEN in environment variables!")
        return
    
    if not ADMIN_IDS:
        logger.warning("No ADMIN_IDS set. Admin panel will be disabled.")
    
    try:
        await dp.start_polling(bot, skip_updates=True)
    except Exception as e:
        logger.error(f"Error: {e}")
    finally:
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
