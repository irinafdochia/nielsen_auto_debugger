"""
Wrapper Python per il Node.js helper che fa matching TLH.

Per ogni URL (solo siti interni GEDI) chiama:
  node tlh_helper/check_url.js <url>

e restituisce:
  {
    'matched':          bool,   # esiste una config TLH per questa URL?
    'brand':            str,    # es. "repubblica"
    'active_host':      str,    # es. "repubblica"
    'has_nielsen':      bool,   # la config ha il campo nielsenStatic?
    'nielsen_static':   str,    # URL del file JS Nielsen (o None)
    'error':            str,    # eventuale messaggio di errore
  }
"""

import subprocess
import json
import os

# Path assoluto del helper Node.js (relativo a questo file → due livelli su)
_BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_HELPER_PATH = os.path.join(_BASE_DIR, 'tlh_helper', 'check_url.js')


def check_url(url):
    """
    Controlla una singola URL. Usa check_urls_batch internamente.
    """
    results = check_urls_batch([url], verbose=False)
    return results.get(url, _error_result("URL non trovata nel risultato"))


def check_urls_batch(urls, verbose=True):
    """
    Controlla tutte le URL in una singola chiamata Node.js (molto più veloce).
    Restituisce un dict { url: result }.
    """
    if not urls:
        return {}

    if verbose:
        print(f"  TLH: invio {len(urls)} URL al helper Node.js...")

    try:
        input_data = "\n".join(urls)
        result = subprocess.run(
            ['node', _HELPER_PATH, '--stdin'],
            input=input_data,
            capture_output=True,
            text=True,
            timeout=120,  # 2 minuti per tutto il batch
        )

        if result.returncode != 0 and not result.stdout.strip():
            err = f"Node.js exit {result.returncode}: {result.stderr.strip()[:200]}"
            return {url: _error_result(err) for url in urls}

        raw = json.loads(result.stdout.strip())

        # raw è un dict { url: { matched, brand, ... } }
        results = {}
        for url in urls:
            data = raw.get(url, {})
            if 'error' in data:
                results[url] = _error_result(data['error'])
            else:
                results[url] = {
                    'matched':        data.get('matched', False),
                    'brand':          data.get('brand'),
                    'active_host':    data.get('activeHost'),
                    'has_nielsen':    data.get('hasNielsenConfig', False),
                    'nielsen_static': data.get('nielsenStaticUrl'),
                    'error':          None,
                }

        if verbose:
            matched = sum(1 for r in results.values() if r.get('matched'))
            has_n   = sum(1 for r in results.values() if r.get('has_nielsen'))
            print(f"  TLH: {matched}/{len(urls)} con config TLH, {has_n}/{len(urls)} con config Nielsen")

        return results

    except subprocess.TimeoutExpired:
        return {url: _error_result("Timeout (120s)") for url in urls}
    except json.JSONDecodeError as e:
        return {url: _error_result(f"JSON decode error: {e}") for url in urls}
    except FileNotFoundError:
        return {url: _error_result("Node.js non trovato nel PATH") for url in urls}
    except Exception as e:
        return {url: _error_result(str(e)) for url in urls}


def _error_result(msg):
    return {
        'matched':        False,
        'brand':          None,
        'active_host':    None,
        'has_nielsen':    False,
        'nielsen_static': None,
        'error':          msg,
    }
