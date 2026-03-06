import logging
import time
import html
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import ContextTypes

from handlers.start import is_admin
from database import get_setting, set_setting
from services.text_gen import generate_post_ideas, get_last_text_provider

logger = logging.getLogger(__name__)

IDEAS_PROFILE_KEY = "ideas_channel_profile"


def _ideas_keyboard(has_profile: bool) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton("📝 Описать канал", callback_data="ideas_set_profile")],
        [InlineKeyboardButton("⚡ Сгенерировать 10 идей", callback_data="ideas_generate")],
    ]
    if has_profile:
        rows.append([InlineKeyboardButton("👀 Показать описание", callback_data="ideas_show_profile")])
    rows.append([InlineKeyboardButton("🏠 Меню", callback_data="menu_back")])
    return InlineKeyboardMarkup(rows)


async def ideas_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /ideas"""
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("⛔ Нет доступа.")
        return

    profile = await get_setting(IDEAS_PROFILE_KEY, "")
    has_profile = bool((profile or "").strip())

    await update.message.reply_text(
        "💡 <b>Генератор идей</b>\n\n"
        "1) Сначала нажмите <b>Описать канал</b> и кратко расскажите про тематику, аудиторию и формат.\n"
        "2) Затем нажимайте <b>Сгенерировать 10 идей</b> — получите список тем для постов.",
        parse_mode="HTML",
        reply_markup=_ideas_keyboard(has_profile),
    )


async def ideas_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Открыть меню генератора идей из inline-меню."""
    query = update.callback_query
    profile = await get_setting(IDEAS_PROFILE_KEY, "")
    has_profile = bool((profile or "").strip())

    await query.edit_message_text(
        "💡 <b>Генератор идей</b>\n\n"
        "Сохраните описание вашего канала и генерируйте 10 идей в один клик.",
        parse_mode="HTML",
        reply_markup=_ideas_keyboard(has_profile),
    )


async def ideas_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка inline-кнопок генератора идей."""
    query = update.callback_query
    await query.answer()

    if not is_admin(query.from_user.id):
        return

    action = query.data

    if action == "ideas_set_profile":
        context.user_data["state"] = "awaiting_ideas_profile"
        await query.edit_message_text(
            "📝 <b>Описание канала</b>\n\n"
            "Отправьте одним сообщением:\n"
            "• о чем канал\n"
            "• для какой аудитории\n"
            "• какой формат постов вам нужен\n\n"
            "Пример: Канал про изучение китайского для начинающих, аудитория 18-35, хочу практичные посты: слова, диалоги, разборы ошибок.",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Меню", callback_data="menu_back")]]),
        )
        return

    if action == "ideas_show_profile":
        profile = await get_setting(IDEAS_PROFILE_KEY, "")
        if not (profile or "").strip():
            await query.edit_message_text(
                "⚠️ Описание канала пока не сохранено.",
                reply_markup=_ideas_keyboard(False),
            )
            return

        trimmed = profile.strip()
        if len(trimmed) > 1200:
            trimmed = trimmed[:1200].rstrip() + "..."

        await query.edit_message_text(
            f"👀 <b>Текущее описание канала:</b>\n\n{html.escape(trimmed)}",
            parse_mode="HTML",
            reply_markup=_ideas_keyboard(True),
        )
        return

    if action == "ideas_generate":
        profile = await get_setting(IDEAS_PROFILE_KEY, "")
        if not (profile or "").strip():
            context.user_data["state"] = "awaiting_ideas_profile"
            await query.edit_message_text(
                "⚠️ Сначала нужно сохранить описание канала.\n\n"
                "Нажмите <b>Описать канал</b> или отправьте описание прямо сейчас.",
                parse_mode="HTML",
                reply_markup=_ideas_keyboard(False),
            )
            return

        loading = await query.edit_message_text("⏳ Генерирую 10 идей для вашего канала...")
        started = time.monotonic()
        ideas_text = await generate_post_ideas(profile, count=10)
        provider = get_last_text_provider() or "неизвестно"
        elapsed = int(time.monotonic() - started)

        await loading.edit_text(
            "💡 10 идей для постов\n\n"
            f"🤖 ИИ: {provider}\n"
            f"⏱ Время: {elapsed} сек\n\n"
            f"{ideas_text}",
            reply_markup=_ideas_keyboard(True),
        )
        return


async def handle_ideas_profile_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Сохранение описания канала для генератора идей."""
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("⛔ Нет доступа.")
        return

    profile = (update.message.text or "").strip()
    if len(profile) < 25:
        await update.message.reply_text(
            "⚠️ Слишком коротко. Добавьте чуть больше деталей (минимум 25 символов)."
        )
        return

    if len(profile) > 4000:
        profile = profile[:4000]

    await set_setting(IDEAS_PROFILE_KEY, profile)
    context.user_data["state"] = None

    await update.message.reply_text(
        "✅ Описание канала сохранено.\n"
        "Теперь нажмите <b>Генератор идей</b> → <b>Сгенерировать 10 идей</b>.",
        parse_mode="HTML",
        reply_markup=_ideas_keyboard(True),
    )

    logger.info("Сохранено описание канала для генератора идей")
