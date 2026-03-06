"""
Генерация текста для бота Chinaя.
Проверенные рабочие API с автоматическим fallback.

Бесплатные (БЕЗ API-ключа):
1. Pollinations (OpenAI endpoint) — модель openai-fast (GPT-OSS 20B)
2. Pollinations (GET endpoint) — та же модель, другой способ вызова
3. Pollinations (alias gpt-oss) — тот же бэкенд, на случай ротации

С API-ключами (бесплатные tier-ы):
4. Google Gemini 2.0 Flash (GEMINI_API_KEY)
5. Groq — Llama 3.3 70B (GROQ_API_KEY)
6. Qwen (Alibaba DashScope, OpenAI-compatible) — QWEN_API_KEY
7. HuggingFace — Qwen 2.5 (HF_API_KEY)
"""

import aiohttp
import logging
import json
import os
import re
import time
import urllib.parse
from contextvars import ContextVar
from config import GEMINI_API_KEY, HF_API_KEY, CHANNEL_STYLE_PROMPT, SCHOOL_CONTEXT_PROMPT
from services.http_session import shared_session

logger = logging.getLogger(__name__)

GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
QWEN_API_KEY = os.getenv("QWEN_API_KEY", "")
QWEN_BASE_URL = os.getenv("QWEN_BASE_URL", "https://dashscope-intl.aliyuncs.com/compatible-mode/v1")
QWEN_MODEL = os.getenv("QWEN_MODEL", "qwen-plus")
HARD_QUALITY_MODE = os.getenv("HARD_QUALITY_MODE", "1").strip().lower() in {"1", "true", "yes", "on"}
GEMINI_COOLDOWN_SECONDS = 60 * 60  # 1 час
QWEN_COOLDOWN_SECONDS = 12 * 60 * 60  # 12 часов
_gemini_disabled_until = 0.0
_qwen_disabled_until = 0.0
_last_text_provider: ContextVar[str] = ContextVar("last_text_provider", default="")


class GeminiQuotaExceeded(Exception):
    """Gemini временно недоступен из-за лимитов/квоты."""


class QwenTemporarilyDisabled(Exception):
    """Qwen временно отключен (ошибка ключа, лимиты или сервис)."""

# ============================================================
# Публичные функции
# ============================================================

async def generate_text(topic: str, style: str = "default") -> str:
    """Генерация текста поста с fallback между API"""
    from config import STYLE_PROMPTS
    style_prompt = STYLE_PROMPTS.get(style, STYLE_PROMPTS["default"])
    prompt = f"""{style_prompt}

{SCHOOL_CONTEXT_PROMPT}

Напиши пост на тему: {topic}

ВАЖНО:
- Пиши ТОЛЬКО готовый текст поста на русском языке
- Без пояснений, комментариев, заголовков вроде 'Вот пост:'
- НЕ УХОДИ от темы пользователя: обязательно раскрывай именно «{topic}»
- Не пиши общие фразы про Китай без прямой связи с темой
- Если тема про иероглифы/грамматику: обязательно дай конкретные примеры, а не общие рассуждения
- Если тема про китайский язык: соблюдай структуру
    1) правило/разница,
    2) минимум 3 примера на китайском + перевод,
    3) частая ошибка ученика,
    4) короткая запоминалка
- Запрещены расплывчатые формулировки без практической пользы"""

    generators = _build_generator_list(prompt)

    for name, factory in generators:
        try:
            logger.info(f"Попытка генерации текста через {name}...")
            text = await factory()
            text = _clean_response(text)
            if text and len(text) > 100 and _looks_like_russian(text):
                quality = _quality_report(text, topic)
                if not quality["relevance_ok"]:
                    logger.warning(f"{name}: текст нерелевантен теме '{topic}', пробую следующий")
                    continue
                if HARD_QUALITY_MODE and not quality["all_ok"]:
                    logger.info(
                        f"{name}: пост не прошёл авто-чек качества ({', '.join(quality['failed_checks'])}), запускаю автопереписывание"
                    )
                    improved_text = await _rewrite_weak_text(text, topic, style_prompt, quality["failed_checks"])
                    if improved_text:
                        improved_quality = _quality_report(improved_text, topic)
                        if improved_quality["all_ok"]:
                            text = improved_text
                _set_last_text_provider(name)
                logger.info(f"Текст сгенерирован через {name} ({len(text)} символов)")
                return text
            elif text and len(text) > 100:
                logger.warning(f"{name}: текст не на русском ({len(text)} симв.), пробую следующий")
            else:
                logger.warning(f"{name}: слишком короткий ответ ({len(text) if text else 0} симв.)")
        except GeminiQuotaExceeded as e:
            logger.warning(f"{name}: {e}. Переключаюсь на fallback API")
            continue
        except QwenTemporarilyDisabled as e:
            logger.warning(f"{name}: {e}. Переключаюсь на fallback API")
            continue
        except Exception as e:
            logger.error(f"{name}: {e}")
            continue

    logger.error("Все API генерации текста недоступны")
    return _fallback_text(topic)


async def generate_with_template(topic: str, template_key: str) -> str:
    """Генерация текста по шаблону"""
    from config import TEMPLATES
    tmpl = TEMPLATES.get(template_key)
    if not tmpl:
        return await generate_text(topic)
    prompt = tmpl["prompt"].format(topic=topic)
    result = await generate_text_with_prompt(prompt)
    if result and HARD_QUALITY_MODE:
        quality = _quality_report(result, topic)
        if not quality["all_ok"]:
            improved = await _rewrite_weak_text(result, topic, CHANNEL_STYLE_PROMPT, quality["failed_checks"])
            if improved:
                result = improved
    return result if result else await generate_text(topic)


async def generate_hashtags(text: str) -> str:
    """Генерация хэштегов для поста"""
    from config import DEFAULT_HASHTAGS_PROMPT
    prompt = f"{DEFAULT_HASHTAGS_PROMPT}\n\nТекст поста:\n{text[:500]}"
    result = await generate_text_with_prompt(prompt)
    if result:
        # Извлекаем только хэштеги
        tags = re.findall(r'#[а-яёa-z0-9_]+', result, re.IGNORECASE)
        if tags:
            return " ".join(tags[:5])
    return ""


async def rewrite_news(news_text: str) -> str:
    """Переписать новость в стиле канала Chinaя"""
    prompt = f"""{CHANNEL_STYLE_PROMPT}

{SCHOOL_CONTEXT_PROMPT}

Перепиши эту новость в стиле канала. Сделай из неё интересный пост:

{news_text}

Важно: НЕ копируй текст, а перепиши своими словами на русском. Добавь контекст, мнение, полезность."""
    return await generate_text_with_prompt(prompt)


async def generate_text_with_prompt(prompt: str) -> str:
    """Генерация с произвольным промптом"""
    generators = _build_generator_list(prompt)

    for name, factory in generators:
        try:
            text = await factory()
            text = _clean_response(text)
            if text and len(text) > 30:
                _set_last_text_provider(name)
                return text
        except GeminiQuotaExceeded as e:
            logger.warning(f"{name}: {e}. Переключаюсь на fallback API")
            continue
        except QwenTemporarilyDisabled as e:
            logger.warning(f"{name}: {e}. Переключаюсь на fallback API")
            continue
        except Exception as e:
            logger.error(f"{name}: {e}")
            continue

    return None


async def generate_image_prompt(topic: str) -> str:
    """Генерация промпта для картинки на основе темы"""
    prompt = f"""Based on this topic about China: "{topic}"
Create a short (1-2 sentences, max 120 characters) image generation prompt in English.
Requirements:
- The image must directly illustrate the specific topic, NOT generic China imagery
- Soft pastel palette, warm muted tones, NO neon
- Minimalist composition, clean thin outlines, cute chibi style
- Keep one clear central subject; simple background with Chinese decorative accents
- If topic is language/hieroglyphs: calligraphy paper, brush, tea set, study desk elements
- If character is present: chibi style in hanfu, gentle expression, no realism
- Avoid photorealism, 3D render, clutter, complex busy scenes, abstraction
- No text on the image, no watermarks
Return ONLY the image prompt, nothing else."""

    result = await generate_text_with_prompt(prompt)
    if result and len(result) < 300:
        result = result.strip('"\'').strip()
        return result
    return f"Cute chibi Chinese-style illustration about {topic}, minimalist pastel scene, traditional details, clear central subject, no text"


# ============================================================
# Построение списка генераторов
# ============================================================

def _build_generator_list(prompt: str) -> list:
    """Собирает список (name, lambda) для fallback-цепочки.
    Используем lambda чтобы корутины создавались лениво (без warnings)."""
    generators = []

    hanzi_topic = _is_hanzi_topic(prompt)

    # --- API с ключами (быстрее и надёжнее) ---
    # Gemini должен быть самым первым
    if GEMINI_API_KEY and not _is_gemini_temporarily_disabled():
        generators.append(("Google Gemini", lambda: _gemini(prompt)))

    # Для тем про иероглифы/китайский язык далее даём приоритет Qwen
    if hanzi_topic and QWEN_API_KEY and not _is_qwen_temporarily_disabled():
        generators.append(("Qwen/DashScope (priority-hanzi)", lambda: _qwen(prompt)))
    elif hanzi_topic and HF_API_KEY:
        generators.append(("HuggingFace/Qwen (priority-hanzi)", lambda: _huggingface(prompt)))

    if GROQ_API_KEY:
        generators.append(("Groq/Llama-3.3-70B", lambda: _groq(prompt, "llama-3.3-70b-versatile")))
        generators.append(("Groq/Llama-3.1-8B", lambda: _groq(prompt, "llama-3.1-8b-instant")))

    if QWEN_API_KEY and not hanzi_topic and not _is_qwen_temporarily_disabled():
        generators.append(("Qwen/DashScope", lambda: _qwen(prompt)))

    # --- Pollinations (бесплатно, без ключа) ---
    generators.append(("Pollinations/openai-fast (POST)", lambda: _pollinations_openai(prompt, "openai-fast")))
    generators.append(("Pollinations/gpt-oss (POST)", lambda: _pollinations_openai(prompt, "gpt-oss")))
    generators.append(("Pollinations/openai-fast (GET)", lambda: _pollinations_get(prompt, "openai-fast")))
    generators.append(("Pollinations/openai (POST)", lambda: _pollinations_openai(prompt, "openai")))

    # --- HuggingFace (если есть ключ) ---
    if HF_API_KEY and not hanzi_topic:
        generators.append(("HuggingFace/Qwen", lambda: _huggingface(prompt)))

    return generators


# ============================================================
# Pollinations.ai — OpenAI-совместимый endpoint (POST)
# Работающая модель: openai-fast (GPT-OSS 20B)
# Алиасы: openai, gpt-oss, gpt-oss-20b, ovh-reasoning
# ============================================================

async def _pollinations_openai(prompt: str, model: str = "openai-fast") -> str:
    """Pollinations через OpenAI-совместимый /openai endpoint"""
    url = "https://text.pollinations.ai/openai"

    payload = {
        "model": model,
        "messages": [
            {
                "role": "system",
                "content": (
                    "Ты — профессиональный русскоязычный копирайтер. "
                    "ВСЕГДА пиши ТОЛЬКО на русском языке. "
                    "Выдавай ТОЛЬКО готовый текст — без пояснений, "
                    "без рассуждений, без комментариев, без вступлений."
                ),
            },
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.8,
        "max_tokens": 1500,
    }

    async with shared_session() as session:
        async with session.post(
            url,
            json=payload,
            timeout=aiohttp.ClientTimeout(total=90),
            headers={"Content-Type": "application/json"},
        ) as resp:
            if resp.status != 200:
                error = await resp.text()
                raise Exception(f"Pollinations/{model} HTTP {resp.status}: {error[:200]}")

            data = await resp.json()
            return _extract_openai_content(data, model)


def _extract_openai_content(data: dict, model: str) -> str:
    """Извлекает текст из OpenAI-формата ответа.
    Некоторые модели (openai-fast) — reasoning models, которые
    возвращают пустой content и кладут ответ в reasoning_content."""
    choices = data.get("choices", [])
    if not choices:
        raise Exception(f"Pollinations/{model}: нет choices в ответе")

    message = choices[0].get("message", {})
    content = message.get("content", "")

    if content and isinstance(content, str) and content.strip():
        return content.strip()

    # Reasoning models (openai-fast, ovh-reasoning) кладут ответ в reasoning_content
    reasoning = message.get("reasoning_content", "")
    if reasoning and isinstance(reasoning, str) and reasoning.strip():
        # reasoning_content может содержать и рассуждения на английском,
        # и ответ на русском — извлекаем последний русскоязычный блок
        extracted = _extract_russian_from_reasoning(reasoning)
        if extracted and len(extracted) > 100:
            logger.info(f"Pollinations/{model}: текст из reasoning_content ({len(extracted)} симв.)")
            return extracted
        # Если русского блока нет, но reasoning длинный — вернуть как есть
        if len(reasoning.strip()) > 200:
            logger.info(f"Pollinations/{model}: весь reasoning_content ({len(reasoning)} симв.)")
            return reasoning.strip()

    raise Exception(f"Pollinations/{model}: пустой content, keys={list(message.keys())}")


def _extract_russian_from_reasoning(reasoning: str) -> str:
    """Извлекает последний крупный русскоязычный блок из reasoning_content.
    Reasoning models часто сначала рассуждают на английском, потом выдают
    готовый текст на русском."""
    # Разбиваем на параграфы
    paragraphs = reasoning.strip().split("\n\n")

    # Ищем последовательные русскоязычные параграфы (с конца)
    russian_blocks = []
    for p in reversed(paragraphs):
        p = p.strip()
        if not p:
            continue
        # Считаем кириллицу
        cyr = sum(1 for c in p if '\u0400' <= c <= '\u04ff')
        ratio = cyr / max(len(p), 1)
        if ratio > 0.3 and len(p) > 30:
            russian_blocks.insert(0, p)
        elif russian_blocks:
            break  # Прервались — нашли границу

    if russian_blocks:
        return "\n\n".join(russian_blocks)
    return ""


# ============================================================
# Pollinations.ai — прямой GET endpoint (запасной)
# ============================================================

async def _pollinations_get(prompt: str, model: str = "openai-fast") -> str:
    """Pollinations через GET — plain text ответ"""
    system = "Ты русскоязычный копирайтер. Пиши ТОЛЬКО на русском. Только готовый текст."
    encoded_prompt = urllib.parse.quote(prompt[:500])
    encoded_system = urllib.parse.quote(system)

    url = (
        f"https://text.pollinations.ai/{encoded_prompt}"
        f"?model={model}&system={encoded_system}"
    )

    async with shared_session() as session:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=90)) as resp:
            if resp.status != 200:
                error = await resp.text()
                raise Exception(f"Pollinations GET/{model} HTTP {resp.status}: {error[:200]}")

            raw = await resp.text()
            return _parse_text_response(raw, model)


def _parse_text_response(raw: str, model: str) -> str:
    """Парсит ответ Pollinations GET (JSON или plain text)"""
    raw = raw.strip()

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        if raw and len(raw) > 20:
            return raw
        raise Exception(f"Pollinations GET/{model}: слишком короткий ({len(raw)} симв.)")

    if isinstance(data, str):
        return data.strip()

    if isinstance(data, dict):
        choices = data.get("choices", [])
        if choices:
            content = choices[0].get("message", {}).get("content", "")
            if content:
                return content.strip()

        content = data.get("content", "")
        if content and isinstance(content, str):
            return content.strip()

        skip = {"role", "model", "reasoning_content", "id", "object", "created"}
        best = ""
        for key, val in data.items():
            if key in skip:
                continue
            if isinstance(val, str) and len(val) > len(best):
                best = val
        if len(best) > 50:
            return best.strip()

    raise Exception(f"Pollinations GET/{model}: не удалось извлечь текст")


# ============================================================
# Google Gemini (бесплатный tier, GEMINI_API_KEY)
# 15 RPM бесплатно — https://aistudio.google.com/apikey
# ============================================================

async def _gemini(prompt: str) -> str:
    """Google Gemini"""
    if not GEMINI_API_KEY:
        raise ValueError("GEMINI_API_KEY не задан")

    url = (
        f"https://generativelanguage.googleapis.com/v1beta/"
        f"models/{GEMINI_MODEL}:generateContent?key={GEMINI_API_KEY}"
    )

    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": 0.8,
            "maxOutputTokens": 1500,
        },
    }

    async with shared_session() as session:
        async with session.post(
            url, json=payload, timeout=aiohttp.ClientTimeout(total=30)
        ) as resp:
            if resp.status != 200:
                error = await resp.text()
                if resp.status == 429 and _is_gemini_quota_message(error):
                    _disable_gemini_temporarily()
                    raise GeminiQuotaExceeded("Gemini quota exceeded (HTTP 429)")
                raise Exception(f"Gemini HTTP {resp.status}: {error[:200]}")

            data = await resp.json()
            candidates = data.get("candidates", [])
            if candidates:
                parts = candidates[0].get("content", {}).get("parts", [])
                if parts:
                    text = parts[0].get("text", "")
                    if text:
                        return text.strip()
            raise Exception("Gemini: пустой ответ")


# ============================================================
# Groq (бесплатный tier, GROQ_API_KEY)
# 30 RPM бесплатно — https://console.groq.com/keys
# Модели: llama-3.3-70b-versatile, llama-3.1-8b-instant
# ============================================================

async def _groq(prompt: str, model: str = "llama-3.3-70b-versatile") -> str:
    """Groq Cloud — очень быстрый inference"""
    if not GROQ_API_KEY:
        raise ValueError("GROQ_API_KEY не задан")

    url = "https://api.groq.com/openai/v1/chat/completions"

    payload = {
        "model": model,
        "messages": [
            {
                "role": "system",
                "content": (
                    "Ты — профессиональный русскоязычный копирайтер. "
                    "Всегда пиши только на русском языке."
                ),
            },
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.8,
        "max_tokens": 1500,
    }

    headers = {
        "Authorization": f"Bearer {GROQ_API_KEY}",
        "Content-Type": "application/json",
    }

    async with shared_session() as session:
        async with session.post(
            url, json=payload, headers=headers,
            timeout=aiohttp.ClientTimeout(total=30),
        ) as resp:
            if resp.status != 200:
                error = await resp.text()
                raise Exception(f"Groq/{model} HTTP {resp.status}: {error[:200]}")

            data = await resp.json()
            choices = data.get("choices", [])
            if choices:
                content = choices[0].get("message", {}).get("content", "")
                if content:
                    return content.strip()
            raise Exception(f"Groq/{model}: пустой ответ")


async def _qwen(prompt: str) -> str:
    """Qwen через OpenAI-совместимый endpoint Alibaba DashScope."""
    if not QWEN_API_KEY:
        raise ValueError("QWEN_API_KEY не задан")

    base_url = QWEN_BASE_URL.rstrip("/")
    url = f"{base_url}/chat/completions"

    payload = {
        "model": QWEN_MODEL,
        "messages": [
            {
                "role": "system",
                "content": (
                    "Ты — профессиональный русскоязычный копирайтер по теме Китая. "
                    "Всегда пиши только на русском языке. "
                    "Если тема про китайский язык и иероглифы — давай точные примеры."
                ),
            },
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.45,
        "max_tokens": 1500,
    }

    headers = {
        "Authorization": f"Bearer {QWEN_API_KEY}",
        "Content-Type": "application/json",
    }

    async with shared_session() as session:
        async with session.post(
            url,
            json=payload,
            headers=headers,
            timeout=aiohttp.ClientTimeout(total=45),
        ) as resp:
            if resp.status != 200:
                error = await resp.text()
                if resp.status in (401, 403):
                    _disable_qwen_temporarily("invalid api key / unauthorized")
                    raise QwenTemporarilyDisabled(
                        f"Qwen авторизация не прошла (HTTP {resp.status})"
                    )
                if resp.status == 429:
                    _disable_qwen_temporarily("rate limit")
                    raise QwenTemporarilyDisabled("Qwen rate limit (HTTP 429)")
                raise Exception(f"Qwen/{QWEN_MODEL} HTTP {resp.status}: {error[:200]}")

            data = await resp.json()
            choices = data.get("choices", [])
            if choices:
                content = choices[0].get("message", {}).get("content", "")
                if content:
                    return content.strip()
            raise Exception(f"Qwen/{QWEN_MODEL}: пустой ответ")


# ============================================================
# HuggingFace (бесплатный tier, HF_API_KEY)
# API: router.huggingface.co (новый endpoint)
# ============================================================

async def _huggingface(prompt: str) -> str:
    """HuggingFace Inference API — Qwen 2.5 72B"""
    if not HF_API_KEY:
        raise ValueError("HF_API_KEY не задан")

    url = "https://api-inference.huggingface.co/models/Qwen/Qwen2.5-72B-Instruct"

    headers = {
        "Authorization": f"Bearer {HF_API_KEY}",
        "Content-Type": "application/json",
    }

    payload = {
        "inputs": prompt,
        "parameters": {
            "max_new_tokens": 1500,
            "temperature": 0.8,
            "return_full_text": False,
        },
    }

    async with shared_session() as session:
        async with session.post(
            url, json=payload, headers=headers,
            timeout=aiohttp.ClientTimeout(total=60),
        ) as resp:
            if resp.status != 200:
                error = await resp.text()
                raise Exception(f"HF HTTP {resp.status}: {error[:200]}")

            data = await resp.json()
            if isinstance(data, list) and data:
                text = data[0].get("generated_text", "")
                if text:
                    return text.strip()
            raise Exception("HuggingFace: пустой ответ")


# ============================================================
# Утилиты
# ============================================================

def _clean_response(text: str | None) -> str:
    """Очистка ответа от мусора"""
    if not text:
        return ""

    text = text.strip()

    remove_prefixes = [
        r"^(Вот|Вот ваш|Here is|Here's)[^:]*:\s*\n*",
        r"^(Готовый пост|Текст поста|Post text)[^:]*:\s*\n*",
        r'^```[a-z]*\s*\n',
    ]
    for pattern in remove_prefixes:
        text = re.sub(pattern, "", text, flags=re.IGNORECASE)

    text = re.sub(r'\n```\s*$', '', text)
    text = text.replace("*", "")

    return text.strip()


def _is_hanzi_topic(text: str) -> bool:
    """Определяет, что тема связана с китайскими иероглифами/языком."""
    if not text:
        return False

    lower = text.lower()
    keywords = [
        "иероглиф",
        "ханьцзы",
        "пиньин",
        "тон",
        "китайск",
        "грамматик",
        "слово",
        "разбор",
        "汉字",
        "拼音",
    ]
    if any(keyword in lower for keyword in keywords):
        return True

    # Любой CJK-символ в теме/промпте
    return bool(re.search(r"[\u4e00-\u9fff]", text))


def _is_relevant_to_topic(text: str, topic: str) -> bool:
    """Проверяет, что текст действительно раскрывает тему пользователя, а не пишет общими фразами."""
    if not text or not topic:
        return False

    normalized_text = text.lower()
    normalized_topic = topic.lower()

    # Если в теме есть иероглифы — они или эквивалентные маркеры должны встречаться в ответе
    topic_hanzi = set(re.findall(r"[\u4e00-\u9fff]", topic))
    if topic_hanzi:
        text_hanzi = set(re.findall(r"[\u4e00-\u9fff]", text))
        if not (topic_hanzi & text_hanzi) and not any(k in normalized_text for k in ("пиньин", "тон", "иероглиф", "汉字")):
            return False

    stopwords = {
        "как", "что", "это", "про", "для", "или", "the", "and", "china", "китай",
        "тема", "post", "about", "от", "при", "над", "под", "без", "между",
    }
    topic_words = re.findall(r"[a-zа-яё0-9]{3,}", normalized_topic)
    topic_words = [w for w in topic_words if w not in stopwords]

    if topic_words:
        hits = sum(1 for w in set(topic_words) if w in normalized_text)
        threshold = 1 if len(set(topic_words)) <= 2 else 2
        if hits < threshold:
            return False

    generic_markers = [
        "китай — это", "китай является", "богатая история", "древняя цивилизация",
        "в современном мире", "не секрет что", "китайская культура очень",
    ]
    generic_hits = sum(1 for marker in generic_markers if marker in normalized_text)
    if generic_hits >= 2 and topic_words and not any(w in normalized_text for w in topic_words):
        return False

    return True


def _is_weak_post(text: str, topic: str) -> bool:
    """Legacy-проверка слабого поста (оставлено для обратной совместимости)."""
    quality = _quality_report(text, topic)
    return not quality["all_ok"]


def _quality_report(text: str, topic: str) -> dict:
    """Авто-проверка качества: фактология + релевантность + полезность для ученика."""
    relevance_ok = _is_relevant_to_topic(text, topic)
    factology_ok = _has_factology_signals(text, topic)
    student_value_ok = _has_student_value(text, topic)

    failed_checks = []
    if not relevance_ok:
        failed_checks.append("релевантность")
    if not factology_ok:
        failed_checks.append("фактология")
    if not student_value_ok:
        failed_checks.append("польза для ученика")

    return {
        "relevance_ok": relevance_ok,
        "factology_ok": factology_ok,
        "student_value_ok": student_value_ok,
        "failed_checks": failed_checks,
        "all_ok": not failed_checks,
    }


def _has_factology_signals(text: str, topic: str) -> bool:
    """Эвристика фактологии: конкретные данные, примеры, правила и языковые маркеры."""
    if not text:
        return False

    low = text.lower()

    has_numbers = bool(re.search(r"\d", text))
    has_structured_examples = ("например" in low) or ("пример" in low) or ("•" in text)
    has_rule_markers = any(k in low for k in ["правило", "используется", "когда", "в случае", "ошибка"])

    if _is_hanzi_topic(topic):
        has_hanzi = bool(re.search(r"[\u4e00-\u9fff]", text))
        has_pinyin = ("пиньин" in low) or (" tone" in low) or bool(re.search(r"\b[a-z]{2,}\d\b", low))
        return has_hanzi and (has_structured_examples or has_rule_markers) and (has_pinyin or "тон" in low)

    return has_structured_examples or has_rule_markers or has_numbers


def _has_student_value(text: str, topic: str) -> bool:
    """Эвристика пользы: есть практическое применение, советы и учебная ценность."""
    if not text:
        return False

    low = text.lower()
    benefit_markers = [
        "как запомнить",
        "запомин",
        "практик",
        "упражн",
        "совет",
        "ошибк",
        "чтобы",
        "используй",
        "примен",
        "в речи",
    ]
    hits = sum(1 for marker in benefit_markers if marker in low)
    if _is_hanzi_topic(topic):
        return hits >= 2 and (("пример" in low) or ("например" in low) or ("•" in text))
    return hits >= 2


async def _rewrite_weak_text(draft: str, topic: str, style_prompt: str, failed_checks: list[str] | None = None) -> str | None:
    """Вторая попытка: переписывает слабый текст в более точный и полезный."""
    checks_text = ", ".join(failed_checks) if failed_checks else "релевантность, фактология, польза для ученика"
    rewrite_prompt = f"""{style_prompt}

Ниже черновик поста, но он слишком общий/слабый:

{draft}

Перепиши пост на тему «{topic}» так, чтобы:
- он был конкретным и соответствовал теме пользователя
- содержал точные факты и примеры
- не содержал общих фраз и воды
- обязательно прошёл авто-проверку: {checks_text}
- был 600-900 символов

Верни ТОЛЬКО готовый текст поста на русском."""

    generators = _build_generator_list(rewrite_prompt)
    for name, factory in generators:
        try:
            text = await factory()
            text = _clean_response(text)
            quality = _quality_report(text, topic) if text else {"all_ok": False}
            if text and len(text) > 200 and _looks_like_russian(text) and quality["all_ok"]:
                return text
        except (GeminiQuotaExceeded, QwenTemporarilyDisabled):
            continue
        except Exception:
            continue

    return None


def _looks_like_russian(text: str) -> bool:
    """Проверяет, что текст содержит достаточно кириллицы"""
    if not text:
        return False
    letters = [c for c in text if c.isalpha()]
    if not letters:
        return False
    cyrillic = sum(1 for c in letters if '\u0400' <= c <= '\u04ff')
    ratio = cyrillic / len(letters)
    return ratio > 0.5


def _fallback_text(topic: str) -> str:
    """Шаблонный текст, когда все API недоступны"""
    return (
        f"\U0001f1e8\U0001f1f3 {topic}\n\n"
        f"\u26a0\ufe0f Автоматическая генерация текста временно недоступна.\n\n"
        f"\U0001f4dd Тема: {topic}\n\n"
        f"Попробуйте ещё раз через пару минут или отправьте текст вручную.\n\n"
        f"#China #Китай"
    )


def _set_last_text_provider(name: str) -> None:
    """Сохраняет провайдера, который успешно сгенерировал последний текст в текущем async-контексте."""
    _last_text_provider.set((name or "").strip())


def get_last_text_provider() -> str:
    """Возвращает имя провайдера, использованного для последней успешной генерации текста."""
    return _last_text_provider.get()


def _is_gemini_quota_message(error_text: str) -> bool:
    """Проверяет, что ошибка Gemini связана именно с квотой/лимитом."""
    msg = (error_text or "").lower()
    markers = [
        "quota",
        "exceeded your current quota",
        "billing",
        "resource_exhausted",
        "too many requests",
    ]
    return any(marker in msg for marker in markers)


def _disable_gemini_temporarily() -> None:
    """Отключает Gemini на cooldown, чтобы не спамить 429 в каждом запросе."""
    global _gemini_disabled_until
    _gemini_disabled_until = time.time() + GEMINI_COOLDOWN_SECONDS
    logger.warning(
        "Gemini отключён на %s сек. из-за quota/rate limit; используем fallback API.",
        GEMINI_COOLDOWN_SECONDS,
    )


def _is_gemini_temporarily_disabled() -> bool:
    """Проверяет, активен ли cooldown Gemini."""
    return time.time() < _gemini_disabled_until


def _disable_qwen_temporarily(reason: str) -> None:
    """Отключает Qwen на cooldown, чтобы не спамить однотипными ошибками."""
    global _qwen_disabled_until
    _qwen_disabled_until = time.time() + QWEN_COOLDOWN_SECONDS
    logger.warning(
        "Qwen отключён на %s сек. Причина: %s. Используем fallback API.",
        QWEN_COOLDOWN_SECONDS,
        reason,
    )


def _is_qwen_temporarily_disabled() -> bool:
    """Проверяет, активен ли cooldown Qwen."""
    return time.time() < _qwen_disabled_until
