import logging
import os
import sys
from datetime import datetime
from zoneinfo import ZoneInfo
from contextlib import suppress

from telegram import Update
from telegram.error import Conflict as TgConflict
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    CallbackQueryHandler, ChatMemberHandler, filters
)

# Добавляем корень проекта в path
sys.path.insert(0, os.path.dirname(__file__))

from config import BOT_TOKEN, TIMEZONE
from database import init_db, get_setting, set_setting
from config import set_channel_id, set_admin_id, ADMIN_ID

# === Логирование ===
LOG_DIR = os.path.join(os.path.dirname(__file__), "logs")
os.makedirs(LOG_DIR, exist_ok=True)

log_file = os.path.join(LOG_DIR, f"bot_{datetime.now().strftime('%Y%m%d')}.log")

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    level=logging.INFO,
    handlers=[
        logging.FileHandler(log_file, encoding="utf-8"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# Уменьшаем шум от httpx
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

_LOCK_HANDLE = None
_LOCK_PATH = os.path.join(os.path.dirname(__file__), ".bot.instance.lock")


def _acquire_single_instance_lock() -> bool:
    """Гарантирует, что запущен только один экземпляр бота."""
    global _LOCK_HANDLE
    if _LOCK_HANDLE is not None:
        return True

    try:
        lock_file = open(_LOCK_PATH, "a+")
    except OSError as e:
        logger.error(f"Не удалось открыть lock-файл: {e}")
        return False

    try:
        if os.name == "nt":
            import msvcrt
            lock_file.seek(0)
            msvcrt.locking(lock_file.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except Exception:
        with suppress(Exception):
            lock_file.close()
        logger.error("Обнаружен уже запущенный экземпляр бота. Второй запуск остановлен.")
        return False

    with suppress(Exception):
        lock_file.seek(0)
        lock_file.truncate()
        lock_file.write(str(os.getpid()))
        lock_file.flush()

    _LOCK_HANDLE = lock_file
    return True


def _release_single_instance_lock() -> None:
    """Освобождает lock при остановке бота."""
    global _LOCK_HANDLE
    if _LOCK_HANDLE is None:
        return

    try:
        if os.name == "nt":
            import msvcrt
            _LOCK_HANDLE.seek(0)
            msvcrt.locking(_LOCK_HANDLE.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            fcntl.flock(_LOCK_HANDLE.fileno(), fcntl.LOCK_UN)
    except Exception:
        pass
    finally:
        with suppress(Exception):
            _LOCK_HANDLE.close()
        _LOCK_HANDLE = None


async def handle_text_message(update: Update, context):
    """Роутер текстовых сообщений в зависимости от состояния"""
    state = context.user_data.get("state")
    
    if state == "awaiting_post_topic":
        from handlers.post import handle_post_topic
        await handle_post_topic(update, context)
    
    elif state == "awaiting_post_edit":
        from handlers.post import handle_post_edit
        await handle_post_edit(update, context)
    
    elif state == "awaiting_schedule_topic":
        from handlers.schedule import handle_schedule_topic
        await handle_schedule_topic(update, context)
    
    elif state == "awaiting_schedule_time":
        from handlers.schedule import handle_schedule_time
        await handle_schedule_time(update, context)
    
    elif state == "awaiting_test_topic":
        from handlers.test import handle_test_topic
        await handle_test_topic(update, context)
    
    elif state == "awaiting_news_input":
        from handlers.news import handle_news_input
        await handle_news_input(update, context)
    
    elif state == "awaiting_news_raw_text":
        from handlers.news import handle_raw_news_text
        await handle_raw_news_text(update, context)
    
    elif state == "awaiting_default_time":
        from handlers.settings import handle_default_time
        await handle_default_time(update, context)
    
    elif state == "awaiting_channel_forward":
        from handlers.channel import handle_channel_forward
        await handle_channel_forward(update, context)
    
    elif state == "awaiting_channel_id":
        from handlers.channel import handle_channel_id_input
        await handle_channel_id_input(update, context)
    
    elif state == "awaiting_template_topic":
        from handlers.templates import handle_template_topic
        await handle_template_topic(update, context)
    
    elif state == "awaiting_queue_topics":
        from handlers.autopost import handle_queue_topics
        await handle_queue_topics(update, context)
    
    elif state == "awaiting_autopost_times":
        from handlers.autopost import handle_autopost_times
        await handle_autopost_times(update, context)
    
    elif state == "awaiting_signature":
        from handlers.settings import handle_signature_input
        await handle_signature_input(update, context)

    elif state == "awaiting_ideas_profile":
        from handlers.ideas import handle_ideas_profile_input
        await handle_ideas_profile_input(update, context)
    
    else:
        if state == "draft_ready":
            await update.message.reply_text(
                "📋 У вас есть готовый черновик. Используйте кнопки выше для публикации, или /start для нового поста."
            )
        else:
            await update.message.reply_text(
                "Используйте /start для начала работы или выберите команду из меню."
            )


async def handle_callback(update: Update, context):
    """Роутер inline кнопок"""
    query = update.callback_query
    data = query.data
    
    if data.startswith("menu_"):
        from handlers.start import menu_callback
        await menu_callback(update, context)
    
    elif data.startswith("post_"):
        from handlers.post import post_callback
        await post_callback(update, context)
    
    elif data.startswith("style_"):
        from handlers.post import style_callback
        await style_callback(update, context)

    elif data.startswith("tstyle_"):
        from handlers.test import test_style_callback
        await test_style_callback(update, context)
    
    elif data.startswith("tmpl_"):
        from handlers.templates import template_callback
        await template_callback(update, context)
    
    elif data.startswith("queue_"):
        from handlers.autopost import queue_callback
        await queue_callback(update, context)
    
    elif data.startswith("autopost_"):
        from handlers.autopost import autopost_callback
        await autopost_callback(update, context)
    
    elif data.startswith("hist_"):
        from handlers.history import history_callback
        await history_callback(update, context)
    
    elif data.startswith("stats_"):
        from handlers.history import stats_callback
        await stats_callback(update, context)
    
    elif data.startswith("settings_"):
        from handlers.settings import settings_callback
        await settings_callback(update, context)

    elif data.startswith("ideas_"):
        from handlers.ideas import ideas_callback
        await ideas_callback(update, context)
    
    elif data.startswith("channel_"):
        from handlers.channel import channel_callback
        await channel_callback(update, context)
    
    elif data.startswith("react_"):
        # Реакции в канале — просто acknowledge
        await query.answer("👍")
    
    else:
        await query.answer("Неизвестное действие")


async def handle_forwarded_message(update: Update, context):
    """Обработка пересланных сообщений (для подключения канала)"""
    state = context.user_data.get("state")
    if state == "awaiting_channel_forward":
        from handlers.channel import handle_channel_forward
        await handle_channel_forward(update, context)
    else:
        # Если не ждём forward, обрабатываем как обычный текст
        await handle_text_message(update, context)


async def post_init(application):
    """Инициализация при запуске"""
    await init_db()
    
    # Загружаем сохранённые настройки из БД
    saved_channel = await get_setting("channel_id")
    from config import CHANNEL_ID as env_channel
    if not env_channel and saved_channel:
        set_channel_id(saved_channel)
        logger.info(f"CHANNEL_ID загружен из БД: {saved_channel}")
    
    saved_admin = await get_setting("admin_id")
    if saved_admin and ADMIN_ID == 0:
        set_admin_id(int(saved_admin))
        logger.info(f"ADMIN_ID загружен из БД: {saved_admin}")
    
    # Восстанавливаем запланированные посты из БД
    from services.scheduler import recover_scheduled_posts
    await recover_scheduled_posts(application.job_queue)
    
    # Автопостинг — восстанавливаем при старте
    from handlers.autopost import setup_autopost_on_start
    await setup_autopost_on_start(application.job_queue)
    
    # Telegram logging — ошибки ERROR+ админу в ЛС
    from services.tg_logging import setup_telegram_logging
    from config import ADMIN_ID as current_admin_id
    setup_telegram_logging(application.bot, current_admin_id)
    
    # Очистка старых картинок каждые 12 часов
    from services.image_gen import cleanup_old_images
    async def _cleanup_job(context):
        await cleanup_old_images(24)
    application.job_queue.run_repeating(
        _cleanup_job,
        interval=43200,  # 12 часов
        first=60,  # Первый запуск через минуту
        name="cleanup_images"
    )
    
    logger.info("=== Бот Chinaя запущен ===")
    logger.info(f"Таймзона: {TIMEZONE}")


async def error_handler(update, context):
    """Глобальный обработчик ошибок"""
    error_text = str(context.error or "")
    if isinstance(context.error, TgConflict) or "terminated by other getupdates request" in error_text.lower():
        logger.critical(
            "Обнаружен Conflict getUpdates: запущено несколько экземпляров бота. "
            "Останавливаю текущий процесс, чтобы убрать спам ошибок."
        )
        if context.application:
            context.application.stop_running()
        return

    logger.error(f"Ошибка при обработке обновления: {context.error}", exc_info=context.error)
    
    if update and update.effective_message:
        try:
            await update.effective_message.reply_text(
                "⚠️ Произошла ошибка. Попробуйте позже или используйте /start"
            )
        except:
            pass


def main():
    """Запуск бота"""
    if not BOT_TOKEN:
        logger.error("BOT_TOKEN не задан! Укажите токен в .env файле")
        sys.exit(1)
    
    logger.info("Запуск бота...")

    if not _acquire_single_instance_lock():
        sys.exit(1)
    
    # Создаём приложение
    async def post_shutdown(application):
        """Закрытие ресурсов при остановке"""
        from services.http_session import close_session
        await close_session()
        _release_single_instance_lock()
        logger.info("Бот остановлен, ресурсы освобождены")
    
    app = Application.builder().token(BOT_TOKEN).post_init(post_init).post_shutdown(post_shutdown).build()
    
    # === Команды ===
    from handlers.start import start_handler, help_handler
    from handlers.post import newpost_handler
    from handlers.schedule import schedule_handler, list_scheduled_handler, cancel_handler
    from handlers.test import testpost_handler
    from handlers.news import news_handler
    from handlers.ideas import ideas_handler
    from handlers.settings import settings_handler
    from handlers.channel import connect_handler, handle_my_chat_member
    
    app.add_handler(CommandHandler("start", start_handler))
    app.add_handler(CommandHandler("help", help_handler))
    app.add_handler(CommandHandler("newpost", newpost_handler))
    app.add_handler(CommandHandler("schedule", schedule_handler))
    app.add_handler(CommandHandler("list", list_scheduled_handler))
    app.add_handler(CommandHandler("cancel", cancel_handler))
    app.add_handler(CommandHandler("testpost", testpost_handler))
    app.add_handler(CommandHandler("news", news_handler))
    app.add_handler(CommandHandler("ideas", ideas_handler))
    app.add_handler(CommandHandler("settings", settings_handler))
    app.add_handler(CommandHandler("connect", connect_handler))
    
    # === Обработка добавления бота в канал ===
    app.add_handler(ChatMemberHandler(handle_my_chat_member, ChatMemberHandler.MY_CHAT_MEMBER))
    
    # === Inline кнопки ===
    app.add_handler(CallbackQueryHandler(handle_callback))
    
    # === Пересланные сообщения ===
    app.add_handler(MessageHandler(filters.FORWARDED & ~filters.COMMAND, handle_forwarded_message))
    
    # === Текстовые сообщения ===
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text_message))
    
    # === Обработка ошибок ===
    app.add_error_handler(error_handler)
    
    # === Запуск ===
    logger.info("Бот запущен и ожидает сообщений...")
    app.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)


if __name__ == "__main__":
    main()
