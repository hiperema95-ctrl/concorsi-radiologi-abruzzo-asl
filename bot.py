"""
Bot Telegram - Concorsi Pubblici Medici Radiologi in Abruzzo
============================================================
Versione per GitHub Actions: script one-shot, scheduling via cron.

Funzionalità:
  - Scraping di InPA, ASL abruzzesi, Gazzetta Ufficiale, Regione Abruzzo
  - Notifica Telegram per ogni nuovo bando rilevato
  - Health check ogni 24h: verifica che il bot stia girando correttamente
  - Alert anti-spam: se il bot non riesce a prelevare info da NESSUNA sorgente,
    manda UN SOLO messaggio di allerta ogni 24h

Secrets da configurare su GitHub (Settings → Secrets → Actions):
  TELEGRAM_TOKEN  →  token del bot da @BotFather
  CHAT_ID         →  il tuo chat ID da @userinfobot
"""

import os
import json
import time
import logging
import hashlib
import asyncio
import requests
from bs4 import BeautifulSoup
from datetime import datetime, date
from telegram import Bot
from telegram.constants import ParseMode

# ─────────────────────────────────────────────
# Credenziali da GitHub Secrets (env vars)
# ─────────────────────────────────────────────
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
CHAT_ID        = os.environ.get("CHAT_ID", "")

SEEN_FILE   = "seen_concorsi.json"
HEALTH_FILE = "health_state.json"

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(message)s",
    level=logging.INFO,
)
log = logging.getLogger(__name__)

# SSL verify disabilitato per siti con certificati scaduti (ASL3, Regione)
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
]

REGION_KEYWORDS = [
    "abruzzo", "l'aquila", "aquila", "teramo", "pescara", "chieti",
    "asl abruzzo", "asl lanciano", "asl avezzano", "asl sulmona",
]

# URL aggiornati e verificati
SOURCES = [
    {
        "name": "InPA — Portale Concorsi PA",
        "url": "https://www.inpa.gov.it/concorsi-pubblici/",
        "type": "inpa",
        "params": {"keyword": "radiologo", "regione": "Abruzzo"},
    },
    {
        "name": "InPA — Ricerca radiologia",
        "url": "https://www.inpa.gov.it/concorsi-pubblici/",
        "type": "inpa",
        "params": {"keyword": "radiologia", "regione": "Abruzzo"},
    },
    {
        "name": "ASL 1 Avezzano-Sulmona-L'Aquila",
        "url": "https://www.asl1abruzzo.it/index.php/albo-on-line",
        "type": "generic",
        "ssl": True,
    },
    {
        "name": "ASL 2 Lanciano-Vasto-Chieti",
        "url": "https://www.asl2abruzzo.it/index.php/albo-online",
        "type": "generic",
        "ssl": True,
    },
    {
        "name": "ASL 3 Pescara",
        "url": "https://www.ausl.pe.it/index.php/albo-pretorio",
        "type": "generic",
        "ssl": False,   # certificato SSL scaduto — disabilitiamo verify
    },
    {
        "name": "ASL 4 Teramo",
        "url": "https://www.aslteramo.it/concorsi",
        "type": "generic",
        "ssl": True,
    },
    {
        "name": "Regione Abruzzo — Bandi",
        "url": "https://www.regione.abruzzo.it/content/bandi-e-concorsi",
        "type": "generic",
        "ssl": False,   # certificato SSL scaduto — disabilitiamo verify
    },
    {
        "name": "Gazzetta Ufficiale — Concorsi",
        "url": "https://www.gazzettaufficiale.it/ricerca/concorsi/ricercaAvanzata?q=radiologo+abruzzo",
        "type": "gazzetta",
        "ssl": True,
    },
    {
        # Backup: portale mobilità SSN con filtro radiologia
        "name": "Mobilità SSN — Radiologia",
        "url": "https://www.ilsole24ore.com/motore-ricerca/risultati?q=concorso+radiologo+abruzzo&topic=salute",
        "type": "generic",
        "ssl": True,
    },
]


# ══════════════════════════════════════════════
# GESTIONE STATO
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
        "last_alert_date":        "",
        "consecutive_failures":   0,
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


def today_str() -> str:
    return date.today().isoformat()


# ══════════════════════════════════════════════
# SCRAPING
# ══════════════════════════════════════════════

def is_relevant(text: str) -> bool:
    t = text.lower()
    return any(kw in t for kw in KEYWORDS)


def fetch(url: str, params: dict = None, ssl_verify: bool = True) -> BeautifulSoup | None:
    try:
        resp = requests.get(
            url,
            headers=HEADERS,
            params=params,
            timeout=25,
            verify=ssl_verify,
        )
        resp.raise_for_status()
        return BeautifulSoup(resp.text, "lxml")
    except Exception as e:
        log.warning(f"Fetch fallito [{url}]: {e}")
        return None


def scrape_generic(source: dict) -> list[dict] | None:
    ssl = source.get("ssl", True)
    soup = fetch(source["url"], ssl_verify=ssl)
    if not soup:
        return None   # None = sorgente non raggiunta (diverso da lista vuota)
    results = []
    for a in soup.find_all("a", href=True):
        title = a.get_text(separator=" ", strip=True)
        href  = a["href"]
        if not title or len(title) < 10 or not is_relevant(title):
            continue
        if href.startswith("http"):
            full_url = href
        else:
            from urllib.parse import urljoin
            full_url = urljoin(source["url"], href)
        results.append({
            "title":  title,
            "url":    full_url,
            "source": source["name"],
            "date":   datetime.now().strftime("%d/%m/%Y"),
        })
    log.info(f"  [{source['name']}] {len(results)} risultati")
    return results   # [] = raggiunta ma nessun bando rilevante


def scrape_inpa(source: dict) -> list[dict] | None:
    params = source.get("params", {})
    soup   = fetch(source["url"], params=params, ssl_verify=True)
    if not soup:
        return None
    cards = soup.select(".concorso-card, .bando-item, article.bando, .card-concorso, li.bando")
    if not cards:
        return scrape_generic({**source, "url": source["url"]})
    results = []
    for card in cards:
        title_el = card.select_one("h2, h3, h4, .title, .titolo, .nome-bando")
        link_el  = card.select_one("a[href]")
        if not title_el or not link_el:
            continue
        title = title_el.get_text(strip=True)
        href  = link_el["href"]
        if not is_relevant(title):
            continue
        full_url = href if href.startswith("http") else f"https://www.inpa.gov.it{href}"
        date_el  = card.select_one(".date, .data, time, .scadenza")
        date_str = date_el.get_text(strip=True) if date_el else datetime.now().strftime("%d/%m/%Y")
        results.append({
            "title":  title,
            "url":    full_url,
            "source": source["name"],
            "date":   date_str,
        })
    log.info(f"  [InPA] {len(results)} bandi")
    return results


def scrape_gazzetta(source: dict) -> list[dict] | None:
    soup = fetch(source["url"], ssl_verify=True)
    if not soup:
        return None
    results = []
    for row in soup.select("tr, .risultato, .atto, article"):
        text    = row.get_text(separator=" ", strip=True)
        link_el = row.select_one("a[href]")
        if not is_relevant(text):
            continue
        if not any(rk in text.lower() for rk in REGION_KEYWORDS):
            continue
        href     = link_el["href"] if link_el else source["url"]
        full_url = href if href.startswith("http") else f"https://www.gazzettaufficiale.it{href}"
        title    = link_el.get_text(strip=True) if link_el else text[:120]
        results.append({
            "title":  title,
            "url":    full_url,
            "source": source["name"],
            "date":   datetime.now().strftime("%d/%m/%Y"),
        })
    log.info(f"  [Gazzetta] {len(results)} bandi")
    return results


def scrape_source(source: dict) -> list[dict] | None:
    t = source.get("type", "generic")
    if t == "inpa":
        return scrape_inpa(source)
    elif t == "gazzetta":
        return scrape_gazzetta(source)
    return scrape_generic(source)


# ══════════════════════════════════════════════
# MESSAGGI TELEGRAM
# ══════════════════════════════════════════════

def fmt_bando(c: dict) -> str:
    return (
        "🏥 *Nuovo Concorso — Radiologo Abruzzo*\n\n"
        f"📋 *Titolo:* {c['title']}\n"
        f"🏛 *Fonte:* {c['source']}\n"
        f"📅 *Rilevato il:* {c['date']}\n"
        f"🔗 [Apri il bando]({c['url']})"
    )


def fmt_health(state: dict, sources_ok: int, sources_total: int, new_count: int) -> str:
    now = datetime.now().strftime("%d/%m/%Y %H:%M")
    return (
        "✅ *Health Check — Bot Concorsi Radiologi*\n\n"
        f"🕐 *Data/ora:* {now}\n"
        f"📡 *Sorgenti raggiunte:* {sources_ok}/{sources_total}\n"
        f"📋 *Nuovi bandi oggi:* {new_count}\n"
        f"🔄 *Run totali:* {state['total_runs']}\n"
        f"📅 *Ultimo scrape OK:* {state['last_successful_scrape'] or 'mai'}\n\n"
        "_Il bot sta funzionando correttamente._"
    )


def fmt_alert(state: dict, failed_sources: list[str]) -> str:
    now = datetime.now().strftime("%d/%m/%Y %H:%M")
    failed_list = "\n".join(f"  • {s}" for s in failed_sources)
    return (
        "⚠️ *ALERT — Bot Concorsi Radiologi*\n\n"
        f"🕐 *Rilevato alle:* {now}\n"
        f"❌ *Problema:* impossibile prelevare dati da nessuna sorgente\n\n"
        f"*Sorgenti non raggiungibili:*\n{failed_list}\n\n"
        f"📊 *Run consecutive fallite:* {state['consecutive_failures']}\n"
        f"📅 *Ultimo scrape riuscito:* {state['last_successful_scrape'] or 'mai'}\n\n"
        "_Controlla la scheda Actions su GitHub per i dettagli._"
    )


async def send_msg(bot: Bot, text: str):
    """Supporta più destinatari separati da virgola nel CHAT_ID."""
    ids = [cid.strip() for cid in CHAT_ID.split(",") if cid.strip()]
    for cid in ids:
        try:
            await bot.send_message(
                chat_id=cid,
                text=text,
                parse_mode=ParseMode.MARKDOWN,
                disable_web_page_preview=True,
            )
        except Exception as e:
            log.error(f"Errore invio Telegram a {cid}: {e}")


# ══════════════════════════════════════════════
# LOGICA HEALTH CHECK
# ══════════════════════════════════════════════

async def handle_health_and_alerts(
    bot: Bot,
    state: dict,
    sources_ok: int,
    sources_total: int,
    failed_sources: list[str],
    new_count: int,
):
    today      = today_str()
    all_failed = (sources_ok == 0)

    if all_failed:
        state["consecutive_failures"] += 1
        if state["last_alert_date"] != today:
            log.warning(f"Nessuna sorgente raggiungibile — invio alert")
            await send_msg(bot, fmt_alert(state, failed_sources))
            state["last_alert_date"] = today
        else:
            log.info("Alert già inviato oggi — skip (anti-spam)")
    else:
        state["consecutive_failures"]   = 0
        state["last_successful_scrape"] = today
        if state["last_health_check"] != today:
            log.info("Invio daily health check")
            await send_msg(bot, fmt_health(state, sources_ok, sources_total, new_count))
            state["last_health_check"] = today
        else:
            log.info("Health check già inviato oggi — skip")


# ══════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════

async def main():
    if not TELEGRAM_TOKEN or not CHAT_ID:
        log.error("TELEGRAM_TOKEN o CHAT_ID mancanti! Configura i GitHub Secrets.")
        return

    log.info("═══ Avvio bot concorsi radiologi Abruzzo ═══")

    seen   = load_seen()
    state  = load_health()
    bot    = Bot(token=TELEGRAM_TOKEN)

    state["total_runs"] += 1
    log.info(f"Run #{state['total_runs']} — {datetime.now().strftime('%d/%m/%Y %H:%M')}")

    new_count      = 0
    sources_ok     = 0
    failed_sources = []

    for source in SOURCES:
        log.info(f"→ Controllo: {source['name']}")
        try:
            concorsi = scrape_source(source)
        except Exception as e:
            log.error(f"  Errore scraping: {e}")
            concorsi = None

        if concorsi is None:
            failed_sources.append(source["name"])
            await asyncio.sleep(2)
            continue

        sources_ok += 1
        for c in concorsi:
            cid = make_id(c["title"], c["url"])
            if cid not in seen:
                await send_msg(bot, fmt_bando(c))
                seen.add(cid)
                new_count += 1
                await asyncio.sleep(1.5)

        await asyncio.sleep(2)

    log.info(f"Scraping completato — sorgenti OK: {sources_ok}/{len(SOURCES)}, nuovi bandi: {new_count}")

    await handle_health_and_alerts(
        bot=bot,
        state=state,
        sources_ok=sources_ok,
        sources_total=len(SOURCES),
        failed_sources=failed_sources,
        new_count=new_count,
    )

    save_seen(seen)
    save_health(state)
    log.info("═══ Fine run ═══")


if __name__ == "__main__":
    asyncio.run(main())
