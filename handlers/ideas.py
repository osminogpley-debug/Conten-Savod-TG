import logging
import time
import html
import json
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import ContextTypes

from handlers.start import is_admin
from database import get_setting, set_setting
from services.text_gen import generate_post_ideas, get_last_text_provider

logger = logging.getLogger(__name__)

IDEAS_PROFILES_KEY = "ideas_profiles_json"
IDEAS_ACTIVE_PROFILE_KEY = "ideas_active_profile"
IDEAS_HISTORY_KEY = "ideas_history_json"


def _ideas_keyboard(has_profile: bool, has_history: bool) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton("📝 Добавить/обновить шаблон", callback_data="ideas_set_profile")],
        [InlineKeyboardButton("📂 Выбрать шаблон", callback_data="ideas_pick_profile")],
        [InlineKeyboardButton("⚡ Сгенерировать 10 идей", callback_data="ideas_generate")],
    ]
    if has_history and has_profile:
        rows.append([InlineKeyboardButton("➕ Еще 10 без повторов", callback_data="ideas_generate_more")])
    if has_profile:
        rows.append([InlineKeyboardButton("👀 Показать активный шаблон", callback_data="ideas_show_profile")])
    rows.append([InlineKeyboardButton("🏠 Меню", callback_data="menu_back")])
    return InlineKeyboardMarkup(rows)


def _load_profiles(raw: str | None) -> dict[str, str]:
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    if not isinstance(data, dict):
        return {}
    out: dict[str, str] = {}
    for name, desc in data.items():
        if not isinstance(name, str) or not isinstance(desc, str):
            continue
        clean_name = name.strip()
        clean_desc = desc.strip()
        if clean_name and clean_desc:
            out[clean_name] = clean_desc
    return out


def _load_history(raw: str | None) -> dict[str, list[str]]:
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    if not isinstance(data, dict):
        return {}
    out: dict[str, list[str]] = {}
    for profile, ideas in data.items():
        if not isinstance(profile, str) or not isinstance(ideas, list):
            continue
        clean = [str(item).strip() for item in ideas if str(item).strip()]
        out[profile] = clean[:200]
    return out


def _extract_idea_lines(text: str, limit: int = 10) -> list[str]:
    lines = []
    for raw in (text or "").replace("\r", "").split("\n"):
        line = raw.strip().replace("*", "")
        if not line:
            continue
        line = line.lstrip("- ").strip()
        if ". " in line[:4] and line[:1].isdigit():
            line = line.split(". ", 1)[1].strip()
        elif ") " in line[:4] and line[:1].isdigit():
            line = line.split(") ", 1)[1].strip()
        if len(line) >= 8:
            lines.append(line)
        if len(lines) >= limit:
            break
    return lines


async def _get_profiles_and_active() -> tuple[dict[str, str], str]:
    profiles = _load_profiles(await get_setting(IDEAS_PROFILES_KEY, "{}"))
    active = (await get_setting(IDEAS_ACTIVE_PROFILE_KEY, "") or "").strip()
    if active not in profiles:
        active = next(iter(profiles.keys()), "")
    return profiles, active


async def _get_history() -> dict[str, list[str]]:
    return _load_history(await get_setting(IDEAS_HISTORY_KEY, "{}"))


async def _save_profiles(profiles: dict[str, str], active: str = "") -> None:
    await set_setting(IDEAS_PROFILES_KEY, json.dumps(profiles, ensure_ascii=False))
    if active:
        await set_setting(IDEAS_ACTIVE_PROFILE_KEY, active)


async def _save_history(history: dict[str, list[str]]) -> None:
    await set_setting(IDEAS_HISTORY_KEY, json.dumps(history, ensure_ascii=False))


def _profiles_pick_keyboard(names: list[str]) -> InlineKeyboardMarkup:
    rows = []
    for idx, name in enumerate(names):
        rows.append([InlineKeyboardButton(name[:45], callback_data=f"ideas_use_{idx}")])
    rows.append([InlineKeyboardButton("🔙 Назад", callback_data="menu_ideas")])
    return InlineKeyboardMarkup(rows)


async def _render_ideas_result(query, ideas_text: str, provider: str, elapsed: int, has_profile: bool, has_history: bool):
    await query.edit_message_text(
        "💡 10 идей для постов\n\n"
        f"🤖 ИИ: {provider}\n"
        f"⏱ Время: {elapsed} сек\n\n"
        f"{ideas_text}",
        reply_markup=_ideas_keyboard(has_profile, has_history),
    )


async def ideas_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /ideas"""
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("⛔ Нет доступа.")
        return

    profiles, active = await _get_profiles_and_active()
    history = await _get_history()
    has_profile = bool(active)
    has_history = bool(active and history.get(active))
    active_line = f"Активный шаблон: <b>{html.escape(active)}</b>\n\n" if active else ""

    await update.message.reply_text(
        "💡 <b>Генератор идей</b>\n\n"
        f"{active_line}"
        "1) Нажмите <b>Добавить/обновить шаблон</b> и отправьте: <code>Название | Описание канала</code>.\n"
        "2) Выберите активный шаблон через <b>Выбрать шаблон</b>.\n"
        "3) Нажимайте <b>Сгенерировать 10 идей</b> или <b>Еще 10 без повторов</b>.",
        parse_mode="HTML",
        reply_markup=_ideas_keyboard(has_profile, has_history),
    )


async def ideas_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Открыть меню генератора идей из inline-меню."""
    query = update.callback_query
    profiles, active = await _get_profiles_and_active()
    history = await _get_history()
    has_profile = bool(active)
    has_history = bool(active and history.get(active))
    active_line = f"Активный шаблон: <b>{html.escape(active)}</b>\n\n" if active else ""

    await query.edit_message_text(
        "💡 <b>Генератор идей</b>\n\n"
        f"{active_line}"
        "Сохраните несколько шаблонов каналов и генерируйте идеи в один клик.",
        parse_mode="HTML",
        reply_markup=_ideas_keyboard(has_profile, has_history),
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
            "📝 <b>Добавить/обновить шаблон канала</b>\n\n"
            "Отправьте одним сообщением в формате:\n"
            "<code>Название шаблона | Описание канала</code>\n\n"
            "Пример:\n"
            "<code>Китай для новичков | Канал про изучение китайского для начинающих 18-35, формат: слова, диалоги, разборы ошибок.</code>",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Меню", callback_data="menu_back")]]),
        )
        return

    if action == "ideas_pick_profile":
        profiles, active = await _get_profiles_and_active()
        names = list(profiles.keys())
        if not names:
            await query.edit_message_text(
                "⚠️ Шаблонов пока нет. Сначала добавьте шаблон.",
                reply_markup=_ideas_keyboard(False, False),
            )
            return

        context.user_data["ideas_profile_order"] = names
        suffix = f"\n\nТекущий: {active}" if active else ""
        await query.edit_message_text(
            f"📂 Выберите активный шаблон:{suffix}",
            reply_markup=_profiles_pick_keyboard(names),
        )
        return

    if action.startswith("ideas_use_"):
        order = context.user_data.get("ideas_profile_order") or []
        try:
            idx = int(action.replace("ideas_use_", ""))
        except ValueError:
            await query.answer("Неверный выбор")
            return
        if idx < 0 or idx >= len(order):
            await query.answer("Шаблон не найден")
            return

        selected = order[idx]
        await set_setting(IDEAS_ACTIVE_PROFILE_KEY, selected)
        history = await _get_history()
        await query.edit_message_text(
            f"✅ Активный шаблон: <b>{html.escape(selected)}</b>",
            parse_mode="HTML",
            reply_markup=_ideas_keyboard(True, bool(history.get(selected))),
        )
        return

    if action == "ideas_show_profile":
        profiles, active = await _get_profiles_and_active()
        history = await _get_history()
        if not active:
            await query.edit_message_text(
                "⚠️ Активный шаблон не выбран.",
                reply_markup=_ideas_keyboard(False, False),
            )
            return

        profile = profiles.get(active, "")

        trimmed = profile.strip()
        if len(trimmed) > 1200:
            trimmed = trimmed[:1200].rstrip() + "..."

        await query.edit_message_text(
            f"👀 <b>Активный шаблон:</b> {html.escape(active)}\n\n{html.escape(trimmed)}",
            parse_mode="HTML",
            reply_markup=_ideas_keyboard(True, bool(history.get(active))),
        )
        return

    if action == "ideas_generate":
        profiles, active = await _get_profiles_and_active()
        if not active:
            await query.edit_message_text(
                "⚠️ Сначала добавьте и выберите шаблон канала.",
                parse_mode="HTML",
                reply_markup=_ideas_keyboard(False, False),
            )
            return

        loading = await query.edit_message_text("⏳ Генерирую 10 идей для вашего канала...")
        started = time.monotonic()
        profile = profiles.get(active, "")
        ideas_text = await generate_post_ideas(profile, count=10)
        generated_items = _extract_idea_lines(ideas_text, 10)
        history = await _get_history()
        history[active] = generated_items
        await _save_history(history)
        provider = get_last_text_provider() or "неизвестно"
        elapsed = int(time.monotonic() - started)
        await _render_ideas_result(loading, ideas_text, provider, elapsed, True, bool(history.get(active)))
        return

    if action == "ideas_generate_more":
        profiles, active = await _get_profiles_and_active()
        history = await _get_history()
        if not active:
            await query.edit_message_text(
                "⚠️ Сначала добавьте и выберите шаблон канала.",
                reply_markup=_ideas_keyboard(False, False),
            )
            return

        old_ideas = history.get(active, [])
        if not old_ideas:
            await query.edit_message_text(
                "⚠️ Сначала нажмите «Сгенерировать 10 идей», затем можно получить еще 10 без повторов.",
                reply_markup=_ideas_keyboard(True, False),
            )
            return

        loading = await query.edit_message_text("⏳ Генерирую еще 10 идей без повторов...")
        started = time.monotonic()
        profile = profiles.get(active, "")
        ideas_text = await generate_post_ideas(profile, count=10, avoid_ideas=old_ideas)
        generated_items = _extract_idea_lines(ideas_text, 10)
        history[active] = (old_ideas + generated_items)[-200:]
        await _save_history(history)
        provider = get_last_text_provider() or "неизвестно"
        elapsed = int(time.monotonic() - started)

        await _render_ideas_result(loading, ideas_text, provider, elapsed, True, True)
        return

    if action == "menu_ideas":
        await ideas_menu(update, context)
        return


async def handle_ideas_profile_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Сохранение описания канала для генератора идей."""
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("⛔ Нет доступа.")
        return

    raw = (update.message.text or "").strip()
    if "|" not in raw:
        await update.message.reply_text(
            "⚠️ Нужен формат: <code>Название | Описание канала</code>",
            parse_mode="HTML",
        )
        return

    name, profile = [part.strip() for part in raw.split("|", 1)]
    if len(name) < 2:
        await update.message.reply_text("⚠️ Слишком короткое название шаблона.")
        return
    if len(profile) < 25:
        await update.message.reply_text(
            "⚠️ Слишком коротко. Добавьте чуть больше деталей (минимум 25 символов)."
        )
        return

    if len(name) > 60:
        name = name[:60].strip()
    if len(profile) > 4000:
        profile = profile[:4000]

    profiles, _active = await _get_profiles_and_active()
    profiles[name] = profile
    await _save_profiles(profiles, active=name)
    context.user_data["state"] = None

    history = await _get_history()

    await update.message.reply_text(
        f"✅ Шаблон <b>{html.escape(name)}</b> сохранен и выбран активным.\n"
        "Теперь нажмите <b>Сгенерировать 10 идей</b>.",
        parse_mode="HTML",
        reply_markup=_ideas_keyboard(True, bool(history.get(name))),
    )

    logger.info("Сохранен шаблон канала для генератора идей: %s", name)
