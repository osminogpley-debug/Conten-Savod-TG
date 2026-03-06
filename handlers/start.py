import logging
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import ContextTypes
from config import ADMIN_ID, CHANNEL_ID, set_admin_id
from database import set_setting
from utils import build_menu_keyboard

logger = logging.getLogger(__name__)


def is_admin(user_id: int) -> bool:
    """Проверка — является ли пользователь админом"""
    if ADMIN_ID == 0:
        return True
    return user_id == ADMIN_ID


async def start_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /start"""
    user = update.effective_user

    if ADMIN_ID == 0:
        set_admin_id(user.id)
        await set_setting("admin_id", str(user.id))
        logger.info(f"Admin ID автоматически установлен: {user.id}")

    if not is_admin(user.id):
        await update.message.reply_text("⛔ У вас нет доступа к этому боту.")
        return

    from config import CHANNEL_ID as current_channel

    keyboard = build_menu_keyboard()

    channel_status = f"✅ подключён (<code>{current_channel}</code>)" if current_channel else "❌ не подключён — /connect"

    text = (
        f"👋 Привет, {user.first_name}!\n\n"
        f"Я бот для управления каналом <b>Chinaя</b>.\n\n"
        f"📊 <b>Статус:</b>\n"
        f"• Канал: {channel_status}\n"
        f"• Админ: ✅ {user.id}\n\n"
        f"Выберите действие:"
    )

    await update.message.reply_text(text, reply_markup=keyboard, parse_mode="HTML")


async def menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка inline кнопок главного меню"""
    query = update.callback_query
    await query.answer()

    if not is_admin(query.from_user.id):
        await query.edit_message_text("⛔ У вас нет доступа.")
        return

    action = query.data

    if action == "menu_back":
        context.user_data["state"] = None
        from config import CHANNEL_ID as current_channel
        keyboard = build_menu_keyboard()
        channel_status = f"✅ подключён (<code>{current_channel}</code>)" if current_channel else "❌ не подключён — /connect"
        text = (
            f"👋 <b>Главное меню</b>\n\n"
            f"📊 <b>Статус:</b>\n"
            f"• Канал: {channel_status}\n\n"
            f"Выберите действие:"
        )
        await query.edit_message_text(text, reply_markup=keyboard, parse_mode="HTML")
        return

    elif action == "menu_newpost":
        # Выбор стиля перед генерацией
        from config import STYLE_NAMES
        rows = []
        for key, name in STYLE_NAMES.items():
            rows.append([InlineKeyboardButton(name, callback_data=f"style_{key}")])
        rows.append([InlineKeyboardButton("🏠 Меню", callback_data="menu_back")])
        await query.edit_message_text(
            "📝 <b>Создание поста</b>\n\n"
            "Выберите стиль:",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(rows)
        )

    elif action == "menu_news":
        await query.edit_message_text(
            "📰 <b>Пост из новости</b>\n\n"
            "Выберите способ:\n"
            "1. Отправьте <b>ссылку</b> на новость\n"
            "2. Отправьте <b>тему</b> для поиска новостей",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Меню", callback_data="menu_back")]])
        )
        context.user_data["state"] = "awaiting_news_input"

    elif action == "menu_schedule":
        await query.edit_message_text(
            "⏰ <b>Запланировать пост</b>\n\n"
            "Отправьте тему поста.\nПотом выберете дату и время.",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Меню", callback_data="menu_back")]])
        )
        context.user_data["state"] = "awaiting_schedule_topic"

    elif action == "menu_testpost":
        await query.edit_message_text(
            "🧪 <b>Тестовый пост</b>\n\n"
            "Отправьте тему. Пост придёт в ЛС.",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Меню", callback_data="menu_back")]])
        )
        context.user_data["state"] = "awaiting_test_topic"

    elif action == "menu_list":
        from handlers.schedule import list_scheduled_handler
        await list_scheduled_handler(update, context)

    elif action == "menu_templates":
        from handlers.templates import templates_menu
        await templates_menu(update, context)

    elif action == "menu_history":
        context.user_data["history_page"] = 0
        from handlers.history import history_menu
        await history_menu(update, context)

    elif action == "menu_stats":
        from handlers.history import stats_menu
        await stats_menu(update, context)

    elif action == "menu_calendar":
        from handlers.calendar import calendar_menu
        await calendar_menu(update, context)

    elif action == "menu_queue":
        from handlers.autopost import queue_menu
        await queue_menu(update, context)

    elif action == "menu_autopost":
        from handlers.autopost import autopost_menu
        await autopost_menu(update, context)

    elif action == "menu_connect":
        await query.edit_message_text(
            "📺 <b>Подключение канала</b>\n\n"
            "Используйте команду /connect",
            parse_mode="HTML"
        )

    elif action == "menu_settings":
        from handlers.settings import show_settings
        await show_settings(update, context)

    elif action == "menu_capabilities":
        text = (
            "❓ <b>Что я могу:</b>\n\n"
            "📝 <b>Создание постов</b>\n"
            "• Генерация постов по теме с ИИ\n"
            "• 5 стилей: стандартный, провокационный, образовательный, лёгкий, деловой\n"
            "• Редактирование текста, хэштеги, реакции, опросы\n"
            "• Генерация иллюстрации к посту\n\n"
            "📑 <b>Шаблоны</b>\n"
            "• Факт дня, Китайская мудрость, Новости недели, Разбор иероглифа\n\n"
            "📰 <b>Контент из новостей</b>\n"
            "• Создание постов по ссылке или теме\n\n"
            "⏰ <b>Планирование</b>\n"
            "• Отложенная публикация на точное время\n"
            "• Просмотр и отмена запланированных\n\n"
            "🤖 <b>Автопостинг</b>\n"
            "• Автоматическая публикация по расписанию\n"
            "• Очередь тем для автопубликации\n\n"
            "📊 <b>Аналитика</b>\n"
            "• История опубликованных постов\n"
            "• Статистика генераций\n"
            "• Календарь контент-плана\n\n"
            "⚙️ <b>Настройки</b>\n"
            "• Подключение канала\n"
            "• Подпись к постам\n"
            "• Авто-хэштеги\n"
        )
        await query.edit_message_text(
            text,
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Меню", callback_data="menu_back")]])
        )


async def help_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /help"""
    text = (
        "📖 <b>Команды бота:</b>\n\n"
        "/start — Главное меню\n"
        "/newpost — Создать пост\n"
        "/testpost — Тестовый пост (в ЛС)\n"
        "/news — Пост из новости\n"
        "/schedule — Запланировать пост\n"
        "/list — Список запланированных\n"
        "/cancel — Отменить пост\n"
        "/connect — Подключить канал\n"
        "/settings — Настройки\n"
        "/help — Эта справка"
    )
    await update.message.reply_text(text, parse_mode="HTML")
