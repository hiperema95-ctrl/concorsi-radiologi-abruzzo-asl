"""
Bot Telegram - Concorsi e Albi Pretori Radiologia (Abruzzo, Marche, Emilia Romagna)
"""

import os
import json
import logging
import hashlib
import asyncio
import cloudscraper
import urllib3
from bs4 import BeautifulSoup
from datetime import datetime, date
from urllib.parse import urljoin
from telegram import Bot
from telegram.constants import ParseMode

# Disabilita gli avvisi fastidiosi per i siti PA senza certificato HTTPS valido
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

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
        "Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "it-IT,it;q=0.9,en-US;q=0.8,en;q=0.7",
}

KEYWORDS = [
    "radiolog",         
    "radiodiagnostic",  
    "tsrm", 
    "tecnico di radiologia",
    "tecnico sanitario di radiologia",
    "neuroradiolog",    
    "interventistica",
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
    # --- SITI CONCORSI TRADIZIONALI (PA Locali) ---
    {"name": "ASL 1 Avezzano (Concorsi)", "url": "https://trasparenza.asl1abruzzo.it/pagina640_concorsi-attivi.html", "type": "local", "ssl": False},
    {"name": "ASL 2 Chieti (Concorsi)", "url": "https://lnx.asl2abruzzo.it/b/", "type": "local", "ssl": False},
    {"name": "ASL 3 Pescara (Concorsi)", "url": "https://www.asl.pe.it/BandiConcorsi.jsp", "type": "local", "ssl": False},
    {"name": "ASL 4 Teramo (Concorsi)", "url": "https://www.aslteramo.it/concorsi", "type": "local", "ssl": False},
    
    # --- ALBI PRETORI - ABRUZZO ---
    {"name": "ASL 1 Avezzano (Albo Pretorio)", "url": "https://trasparenza.asl1abruzzo.it/pagina638_albo-pretorio-storico.html", "type": "local", "ssl": False},
    {"name": "ASL 2 Chieti (Albo Pretorio)", "url": "https://lnx.asl2abruzzo.it/albo/", "type": "local", "ssl": False},
    {"name": "ASL 3 Pescara (Albo Pretorio)", "url": "https://www.asl.pe.it/Albo_Pretorio.jsp", "type": "local", "ssl": False},
    {"name": "ASL 4 Teramo (Albo Pretorio)", "url": "https://alboaziendale.aslteramo.it/?ELEMENTI_PER_PAGINA=50", "type": "local", "ssl": False},

    # --- ALBI PRETORI - MARCHE (AST) ---
    {"name": "AST Ancona (Albo Pretorio)", "url": "https://www.astancona.marche.it/albo-pretorio/", "type": "local", "ssl": False},
    {"name": "AST Pesaro Urbino (Albo Pretorio)", "url": "https://www.astpesaro.marche.it/albo-pretorio/", "type": "local", "ssl": False},
    {"name": "AST Macerata (Albo Pretorio)", "url": "https://www.astmacerata.marche.it/albo-pretorio/", "type": "local", "ssl": False},
    {"name": "AST Fermo (Albo Pretorio)", "url": "https://www.astfermo.marche.it/albo-pretorio/", "type": "local", "ssl": False},
    {"name": "AST Ascoli Piceno (Albo Pretorio)", "url": "https://www.astascoli.marche.it/albo-pretorio/", "type": "local", "ssl": False},

    # --- ALBI PRETORI - EMILIA ROMAGNA ---
    {"name": "AUSL Romagna - Ravenna/Forlì/Cesena (Albo)", "url": "https://www.auslromagna.it/albo-pretorio", "type": "local", "ssl": False},
    {"name": "AUSL Bologna (Albo Pretorio)", "url": "https://www.ausl.bologna.it/amministrazione-trasparente/albo-pretorio/", "type": "local", "ssl": False},
    {"name": "AUSL Imola (Albo Pretorio)", "url": "https://www.ausl.imola.bo.it/albo-pretorio", "type": "local", "ssl": False},
    {"name": "AUSL Modena (Albo Pretorio)", "url": "https://www.ausl.mo.it/albo-pretorio/", "type": "local", "ssl": False},
    {"name": "AUSL Reggio Emilia (Albo Pretorio)", "url": "https://www.ausl.re.it/albo-pretorio", "type": "local", "ssl": False},
    {"name": "AUSL Parma (Albo Pretorio)", "url": "https://www.ausl.pr.it/albo_pretorio/", "type": "local", "ssl": False},
    {"name": "AUSL Piacenza (Albo Pretorio)", "url": "https://www.ausl.pc.it/it/albo-pretorio", "type": "local", "ssl": False},
    {"name": "AUSL Ferrara (Albo Pretorio)", "url": "https://www.ausl.fe.it/albo-pretorio", "type": "local", "ssl": False},

    # --- SORGENTI NAZIONALI ---
    {"name": "SIRM — Società Italiana Radiologia Medica", "url": "https://sirm.org/concorsi-2/", "type": "national", "ssl": True},
    {"name": "FNO TSRM — Rubrica Concorsi", "url": "https://www.tsrm-pstrp.org/index.php/rubrica_concorsi/", "type": "national", "ssl": True},
    {"name": "InfoConcorsi (EdiSES) — Ricerca Radiologia", "url": "https://infoconcorsi.edises.it/ricerca?q=radiologia", "type": "national", "ssl": True},
    {"name": "Anaao Assomed — Concorsi Dirigenza Medica", "url": "https://www.anaao.it/content.php?id=31", "type": "national", "ssl": True}
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
    defaults = {
        "last_health_check": "", 
        "source_alert_dates": {}, 
        "js_alert_dates": {}, 
        "total_runs": 0, 
        "last_successful_scrape": ""
    }
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
        # Usa cloudscraper invece di requests per eludere WAF e Cloudflare
        scraper = cloudscraper.create_scraper(browser={'browser': 'chrome', 'platform': 'windows', 'mobile': False})
        resp = scraper.get(url, headers=HEADERS, params=params, timeout=30, verify=ssl_verify)
        resp.raise_for_status()
        return BeautifulSoup(resp.text, "lxml")
    except Exception as e:
        log.warning(f"Fetch fallito [{url}]: {e}")
        return None

# ... [IL RESTO DEL CODICE RIMANE IDENTICO A PARTIRE DA "def get_region(text):"] ...
