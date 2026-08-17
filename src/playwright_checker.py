"""
Verifica con Playwright se una pagina carica il TLH, l'SDK Nielsen e invia il ping.

Metodi di rilevamento:
  - TLH in pagina : ispezione DOM dei tag <script> in <head> (piu' affidabile
                    della network interception perche' funziona anche con script cachati)
  - SDK caricato  : network request a "imrworldwide.com/conf/"
  - Ping inviato  : network request a "imrworldwide.com/cgi-bin/gn"

Il check avviene senza consensare la CMP (session ping rilevabile senza consenso).
"""

import asyncio
import random
from playwright.async_api import async_playwright, TimeoutError as PWTimeout
from playwright_stealth import Stealth

# Istanza riusata su tutti i check: locale italiano, piattaforma macOS (coerente con UA desktop)
_STEALTH = Stealth(
    navigator_languages_override=('it-IT', 'it', 'en'),
    navigator_platform_override='MacIntel',
)
# Variante mobile: piattaforma iPhone (coerente con UA iPhone)
_STEALTH_MOBILE = Stealth(
    navigator_languages_override=('it-IT', 'it', 'en'),
    navigator_platform_override='iPhone',
)

SDK_PATTERN           = "imrworldwide.com/conf/"
PING_PATTERN          = "imrworldwide.com/cgi-bin/gn"
NIELSEN_STATIC_PATTERN = "gedistatic.it/corporate/nielsen/"

# Script JS da eseguire nel browser per verificare la presenza del tag TLH nel DOM.
# Rispecchia la logica di _checkTlhPresence() usata internamente da GEDI.
_TLH_DOM_CHECK_JS = """
() => {
    const names = [
        'adsetup.js', 'adsetup_cmp.js', 'adsetup_pcmp.js',
        'adsetup_pcmp_video.js', 'adsetup_webview.js',
        'tlh.js', 'tlh_webview.js'
    ];
    for (const script of document.querySelectorAll('head script')) {
        // data-tbdelay-src: lazy loading via TurboJS (type="tuurbo/javascript")
        const src = script.src || script.getAttribute('data-tbdelay-src') || '';
        for (const name of names) {
            if (src.includes(name)) {
                return src;
            }
        }
    }
    return null;
}
"""


async def check_url(url, timeout_sec=30, observation_sec=5, mobile=False, debug=False):
    """
    Apre l'URL con Playwright e verifica TLH, SDK e ping Nielsen.

    Restituisce:
    {
        'tlh_loaded':  bool,
        'tlh_url':     str | None,   # src del tag <script> TLH trovato nel DOM
        'sdk_loaded':  bool,
        'sdk_url':     str | None,   # URL della request SDK intercettata
        'ping_sent':   bool,
        'ping_url':    str | None,   # URL della request ping intercettata
        'nielsen_mapping_loaded': bool,
        'nielsen_mapping_url':    str | None,   # URL del bundle nielsen_static_mapping_*.js intercettata
        'error':       str | None,
        'final_url':   str | None,   # valorizzato se c'e stato un redirect
        'http_status': int | None,   # valorizzato se status >= 400
    }
    """
    result = {
        'tlh_loaded': False, 'tlh_url': None,
        'sdk_loaded': False, 'sdk_url': None, 'sdk_appid_invalid': False, 'sdk_count': 0,
        'ping_sent':  False, 'ping_url': None, 'ping_count': 0,
        'nielsen_mapping_loaded': False, 'nielsen_mapping_url': None,
        'error': None, 'final_url': None, 'http_status': None,
        'http_to_https': False,
    }

    # URL http://: Nielsen traccia sempre la versione HTTPS — skippa l'analisi
    if url.startswith('http://'):
        result['http_to_https'] = True
        return result

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=["--disable-blink-features=AutomationControlled"],
        )
        if mobile:
            device = p.devices["iPhone 14"]
            context = await browser.new_context(
                **device,
                ignore_https_errors=True,
                locale="it-IT",
                extra_http_headers={"Accept-Language": "it-IT,it;q=0.9,en;q=0.8"},
            )
        else:
            context = await browser.new_context(
                ignore_https_errors=True,
                user_agent=(
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0.0.0 Safari/537.36"
                ),
                viewport={"width": 1280, "height": 800},
                locale="it-IT",
                extra_http_headers={"Accept-Language": "it-IT,it;q=0.9,en;q=0.8"},
            )
        # Rimuove navigator.webdriver prima che la pagina carichi (principale segnale anti-bot)
        await context.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
        )
        page = await context.new_page()
        stealth = _STEALTH_MOBILE if mobile else _STEALTH
        await stealth.apply_stealth_async(page)

        if debug:
            def _on_console(msg):
                if msg.type not in ("error", "warning"):
                    return
                text = msg.text
                if "TLH_RT_Debugger" in text:
                    return  # noise: RT debugger non attivo per questa testata
                print(f"    [JS {msg.type.upper()}] {text[:120]}")
            page.on("console", _on_console)
            page.on("pageerror", lambda err: print(f"    [JS EXCEPTION] {str(err)[:120]}"))

        sdk_event  = asyncio.Event()
        ping_event = asyncio.Event()

        def on_request(request):
            req_url = request.url
            if debug and any(p in req_url for p in ('imrworldwide', 'gedistatic', 'adsetup', 'tlh.js')):
                print(f"    [DEBUG request] {req_url[:120]}")
            if NIELSEN_STATIC_PATTERN in req_url:
                if not result['nielsen_mapping_loaded']:
                    result['nielsen_mapping_loaded'] = True
                    result['nielsen_mapping_url'] = req_url
            if SDK_PATTERN in req_url:
                result['sdk_count'] += 1
                if not result['sdk_loaded']:
                    result['sdk_loaded'] = True
                    result['sdk_url'] = req_url
                    appid = req_url.split('/conf/')[-1].split('.js')[0].split('?')[0].split('#')[0]
                    result['sdk_appid_invalid'] = (appid == 'undefined' or not appid)
                    sdk_event.set()
            if PING_PATTERN in req_url:
                result['ping_count'] += 1
                if not result['ping_sent']:
                    result['ping_sent'] = True
                    result['ping_url'] = req_url
                    ping_event.set()

        page.on("request", on_request)

        try:
            response = await page.goto(url, timeout=timeout_sec * 1000, wait_until="domcontentloaded")

            # Rileva redirect (trailing slash ignorata per evitare falsi positivi)
            final_url = page.url
            if final_url.rstrip('/') != url.rstrip('/'):
                result['final_url'] = final_url

            # Rileva errori HTTP
            if response and response.status >= 400:
                result['http_status'] = response.status

            # URL HTTP che redirige su HTTPS: non gestibile lato TLH, skippa analisi
            if url.startswith('http://') and page.url.startswith('https://'):
                result['http_to_https'] = True
            else:
                # Verifica TLH tramite DOM inspection (logica _checkTlhPresence di GEDI)
                try:
                    tlh_src = await page.evaluate(_TLH_DOM_CHECK_JS)
                    if tlh_src:
                        result['tlh_loaded'] = True
                        result['tlh_url'] = tlh_src
                except Exception:
                    pass  # pagina crashata o JS bloccato: tlh_loaded rimane False


                if observation_sec >= 30:
                    # Finestra lunga (es. 30s per Errore 22): aspetta l'intero intervallo
                    # e raccoglie tutti i ping che arrivano, come fa PwC nel check semi-statico.
                    await asyncio.sleep(observation_sec)
                else:
                    # Fast path event-driven: esce appena arriva SDK+ping, o allo scadere
                    # di observation_sec (default 10s — alcuni siti caricano Nielsen dopo 6-8s).
                    try:
                        await asyncio.wait_for(sdk_event.wait(), timeout=float(observation_sec))
                    except asyncio.TimeoutError:
                        pass
                    if result['sdk_loaded']:
                        try:
                            await asyncio.wait_for(ping_event.wait(), timeout=5.0)
                        except asyncio.TimeoutError:
                            pass

        except PWTimeout:
            result['error'] = f"Timeout ({timeout_sec}s)"
        except Exception as e:
            error_str = str(e).split('\n')[0][:200]
            # URL http:// che il server rifiuta sulla porta 80 (reset/refused prima del redirect):
            # trattiamo come http_to_https perché il comportamento corretto è escluderle da Audicom
            if url.startswith('http://') and any(
                sig in error_str for sig in ('ERR_CONNECTION_RESET', 'ERR_CONNECTION_REFUSED')
            ):
                result['http_to_https'] = True
            else:
                result['error'] = error_str
        finally:
            await browser.close()

    return result


async def check_urls_batch(urls, concurrency=3, timeout_sec=30, observation_sec=5, verbose=True, mobile_urls=None):
    """
    Controlla una lista di URL in parallelo con un limite di concorrenza.
    Restituisce un dict { url: result }.

    mobile_urls: set di URL da testare con emulazione mobile (iPhone 14).
                 Le URL non presenti nel set usano il context desktop standard.
    """
    semaphore  = asyncio.Semaphore(concurrency)
    results    = {}
    total      = len(urls)
    done       = [0]
    mobile_set = set(mobile_urls) if mobile_urls else set()

    async def check_one(url):
        async with semaphore:
            # Jitter: staggera l'avvio dei browser per evitare burst simultanei
            await asyncio.sleep(random.uniform(0.5, 2.0))

            # Retry su ERR_CONNECTION_RESET con backoff lineare (5s, 10s)
            max_retries = 2
            for attempt in range(max_retries + 1):
                res = await check_url(url, timeout_sec=timeout_sec, observation_sec=observation_sec, mobile=(url in mobile_set))
                if res.get('error') and 'ERR_CONNECTION_RESET' in res['error'] and attempt < max_retries:
                    wait = 5 * (attempt + 1)
                    if verbose:
                        print(f"  [retry {attempt + 1}/{max_retries}] ERR_CONNECTION_RESET — attendo {wait}s  {url[:60]}")
                    await asyncio.sleep(wait)
                    continue
                break

            results[url] = res
            done[0] += 1
            if verbose:
                tlh  = "✓" if res['tlh_loaded'] else "✗"
                sdk  = "✓" if res['sdk_loaded'] else "✗"
                ping = "✓" if res['ping_sent']  else "✗"
                err  = f" [{res['error'][:40]}]" if res['error'] else ""
                print(f"  [{done[0]}/{total}] TLH:{tlh} SDK:{sdk} Ping:{ping}{err}  {url[:70]}")

    await asyncio.gather(*[check_one(url) for url in urls])
    return results
