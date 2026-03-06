"""
Генерация картинок для бота Chinaя.
Проверенные рабочие API с fallback.

Бесплатные (БЕЗ API-ключа):
1. Stable Horde — распределённая сеть, 512×512, анонимный доступ
   Модели: stable_diffusion, Deliberate, AlbedoBase XL, Dreamshaper
2. Pollinations.ai — flux (может быть временно недоступен)

С API-ключами:
3. HuggingFace Stable Diffusion XL (HF_API_KEY)
"""

import aiohttp
import asyncio
import io
import json
import logging
import os
import time
import uuid
import urllib.parse
from PIL import Image
from config import HF_API_KEY, IMAGE_PROMPT_STYLE
from services.http_session import shared_session

logger = logging.getLogger(__name__)

IMAGES_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "images")
os.makedirs(IMAGES_DIR, exist_ok=True)


async def generate_image(prompt: str) -> str | None:
    """Генерация картинки с fallback между API. Возвращает путь к файлу."""
    from utils import prompt_hash
    from database import get_image_cache, set_image_cache

    # Проверяем кэш
    ph = prompt_hash(prompt)
    cached = await get_image_cache(ph)
    if cached:
        logger.info(f"Картинка из кэша: {cached}")
        return cached

    consistency_tail = (
        " unified style, same visual language across posts, "
        "chibi minimal aesthetic, pastel colors, clean line art, "
        "avoid photorealism, avoid abstract shapes, avoid dark heavy contrast"
    )
    full_prompt = f"{IMAGE_PROMPT_STYLE} {prompt}. {consistency_tail}".strip()
    if len(full_prompt) > 300:
        full_prompt = full_prompt[:300]

    generators = [
        # Stable Horde — работает стабильно, бесплатно, без ключа
        ("StableHorde/Dreamshaper", lambda: _stable_horde(full_prompt, "Dreamshaper")),
        ("StableHorde/Deliberate", lambda: _stable_horde(full_prompt, "Deliberate")),
        ("StableHorde/stable_diffusion", lambda: _stable_horde(full_prompt, "stable_diffusion")),
        # Pollinations — может восстановиться
        ("Pollinations/flux", lambda: _pollinations_get(full_prompt, "flux")),
        ("Pollinations/turbo", lambda: _pollinations_get(full_prompt, "turbo")),
    ]

    if HF_API_KEY:
        generators.append(("HuggingFace/SD-XL", lambda: _hf_sd(full_prompt)))

    for name, gen_fn in generators:
        try:
            logger.info(f"Попытка генерации картинки через {name}...")
            image_path = await gen_fn()
            if image_path and os.path.exists(image_path):
                file_size = os.path.getsize(image_path)
                if file_size > 5000:
                    logger.info(f"Картинка через {name}: {image_path} ({file_size} bytes)")
                    # Сохраняем в кэш
                    await set_image_cache(ph, image_path)
                    return image_path
                else:
                    logger.warning(f"{name}: слишком маленький файл ({file_size} bytes)")
                    os.remove(image_path)
        except Exception as e:
            logger.error(f"{name}: {e}")
            continue

    logger.error("Все API генерации картинок недоступны")
    return None


# ============================================================
# Stable Horde — распределённая БЕСПЛАТНАЯ генерация
# Анонимный ключ: 0000000000
# https://stablehorde.net
# ============================================================

_HORDE_API = "https://stablehorde.net/api/v2"
_HORDE_ANON_KEY = "0000000000"


async def _stable_horde(
    prompt: str,
    model: str = "Dreamshaper",
    width: int = 512,
    height: int = 512,
    steps: int = 20,
) -> str | None:
    """
    Stable Horde — бесплатная распределённая генерация.
    Шаги: submit → poll → download.
    Модели (по количеству workers):
      stable_diffusion (9), Deliberate (7), AlbedoBase XL 3.1 (6),
      AbsoluteReality (5), Dreamshaper (5), Anything Diffusion (5)
    """

    # Шаг 1: Отправляем задачу
    submit_url = f"{_HORDE_API}/generate/async"
    payload = {
        "prompt": prompt,
        "params": {
            "width": width,
            "height": height,
            "steps": steps,
            "cfg_scale": 7,
            "sampler_name": "k_euler_a",
            "post_processing": [],
        },
        "nsfw": False,
        "censor_nsfw": True,
        "models": [model],
        "r2": True,
    }
    headers = {
        "apikey": _HORDE_ANON_KEY,
        "Content-Type": "application/json",
        "Client-Agent": "ChinayaBot:1.0:telegram",
    }

    async with shared_session() as session:
        async with session.post(
            submit_url,
            json=payload,
            headers=headers,
            timeout=aiohttp.ClientTimeout(total=30),
        ) as resp:
            if resp.status not in (200, 202):
                error = await resp.text()
                raise Exception(f"Horde submit HTTP {resp.status}: {error[:300]}")

            data = await resp.json()
            job_id = data.get("id")
            if not job_id:
                raise Exception(f"Horde: нет job ID в ответе: {data}")

        logger.info(f"Horde/{model}: задача {job_id} создана, ожидаем генерацию...")

        # Шаг 2: Опрашиваем статус (до 3 минут)
        check_url = f"{_HORDE_API}/generate/check/{job_id}"
        status_url = f"{_HORDE_API}/generate/status/{job_id}"

        max_polls = 36  # 36 × 5 сек = 3 минуты
        for i in range(max_polls):
            await asyncio.sleep(5)

            try:
                async with session.get(
                    check_url,
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as check_resp:
                    check_data = await check_resp.json()

                    if check_data.get("faulted"):
                        raise Exception(f"Horde/{model}: задача завершилась с ошибкой")

                    if not check_data.get("is_possible", True):
                        raise Exception(f"Horde/{model}: нет доступных workers для модели")

                    wait_time = check_data.get("wait_time", 0)
                    done = check_data.get("done", False)

                    if i % 4 == 0:
                        logger.info(
                            f"Horde/{model}: poll {i+1}/{max_polls}, "
                            f"done={done}, wait={wait_time}s"
                        )

                    if done:
                        break

            except aiohttp.ClientError as e:
                logger.warning(f"Horde check poll {i+1}: {e}")
                continue
        else:
            raise Exception(f"Horde/{model}: таймаут ожидания (3 мин)")

        # Шаг 3: Получаем результат
        async with session.get(
            status_url,
            timeout=aiohttp.ClientTimeout(total=15),
        ) as status_resp:
            status_data = await status_resp.json()
            generations = status_data.get("generations", [])

            if not generations:
                raise Exception(f"Horde/{model}: нет картинок в результате")

            img_url = generations[0].get("img")
            if not img_url:
                raise Exception(f"Horde/{model}: нет URL картинки")

        # Шаг 4: Скачиваем картинку
        async with session.get(
            img_url,
            timeout=aiohttp.ClientTimeout(total=30),
        ) as img_resp:
            if img_resp.status != 200:
                raise Exception(f"Horde download HTTP {img_resp.status}")

            img_data = await img_resp.read()

            if len(img_data) < 5000:
                raise Exception(f"Horde/{model}: слишком маленький файл ({len(img_data)} bytes)")

            # Определяем формат
            is_png = img_data[:4] == b'\x89PNG'
            is_jpeg = img_data[:2] == b'\xff\xd8'
            is_webp = img_data[:4] == b'RIFF' and img_data[8:12] == b'WEBP'

            if not (is_png or is_jpeg or is_webp):
                text_preview = img_data[:100].decode("utf-8", errors="ignore")
                raise Exception(f"Horde/{model}: не картинка: {text_preview}")

            # Конвертируем WebP→PNG для совместимости с Telegram (WebP отправляется как стикер)
            if is_webp:
                converted = _convert_webp_to_png(img_data)
                if converted[:4] == b'\x89PNG':
                    img_data = converted
                    is_png = True
                    is_webp = False
                    logger.info(f"Horde/{model}: WebP → PNG ({len(img_data)} bytes)")
                else:
                    logger.warning(f"Horde/{model}: WebP→PNG конвертация не удалась")

            ext = "png" if is_png else ("jpg" if is_jpeg else "webp")
            filename = f"{uuid.uuid4().hex}.{ext}"
            filepath = os.path.join(IMAGES_DIR, filename)

            with open(filepath, "wb") as f:
                f.write(img_data)

            return filepath


# ============================================================
# Pollinations.ai Image — GET запрос (может быть временно недоступен)
# ============================================================

async def _pollinations_get(
    prompt: str,
    model: str = "flux",
    width: int = 1024,
    height: int = 1024,
) -> str | None:
    """Pollinations Image API через GET"""

    short_prompt = prompt[:200] if len(prompt) > 200 else prompt
    encoded = urllib.parse.quote(short_prompt)
    seed = uuid.uuid4().int % 100000

    url = (
        f"https://image.pollinations.ai/prompt/{encoded}"
        f"?width={width}&height={height}"
        f"&model={model}&nologo=true&seed={seed}"
    )

    for attempt in range(2):
        try:
            async with shared_session() as session:
                async with session.get(
                    url,
                    timeout=aiohttp.ClientTimeout(total=120),
                    allow_redirects=True,
                ) as resp:
                    if resp.status == 200:
                        data = await resp.read()

                        if len(data) < 5000:
                            raise Exception(f"Слишком маленький: {len(data)} bytes")

                        is_png = data[:4] == b'\x89PNG'
                        is_jpeg = data[:2] == b'\xff\xd8'
                        if not is_png and not is_jpeg:
                            text_preview = data[:200].decode("utf-8", errors="ignore")
                            raise Exception(f"Не картинка: {text_preview}")

                        ext = "png" if is_png else "jpg"
                        filename = f"{uuid.uuid4().hex}.{ext}"
                        filepath = os.path.join(IMAGES_DIR, filename)

                        with open(filepath, "wb") as f:
                            f.write(data)

                        return filepath

                    logger.warning(f"Pollinations/{model} attempt {attempt+1}/2: HTTP {resp.status}")

        except asyncio.TimeoutError:
            logger.warning(f"Pollinations/{model} attempt {attempt+1}/2: таймаут")
        except Exception as e:
            logger.warning(f"Pollinations/{model} attempt {attempt+1}/2: {e}")

        if attempt < 1:
            await asyncio.sleep(2)

    raise Exception(f"Pollinations/{model}: все 2 попытки неудачны")


# ============================================================
# HuggingFace Stable Diffusion XL (нужен HF_API_KEY)
# API: router.huggingface.co (новый endpoint)
# ============================================================

async def _hf_sd(prompt: str) -> str | None:
    """HuggingFace Stable Diffusion XL"""
    if not HF_API_KEY:
        raise ValueError("HF_API_KEY не задан")

    url = "https://router.huggingface.co/hf-inference/models/stabilityai/stable-diffusion-xl-base-1.0"

    headers = {
        "Authorization": f"Bearer {HF_API_KEY}",
        "Content-Type": "application/json",
    }

    payload = {
        "inputs": prompt,
        "parameters": {"num_inference_steps": 30},
    }

    async with shared_session() as session:
        async with session.post(
            url,
            json=payload,
            headers=headers,
            timeout=aiohttp.ClientTimeout(total=120),
        ) as resp:
            if resp.status == 200:
                data = await resp.read()
                if len(data) < 5000:
                    raise Exception(f"HF SD: слишком маленький ({len(data)} bytes)")

                filename = f"{uuid.uuid4().hex}.png"
                filepath = os.path.join(IMAGES_DIR, filename)

                with open(filepath, "wb") as f:
                    f.write(data)

                return filepath
            else:
                error = await resp.text()
                raise Exception(f"HF SD HTTP {resp.status}: {error[:200]}")


# ============================================================
# Очистка
# ============================================================

def _convert_webp_to_png(data: bytes) -> bytes:
    """Конвертирует WebP в PNG для совместимости с Telegram"""
    try:
        img = Image.open(io.BytesIO(data))
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()
    except Exception as e:
        logger.warning(f"Не удалось конвертировать WebP→PNG: {e}")
        return data  # Возвращаем как есть, если не удалось


async def cleanup_old_images(max_age_hours: int = 24):
    """Удаление старых картинок"""
    now = time.time()
    count = 0
    for filename in os.listdir(IMAGES_DIR):
        filepath = os.path.join(IMAGES_DIR, filename)
        if os.path.isfile(filepath):
            age_hours = (now - os.path.getmtime(filepath)) / 3600
            if age_hours > max_age_hours:
                os.remove(filepath)
                count += 1
    if count > 0:
        logger.info(f"Удалено {count} старых картинок")
