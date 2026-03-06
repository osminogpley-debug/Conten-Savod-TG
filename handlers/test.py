import logging
import os
import asyncio
import time
import contextlib
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import ContextTypes
from handlers.start import is_admin
from services.text_gen import generate_text, generate_image_prompt, get_last_text_provider
from services.image_gen import generate_image
from config import MAX_POST_LENGTH
from utils import fit_caption, TEST_PREFIX, CAPTION_MAX

logger = logging.getLogger(__name__)


async def testpost_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /testpost"""
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("⛔ Нет доступа.")
        return
    
    from config import STYLE_NAMES
    rows = [[InlineKeyboardButton(name, callback_data=f"tstyle_{key}")] for key, name in STYLE_NAMES.items()]
    rows.append([InlineKeyboardButton("🏠 Меню", callback_data="menu_back")])

    await update.message.reply_text(
        "🧪 <b>Тестовый пост</b>\n\n"
        "Выберите стиль, затем отправьте тему. Пост придёт вам в ЛС.",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(rows)
    )
    context.user_data["test_style"] = "default"
    context.user_data["state"] = "awaiting_test_topic"


async def test_style_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Выбор стиля для тестового поста."""
    query = update.callback_query
    await query.answer()
    if not is_admin(query.from_user.id):
        return

    style_key = query.data.replace("tstyle_", "")
    context.user_data["test_style"] = style_key
    context.user_data["state"] = "awaiting_test_topic"

    from config import STYLE_NAMES
    style_name = STYLE_NAMES.get(style_key, "Стандартный")
    await query.edit_message_text(
        f"🧪 Тестовый стиль: <b>{style_name}</b>\n\n"
        "Отправьте тему. Я сгенерирую тестовый пост и отправлю в ЛС.",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Меню", callback_data="menu_back")]])
    )


async def handle_test_topic(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка темы для тестового поста"""
    topic = update.message.text.strip()
    user_id = update.effective_user.id
    style = context.user_data.get("test_style", "default")

    loading_msg = await update.message.reply_text("⏳ Loading тестового поста... Шаг 1/3: генерирую текст")
    start_ts = time.monotonic()
    timer_stop = asyncio.Event()
    timer_task = None
    
    try:
        # Шаг 1: Генерация текста
        text = await generate_text(topic, style=style)
        provider = get_last_text_provider() or "неизвестно"
        context.user_data["test_ai_provider"] = provider

        await loading_msg.edit_text(f"⏳ Loading тестового поста... Шаг 2/3: готовлю промпт картинки\n🤖 ИИ текста: {provider}")

        # Умная обрезка с учётом тестового префикса
        text = fit_caption(text, TEST_PREFIX)
        
        # Шаг 2: Промпт картинки
        img_prompt = await generate_image_prompt(topic)

        # Шаг 3: Генерация картинки + живой таймер ожидания
        async def _loading_timer():
            while not timer_stop.is_set():
                elapsed = int(time.monotonic() - start_ts)
                try:
                    await loading_msg.edit_text(
                        "⏳ Loading тестового поста... Шаг 3/3: генерирую картинку\n"
                        f"🤖 ИИ текста: {provider}\n"
                        f"⏱ Прошло: {elapsed} сек"
                    )
                except Exception:
                    pass
                await asyncio.sleep(6)

        timer_task = asyncio.create_task(_loading_timer())
        image_path = await generate_image(img_prompt)
        timer_stop.set()
        with contextlib.suppress(Exception):
            await timer_task
        
        test_text = f"{TEST_PREFIX}🤖 <b>ИИ:</b> {provider}\n\n{text}"
        
        if image_path and os.path.exists(image_path):
            with open(image_path, "rb") as photo:
                await context.bot.send_photo(
                    chat_id=user_id,
                    photo=photo,
                    caption=test_text,
                    parse_mode="HTML"
                )
        else:
            await context.bot.send_message(
                chat_id=user_id,
                text=test_text,
                parse_mode="HTML"
            )
        
        await loading_msg.edit_text("✅ Тестовый пост сгенерирован и отправлен в ЛС")

        await update.message.reply_text(
            "✅ Тестовый пост отправлен!",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🏠 Меню", callback_data="menu_back")]
            ])
        )
        context.user_data["state"] = None
        
        logger.info(f"Тестовый пост отправлен: {topic[:50]}")
        
    except Exception as e:
        timer_stop.set()
        if timer_task:
            with contextlib.suppress(Exception):
                await timer_task
        logger.error(f"Ошибка тестового поста: {e}", exc_info=True)
        try:
            await loading_msg.edit_text("❌ Ошибка генерации тестового поста")
        except Exception:
            pass
        await update.message.reply_text(
            f"❌ Ошибка: {e}",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🏠 Меню", callback_data="menu_back")]
            ])
        )
        context.user_data["state"] = None
