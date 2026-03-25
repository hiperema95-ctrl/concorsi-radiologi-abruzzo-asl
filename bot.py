"""
Bot Telegram - Concorsi Pubblici Medici Radiologi in Abruzzo
============================================================
Versione per GitHub Actions: script one-shot, scheduling via cron.

Funzionalità:
  - Scraping di InPA, ASL abruzzesi, Gazzetta Ufficiale, Regione Abruzzo
  - Notifica Telegram per ogni nuovo bando rilevato
  - Health check ogni 24h: verifica che il bot stia girando correttamente
  - Alert anti-spam: se il bot non riesce a prelevare info da NESSUNA sorgente,
    manda UN SOLO messaggio di allerta ogni 24h (non uno per ogni run)

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

# File di stato (committati nel repo per persistere tra le run)
SEEN_FILE       = "seen_concorsi.json"    # bandi già notificati
HEALTH_FILE     = "health_state.json"     # stato health check

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(message)s",
    level=logging.INFO,
)
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
]

REGION_KEYWORDS = [
    "abruzzo", "l'aquila", "aquila", "teramo", "pescara", "chieti",
    "asl abruzzo", "asl lanciano", "asl avezzano", "asl sulmona",
]

SOURCES = [
    {
        "name": "InPA — radiologo abruzzo",
        "url": "https://www.inpa.gov.it/concorsi/?search=radiologo+abruzzo",
        "type": "inpa",
    },
    {
        "name": "InPA — radiologia abruzzo",
        "url": "https://www.inpa.gov.it/concorsi/?search=radiologia&regione=abruzzo",
        "type": "inpa",
    },
    {
        "name": "ASL 1 Avezzano-Sulmona-L'Aquila",
        "url": "https://www.asl1abruzzo.it/bandi-concorsi.html",
        "type": "generic",
    },
    {
        "name": "ASL 2 Lanciano-Vasto-Chieti",
        "url": "https://www.asl2abruzzo.it/concorsi-e-avvisi.html",
        "type": "generic",
    },
    {
        "name": "ASL 3 Pescara",
        "url": "https://www.ausl.pe.it/index.php/concorsi-e-avvisi",
        "type": "generic",
    },
    {
        "name": "ASL 4 Teramo",
        "url": "https://www.aslteramo.it/concorsi",
        "type": "generic",
    },
    {
        "name": "Regione Abruzzo — Concorsi",
        "url": "https://www.regione.abruzzo.it/content/concorsi",
        "type": "generic",
    },
    {
        "name": "Gazzetta Ufficiale",
        "url": "https://www.gazzettaufficiale.it/ricerca/concorsi/ricercaAvanzata?q=radiologo+abruzzo",
        "type": "gazzetta",
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
    """
    Struttura dello stato health:
    {
      "last_health_check": "2024-01-15",   // data dell'ultimo heartbeat inviato
      "last_alert_date":   "2024-01-15",   // data dell'ultimo alert di errore inviato
      "consecutive_failures": 3,           // run consecutive senza dati
      "total_runs": 42,                    // totale esecuzioni
      "last_successful_scrape": "2024-01-14"  // ultima run con almeno 1 sorgente OK
    }
    """
    defaults = {
        "last_health_check":    "",
        "last_alert_date":      "",
        "consecutive_failures": 0,
        "total_runs":           0,
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
    return date.today().isoformat()   # "YYYY-MM-DD"


# ══════════════════════════════════════════════
# SCRAPING
# ══════════════════════════════════════════════

def is_relevant(text: str) -> bool:
    t = text.lower()
    return any(kw in t for kw in KEYWORDS)


def fetch(url: str) -> BeautifulSoup | None:
    try:
        resp = requests.get(url, headers=HEADERS, timeout=20)
        resp.raise_for_status()
        return BeautifulSoup(resp.text, "lxml")
    except Exception as e:
        log.warning(f"Fetch fallito [{url}]: {e}")
        return None


def scrape_generic(source: dict) -> list[dict]:
    soup = fetch(source["url"])
    if not soup:
        return []
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
    return results


def scrape_inpa(source: dict) -> list[dict]:
    soup = fetch(source["url"])
    if not soup:
        return []
    cards = soup.select(".concorso-card, .bando-item, article, .card")
    if not cards:
        return scrape_generic(source)
    results = []
    for card in cards:
        title_el = card.select_one("h2, h3, h4, .title, .titolo")
        link_el  = card.select_one("a[href]")
        if not title_el or not link_el:
            continue
        title = title_el.get_text(strip=True)
        href  = link_el["href"]
        if not is_relevant(title):
            continue
        full_url = href if href.startswith("http") else f"https://www.inpa.gov.it{href}"
        date_el  = card.select_one(".date, .data, time")
        date_str = date_el.get_text(strip=True) if date_el else datetime.now().strftime("%d/%m/%Y")
        results.append({"title": title, "url": full_url, "source": source["name"], "date": date_str})
    log.info(f"  [InPA] {len(results)} bandi")
    return results


def scrape_gazzetta(source: dict) -> list[dict]:
    soup = fetch(source["url"])
    if not soup:
        return []
    results = []
    for row in soup.select("tr, .risultato, .atto"):
        text    = row.get_text(separator=" ", strip=True)
        link_el = row.select_one("a[href]")
        if not is_relevant(text):
            continue
        if not any(rk in text.lower() for rk in REGION_KEYWORDS):
            continue
        href     = link_el["href"] if link_el else source["url"]
        full_url = href if href.startswith("http") else f"https://www.gazzettaufficiale.it{href}"
        title    = link_el.get_text(strip=True) if link_el else text[:120]
        results.append({"title": title, "url": full_url, "source": source["name"], "date": datetime.now().strftime("%d/%m/%Y")})
    log.info(f"  [Gazzetta] {len(results)} bandi")
    return results


def scrape_source(source: dict) -> list[dict]:
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
    try:
        await bot.send_message(
            chat_id=CHAT_ID,
            text=text,
            parse_mode=ParseMode.MARKDOWN,
            disable_web_page_preview=True,
        )
    except Exception as e:
        log.error(f"Errore invio Telegram: {e}")


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
    """
    Regole:
    1. Health check (report "tutto OK"): inviato una volta ogni 24h
       solo se ALMENO UNA sorgente ha risposto correttamente.
    2. Alert errore: inviato una volta ogni 24h se NESSUNA sorgente
       ha risposto. Non genera spam: max 1 messaggio al giorno.
    """
    today = today_str()
    all_failed = (sources_ok == 0)

    if all_failed:
        # ── Caso errore ────────────────────────────────────────────
        state["consecutive_failures"] += 1

        if state["last_alert_date"] != today:
            # Non abbiamo ancora mandato l'alert oggi → lo mandiamo
            log.warning(f"Nessuna sorgente raggiungibile. Invio alert (run #{state['consecutive_failures']})")
            await send_msg(bot, fmt_alert(state, failed_sources))
            state["last_alert_date"] = today
        else:
            log.info("Alert già inviato oggi — skip (anti-spam)")

    else:
        # ── Caso OK ───────────────────────────────────────────────
        state["consecutive_failures"] = 0
        state["last_successful_scrape"] = today

        if state["last_health_check"] != today:
            # Non abbiamo ancora inviato il daily health check → lo inviamo
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

    # ── Scraping ──────────────────────────────
    new_count      = 0
    sources_ok     = 0
    failed_sources = []

    for source in SOURCES:
        log.info(f"→ Controllo: {source['name']}")
        try:
            concorsi = scrape_source(source)
            if concorsi is not None:  # None = fetch fallito, [] = fetch OK ma niente di rilevante
                sources_ok += 1
        except Exception as e:
            log.error(f"  Errore scraping: {e}")
            concorsi = None

        if concorsi is None:
            failed_sources.append(source["name"])
            await asyncio.sleep(2)
            continue

        for c in concorsi:
            cid = make_id(c["title"], c["url"])
            if cid not in seen:
                await send_msg(bot, fmt_bando(c))
                seen.add(cid)
                new_count += 1
                await asyncio.sleep(1.5)

        await asyncio.sleep(2)

    log.info(f"Scraping completato — sorgenti OK: {sources_ok}/{len(SOURCES)}, nuovi bandi: {new_count}")

    # ── Health check / Alert ──────────────────
    await handle_health_and_alerts(
        bot=bot,
        state=state,
        sources_ok=sources_ok,
        sources_total=len(SOURCES),
        failed_sources=failed_sources,
        new_count=new_count,
    )

    # ── Salva stato ───────────────────────────
    save_seen(seen)
    save_health(state)
    log.info("═══ Fine run ═══")


if __name__ == "__main__":
    asyncio.run(main())
