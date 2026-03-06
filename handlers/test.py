import logging
import os
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import ContextTypes
from handlers.start import is_admin
from services.text_gen import generate_text, generate_image_prompt
from services.image_gen import generate_image
from config import MAX_POST_LENGTH
from utils import fit_caption, TEST_PREFIX, CAPTION_MAX

logger = logging.getLogger(__name__)


async def testpost_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /testpost"""
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("⛔ Нет доступа.")
        return
    
    await update.message.reply_text(
        "🧪 <b>Тестовый пост</b>\n\n"
        "Отправьте тему. Пост придёт вам в ЛС (не в канал).",
        parse_mode="HTML"
    )
    context.user_data["state"] = "awaiting_test_topic"


async def handle_test_topic(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка темы для тестового поста"""
    topic = update.message.text.strip()
    user_id = update.effective_user.id
    
    await update.message.reply_text("⏳ Генерирую тестовый пост...")
    
    try:
        # Генерация текста
        text = await generate_text(topic)
        # Умная обрезка с учётом тестового префикса
        text = fit_caption(text, TEST_PREFIX)
        
        # Генерация картинки
        img_prompt = await generate_image_prompt(topic)
        image_path = await generate_image(img_prompt)
        
        test_text = f"{TEST_PREFIX}{text}"
        
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
        
        await update.message.reply_text(
            "✅ Тестовый пост отправлен!",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🏠 Меню", callback_data="menu_back")]
            ])
        )
        context.user_data["state"] = None
        
        logger.info(f"Тестовый пост отправлен: {topic[:50]}")
        
    except Exception as e:
        logger.error(f"Ошибка тестового поста: {e}", exc_info=True)
        await update.message.reply_text(
            f"❌ Ошибка: {e}",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🏠 Меню", callback_data="menu_back")]
            ])
        )
        context.user_data["state"] = None
