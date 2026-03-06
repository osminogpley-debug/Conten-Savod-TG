"""Утилиты для бота Chinaя"""
import hashlib
import re


def smart_truncate(text: str, max_length: int, suffix: str = "") -> str:
    """Обрезает текст по последнему завершённому предложению, не превышая max_length."""
    if len(text) <= max_length:
        return text
    
    budget = max_length - len(suffix)
    if budget <= 0:
        return text[:max_length]
    
    chunk = text[:budget]
    
    sentence_end = None
    for m in re.finditer(r'[.!?…»)"][)\s]*', chunk):
        pos = m.end()
        if pos <= budget:
            sentence_end = pos
    
    if sentence_end and sentence_end > budget * 0.3:
        return text[:sentence_end].rstrip() + suffix
    
    last_space = chunk.rfind(' ')
    if last_space > budget * 0.3:
        return text[:last_space].rstrip() + suffix
    
    return chunk.rstrip() + suffix


# Константы для лимитов caption
CAPTION_MAX = 1024
PREVIEW_PREFIX = "📋 <b>Превью поста:</b>\n\n"
PREVIEW_UPD_PREFIX = "📋 <b>Превью поста (обновлено):</b>\n\n"
PREVIEW_IMG_PREFIX = "📋 <b>Превью поста (новая картинка):</b>\n\n"
TEST_PREFIX = "🧪 <b>ТЕСТОВЫЙ ПОСТ</b>\n\n"


def fit_caption(text: str, prefix: str = "", max_length: int = CAPTION_MAX) -> str:
    """Подгоняет текст под лимит Telegram caption с учётом префикса."""
    budget = max_length - len(prefix)
    return smart_truncate(text, budget)


def prompt_hash(prompt: str) -> str:
    """SHA256 хэш промпта для кэширования."""
    return hashlib.sha256(prompt.strip().lower().encode()).hexdigest()[:16]


def strip_html(text: str) -> str:
    """Убирает HTML-теги из текста."""
    return re.sub(r'<[^>]+>', '', text)


def build_menu_keyboard():
    """Стандартная клавиатура главного меню."""
    from telegram import InlineKeyboardMarkup, InlineKeyboardButton
    from config import CHANNEL_ID
    rows = [
        [InlineKeyboardButton("📝 Новый пост", callback_data="menu_newpost")],
        [InlineKeyboardButton("📰 Из новости", callback_data="menu_news"),
         InlineKeyboardButton("📑 Шаблоны", callback_data="menu_templates")],
        [InlineKeyboardButton("⏰ Запланировать", callback_data="menu_schedule"),
         InlineKeyboardButton("📋 Список", callback_data="menu_list")],
        [InlineKeyboardButton("📊 История", callback_data="menu_history"),
         InlineKeyboardButton("📈 Статистика", callback_data="menu_stats")],
        [InlineKeyboardButton("📅 Календарь", callback_data="menu_calendar"),
         InlineKeyboardButton("📚 Очередь тем", callback_data="menu_queue")],
        [InlineKeyboardButton("🤖 Автопостинг", callback_data="menu_autopost"),
         InlineKeyboardButton("🧪 Тест", callback_data="menu_testpost")],
    ]
    if not CHANNEL_ID:
        rows.append([InlineKeyboardButton("📺 Подключить канал", callback_data="menu_connect")])
    rows.append([InlineKeyboardButton("❓ Что я могу", callback_data="menu_capabilities"),
                 InlineKeyboardButton("⚙️ Настройки", callback_data="menu_settings")])
    return InlineKeyboardMarkup(rows)


def back_button(callback: str = "menu_back"):
    """Одна кнопка 'Меню'."""
    from telegram import InlineKeyboardMarkup, InlineKeyboardButton
    return InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Меню", callback_data=callback)]])
