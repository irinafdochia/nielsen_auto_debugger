#!/usr/bin/env python3
"""
Auto Debug Nielsen - Entry point

Uso:
  python3 main.py                    # usa config.yaml nella stessa cartella
  python3 main.py --no-mail          # non invia mail alla fine
  python3 main.py --url <url>        # testa una singola URL (debug)
"""

import sys
import os
import asyncio
import argparse
import yaml

# Aggiunge src/ al path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))

import excel_parser
import tlh_matcher
import playwright_checker
import report_builder
import mailer


def load_config(config_path="config.yaml"):
    with open(config_path) as f:
        return yaml.safe_load(f)


def run(args, config):
    segnalazioni_path = os.path.join(
        os.path.dirname(__file__),
        config.get('segnalazioni_path', 'untracked')
    )
    output_path = os.path.join(
        os.path.dirname(__file__),
        config.get('output_path', 'untracked/output')
    )
    concurrency  = config.get('playwright_concurrency', 3)
    timeout_sec  = config.get('playwright_timeout', 30)

    # ----------------------------------------------------------------
    # 1. Parsing Excel
    # ----------------------------------------------------------------
    print(f"\n[1/4] Parsing Excel da: {segnalazioni_path}")
    segnalazioni = excel_parser.find_segnalazioni(segnalazioni_path)
    excel_parser.stampa_riepilogo(segnalazioni)

    if not segnalazioni:
        print("Nessuna segnalazione trovata. Controlla il path in config.yaml.")
        return

    # Escludi URL mobile: il check browser non è applicabile (impossibile simulare app native)
    n_before = len(segnalazioni)
    segnalazioni = [s for s in segnalazioni if 'mobile' not in s['tipo']]
    n_skipped_mobile = n_before - len(segnalazioni)
    if n_skipped_mobile:
        print(f"  Escluse {n_skipped_mobile} segnalazioni mobile (solo desktop supportato)")

    if not segnalazioni:
        print("Nessuna segnalazione desktop trovata.")
        return

    by_url = excel_parser.get_unique_urls(segnalazioni)
    all_unique_urls  = list(by_url.keys())
    gedi_unique_urls = [u for u in all_unique_urls if any(s['is_gedi'] for s in by_url[u])]

    # Pattern URL non tracciabili (login, API, funnel, ecc.)
    # Appaiono nel report con nota ma non vengono analizzate con Playwright
    skip_patterns = config.get('skip_url_patterns', [
        '/api/', '/login', '/account/', '/checkout', '/registr',
        '/codici-sconto', '/abbonamento/', '/feed/',
    ])

    def _skip_reason(url):
        for p in skip_patterns:
            if p in url:
                return f"URL non tracciabile ({p.strip('/')}): escludere dal mapping TLH"
        return None

    url_skip_reasons = {u: _skip_reason(u) for u in all_unique_urls if _skip_reason(u)}
    if url_skip_reasons:
        print(f"  {len(url_skip_reasons)} URL escluse dall'analisi (login/api/funnel)")

    # ----------------------------------------------------------------
    # 2. TLH matching (solo URL GEDI, incluse quelle strane — utile sapere se sono nel mapping)
    # ----------------------------------------------------------------
    print(f"\n[2/4] TLH matching per {len(gedi_unique_urls)} URL GEDI...")
    if gedi_unique_urls:
        tlh_results = tlh_matcher.check_urls_batch(gedi_unique_urls, verbose=True)
    else:
        tlh_results = {}
        print("  (nessuna URL GEDI)")

    # ----------------------------------------------------------------
    # 3. Playwright check (tutte le URL tranne quelle pre-filtrate)
    # ----------------------------------------------------------------
    urls_for_playwright = [u for u in all_unique_urls if u not in url_skip_reasons]
    print(f"\n[3/4] Playwright check per {len(urls_for_playwright)} URL (concorrenza: {concurrency})...")
    playwright_results = asyncio.run(
        playwright_checker.check_urls_batch(
            urls_for_playwright,
            concurrency=concurrency,
            timeout_sec=timeout_sec,
            verbose=True,
        )
    )
    # Aggiunge i risultati pre-compilati per le URL saltate
    for u, reason in url_skip_reasons.items():
        playwright_results[u] = {
            'tlh_loaded': False, 'tlh_url': None,
            'sdk_loaded': False, 'sdk_url': None,
            'ping_sent':  False, 'ping_url': None,
            'error': None, 'final_url': None, 'http_status': None,
            'http_to_https': False, 'skipped_reason': reason,
        }

    # ----------------------------------------------------------------
    # 4. Report + Mail
    # ----------------------------------------------------------------
    print(f"\n[4/4] Generazione report...")
    gedi_path, manzoni_path = report_builder.build_reports(
        segnalazioni, tlh_results, playwright_results, output_path
    )

    if not args.no_mail:
        testo = mailer.build_testo_mail(segnalazioni, tlh_results, playwright_results)
        print(f"\n[mail] Invio report...")
        mailer.invia_report([gedi_path, manzoni_path], testo, config)
    else:
        print("\n[mail] Skip invio mail (--no-mail)")

    print("\nFatto!")


def run_single_url(url, config):
    """Modalità debug: testa una singola URL."""
    print(f"\n=== TEST SINGOLA URL ===\n{url}\n")

    print("[TLH]")
    tlh = tlh_matcher.check_url(url)
    print(f"  Matched       : {tlh['matched']}")
    print(f"  Brand         : {tlh['brand']}")
    print(f"  Has Nielsen   : {tlh['has_nielsen']}")
    print(f"  Nielsen static: {tlh['nielsen_static']}")
    if tlh['error']:
        print(f"  Errore        : {tlh['error']}")

    print("\n[Playwright]")
    timeout_sec = config.get('playwright_timeout', 30)
    pw = asyncio.run(playwright_checker.check_url(url, timeout_sec=timeout_sec))
    print(f"  SDK caricato  : {pw['sdk_loaded']}")
    print(f"  Ping inviato  : {pw['ping_sent']}")
    if pw['sdk_url']:
        print(f"  SDK URL       : {pw['sdk_url']}")
    if pw['ping_url']:
        print(f"  Ping URL      : {pw['ping_url']}")
    if pw['error']:
        print(f"  Errore        : {pw['error']}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Auto Debug Nielsen")
    parser.add_argument("--no-mail", action="store_true", help="Non invia la mail finale")
    parser.add_argument("--url",           type=str,            help="Testa una singola URL (debug)")
    parser.add_argument("--config",        type=str, default="config.yaml", help="Path del file di config")
    args = parser.parse_args()

    config = load_config(args.config)

    if args.url:
        run_single_url(args.url, config)
    else:
        run(args, config)
