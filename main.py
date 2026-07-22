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
from urllib.parse import urlparse

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

    by_url = excel_parser.get_unique_urls(segnalazioni)
    all_unique_urls  = list(by_url.keys())
    gedi_unique_urls = [u for u in all_unique_urls if any(s['is_gedi'] for s in by_url[u])]

    # ----------------------------------------------------------------
    # 2. TLH matching (solo URL GEDI)
    # ----------------------------------------------------------------
    print(f"\n[2/4] TLH matching per {len(gedi_unique_urls)} URL GEDI...")
    if gedi_unique_urls:
        tlh_results = tlh_matcher.check_urls_batch(gedi_unique_urls, verbose=True)
    else:
        tlh_results = {}
        print("  (nessuna URL GEDI)")

    # ----------------------------------------------------------------
    # 3. Playwright check
    # ----------------------------------------------------------------

    # Phase 3a: homepage probe per ogni dominio GEDI unico.
    # Se l'homepage non mostra SDK (senza errori ne redirect), tutte le URL
    # anomale di quel dominio ereditano sdk=No, ping=No senza aprire il browser.
    gedi_domain_homepage = {}
    for u in gedi_unique_urls:
        p = urlparse(u)
        if p.netloc not in gedi_domain_homepage:
            gedi_domain_homepage[p.netloc] = f"{p.scheme}://{p.netloc}/"

    homepage_probe_urls = list(set(gedi_domain_homepage.values()))
    print(f"\n[3a/4] Homepage probe per {len(homepage_probe_urls)} domini GEDI"
          f" (concorrenza: {concurrency})...")
    homepage_probe_results = asyncio.run(
        playwright_checker.check_urls_batch(
            homepage_probe_urls, concurrency=concurrency,
            timeout_sec=timeout_sec, verbose=True,
        )
    )

    # Domini senza Nielsen in homepage: no SDK, nessun errore tecnico, nessun redirect
    domains_no_nielsen = set()
    for netloc, hp_url in gedi_domain_homepage.items():
        res = homepage_probe_results.get(hp_url, {})
        if not res.get('sdk_loaded') and not res.get('error') and not res.get('final_url'):
            domains_no_nielsen.add(netloc)

    # Phase 3b: check sulle URL anomale
    playwright_results = {}
    urls_for_pw = []

    for u in all_unique_urls:
        p = urlparse(u)
        is_gedi_url = any(s['is_gedi'] for s in by_url.get(u, []))

        if u in homepage_probe_results:
            # URL gia controllata nella probe: riusa il risultato
            playwright_results[u] = homepage_probe_results[u]
        elif is_gedi_url and p.netloc in domains_no_nielsen:
            playwright_results[u] = {
                'sdk_loaded': False, 'ping_sent': False,
                'sdk_url': None, 'ping_url': None,
                'error': None, 'final_url': None, 'http_status': None,
                'homepage_no_sdk': True,
            }
        else:
            urls_for_pw.append(u)

    n_inherited = len(playwright_results)
    print(f"\n[3b/4] Playwright check per {len(urls_for_pw)} URL"
          f" (concorrenza: {concurrency}, {n_inherited} ereditate da homepage probe)...")
    actual_results = asyncio.run(
        playwright_checker.check_urls_batch(
            urls_for_pw, concurrency=concurrency,
            timeout_sec=timeout_sec, verbose=True,
        )
    )
    playwright_results.update(actual_results)

    # ----------------------------------------------------------------
    # 4. Report + Mail
    # ----------------------------------------------------------------
    print(f"\n[4/4] Generazione report...")
    report_path = report_builder.build_report(
        segnalazioni, tlh_results, playwright_results, output_path
    )

    if not args.no_mail:
        testo = mailer.build_testo_mail(segnalazioni, tlh_results, playwright_results)
        print(f"\n[mail] Invio report...")
        mailer.invia_report(report_path, testo, config)
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
