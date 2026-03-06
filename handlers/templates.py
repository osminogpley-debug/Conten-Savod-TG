"""Шаблоны постов — Факт дня, Китайская мудрость, Новости недели, Разбор иероглифа"""
import asyncio
import logging
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import ContextTypes
from handlers.start import is_admin
from config import TEMPLATES
from services.text_gen import generate_with_template, generate_image_prompt, get_last_text_provider
from services.image_gen import generate_image
from utils import fit_caption, PREVIEW_PREFIX, back_button

logger = logging.getLogger(__name__)


async def templates_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показать меню шаблонов"""
    query = update.callback_query
    if query:
        await query.answer()
        if not is_admin(query.from_user.id):
            return

    rows = []
    for key, tmpl in TEMPLATES.items():
        rows.append([InlineKeyboardButton(tmpl["name"], callback_data=f"tmpl_{key}")])
    rows.append([InlineKeyboardButton("🏠 Меню", callback_data="menu_back")])

    text = (
        "📑 <b>Шаблоны постов</b>\n\n"
        "Выберите шаблон — бот сгенерирует пост в заданном формате.\n"
        "После выбора отправьте тему."
    )

    if query:
        await query.edit_message_text(text, parse_mode="HTML",
                                       reply_markup=InlineKeyboardMarkup(rows))
    else:
        await update.message.reply_text(text, parse_mode="HTML",
                                         reply_markup=InlineKeyboardMarkup(rows))


async def template_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка выбора шаблона"""
    query = update.callback_query
    await query.answer()

    if not is_admin(query.from_user.id):
        return

    template_key = query.data.replace("tmpl_", "")
    tmpl = TEMPLATES.get(template_key)
    if not tmpl:
        await query.edit_message_text("❌ Шаблон не найден.", reply_markup=back_button())
        return

    context.user_data["template_key"] = template_key
    context.user_data["state"] = "awaiting_template_topic"

    await query.edit_message_text(
        f"📑 Шаблон: <b>{tmpl['name']}</b>\n\n"
        f"Отправьте тему для генерации.",
        parse_mode="HTML",
        reply_markup=back_button()
    )


async def handle_template_topic(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка темы для шаблонного поста"""
    topic = update.message.text.strip()
    template_key = context.user_data.get("template_key", "fact")

    tmpl = TEMPLATES.get(template_key, {})
    tmpl_name = tmpl.get("name", "Шаблон")

    loading_msg = await update.message.reply_text(
        f"⏳ Loading поста... Генерирую по шаблону «{tmpl_name}»..."
    )

    try:
        text, img_prompt = await asyncio.gather(
            generate_with_template(topic, template_key),
            generate_image_prompt(topic),
        )
        provider = get_last_text_provider() or "неизвестно"
        context.user_data["draft_ai_provider"] = provider

        text = fit_caption(text, PREVIEW_PREFIX)
        image_path = await generate_image(img_prompt)

        context.user_data["draft_text"] = text
        context.user_data["draft_image"] = image_path
        context.user_data["draft_topic"] = topic
        context.user_data["draft_img_prompt"] = img_prompt
        context.user_data["state"] = "draft_ready"

        await loading_msg.edit_text(f"✅ Пост по шаблону готов. 🤖 ИИ: {provider}")

        from handlers.post import _show_preview
        await _show_preview(update, context, text, image_path)

    except Exception as e:
        logger.error(f"Ошибка генерации по шаблону: {e}", exc_info=True)
        try:
            await loading_msg.edit_text("❌ Ошибка генерации поста по шаблону.")
        except Exception:
            pass
        await update.message.reply_text(f"❌ Ошибка: {e}", reply_markup=back_button())
        context.user_data["state"] = None
