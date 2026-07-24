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
from playwright.async_api import async_playwright, TimeoutError as PWTimeout

SDK_PATTERN  = "imrworldwide.com/conf/"
PING_PATTERN = "imrworldwide.com/cgi-bin/gn"

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
        for (const name of names) {
            if (script.src && script.src.includes(name)) {
                return script.src;
            }
        }
    }
    return null;
}
"""


async def check_url(url, timeout_sec=30):
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
        'error':       str | None,
        'final_url':   str | None,   # valorizzato se c'e stato un redirect
        'http_status': int | None,   # valorizzato se status >= 400
    }
    """
    result = {
        'tlh_loaded': False, 'tlh_url': None,
        'sdk_loaded': False, 'sdk_url': None,
        'ping_sent':  False, 'ping_url': None,
        'error': None, 'final_url': None, 'http_status': None,
        'http_to_https': False,
    }

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            ignore_https_errors=True,
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
        )
        page = await context.new_page()

        sdk_event  = asyncio.Event()
        ping_event = asyncio.Event()

        def on_request(request):
            req_url = request.url
            if SDK_PATTERN in req_url and not result['sdk_loaded']:
                result['sdk_loaded'] = True
                result['sdk_url'] = req_url
                sdk_event.set()
            if PING_PATTERN in req_url and not result['ping_sent']:
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

                # Aspetta SDK (max 5s); se arriva, aspetta ping (max 5s aggiuntivi)
                try:
                    await asyncio.wait_for(sdk_event.wait(), timeout=5.0)
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
            result['error'] = str(e)[:200]
        finally:
            await browser.close()

    return result


async def check_urls_batch(urls, concurrency=3, timeout_sec=30, verbose=True):
    """
    Controlla una lista di URL in parallelo con un limite di concorrenza.
    Restituisce un dict { url: result }.
    """
    semaphore = asyncio.Semaphore(concurrency)
    results   = {}
    total     = len(urls)
    done      = [0]

    async def check_one(url):
        async with semaphore:
            res = await check_url(url, timeout_sec=timeout_sec)
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
