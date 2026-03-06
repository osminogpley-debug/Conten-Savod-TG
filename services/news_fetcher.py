import aiohttp
import logging
import feedparser
from datetime import datetime, timedelta
from bs4 import BeautifulSoup
from config import NEWSAPI_KEY
from services.http_session import shared_session

logger = logging.getLogger(__name__)


async def fetch_news_by_topic(topic: str) -> dict | None:
    """Поиск свежей новости по теме. Возвращает {title, text, url, date}"""
    
    fetchers = [
        ("NewsAPI", _fetch_newsapi),
        ("Google News RSS", _fetch_google_news_rss),
    ]
    
    for name, fetcher in fetchers:
        try:
            logger.info(f"Поиск новостей через {name}: {topic}")
            news = await fetcher(topic)
            if news:
                logger.info(f"Найдена новость через {name}: {news['title'][:50]}...")
                return news
        except Exception as e:
            logger.error(f"Ошибка поиска новостей через {name}: {e}")
            continue
    
    logger.warning(f"Свежие новости по теме '{topic}' не найдены")
    return None


async def extract_article_text(url: str) -> str | None:
    """Извлечь текст статьи по URL"""
    try:
        async with shared_session() as session:
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
            }
            async with session.get(url, headers=headers,
                                   timeout=aiohttp.ClientTimeout(total=15)) as resp:
                if resp.status == 200:
                    html = await resp.text()
                    soup = BeautifulSoup(html, "html.parser")
                    
                    # Удаляем скрипты и стили
                    for tag in soup(["script", "style", "nav", "footer", "header", "aside"]):
                        tag.decompose()
                    
                    # Ищем основной контент
                    article = soup.find("article")
                    if article:
                        text = article.get_text(separator="\n", strip=True)
                    else:
                        # Ищем по тегам p
                        paragraphs = soup.find_all("p")
                        text = "\n".join(p.get_text(strip=True) for p in paragraphs if len(p.get_text(strip=True)) > 30)
                    
                    if text and len(text) > 100:
                        # Обрезаем до разумной длины
                        return text[:3000]
                    
        logger.warning(f"Не удалось извлечь текст из {url}")
        return None
    except Exception as e:
        logger.error(f"Ошибка извлечения текста из {url}: {e}")
        return None


# === NewsAPI ===

async def _fetch_newsapi(topic: str) -> dict | None:
    if not NEWSAPI_KEY:
        raise ValueError("NEWSAPI_KEY не задан")
    
    # Добавляем "Китай" к запросу, если его нет
    query = topic
    if "китай" not in topic.lower() and "china" not in topic.lower():
        query = f"{topic} Китай"
    
    url = "https://newsapi.org/v2/everything"
    params = {
        "q": query,
        "language": "ru",
        "sortBy": "publishedAt",
        "pageSize": 5,
        "apiKey": NEWSAPI_KEY
    }
    
    async with shared_session() as session:
        async with session.get(url, params=params,
                               timeout=aiohttp.ClientTimeout(total=10)) as resp:
            if resp.status == 200:
                data = await resp.json()
                articles = data.get("articles", [])
                
                now = datetime.utcnow()
                for article in articles:
                    pub_date = article.get("publishedAt", "")
                    if pub_date:
                        try:
                            dt = datetime.fromisoformat(pub_date.replace("Z", "+00:00"))
                            dt_naive = dt.replace(tzinfo=None)
                            if now - dt_naive > timedelta(hours=24):
                                continue
                        except:
                            pass
                    
                    title = article.get("title", "")
                    description = article.get("description", "")
                    content = article.get("content", "")
                    article_url = article.get("url", "")
                    
                    text = content or description or title
                    if text and len(text) > 50:
                        return {
                            "title": title,
                            "text": text,
                            "url": article_url,
                            "date": pub_date
                        }
            else:
                error = await resp.text()
                raise Exception(f"NewsAPI error {resp.status}: {error}")
    
    return None


# === Google News RSS (без API ключа) ===

async def _fetch_google_news_rss(topic: str) -> dict | None:
    import urllib.parse
    
    query = topic
    if "китай" not in topic.lower() and "china" not in topic.lower():
        query = f"{topic} Китай"
    
    encoded_query = urllib.parse.quote(query)
    rss_url = f"https://news.google.com/rss/search?q={encoded_query}&hl=ru&gl=RU&ceid=RU:ru"
    
    async with shared_session() as session:
        headers = {"User-Agent": "Mozilla/5.0"}
        async with session.get(rss_url, headers=headers,
                               timeout=aiohttp.ClientTimeout(total=10)) as resp:
            if resp.status == 200:
                content = await resp.text()
                feed = feedparser.parse(content)
                
                now = datetime.utcnow()
                for entry in feed.entries[:5]:
                    # Проверяем дату
                    pub_date = entry.get("published_parsed")
                    if pub_date:
                        from time import mktime
                        entry_time = datetime.fromtimestamp(mktime(pub_date))
                        if now - entry_time > timedelta(hours=24):
                            continue
                    
                    title = entry.get("title", "")
                    link = entry.get("link", "")
                    summary = entry.get("summary", "")
                    
                    # Очищаем summary от HTML
                    if summary:
                        soup = BeautifulSoup(summary, "html.parser")
                        summary = soup.get_text(strip=True)
                    
                    text = summary if summary else title
                    if text and len(text) > 20:
                        return {
                            "title": title,
                            "text": text,
                            "url": link,
                            "date": entry.get("published", "")
                        }
    
    return None
