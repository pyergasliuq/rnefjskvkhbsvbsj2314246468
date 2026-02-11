#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Главный файл запуска для Bothost.ru
Запускает и бота, и API в одном процессе
"""

import os
import sys
import asyncio
import logging
from threading import Thread
import os



logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Импорт бота
try:
    from bot import main as bot_main, BOT_TOKEN, ADMIN_IDS
except ImportError as e:
    logger.error(f"Failed to import bot: {e}")
    sys.exit(1)

# Импорт API
try:
    from api import app as flask_app
except ImportError as e:
    logger.error(f"Failed to import API: {e}")
    sys.exit(1)


def run_flask():
    """Запуск Flask API в отдельном потоке"""
    try:
        port = "8080"
        logger.info(f"Starting Flask API on port {port}")
        flask_app.run(host='0.0.0.0', port=port, debug=False, use_reloader=False)
    except Exception as e:
        logger.error(f"Flask error: {e}")


async def main():
    """Главная функция"""
    logger.info("=" * 50)
    logger.info("Starting Timecyc Editor License System")
    logger.info("=" * 50)
    
    # Проверка конфигурации
    if BOT_TOKEN == "YOUR_BOT_TOKEN_HERE":
        logger.error("ERROR: BOT_TOKEN not set!")
        logger.error("Please set BOT_TOKEN environment variable")
        sys.exit(1)
    
    if not ADMIN_IDS:
        logger.warning("WARNING: No ADMIN_IDS set. Admin panel will be disabled.")
        logger.warning("Set ADMIN_IDS environment variable (comma-separated)")
    
    logger.info(f"Bot token: {BOT_TOKEN[:10]}...")
    logger.info(f"Admin IDs: {ADMIN_IDS}")
    
    # Запускаем Flask в отдельном потоке
    flask_thread = Thread(target=run_flask, daemon=True)
    flask_thread.start()
    logger.info("Flask API thread started")
    
    # Небольшая задержка для инициализации Flask
    await asyncio.sleep(2)
    
    # Запускаем бота в главном потоке
    logger.info("Starting Telegram bot...")
    try:
        await bot_main()
    except KeyboardInterrupt:
        logger.info("Shutting down...")
    except Exception as e:
        logger.error(f"Bot error: {e}")
        raise


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Stopped by user")
    except Exception as e:
        logger.error(f"Fatal error: {e}")
        sys.exit(1)
