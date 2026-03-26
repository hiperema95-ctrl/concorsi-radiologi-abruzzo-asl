"""
Bot Telegram - Concorsi Pubblici Medici Radiologi in Abruzzo
"""

import os
import json
import logging
import hashlib
import asyncio
import requests
from bs4 import BeautifulSoup
from datetime import datetime, date
from urllib.parse import urljoin
from telegram import Bot
from telegram.constants import ParseMode

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
CHAT_ID        = os.environ.get("CHAT_ID", "")

SEEN_FILE      = "seen_concorsi.json"
HEALTH_FILE    = "health_state.json"
SEEN_NEWS_FILE = "seen_news.json"

logging.basicConfig(format="%(asctime)s [%(levelname)s] %(message)s", level=logging.INFO)
log = logging.getLogger(__name__)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )
}

KEYWORDS = [
    "radiologo", "radiologia", "radiodiagnostica",
    "medico radiologo", "specialista radiologo",
    "diagnostica per immagini", "radiologia diagnostica",
    "tecnico sanitario di radiologia", "tsrm",
    "neuroradiologia", "radiologia interventistica",
]

ABRUZZO_STRICT = [
    "abruzzo",
    "asl 1 avezzano", "asl avezzano", "asl sulmona", "asl l'aquila", "asl aquila",
    "asl 2 lanciano", "asl lanciano", "asl vasto", "asl chieti",
    "asl 3 pescara", "asl pescara",
    "asl 4 teramo", "asl teramo",
    "avezzano", "sulmona", "l'aquila", "lanciano", "vasto",
    "pescara", "teramo", "chieti",
    "regione abruzzo",
]

SOURCES = [
    {
        "name": "ASL 1 Avezzano-Sulmona-L'Aquila",
        "url": "https://trasparenza.asl1abruzzo.it/pagina640_concorsi-attivi.html",
        "type": "local",
        "ssl": True,
    },
    {
        "name": "ASL 2 Lanciano-Vasto-Chieti",
        "url": "https://lnx.asl2abruzzo.it/b/",
        "type": "local",
        "ssl": True,
    },
    {
        "name": "ASL 3 Pescara",
        "url": "https://www.asl.pe.it/BandiConcorsi.jsp",
        "type": "local",
        "ssl": True,
    },
    {
        "name": "ASL 4 Teramo",
        "url": "https://www.aslteramo.it/concorsi",
        "type": "local",
        "ssl": True,
    },
    {
        "name": "SIRM — Società Italiana Radiologia Medica",
        "url": "https://sirm.org/concorsi-2/",
        "type": "national",
        "ssl": True,
    },
    {
        "name": "FNO TSRM — Rubrica Concorsi",
        "url": "https://www.tsrm-pstrp.org/index.php/rubrica_concorsi/",
        "type": "national",
        "ssl": True,
    },
    {
        "name": "ConcorsiPubblici.com — Radiologo",
        "url": "https://www.concorsipubblici.com/concorsi-radiologo.htm",
        "type": "national",
        "ssl": True,
    },
    {
        "name": "Concorsi.it — Radiologia",
        "url": "https://www.concorsi.it/risultati?ric=radiologia",
        "type": "national",
        "ssl": True,
    },
]

NEWS_SOURCES = [
    {
        "name": "ESR — European Society of Radiology",
        "url": "https://www.myesr.org/news",
        "selector": "article a, .news-item a, h2 a, h3 a",
        "base": "https://www.myesr.org",
    },
    {
        "name": "RSNA News",
        "url": "https://www.rsna.org/news",
        "selector": "article a, .news-card a, h2 a, h3 a",
        "base": "https://www.rsna.org",
    },
    {
        "name": "AuntMinnie",
        "url": "https://www.auntminnie.com/index.aspx?sec=nws",
        "selector": "a.article-title, h2 a, h3 a, .headline a",
        "base": "https://www.auntminnie.com",
    },
    {
        "name": "Radiology Today",
        "url": "https://www.radiologytoday.net",
        "selector": ".entry-title a, h2 a, h3 a, article a",
        "base": "https://www.radiologytoday.net",
    },
    {
        "name": "Imaging Technology News",
        "url": "https://www.itnonline.com/channel/radiology",
        "selector": "h2 a, h3 a, .article-title a",
        "base": "https://www.itnonline.com",
    },
    {
        "name": "Radiology Business",
        "url": "https://www.radiologybusiness.com/topics/imaging",
        "selector": "h2 a, h3 a, .article-title a, .entry-title a",
        "base": "https://www.radiologybusiness.com",
    },
    {
        "name": "Applied Radiology",
        "url": "https://appliedradiology.com/articles",
        "selector": "h2 a, h3 a, .article-title a",
        "base": "https://appliedradiology.com",
    },
    {
        "name": "Diagnostic Imaging",
        "url": "https://www.diagnosticimaging.com/view/news",
        "selector": "h2 a, h3 a, .article-title a",
        "base": "https://www.diagnosticimaging.com",
    },
]

NEWS_KEYWORDS = [
    "ai", "artificial intelligence", "machine learning",
    "mri", "ct", "ultrasound", "x-ray", "pet",
    "radiology", "imaging", "diagnostic",
    "cancer", "tumor", "detection",
    "innovation", "breakthrough", "study", "research",
    "scanner", "deep learning", "algorithm",
]


def load_seen() -> set:
    if os.path.exists(SEEN_FILE):
        with open(SEEN_FILE) as f:
            return set(json.load(f))
    return set()


def save_seen(seen: set):
    with open(SEEN_FILE, "w") as f:
        json.dump(sorted(list(seen)), f, indent=2)


def load_seen_news() -> set:
    if os.path.exists(SEEN_NEWS_FILE):
        with open(SEEN_NEWS_FILE) as f:
            return set(json.load(f))
    return set()


def save_seen_news(seen_news: set):
    items = sorted(list(seen_news))[-200:]
    with open(SEEN_NEWS_FILE, "w") as f:
        json.dump(items, f, indent=2)


def load_health() -> dict:
    defaults = {
        "last_health_check":      "",
        "source_alert_dates":     {},
        "total_runs":             0,
        "last_successful_scrape": "",
    }
    if os.path.exists(HEALTH_FILE):
        with open(HEALTH_FILE) as f:
            data = json.load(f)
        defaults.update(data)
    return defaults


def save_health(state: dict):
    with open(HEALTH_FILE, "w") as f:
        json.dump(state, f, indent=2)


def make_id(title: str, url: str) -> str:
    return hashlib.md5(f"{title.strip().lower()}|{url.strip()}".encode()).hexdigest()


def make_news_id(title: str) -> str:
    return hashlib.md5(title.strip().lower().encode()).hexdigest()


def today_str() -> str:
    return date.today().isoformat()


def fetch(url: str, params: dict = None, ssl_verify: bool = True) -> BeautifulSoup | None:
    try:
        resp = requests.get(
            url, headers=HEADERS, params=params,
            timeout=25, verify=ssl_verify
        )
        resp.raise_for_status()
        return BeautifulSoup(resp.text, "lxml")
    except Exception as e:
        log.warning(f"Fetch fallito [{url}]: {e}")
        return None


def get_daily_news(seen_news: set) -> tuple[dict | None, set]:
    for ns in NEWS_SOURCES:
        log.info(f"  Cerco news su {ns['name']}...")
        soup = fetch(ns["url"])
        if not soup:
            continue
        for a in soup.select(ns["selector"]):
            title = a.get_text(separator=" ", strip=True)
            href  = a.get("href", "")
            if not title or len(title) < 20:
                continue
            if not any(kw in title.lower() for kw in NEWS_KEYWORDS):
                continue
            if href.startswith("http"):
                full_url = href
            elif href.startswith("/"):
                full_url = ns["base"] + href
            else:
                continue
            news_id = make_news_id(title)
            if news_id in seen_news:
                continue
            log.info(f"  News nuova [{ns['name']}]: {title[:70]}")
            seen_news.add(news_id)
            return {"title": title, "url": full_url, "source": ns["name"]}, seen_news

    log.warning("Tutte le news già viste — reset memoria")
    seen_news = set()
    for ns in NEWS_SOURCES:
        soup = fetch(ns["url"])
        if not soup:
            continue
        for a in soup.select(ns["selector"]):
            title = a.get_text(separator=" ", strip=True)
            href  = a.get("href", "")
            if not title or len(title) < 20:
                continue
            if not any(kw in title.lower() for kw in NEWS_KEYWORDS):
                continue
            if href.startswith("http"):
                full_url = href
            elif href.startswith("/"):
                full_url = ns["base"] + href
            else:
                continue
            news_id = make_news_id(title)
            seen_news.add(news_id)
            return {"title": title, "url": full_url, "source": ns["name"]}, seen_news

    return None, seen_news


def is_relevant(text: str) -> bool:
    return any(kw in text.lower() for kw in KEYWORDS)


def is_abruzzo_strict(text: str) -> bool:
    t = text.lower()
    return any(kw in t for kw in ABRUZZO_STRICT)


def scrape_source(source: dict) -> list[dict] | None:
    soup = fetch(source["url"], ssl_verify=source.get("ssl", True))
    if not soup:
        return None
    is_national = source.get("type") == "national"
    results = []
    for a in soup.find_all("a", href=True):
        title = a.get_text(separator=" ", strip=True)
        href  = a["href"]
        if not title or len(title) < 10:
            continue
        if not is_relevant(title):
            continue
        parent = a.parent
        context = f"{title} {parent.get_text(separator=' ', strip=True) if parent else ''}"
        if is_national and not is_abruzzo_strict(context):
            continue
        full_url = href if href.startswith("http") else urljoin(source["url"], href)
        results.append({
            "title":  title,
            "url":    full_url,
            "source": source["name"],
            "date":   datetime.now().strftime("%d/%m/%Y"),
        })
    log.info(f"  [{source['name']}] {len(results)} bandi rilevanti")
    return results


def fmt_bando(c: dict) -> str:
    return (
        "🏥 *Nuovo concorso — Radiologo Abruzzo*\n\n"
        f"📋 *{c['title']}*\n\n"
        f"🏛 {c['source']}\n"
        f"📅 {c['date']}\n\n"
        f"👉 [Apri il bando]({c['url']})"
    )


def fmt_daily(new_today: int, total_active: int, news: dict | None) -> str:
    oggi = datetime.now().strftime("%d/%m/%Y")
    if new_today > 0:
        bandi_txt = f"📋 Nuovi concorsi oggi: *{new_today}*\n"
    else:
        bandi_txt = "📋 Nessun nuovo concorso oggi\n"
    if total_active > 0:
        bandi_txt += f"📂 Concorsi attivi monitorati: *{total_active}*\n"
    if news:
        news_txt = (
            f"\n📰 *News dal mondo della radiologia*\n\n"
            f"*{news['title']}*\n"
            f"_{news['source']}_\n\n"
            f"👉 [Leggi l'articolo]({news['url']})"
        )
    else:
        news_txt = "\n_Nessuna news disponibile oggi._"
    return f"☀️ *{oggi} — Report giornaliero*\n\n{bandi_txt}{news_txt}"


def fmt_source_alert(source_name: str) -> str:
    oggi = datetime.now().strftime("%d/%m/%Y")
    return (
        f"⚠️ *Sorgente offline — {oggi}*\n\n"
        f"❌ *{source_name}*\n\n"
        "_Questo portale non è raggiungibile. "
        "I suoi bandi potrebbero non essere monitorati fino al ripristino._"
    )


async def send_msg(bot: Bot, text: str):
    ids = [cid.strip() for cid in CHAT_ID.split(",") if cid.strip()]
    for cid in ids:
        try:
            await bot.send_message(
                chat_id=cid,
                text=text,
                parse_mode=ParseMode.MARKDOWN,
                disable_web_page_preview=False,
            )
        except Exception as e:
            log.error(f"Errore invio Telegram a {cid}: {e}")


async def main():
    if not TELEGRAM_TOKEN or not CHAT_ID:
        log.error("TELEGRAM_TOKEN o CHAT_ID mancanti!")
        return

    log.info("═══ Avvio bot concorsi radiologi Abruzzo ═══")

    seen      = load_seen()
    seen_news = load_seen_news()
    state     = load_health()
    bot       = Bot(token=TELEGRAM_TOKEN)

    state["total_runs"] += 1
    if "source_alert_dates" not in state:
        state["source_alert_dates"] = {}

    today     = today_str()
    new_count = 0

    for source in SOURCES:
        log.info(f"→ {source['name']}")
        try:
            concorsi = scrape_source(source)
        except Exception as e:
            log.error(f"  Errore: {e}")
            concorsi = None

        if concorsi is None:
            last = state["source_alert_dates"].get(source["name"], "")
            if last != today:
                log.warning(f"Sorgente offline: {source['name']}")
                await send_msg(bot, fmt_source_alert(source["name"]))
                state["source_alert_dates"][source["name"]] = today
            else:
                log.info(f"  Alert già inviato oggi — skip")
            await asyncio.sleep(2)
            continue

        for c in concorsi:
            cid = make_id(c["title"], c["url"])
            if cid not in seen:
                log.info(f"  🆕 {c['title'][:60]}")
                await send_msg(bot, fmt_bando(c))
                seen.add(cid)
                new_count += 1
                await asyncio.sleep(1.5)

        await asyncio.sleep(2)

    log.info(f"Nuovi bandi oggi: {new_count} — Totale monitorati: {len(seen)}")

    if state["last_health_check"] != today:
        log.info("Cerco news nuova...")
        news, seen_news = get_daily_news(seen_news)
        await send_msg(bot, fmt_daily(new_count, len(seen), news))
        state["last_health_check"] = today
        save_seen_news(seen_news)
    else:
        log.info("Report giornaliero già inviato oggi — skip")

    state["last_successful_scrape"] = today
    save_seen(seen)
    save_health(state)
    log.info("═══ Fine run ═══")


if __name__ == "__main__":
    asyncio.run(main())
