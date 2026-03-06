import logging
import re
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import ContextTypes
from handlers.start import is_admin
from services.news_fetcher import fetch_news_by_topic, extract_article_text
from services.text_gen import rewrite_news, generate_text, generate_image_prompt
from services.image_gen import generate_image
from config import MAX_POST_LENGTH
from utils import fit_caption, PREVIEW_PREFIX

logger = logging.getLogger(__name__)

URL_PATTERN = re.compile(r'https?://\S+')


async def news_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /news"""
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("⛔ Нет доступа.")
        return
    
    await update.message.reply_text(
        "📰 <b>Пост из новости</b>\n\n"
        "📎 Отправьте <b>ссылку</b> на новость — бот перепишет\n"
        "📝 Или отправьте <b>тему</b> — бот найдёт свежую новость\n\n"
        "Примеры тем:\n"
        "• <i>китайские визы</i>\n"
        "• <i>бизнес с Китаем</i>\n"
        "• <i>технологии Китая</i>",
        parse_mode="HTML"
    )
    context.user_data["state"] = "awaiting_news_input"


async def handle_news_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка ввода для новостного поста"""
    user_input = update.message.text.strip()
    
    # Определяем — ссылка или тема
    url_match = URL_PATTERN.search(user_input)
    
    if url_match:
        await _process_news_url(update, context, url_match.group())
    else:
        await _process_news_topic(update, context, user_input)


async def _process_news_url(update: Update, context: ContextTypes.DEFAULT_TYPE, url: str):
    """Обработка ссылки на новость"""
    await update.message.reply_text(f"📎 Извлекаю текст из: {url}\n⏳ Подождите...")
    
    try:
        article_text = await extract_article_text(url)
        
        if not article_text:
            await update.message.reply_text(
                "❌ Не удалось извлечь текст из статьи.\n"
                "Попробуйте скопировать и отправить текст новости напрямую."
            )
            context.user_data["state"] = "awaiting_news_raw_text"
            return
        
        await update.message.reply_text("📝 Переписываю в стиле канала...")
        
        # Переписываем
        rewritten = await rewrite_news(article_text)
        if not rewritten or len(rewritten) < 50:
            rewritten = await generate_text(f"Новость: {article_text[:500]}")
        
        rewritten = fit_caption(rewritten, PREVIEW_PREFIX)
        
        # Генерация картинки
        img_prompt = await generate_image_prompt(article_text[:200])
        image_path = await generate_image(img_prompt)
        
        # Сохраняем черновик
        context.user_data["draft_text"] = rewritten
        context.user_data["draft_image"] = image_path
        context.user_data["draft_topic"] = f"Новость: {url}"
        context.user_data["draft_img_prompt"] = img_prompt
        context.user_data["state"] = "draft_ready"
        
        # Показываем превью
        from handlers.post import _show_preview
        await _show_preview(update, context, rewritten, image_path)
        
    except Exception as e:
        logger.error(f"Ошибка обработки новости: {e}", exc_info=True)
        await update.message.reply_text(f"❌ Ошибка: {e}")
        context.user_data["state"] = None


async def _process_news_topic(update: Update, context: ContextTypes.DEFAULT_TYPE, topic: str):
    """Поиск и обработка новости по теме"""
    await update.message.reply_text(f"🔍 Ищу свежие новости по теме: <i>{topic}</i>...", parse_mode="HTML")
    
    try:
        news = await fetch_news_by_topic(topic)
        
        if not news:
            await update.message.reply_text(
                f"❌ Свежих новостей по теме «{topic}» не найдено.\n\n"
                f"Попробуйте другую тему или отправьте ссылку на новость.",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Меню", callback_data="menu_back")]])
            )
            context.user_data["state"] = None
            return
        
        await update.message.reply_text(
            f"📰 Найдено: <b>{news['title'][:80]}</b>\n"
            f"📝 Переписываю в стиле канала...",
            parse_mode="HTML"
        )
        
        # Пытаемся получить полный текст
        full_text = news["text"]
        if news.get("url"):
            extracted = await extract_article_text(news["url"])
            if extracted and len(extracted) > len(full_text):
                full_text = extracted
        
        # Переписываем
        rewritten = await rewrite_news(full_text)
        if not rewritten or len(rewritten) < 50:
            rewritten = await generate_text(f"Новость о Китае: {news['title']}")
        
        rewritten = fit_caption(rewritten, PREVIEW_PREFIX)
        
        # Картинка
        img_prompt = await generate_image_prompt(news["title"])
        image_path = await generate_image(img_prompt)
        
        # Черновик
        context.user_data["draft_text"] = rewritten
        context.user_data["draft_image"] = image_path
        context.user_data["draft_topic"] = f"Новость: {news['title'][:50]}"
        context.user_data["draft_img_prompt"] = img_prompt
        context.user_data["state"] = "draft_ready"
        
        from handlers.post import _show_preview
        await _show_preview(update, context, rewritten, image_path)
        
    except Exception as e:
        logger.error(f"Ошибка поиска новостей: {e}", exc_info=True)
        await update.message.reply_text(f"❌ Ошибка: {e}")
        context.user_data["state"] = None


async def handle_raw_news_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка сырого текста новости (отправленного вручную)"""
    raw_text = update.message.text.strip()
    
    await update.message.reply_text("📝 Переписываю в стиле канала...")
    
    try:
        rewritten = await rewrite_news(raw_text)
        rewritten = fit_caption(rewritten, PREVIEW_PREFIX)
        
        img_prompt = await generate_image_prompt(raw_text[:200])
        image_path = await generate_image(img_prompt)
        
        context.user_data["draft_text"] = rewritten
        context.user_data["draft_image"] = image_path
        context.user_data["draft_topic"] = f"Новость (ручной ввод)"
        context.user_data["draft_img_prompt"] = img_prompt
        context.user_data["state"] = "draft_ready"
        
        from handlers.post import _show_preview
        await _show_preview(update, context, rewritten, image_path)
        
    except Exception as e:
        await update.message.reply_text(f"❌ Ошибка: {e}")
        context.user_data["state"] = None
