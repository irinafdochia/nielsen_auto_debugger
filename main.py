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

    if not segnalazioni:
        print("Nessuna segnalazione trovata. Controlla il path in config.yaml.")
        return

    # --- Filtro --tipo ---
    if args.tipo == "gedi":
        segnalazioni = [s for s in segnalazioni if s['is_gedi']]
        print(f"  --tipo gedi: solo siti interni GEDI")
    elif args.tipo == "manzoni":
        segnalazioni = [s for s in segnalazioni if not s['is_gedi']]
        print(f"  --tipo manzoni: solo editori terzi Manzoni")
    if args.tipo and not segnalazioni:
        print(f"  Nessuna segnalazione trovata per --tipo {args.tipo}.")
        return

    # --- Filtro --domain ---
    if args.domain:
        n_prima = len(set(s['url'] for s in segnalazioni))
        segnalazioni = [s for s in segnalazioni if args.domain in s['url']]
        n_dopo = len(set(s['url'] for s in segnalazioni))
        print(f"  --domain '{args.domain}': {n_prima} → {n_dopo} URL uniche")
        if not segnalazioni:
            print(f"  Nessuna URL trovata per il dominio '{args.domain}'.")
            return

    excel_parser.stampa_riepilogo(segnalazioni)

    by_url = excel_parser.get_unique_urls(segnalazioni)
    all_unique_urls  = list(by_url.keys())

    # --- Filtro --limit ---
    if args.limit and args.limit < len(all_unique_urls):
        all_unique_urls = all_unique_urls[:args.limit]
        limited_set     = set(all_unique_urls)
        segnalazioni    = [s for s in segnalazioni if s['url'] in limited_set]
        by_url          = {u: v for u, v in by_url.items() if u in limited_set}
        print(f"  --limit {args.limit}: analisi limitata alle prime {len(all_unique_urls)} URL")

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
                if p == '/corporate/privacy':
                    return "URL cookie/privacy policy interna GEDI"
                return f"URL di servizio ({p.strip('/')}): da verificare se necessaria la misurazione Nielsen"
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
    # 3. Playwright check (tutte le URL — nessuna esclusa)
    #    Le URL Errore 22 usano finestra da 30s (metodologia PwC semi-statico),
    #    tutte le altre usano il fast path event-driven (5s).
    # ----------------------------------------------------------------
    errore22_urls = [u for u in all_unique_urls if any(s.get('errore') == 'Errore 22' for s in by_url[u])]
    other_urls    = [u for u in all_unique_urls if u not in set(errore22_urls)]

    print(f"\n[3/4] Playwright check per {len(all_unique_urls)} URL (concorrenza: {concurrency})...")
    if errore22_urls:
        print(f"       {len(other_urls)} URL normali (5s) + {len(errore22_urls)} URL Errore 22 (finestra 30s)")

    async def _run_playwright():
        results = {}
        if other_urls:
            r = await playwright_checker.check_urls_batch(
                other_urls,
                concurrency=concurrency,
                timeout_sec=timeout_sec,
                ping_observation_sec=5,
                verbose=True,
            )
            results.update(r)
        if errore22_urls:
            print(f"\n  [Errore 22] Avvio finestra 30s per {len(errore22_urls)} URL...")
            r = await playwright_checker.check_urls_batch(
                errore22_urls,
                concurrency=concurrency,
                timeout_sec=timeout_sec + 30,
                ping_observation_sec=30,
                verbose=True,
            )
            results.update(r)
        return results

    playwright_results = asyncio.run(_run_playwright())
    # Per le URL di servizio aggiunge solo una nota informativa (senza bloccare il check)
    for u, reason in url_skip_reasons.items():
        if u in playwright_results:
            playwright_results[u]['service_note'] = reason

    # ----------------------------------------------------------------
    # 4. Report + Mail
    # ----------------------------------------------------------------
    print(f"\n[4/4] Generazione report...")
    gedi_path, manzoni_path = report_builder.build_reports(
        segnalazioni, tlh_results, playwright_results, output_path, tipo=args.tipo
    )

    if not args.no_mail:
        testo = mailer.build_testo_mail(segnalazioni, tlh_results, playwright_results)
        allegati = [p for p in [gedi_path, manzoni_path] if p]
        print(f"\n[mail] Invio report...")
        mailer.invia_report(allegati, testo, config)
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
    parser.add_argument("--url",    type=str, help="Testa una singola URL (debug)")
    parser.add_argument("--config", type=str, default="config.yaml", help="Path del file di config")
    parser.add_argument("--domain", type=str, help="Limita l'analisi alle URL che contengono questo dominio (es. repubblica.it)")
    parser.add_argument("--limit",  type=int, help="Limita l'analisi alle prime N URL uniche")
    parser.add_argument("--tipo",   choices=["gedi", "manzoni"], help="Analizza solo siti interni GEDI ('gedi') o solo editori terzi Manzoni ('manzoni')")
    args = parser.parse_args()

    config = load_config(args.config)

    if args.url:
        run_single_url(args.url, config)
    else:
        run(args, config)
