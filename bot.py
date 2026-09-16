"""
Bot Telegram - Concorsi Pubblici Medici Radiologi (Abruzzo, Marche, Emilia Romagna)
============================================================
MODALITÀ DEBUG ATTIVA: Invia sempre un report di diagnostica su Telegram a fine ciclo.
"""

import os
import json
import logging
import hashlib
import asyncio
import requests
import re
import urllib3
from bs4 import BeautifulSoup
from datetime import datetime, date
from urllib.parse import urljoin
from telegram import Bot
from telegram.constants import ParseMode

import warnings
from bs4 import XMLParsedAsHTMLWarning

warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
CHAT_ID        = os.environ.get("CHAT_ID", "")

BOT_START_DATE = date(2026, 3, 25)
SEEN_FILE      = "seen_concorsi.json"
HEALTH_FILE    = "health_state.json"
SEEN_NEWS_FILE = "seen_news.json"

logging.basicConfig(format="%(asctime)s [%(levelname)s] %(message)s", level=logging.INFO)
log = logging.getLogger(__name__)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
    )
}

KEYWORDS = [
    "radiolog", "radiodiagnostic", "tsrm",
    "tecnico sanitario di radiologia", "tecnico di radiologia",
    "diagnostica per immagini", "neuroradiolog", "interventistica"
]

REGION_KEYWORDS = [
    "abruzzo", "l'aquila", "aquila", "teramo", "pescara", "chieti",
    "lanciano", "vasto", "avezzano", "sulmona", "asl 1", "asl 2", "asl 3", "asl 4",
    "marche", "ancona", "pesaro", "urbino", "macerata", "fermo", "ascoli", "ast", "asur",
    "emilia", "romagna", "bologna", "modena", "reggio emilia", "parma", "piacenza",
    "ferrara", "ravenna", "forlì", "cesena", "rimini", "imola", "ausl"
]

SOURCES = [
    {"name": "ASL 1 Avezzano", "url": "https://trasparenza.asl1abruzzo.it/page/75/concorsi-attivi", "type": "local_html", "ssl": False, "region_filter": False},
    {"name": "ASL 2 Chieti", "url": "https://lnx.asl2abruzzo.it/b/", "type": "local_html", "ssl": False, "region_filter": False},
    {"name": "ASL 3 Pescara", "url": "https://www.asl.pe.it/BandiConcorsi.jsp", "type": "local_html", "ssl": False, "region_filter": False},
    {"name": "ASL 4 Teramo", "url": "https://www.aslteramo.it/concorsi", "type": "local_html", "ssl": False, "region_filter": False},
    {"name": "SIRM (Radiologia Medica)", "url": "https://sirm.org/concorsi-2/", "type": "national_html", "ssl": True, "region_filter": True},
    {"name": "FNO TSRM", "url": "https://www.tsrm-pstrp.org/index.php/rubrica_concorsi/", "type": "national_html", "ssl": True, "region_filter": True},
    {"name": "Gazzetta Ufficiale (Concorsi)", "url": "https://www.gazzettaufficiale.it/rss/S4", "type": "rss", "ssl": True, "region_filter": True}
]

MONTHS_IT = {
    "gennaio": 1, "febbraio": 2, "marzo": 3, "aprile": 4,
    "maggio": 5, "giugno": 6, "luglio": 7, "agosto": 8,
    "settembre": 9, "ottobre": 10, "novembre": 11, "dicembre": 12,
}

DATE_PATTERNS = [
    (r'\b(\d{1,2})[/-](\d{1,2})[/-](20\d{2})\b', "dmy"),
    (r'\b(20\d{2})-(\d{2})-(\d{2})\b',            "iso"),
    (r'\b(\d{1,2})\s+(gennaio|febbraio|marzo|aprile|maggio|giugno|'
     r'luglio|agosto|settembre|ottobre|novembre|dicembre)\s+(20\d{2})\b', "it"),
]

def extract_date(text: str) -> date | None:
    t = text.lower()
    for pattern, fmt in DATE_PATTERNS:
        m = re.search(pattern, t)
        if not m: continue
        try:
            if fmt == "iso": return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
            elif fmt == "it":
                month = MONTHS_IT.get(m.group(2), 0)
                if month: return date(int(m.group(3)), month, int(m.group(1)))
            else: return date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
        except ValueError: continue
    return None

def is_recent(text: str) -> bool:
    found = extract_date(text)
    if found is None: return True
    if found >= BOT_START_DATE: return True
    return False

def get_region(text: str) -> str:
    t = text.lower()
    if any(k in t for k in ["abruzzo", "l'aquila", "aquila", "avezzano", "sulmona", "lanciano", "vasto", "chieti", "pescara", "teramo"]): return "ABRUZZO"
    if any(k in t for k in ["emilia", "romagna", "bologna", "modena", "reggio emilia", "parma", "piacenza", "ferrara", "ravenna", "forlì", "cesena", "rimini", "imola"]): return "EMILIA ROMAGNA"
    if any(k in t for k in ["marche", "ancona", "pesaro", "urbino", "macerata", "fermo", "ascoli"]): return "MARCHE"
    return "REGIONE DA VERIFICARE"

def load_json(file_path, default):
    if os.path.exists(file_path):
        with open(file_path) as f: return json.load(f)
    return default

def save_json(file_path, data):
    with open(file_path, "w") as f: json.dump(data, f, indent=2)

def make_id(title: str, url: str) -> str: return hashlib.md5(f"{title.strip().lower()}|{url.strip()}".encode()).hexdigest()
def today_str() -> str: return date.today().isoformat()

def fetch(url: str, params: dict = None, ssl_verify: bool = True) -> BeautifulSoup | None:
    try:
        resp = requests.get(url, headers=HEADERS, params=params, timeout=25, verify=ssl_verify)
        resp.raise_for_status()
        if "rss" in url.lower():
            return BeautifulSoup(resp.content, "xml")
        return BeautifulSoup(resp.text, "lxml")
    except Exception as e:
        log.warning(f"Fetch fallito [{url}]: {e}")
        return None

def is_relevant(text: str) -> bool: return any(kw in text.lower() for kw in KEYWORDS)
def is_target_region(text: str) -> bool: return any(rk in text.lower() for rk in REGION_KEYWORDS)

def scrape_source(source: dict) -> list[dict] | None:
    soup = fetch(source["url"], ssl_verify=source.get("ssl", True))
    if not soup: return None
    
    region_filter = source.get("region_filter", False)
    results = []
    
    if source.get("type") == "rss":
        for item in soup.find_all("item"):
            title = item.title.text.strip() if item.title else ""
            desc = item.description.text.strip() if item.description else ""
            link = item.link.text.strip() if item.link else ""
            context = f"{title} {desc}"
            if not is_relevant(context): continue
            if region_filter and not is_target_region(context): continue
            results.append({
                "title": title, "url": link, "source": source["name"],
                "date": datetime.now().strftime("%d/%m/%Y"),
                "region": get_region(context) if region_filter else "ABRUZZO"
            })
    else:
        for a in soup.find_all("a", href=True):
            title = a.get_text(separator=" ", strip=True)
            href  = a["href"]
            if not title or len(title) < 10 or not is_relevant(title): continue
            parent = a.parent
            context = f"{title} {parent.get_text(separator=' ', strip=True) if parent else ''}"
            if region_filter and not is_target_region(context): continue
            if not is_recent(context): continue
            full_url = href if href.startswith("http") else urljoin(source["url"], href)
            results.append({
                "title": title, "url": full_url, "source": source["name"],
                "date": datetime.now().strftime("%d/%m/%Y"),
                "region": get_region(context) if region_filter else "ABRUZZO"
            })
    return results

def fmt_bando(c: dict) -> str:
    return f"🏥 *Nuovo concorso — Radiologia*\n📍 *{c['region']}*\n\n📋 *{c['title']}*\n\n🏛 {c['source']}\n🗓 Rilevato il: {c['date']}\n\n👉 [Apri il bando]({c['url']})"

async def send_msg(bot: Bot, text: str):
    for cid in [cid.strip() for cid in CHAT_ID.split(",") if cid.strip()]:
        try: await bot.send_message(chat_id=cid, text=text, parse_mode=ParseMode.MARKDOWN)
        except Exception as e: log.error(f"Errore Telegram: {e}")

async def main():
    if not TELEGRAM_TOKEN or not CHAT_ID: 
        log.error("Token o Chat ID mancanti!")
        return
    
    seen = set(load_json(SEEN_FILE, []))
    state = load_json(HEALTH_FILE, {"last_health_check": "", "source_alert_dates": {}, "total_runs": 0, "active_bandi_count": 0})
    
    bot = Bot(token=TELEGRAM_TOKEN)
    today = today_str()
    state["total_runs"] += 1
    new_today = 0
    
    # Dizionario per accumulare i risultati del debug
    debug_status = {}
    
    for source in SOURCES:
        concorsi = scrape_source(source)
        if concorsi is None:
            debug_status[source["name"]] = "❌ ERRORE/OFFLINE"
            # Avviso normale limitato a una volta al giorno
            if state.get("source_alert_dates", {}).get(source["name"]) != today:
                await send_msg(bot, f"⚠️ *Sorgente offline — {today}*\n❌ {source['name']}")
                if "source_alert_dates" not in state: state["source_alert_dates"] = {}
                state["source_alert_dates"][source["name"]] = today
        else:
            debug_status[source["name"]] = f"✅ OK ({len(concorsi)} bandi validi)"
            for c in concorsi:
                if (cid := make_id(c["title"], c["url"])) not in seen:
                    await send_msg(bot, fmt_bando(c))
                    seen.add(cid)
                    new_today += 1
                    state["active_bandi_count"] += 1
                    await asyncio.sleep(1.5)

    # ==========================================
    # INVIO FORZATO DEL REPORT DI DEBUG
    # ==========================================
    debug_msg = f"🛠 *DEBUG ESECUZIONE BOT* ({today})\n\n"
    debug_msg += f"Esecuzione numero: {state['total_runs']}\n"
    debug_msg += f"Bandi totali in archivio: {len(seen)}\n"
    debug_msg += f"Nuovi bandi elaborati ora: {new_today}\n\n"
    debug_msg += "📡 *Stato Sorgenti:*\n"
    for nome, esito in debug_status.items():
        debug_msg += f"- {esito}: {nome}\n"
    
    await send_msg(bot, debug_msg)
    # ==========================================
        
    save_json(SEEN_FILE, list(seen))
    save_json(HEALTH_FILE, state)

if __name__ == "__main__":
    asyncio.run(main())
