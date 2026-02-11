#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Главный файл запуска (упрощенная версия)
Запускает бота и API в одном процессе
"""

import os
import sys
import asyncio
import logging
from threading import Thread

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# ============================================================================
# ПРОВЕРКА НАСТРОЕК
# ============================================================================

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
ADMIN_IDS = os.getenv("ADMIN_IDS", "")
SELLER_USERNAME = os.getenv("SELLER_USERNAME", "")

logger.info("=" * 50)
logger.info("Timecyc Editor License System")
logger.info("=" * 50)

# Проверка обязательных настроек
if not BOT_TOKEN or BOT_TOKEN == "YOUR_BOT_TOKEN_HERE":
    logger.error("❌ ERROR: BOT_TOKEN not set!")
    logger.error("Установите переменную окружения BOT_TOKEN")
    logger.error("Пример: BOT_TOKEN=1234567890:ABCdef...")
    sys.exit(1)

if not ADMIN_IDS:
    logger.warning("⚠️  WARNING: ADMIN_IDS not set!")
    logger.warning("Админ-панель будет недоступна")
    logger.warning("Установите: ADMIN_IDS=123456789")
else:
    logger.info(f"✅ Admin IDs: {ADMIN_IDS}")

if not SELLER_USERNAME:
    logger.warning("⚠️  WARNING: SELLER_USERNAME not set!")
    logger.warning("Используется значение по умолчанию")
    logger.warning("Установите: SELLER_USERNAME=ваш_telegram")
else:
    logger.info(f"✅ Seller: @{SELLER_USERNAME}")

logger.info(f"✅ Bot token: {BOT_TOKEN[:10]}...")

# ============================================================================
# ИМПОРТ МОДУЛЕЙ
# ============================================================================

try:
    from bot_simple import main as bot_main
    logger.info("✅ Bot module loaded")
except ImportError as e:
    logger.error(f"❌ Failed to import bot_simple: {e}")
    sys.exit(1)

try:
    from api_simple import app as flask_app
    logger.info("✅ API module loaded")
except ImportError as e:
    logger.error(f"❌ Failed to import api_simple: {e}")
    sys.exit(1)

# ============================================================================
# FLASK В ОТДЕЛЬНОМ ПОТОКЕ
# ============================================================================

def run_flask():
    """Запуск Flask API"""
    try:
        port = int(os.getenv("PORT", "8080"))
        logger.info(f"Starting Flask API on port {port}")
        flask_app.run(host='0.0.0.0', port=port, debug=False, use_reloader=False)
    except Exception as e:
        logger.error(f"Flask error: {e}")


# ============================================================================
# ГЛАВНАЯ ФУНКЦИЯ
# ============================================================================

async def main():
    """Главная функция"""
    logger.info("=" * 50)
    logger.info("Starting services...")
    logger.info("=" * 50)
    
    # Запускаем Flask в отдельном потоке
    flask_thread = Thread(target=run_flask, daemon=True)
    flask_thread.start()
    logger.info("✅ Flask API thread started")
    
    # Даем время на запуск Flask
    await asyncio.sleep(2)
    
    # Показываем API URL
    port = int(os.getenv("PORT", "8080"))
    logger.info("")
    logger.info("=" * 50)
    logger.info("🌐 API URL (вставьте в редактор):")
    logger.info(f"http://localhost:{port}")
    logger.info("Или на Bothost.ru это будет:")
    logger.info("http://ваш-логин.bothost.ru")
    logger.info("=" * 50)
    logger.info("")
    
    # Запускаем бота
    logger.info("Starting Telegram bot...")
    try:
        await bot_main()
    except KeyboardInterrupt:
        logger.info("Shutting down...")
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
        sys.exit(1)
