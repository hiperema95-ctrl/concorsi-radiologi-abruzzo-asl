"""
Bot Telegram - Concorsi Pubblici Medici Radiologi (Abruzzo, Marche, Emilia Romagna)
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

GEO_STRICT = [
    "abruzzo", "regione abruzzo",
    "asl 1 avezzano", "asl avezzano", "asl sulmona", "asl l'aquila", "asl aquila",
    "asl 2 lanciano", "asl lanciano", "asl vasto", "asl chieti",
    "asl 3 pescara", "asl pescara",
    "asl 4 teramo", "asl teramo",
    "avezzano", "sulmona", "l'aquila", "lanciano", "vasto", "pescara", "teramo", "chieti",
    "marche", "regione marche", "asur", "ast ancona", "ast pesaro", "ast urbino", 
    "ast macerata", "ast fermo", "ast ascoli piceno", "ospedali riuniti", "torrette",
    "ancona", "pesaro", "urbino", "macerata", "fermo", "ascoli piceno", "ascoli",
    "emilia romagna", "emilia-romagna", "regione emilia romagna",
    "ausl bologna", "ausl modena", "ausl reggio emilia", "ausl parma", "ausl piacenza",
    "ausl ferrara", "ausl ravenna", "ausl forlì", "ausl cesena", "ausl romagna", "ausl rimini", "ausl imola",
    "bologna", "modena", "reggio emilia", "parma", "piacenza", "ferrara", "ravenna", 
    "forlì", "cesena", "rimini", "imola", "sant'orsola", "maggiore"
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
        "name": "InfoConcorsi (EdiSES) — Ricerca Radiologia",
        "url": "https://infoconcorsi.edises.it/ricerca?q=radiologia",
        "type": "national",
        "ssl": True,
    },
    {
        "name": "Anaao Assomed — Concorsi Dirigenza Medica",
        "url": "https://www.anaao.it/content.php?id=31",
        "type": "national",
        "ssl": True,
    }
]

NEWS_SOURCES = [
    {"name": "ESR", "url": "https://www.myesr.org/news", "selector": "article a, .news-item a, h2 a, h3 a", "base": "https://www.myesr.org"},
    {"name": "RSNA News", "url": "https://www.rsna.org/news", "selector": "article a, .news-card a, h2 a, h3 a", "base": "https://www.rsna.org"},
    {"name": "AuntMinnie", "url": "https://www.auntminnie.com/index.aspx?sec=nws", "selector": "a.article-title, h2 a, h3 a, .headline a", "base": "https://www.auntminnie.com"},
    {"name": "Radiology Today", "url": "https://www.radiologytoday.net", "selector": ".entry-title a, h2 a, h3 a, article a", "base": "https://www.radiologytoday.net"},
    {"name": "Imaging Technology News", "url": "https://www.itnonline.com/channel/radiology", "selector": "h2 a, h3 a, .article-title a", "base": "https://www.itnonline.com"},
    {"name": "Radiology Business", "url": "https://www.radiologybusiness.com/topics/imaging", "selector": "h2 a, h3 a, .article-title a, .entry-title a", "base": "https://www.radiologybusiness.com"},
    {"name": "Applied Radiology", "url": "https://appliedradiology.com/articles", "selector": "h2 a, h3 a, .article-title a", "base": "https://appliedradiology.com"},
    {"name": "Diagnostic Imaging", "url": "https://www.diagnosticimaging.com/view/news", "selector": "h2 a, h3 a, .article-title a", "base": "https://www.diagnosticimaging.com"},
]

NEWS_KEYWORDS = ["ai", "artificial intelligence", "mri", "ct", "ultrasound", "x-ray", "radiology", "imaging", "cancer", "detection", "study", "research"]

def load_seen():
    if os.path.exists(SEEN_FILE):
        with open(SEEN_FILE) as f: return set(json.load(f))
    return set()

def save_seen(seen):
    with open(SEEN_FILE, "w") as f: json.dump(sorted(list(seen)), f, indent=2)

def load_seen_news():
    if os.path.exists(SEEN_NEWS_FILE):
        with open(SEEN_NEWS_FILE) as f: return set(json.load(f))
    return set()

def save_seen_news(seen_news):
    items = sorted(list(seen_news))[-200:]
    with open(SEEN_NEWS_FILE, "w") as f: json.dump(items, f, indent=2)

def load_health():
    defaults = {"last_health_check": "", "source_alert_dates": {}, "total_runs": 0, "last_successful_scrape": ""}
    if os.path.exists(HEALTH_FILE):
        with open(HEALTH_FILE) as f: defaults.update(json.load(f))
    return defaults

def save_health(state):
    with open(HEALTH_FILE, "w") as f: json.dump(state, f, indent=2)

def make_id(title, url): return hashlib.md5(f"{title.strip().lower()}|{url.strip()}".encode()).hexdigest()
def make_news_id(title): return hashlib.md5(title.strip().lower().encode()).hexdigest()
def today_str(): return date.today().isoformat()

def fetch(url, params=None, ssl_verify=True):
    try:
        resp = requests.get(url, headers=HEADERS, params=params, timeout=25, verify=ssl_verify)
        resp.raise_for_status()
        return BeautifulSoup(resp.text, "lxml")
    except Exception as e:
        log.warning(f"Fetch fallito [{url}]: {e}")
        return None

def get_region(text):
    t = text.lower()
    abruzzo = ["abruzzo", "l'aquila", "aquila", "avezzano", "sulmona", "lanciano", "vasto", "chieti", "pescara", "teramo"]
    emilia = ["emilia", "romagna", "bologna", "modena", "reggio emilia", "parma", "piacenza", "ferrara", "ravenna", "forlì", "cesena", "rimini", "imola", "sant'orsola", "maggiore"]
    marche = ["marche", "ancona", "pesaro", "urbino", "macerata", "fermo", "ascoli", "torrette"]
    found = []
    if any(k in t for k in abruzzo): found.append("ABRUZZO")
    if any(k in t for k in emilia): found.append("EMILIA ROMAGNA")
    if any(k in t for k in marche): found.append("MARCHE")
    return " / ".join(found) if found else "REGIONE DA VERIFICARE"

def get_daily_news(seen_news):
    for ns in NEWS_SOURCES:
        soup = fetch(ns["url"])
        if not soup: continue
        for a in soup.select(ns["selector"]):
            title = a.get_text(separator=" ", strip=True)
            if len(title) < 20 or not any(kw in title.lower() for kw in NEWS_KEYWORDS): continue
            full_url = href if (href := a.get("href", "")).startswith("http") else ns["base"] + href
            if (news_id := make_news_id(title)) in seen_news: continue
            seen_news.add(news_id)
            return {"title": title, "url": full_url, "source": ns["name"]}, seen_news
    return None, seen_news

def is_relevant(text): return any(kw in text.lower() for kw in KEYWORDS)
def is_geo_strict(text): return any(kw in text.lower() for kw in GEO_STRICT)

def scrape_source(source):
    soup = fetch(source["url"], ssl_verify=source.get("ssl", True))
    if not soup: return None
    is_national = source.get("type") == "national"
    results = []
    for a in soup.find_all("a", href=True):
        title = a.get_text(separator=" ", strip=True)
        if len(title) < 10 or not is_relevant(title): continue
        context = f"{title} {a.parent.get_text(separator=' ', strip=True) if a.parent else ''}"
        if is_national and not is_geo_strict(context): continue
        full_url = a["href"] if a["href"].startswith("http") else urljoin(source["url"], a["href"])
        results.append({
            "title": title, "url": full_url, "source": source["name"],
            "date": datetime.now().strftime("%d/%m/%Y"), "region": get_region(context)
        })
    return results

def fmt_bando(c):
    return (
        "🏥 *Nuovo concorso — Radiologia*\n"
        f"📍 *{c['region']}*\n\n"
        f"📋 *{c['title']}*\n\n"
        f"🏛 {c['source']}\n"
        f"🗓 Rilevato il: {c['date']}\n\n"
        f"👉 [Apri il bando]({c['url']})"
    )

def fmt_daily(new_today, total_active, news):
    oggi = datetime.now().strftime("%d/%m/%Y")
    bandi_txt = f"📋 Nuovi concorsi oggi: *{new_today}*\n" if new_today > 0 else "📋 Nessun nuovo concorso oggi\n"
    if total_active > 0: bandi_txt += f"📂 Concorsi attivi monitorati: *{total_active}*\n"
    news_txt = f"\n📰 *News*\n\n*{news['title']}*\n_{news['source']}_\n👉 [Leggi]({news['url']})" if news else "\n_Nessuna news oggi._"
    return f"☀️ *{oggi} — Report giornaliero*\n\n{bandi_txt}{news_txt}"

async def send_msg(bot, text):
    for cid in [cid.strip() for cid in CHAT_ID.split(",") if cid.strip()]:
        try: await bot.send_message(chat_id=cid, text=text, parse_mode=ParseMode.MARKDOWN)
        except Exception as e: log.error(f"Errore Telegram: {e}")

async def main():
    if not TELEGRAM_TOKEN or not CHAT_ID: return
    seen, seen_news, state = load_seen(), load_seen_news(), load_health()
    bot, today, new_count = Bot(token=TELEGRAM_TOKEN), today_str(), 0
    state["total_runs"] = state.get("total_runs", 0) + 1
    
    for source in SOURCES:
        concorsi = scrape_source(source)
        if concorsi is None: continue
        for c in concorsi:
            if (cid := make_id(c["title"], c["url"])) not in seen:
                await send_msg(bot, fmt_bando(c))
                seen.add(cid)
                new_count += 1
                await asyncio.sleep(1.5)

    if state.get("last_health_check") != today:
        news, seen_news = get_daily_news(seen_news)
        await send_msg(bot, fmt_daily(new_count, len(seen), news))
        state["last_health_check"] = today
        save_seen_news(seen_news)
    
    save_seen(seen)
    save_health(state)

if __name__ == "__main__":
    asyncio.run(main())
