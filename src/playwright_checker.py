"""
Verifica con Playwright se una pagina web carica l'SDK Nielsen e invia il ping.

Cosa si cerca nel network:
  - SDK caricato : URL contiene "imrworldwide.com/conf/"
  - Ping inviato : URL contiene "imrworldwide.com/cgi-bin/gn"

Il check avviene senza consensare la CMP (come da specifiche).
"""

import asyncio
from playwright.async_api import async_playwright, TimeoutError as PWTimeout

# Pattern di rete da intercettare
SDK_PATTERN  = "imrworldwide.com/conf/"
PING_PATTERN = "imrworldwide.com/cgi-bin/gn"


async def check_url(url, timeout_sec=30):
    """
    Apre l'URL con Playwright e verifica l'erogazione Nielsen.

    Restituisce:
    {
        'sdk_loaded':  bool,
        'ping_sent':   bool,
        'sdk_url':     str | None,   # URL effettiva dell'SDK intercettata
        'ping_url':    str | None,   # URL effettiva del ping intercettata
        'error':       str | None,
    }
    """
    result = {
        'sdk_loaded': False, 'ping_sent': False,
        'sdk_url': None, 'ping_url': None,
        'error': None, 'final_url': None, 'http_status': None,
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

            # Rileva redirect (confronto senza trailing slash per evitare falsi positivi)
            final_url = page.url
            if final_url.rstrip('/') != url.rstrip('/'):
                result['final_url'] = final_url

            # Rileva errori HTTP
            if response and response.status >= 400:
                result['http_status'] = response.status

            # Aspetta al massimo 5s che arrivi l'SDK.
            # Se non arriva, il ping non arriverà mai → usciamo subito.
            # Se arriva, aspetta altri 5s che arrivi il ping.
            try:
                await asyncio.wait_for(sdk_event.wait(), timeout=5.0)
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
    results = {}
    total = len(urls)
    done_count = [0]

    async def check_one(url):
        async with semaphore:
            res = await check_url(url, timeout_sec=timeout_sec)
            results[url] = res
            done_count[0] += 1
            if verbose:
                sdk  = "✓" if res['sdk_loaded'] else "✗"
                ping = "✓" if res['ping_sent']  else "✗"
                err  = f" [{res['error'][:40]}]" if res['error'] else ""
                print(f"  [{done_count[0]}/{total}] SDK:{sdk} Ping:{ping}{err}  {url[:70]}")

    tasks = [check_one(url) for url in urls]
    await asyncio.gather(*tasks)

    return results
