"""
Bot Telegram - Concorsi Pubblici Medici Radiologi in Abruzzo
============================================================
Fix 25/03/2026:
  - Rimossi Concorsi.it e ConcorsiPubblici.com (non filtrabili per regione)
  - URL Regione Abruzzo corretto
  - Health check inviato ogni giorno indipendentemente dai bandi trovati
  - Messaggio giornaliero include conteggio bandi attivi trovati oggi
"""

import os
import json
import logging
import hashlib
import asyncio
import requests
import re
from bs4 import BeautifulSoup
from datetime import datetime, date
from urllib.parse import urljoin
from telegram import Bot
from telegram.constants import ParseMode

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
CHAT_ID        = os.environ.get("CHAT_ID", "")

SEEN_FILE   = "seen_concorsi.json"
HEALTH_FILE = "health_state.json"

BOT_START_DATE = date(2026, 3, 25)

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

REGION_KEYWORDS = [
    "abruzzo", "l'aquila", "aquila", "teramo", "pescara", "chieti",
    "lanciano", "vasto", "avezzano", "sulmona", "asl",
]

# ──────────────────────────────────────────────
# SORGENTI — verificate manualmente
# ──────────────────────────────────────────────
SOURCES = [
    {
        # ASL locali — nessun filtro regione (sono già abruzzesi)
        "name": "ASL 1 Avezzano-Sulmona-L'Aquila",
        "url": "https://trasparenza.asl1abruzzo.it/pagina640_concorsi-attivi.html",
        "type": "generic",
        "ssl": True,
        "region_filter": False,
    },
    {
        "name": "ASL 2 Lanciano-Vasto-Chieti",
        "url": "https://lnx.asl2abruzzo.it/b/",
        "type": "generic",
        "ssl": True,
        "region_filter": False,
    },
    {
        "name": "ASL 3 Pescara",
        "url": "https://www.asl.pe.it/BandiConcorsi.jsp",
        "type": "generic",
        "ssl": True,
        "region_filter": False,
    },
    {
        "name": "ASL 4 Teramo",
        "url": "https://www.aslteramo.it/concorsi",
        "type": "generic",
        "ssl": True,
        "region_filter": False,
    },
    {
        # Regione Abruzzo — URL corretto con parametri
        "name": "Regione Abruzzo — Concorsi Aperti",
        "url": "http://www2.regione.abruzzo.it/concorsi/index.asp",
        "params": {
            "modello": "bandoAperti",
            "msv": "concorsi3",
            "servizio": "xList",
            "stileDiv": "mono",
            "template": "default",
        },
        "type": "generic",
        "ssl": False,   # http, non https
        "region_filter": False,
    },
    {
        # SIRM — aggiornato, specifico per radiologia, filtra per Abruzzo
        "name": "SIRM — Società Italiana Radiologia Medica",
        "url": "https://sirm.org/concorsi-2/",
        "type": "generic",
        "ssl": True,
        "region_filter": True,
    },
    {
        # FNO TSRM — rubrica settimanale concorsi radiologia, filtra per Abruzzo
        "name": "FNO TSRM — Rubrica Concorsi",
        "url": "https://www.tsrm-pstrp.org/index.php/rubrica_concorsi/",
        "type": "generic",
        "ssl": True,
        "region_filter": True,
    },
]

# ──────────────────────────────────────────────
# SORGENTI NEWS RADIOLOGIA
# ──────────────────────────────────────────────
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
]

NEWS_KEYWORDS = [
    "ai", "artificial intelligence", "machine learning",
    "mri", "ct", "ultrasound", "x-ray", "pet",
    "radiology", "imaging", "diagnostic",
    "cancer", "tumor", "detection",
    "innovation", "breakthrough", "study", "research",
]


# ══════════════════════════════════════════════
# FILTRO DATA
# ══════════════════════════════════════════════

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
        if not m:
            continue
        try:
            if fmt == "iso":
                return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
            elif fmt == "it":
                month = MONTHS_IT.get(m.group(2), 0)
                if month:
                    return date(int(m.group(3)), month, int(m.group(1)))
            else:
                return date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
        except ValueError:
            continue
    return None


def is_recent(text: str) -> bool:
    found = extract_date(text)
    if found is None:
        return True
    if found >= BOT_START_DATE:
        return True
    log.info(f"  Ignorato (data {found} < {BOT_START_DATE}): {text[:50]}")
    return False


# ══════════════════════════════════════════════
# STATO
# ══════════════════════════════════════════════

def load_seen() -> set:
    if os.path.exists(SEEN_FILE):
        with open(SEEN_FILE) as f:
            return set(json.load(f))
    return set()


def save_seen(seen: set):
    with open(SEEN_FILE, "w") as f:
        json.dump(sorted(list(seen)), f, indent=2)


def load_health() -> dict:
    defaults = {
        "last_health_check":      "",
        "source_alert_dates":     {},
        "total_runs":             0,
        "last_successful_scrape": "",
        "active_bandi_count":     0,   # bandi attivi trovati oggi
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


def today_str() -> str:
    return date.today().isoformat()


# ══════════════════════════════════════════════
# FETCH
# ══════════════════════════════════════════════

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


# ══════════════════════════════════════════════
# NEWS RADIOLOGIA
# ══════════════════════════════════════════════

def get_daily_news() -> dict | None:
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
            log.info(f"News trovata [{ns['name']}]: {title[:70]}")
            return {"title": title, "url": full_url, "source": ns["name"]}
    return None


# ══════════════════════════════════════════════
# SCRAPING CONCORSI
# ══════════════════════════════════════════════

def is_relevant(text: str) -> bool:
    return any(kw in text.lower() for kw in KEYWORDS)


def is_abruzzo(text: str) -> bool:
    return any(rk in text.lower() for rk in REGION_KEYWORDS)


def scrape_generic(source: dict) -> list[dict] | None:
    soup = fetch(
        source["url"],
        params=source.get("params"),
        ssl_verify=source.get("ssl", True)
    )
    if not soup:
        return None

    region_filter = source.get("region_filter", False)
    results = []

    for a in soup.find_all("a", href=True):
        title = a.get_text(separator=" ", strip=True)
        href  = a["href"]

        if not title or len(title) < 10 or not is_relevant(title):
            continue

        # Testo di contesto (riga/paragrafo circostante)
        parent = a.parent
        context = f"{title} {parent.get_text(separator=' ', strip=True) if parent else ''}"

        # Filtro regione per sorgenti nazionali
        if region_filter and not is_abruzzo(context):
            continue

        # Filtro data
        if not is_recent(context):
            continue

        full_url = href if href.startswith("http") else urljoin(source["url"], href)
        found_date = extract_date(context)
        date_str   = found_date.strftime("%d/%m/%Y") if found_date else datetime.now().strftime("%d/%m/%Y")

        results.append({
            "title":  title,
            "url":    full_url,
            "source": source["name"],
            "date":   date_str,
        })

    log.info(f"  [{source['name']}] {len(results)} bandi rilevanti e recenti")
    return results


def scrape_source(source: dict) -> list[dict] | None:
    return scrape_generic(source)


# ══════════════════════════════════════════════
# MESSAGGI TELEGRAM
# ══════════════════════════════════════════════

def fmt_bando(c: dict) -> str:
    return (
        "🏥 *Nuovo concorso — Radiologo Abruzzo*\n\n"
        f"📋 *{c['title']}*\n\n"
        f"🏛 {c['source']}\n"
        f"📅 {c['date']}\n\n"
        f"👉 [Apri il bando]({c['url']})"
    )


def fmt_health(new_today: int, total_active: int, news: dict | None) -> str:
    """
    Messaggio giornaliero con:
    - nuovi bandi trovati oggi
    - totale bandi attivi dal 25/03/2026
    - news del giorno (se disponibile)
    """
    oggi = datetime.now().strftime("%d/%m/%Y")

    if new_today > 0:
        bandi_line = f"📋 *Nuovi bandi oggi:* {new_today} — vedi sopra"
    else:
        bandi_line = "📋 *Nuovi bandi oggi:* nessuno"

    totale_line = f"📂 *Bandi attivi dal 25/03/2026:* {total_active}"

    base = f"☀️ *{oggi}*\n\n{bandi_line}\n{totale_line}"

    if news:
        base += (
            f"\n\n─────────────────\n"
            f"📰 *News dal mondo della radiologia*\n\n"
            f"*{news['title']}*\n"
            f"_{news['source']}_\n\n"
            f"👉 [Leggi l'articolo]({news['url']})"
        )

    return base


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


# ══════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════

async def main():
    if not TELEGRAM_TOKEN or not CHAT_ID:
        log.error("TELEGRAM_TOKEN o CHAT_ID mancanti!")
        return

    log.info("═══ Avvio bot concorsi radiologi Abruzzo ═══")
    log.info(f"Filtro data attivo: ignoro bandi precedenti al {BOT_START_DATE}")

    seen  = load_seen()
    state = load_health()
    bot   = Bot(token=TELEGRAM_TOKEN)

    state["total_runs"] += 1
    if "source_alert_dates" not in state:
        state["source_alert_dates"] = {}
    if "active_bandi_count" not in state:
        state["active_bandi_count"] = 0

    today         = today_str()
    new_today     = 0   # bandi NUOVI trovati in questa run

    # ── Scraping concorsi ──────────────────────
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
                new_today += 1
                state["active_bandi_count"] += 1
                await asyncio.sleep(1.5)

        await asyncio.sleep(2)

    log.info(f"Nuovi bandi notificati oggi: {new_today}")
    log.info(f"Totale bandi attivi dal {BOT_START_DATE}: {state['active_bandi_count']}")

    # ── Health check giornaliero ───────────────
    # Inviato UNA VOLTA al giorno, SEMPRE, indipendentemente dai bandi trovati
    if state["last_health_check"] != today:
        log.info("Recupero news radiologia del giorno...")
        news = get_daily_news()
        await send_msg(bot, fmt_health(new_today, state["active_bandi_count"], news))
        state["last_health_check"] = today
        state["last_successful_scrape"] = today
    else:
        log.info("Health check già inviato oggi — skip")

    save_seen(seen)
    save_health(state)
    log.info("═══ Fine run ═══")


if __name__ == "__main__":
    asyncio.run(main())
