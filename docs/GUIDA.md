# Auto Debug Nielsen — Guida al progetto

Questo documento spiega **cosa fa ogni file, come interagiscono tra loro, e come si usa
il sistema** — pensato per chi si avvicina al progetto per la prima volta.

---

## Problema che risolve

Ogni mese Audicom invia una cartella con file Excel che segnalano anomalie Nielsen
su un elenco di URL. Il processo manuale di verifica (aprire ogni URL, guardare se
l'SDK Nielsen si carica, se il ping parte) era fatto a mano. Questo sistema lo
automatizza completamente.

---

## Schema del flusso

```
┌─────────────────────────────────────────────────────────────┐
│                          main.py                            │
│                       (entry point)                         │
└──────────────┬──────────────┬───────────────┬──────────────┘
               │              │               │
        [1] Parsing     [2] TLH check    [3] Playwright
               │              │               │
       excel_parser.py  tlh_matcher.py  playwright_checker.py
               │              │               │
               │        (usa Node.js)    (apre browser)
               │              │               │
               └──────────────┴───────────────┘
                                   │
                            [4] Report Excel
                                   │
                          report_builder.py
                                   │
                            [5] Invio mail
                                   │
                              mailer.py
```

---

## Struttura dei file

```
Auto Debug NIELSEN/
│
├── main.py                    entry point, gestisce la sequenza e gli argomenti CLI
├── config.yaml                tutto ciò che si configura (path, mail, timeouts)
├── requirements.txt           dipendenze Python (pip install -r requirements.txt)
│
├── tlh_helper/
│   └── check_url.js           helper Node.js: scarica il TLH da prod e matcha le URL
│
├── src/
│   ├── excel_parser.py        scansiona le cartelle Audicom, estrae le URL dagli Excel
│   ├── tlh_matcher.py         chiama il helper Node.js e restituisce i risultati in Python
│   ├── playwright_checker.py  apre le pagine con Chromium e intercetta le request Nielsen
│   ├── report_builder.py      genera il file Excel di output con colori e riepilogo
│   └── mailer.py              invia il report via SMTP con allegato
│
├── segnalazioni/              gitignored — metti qui la cartella segnalazioni Audicom mensile
│   └── README.md              istruzioni per i colleghi
│
└── output/                    gitignored — i report Excel generati finiscono qui
    └── README.md
```

---

## Come si usa

```bash
# Setup iniziale (una sola volta)
pip3 install -r requirements.txt
python3 -m playwright install chromium

# Run completo (parsing → TLH → Playwright → report → mail)
python3 main.py

# Run senza inviare la mail
python3 main.py --no-mail

# Testa una singola URL e stampa tutto a console (modalità debug)
python3 main.py --url "https://www.repubblica.it/economia/test.html"

# Usa un file di config diverso
python3 main.py --config altro_config.yaml

# Analizza solo le URL di un dominio specifico (utile per test veloci)
python3 main.py --domain "repubblica.it" --no-mail

# Analizza solo le prime N URL uniche
python3 main.py --limit 20 --no-mail

# Genera solo il report GEDI (siti interni)
python3 main.py --tipo gedi --no-mail

# Genera solo il report Manzoni (editori terzi)
python3 main.py --tipo manzoni --no-mail

# Combinazioni possibili
python3 main.py --tipo gedi --domain "repubblica.it" --limit 10 --no-mail
```

### Flag disponibili

| Flag | Valore | Descrizione |
|---|---|---|
| `--no-mail` | — | Non invia la mail finale |
| `--url` | URL | Testa una singola URL in modalità debug (stampa tutto a console) |
| `--config` | path | Usa un file config diverso da `config.yaml` |
| `--domain` | stringa | Filtra le segnalazioni alle URL che contengono questo dominio |
| `--limit` | N | Analizza solo le prime N URL uniche (dopo gli altri filtri) |
| `--tipo` | `gedi` \| `manzoni` | Genera solo il report per siti interni o solo per editori terzi |

I filtri si applicano nell'ordine: `--tipo` → `--domain` → `--limit`.

---

## Cosa fa ogni file

---

### `main.py` — entry point

Coordina l'intera esecuzione in quattro step sequenziali.

```
[1/4] Parsing Excel       → chiama excel_parser.find_segnalazioni()
                            applica filtri --tipo, --domain, --limit

[2/4] TLH matching        → chiama tlh_matcher.check_urls_batch()  (solo URL GEDI)

[3/4] Playwright check    → tutte le URL passano per Playwright (nessuna esclusa)
                            le URL che matchano skip_url_patterns ricevono una
                            service_note nelle Note del report, ma vengono comunque
                            verificate con SDK/ping
                            le URL Errore 22 usano una finestra di 30s (metodologia PwC);
                            le altre usano il fast path event-driven (5s)

[4/4] Report + mail       → chiama report_builder.build_reports(..., tipo=args.tipo)
                            se --tipo è specificato, genera solo il report corrispondente
                            chiama mailer.invia_report() solo con gli allegati generati
```

Gestisce anche la modalità `--url` per testare una singola URL in debug.

---

### `config.yaml` — configurazione

Il file da modificare ogni mese quando cambia la cartella segnalazioni.

```yaml
segnalazioni_path: segnalazioni/06_2026_GEDI-MANZONI   ← cambia questo ogni mese
output_path: output

playwright_concurrency: 6   ← quante pagine aprire in parallelo (abbassa se crasha)
playwright_timeout: 30      ← secondi di attesa per pagina

skip_url_patterns:          ← URL che ricevono solo una nota, comunque verificate
  - /login
  - /account/
  - /checkout
  - /registr
  - /corporate/privacy      ← nota specifica "URL cookie/privacy policy interna GEDI"
  - /api/

mail:
  mittente: advwebintegration@gedidigital.it
  destinatari:
    - irina.f.dochia@accenture.com
  smtp_host: smtp.office365.com
  smtp_port: 587
  smtp_password: ""          ← metti la password qui o in SMTP_PASSWORD env var
```

---

### `src/excel_parser.py` — parsing degli Excel Audicom

**Input:** cartella root con la struttura Audicom
**Output:** lista di dict, una voce per ogni URL segnalata

Scansiona ricorsivamente la cartella segnalata in `config.yaml`. La struttura delle
cartelle Audicom è sempre:

```
root/
├── GEDI Gruppo Editoriale/        ← siti interni GEDI
│   └── {Testata}/
│       └── {tipo}/                ← semi_statico_desktop / semi_statico_mobile
│           └── file.xlsx
└── {AltroGruppo}/                 ← editori terzi Manzoni
    └── stessa struttura
```

Le cartelle `dinamico` vengono ignorate (contengono PDF, non Excel di anomalie).
Il file `Apps_Report_GEDI.xlsx` nella root viene ignorato (struttura diversa; la
funzione `read_app_report()` è disponibile in `excel_parser.py` ma non viene chiamata
automaticamente — da allegare manualmente alla mail se necessario).

**Nota su semi_statico_mobile:** le URL mobile sono pagine web ottimizzate per
dispositivi mobili, non app native. Il tracciamento Nielsen è URL-based (non dipende
dallo user agent), quindi vengono analizzate con la stessa logica del desktop.

**Formato degli Excel Audicom:**
- Riga 1: descrizione errore (es. `"Zero page views"`)
- Riga 2: vuota
- Righe 3+: URL, una per riga
- Uno sheet per codice errore (`Errore 21`, `Errore 22`, ...)

**Nota importante:** `Errore 21 = Zero page views`, `Errore 22 = Troppi page views`
(il task originale li aveva invertiti — la distinzione corretta è nei dati reali).

Ogni voce in output ha questa forma:
```python
{
    'url':       'https://www.repubblica.it/economia/...',
    'errore':    'Errore 21',
    'descr':     'Zero page views',
    'gruppo':    'GEDI Gruppo Editoriale',
    'testata':   'La Repubblica',
    'tipo':      'semi_statico_desktop',
    'is_gedi':   True,
    'xlsx_path': '/.../.../file.xlsx',
}
```

La stessa URL può apparire più volte (da file Excel diversi). Il metodo
`get_unique_urls()` deduplicazione: restituisce `{ url: [lista_segnalazioni] }`.

---

### `tlh_helper/check_url.js` — matching TLH (Node.js)

**Input:** URL da controllare (passate via stdin, una per riga)
**Output:** JSON `{ url: { matched, brand, hasNielsenConfig, nielsenStaticUrl } }`

Il TLH (Tag Loader Hub) è il file JS di GEDI che decide quale configurazione di
analytics caricare in base all'URL. Contiene centinaia di entry del tipo:
```javascript
tlh_configs.push([
    /^https:\/\/www\.repubblica\.it/,   // regex URL
    false,                               // regex URL video (o false)
    {
        activeBrand: "repubblica",
        nielsenStatic: "https://www.gedistatic.it/corporate/nielsen/nielsen_static_mapping_repubblica.min.js",
        // ...
    }
]);
```

**Perché Node.js e non Python:** le entry usano regex JavaScript nativa, quindi la
soluzione più affidabile è eseguire il codice TLH originale in Node.js invece di
riscrivere il matching in Python.

**Perché non eseguire tutto il TLH:** il file intero crasha in Node.js alla riga ~7041
perché usa API browser (`window.TLH_Utils`, `document`, ecc.) che non esistono fuori
dal browser. La sezione delle `tlh_configs.push()` (righe 14199-25706) è invece
self-contained e si esegue senza problemi con un mock minimale di `window`.

**Performance:** tutte le URL vengono passate in batch via stdin in una sola chiamata
Node.js, invece di avviare un processo per URL.

**Cache:** il file TLH viene scaricato una volta e cachato 12 ore in `$TMPDIR`.
Per forzare il refresh, eliminare `$TMPDIR/tlh_prod_deob_cached.js`.

**URL TLH di produzione:** `https://tlh.gedidigital.it/tlh/js/adsetup_tlh.js`

---

### `src/tlh_matcher.py` — wrapper Python per il helper Node.js

**Input:** lista di URL GEDI
**Output:** dict `{ url: result }` dove `result` ha questa forma:
```python
{
    'matched':        True,
    'brand':          'repubblica',
    'active_host':    'repubblica',
    'has_nielsen':    True,
    'nielsen_static': 'https://www.gedistatic.it/corporate/nielsen/nielsen_static_mapping_repubblica.min.js',
    'error':          None,
}
```

Questo modulo è solo un wrapper: chiama `node tlh_helper/check_url.js --stdin`,
gli passa le URL via stdin, e converte il JSON di output nel formato Python.

**Casi possibili per ogni URL:**
| `matched` | `has_nielsen` | Significato |
|---|---|---|
| `False` | `False` | URL non riconosciuta dal TLH (nessuna testata la gestisce) |
| `True` | `False` | Config TLH trovata, ma senza Nielsen (es. pagine funnel/login) |
| `True` | `True` | Nielsen attivo — l'SDK dovrebbe caricarsi |

---

### `src/playwright_checker.py` — verifica Nielsen in-browser

**Input:** lista di URL (GEDI + terzi Manzoni — tutte, nessuna esclusa)
**Output:** dict `{ url: result }` dove `result` ha questa forma:
```python
{
    'tlh_loaded':       True,
    'tlh_url':          'https://tlh.gedidigital.it/tlh/js/adsetup.js',
    'sdk_loaded':       True,
    'sdk_url':          'https://cdn-gl.imrworldwide.com/conf/P78FA9AF2-....js',
    'sdk_appid_invalid': False,   # True se l'URL SDK contiene conf/undefined.js
    'sdk_count':        1,        # quante volte è stato caricato l'SDK
    'ping_sent':        True,
    'ping_url':         'https://secure-it.imrworldwide.com/cgi-bin/gn?prd=session&...',
    'ping_count':       1,        # quanti ping totali intercettati nella finestra
    'error':            None,
    'final_url':        None,     # valorizzato se c'è stato un redirect
    'http_status':      None,     # valorizzato se status >= 400
    'http_to_https':    False,    # True se URL http:// redirige su https://
}
```

**Come funziona:**

1. **TLH in pagina (DOM inspection):** dopo il caricamento (`domcontentloaded`)
   esegue via `page.evaluate()` uno script JS che cerca nei tag `<head><script>`
   sette filename specifici del TLH GEDI: `adsetup.js`, `adsetup_cmp.js`,
   `adsetup_pcmp.js`, `adsetup_pcmp_video.js`, `adsetup_webview.js`, `tlh.js`,
   `tlh_webview.js`. Questa tecnica funziona anche se lo script è cachato
   (la network interception non intercetterebbe uno script già in cache).

2. **SDK Nielsen:** intercetta network request a `imrworldwide.com/conf/`; rileva
   anche se l'`appId` è `undefined` (caso `conf/undefined.js` — mapping incompleto)
3. **Ping Nielsen:** intercetta network request a `imrworldwide.com/cgi-bin/gn`;
   conta tutti i ping nella finestra, non solo il primo

**Finestra di osservazione:**
- **URL Errore 22** (doppia inizializzazione): `observation_sec=30` — il browser aspetta
  l'intera finestra di 30 secondi raccogliendo tutti i ping (metodologia PwC semi-statico)
- **Tutte le altre URL** (Errore 21): fast path event-driven — esce appena riceve SDK + ping
  (max ~5s per evento)

Le due tipologie vengono eseguite in due batch async separati in `main.py`.

**Stealth anti-bot:** per ridurre i `ERR_CONNECTION_RESET` su siti con protezioni:
- `--disable-blink-features=AutomationControlled`
- rimozione di `navigator.webdriver` via `add_init_script`
- User-Agent Chrome su macOS realistico
- Viewport 1280×800, locale `it-IT`, header `Accept-Language: it-IT`

**HTTP → HTTPS:** se l'URL originale è `http://` e il browser finisce su `https://`,
il risultato contiene `http_to_https=True` e l'analisi TLH/SDK/ping viene saltata.
Queste URL non sono gestibili lato TLH (redirect lato server — da escludere
dalle segnalazioni Audicom).

**Il check avviene senza consensare la CMP.** Nielsen emette comunque un "session ping"
anche senza consenso. Questa è la modalità scelta per coerenza con le segnalazioni Audicom.

**Concorrenza:** `asyncio.Semaphore`, limite configurabile con `playwright_concurrency`.

---

### `src/report_builder.py` — generazione report Excel

**Input:** segnalazioni + risultati TLH + risultati Playwright + `tipo` opzionale
**Output:** uno o due file Excel in `output_path` (se `tipo` è specificato, solo uno)

#### `nielsen_gedi_YYYYMMDD_HHMM.xlsx` — siti interni GEDI

Contiene due sheet: **Riepilogo** e uno sheet per ciascun codice errore presente.

**Riepilogo:** tabella a 3 colonne (Metrica | Con ✓ | Senza ✗), con verde per i
valori positivi e rosso per quelli negativi, per leggere a colpo d'occhio le lacune.

**Sheet errori** (es. "Errore 21 - Zero page views"):

| Colonna | Cosa mostra |
|---|---|
| URL | L'URL segnalata |
| Testata | Testata(e) che ha segnalato quell'URL |
| TLH in pagina | Sì/No — verde/rosso; **N/A** se non verificabile |
| Config TLH trovata | Sì/No — verde/rosso |
| Mapping Nielsen | Sì/No — verde/giallo |
| Soluzione | Azione correttiva suggerita — giallo |
| SDK in pagina | Sì/No — verde/rosso; giallo se `appId` è `undefined` |
| Ping inviato | Sì/No — verde/rosso; **per Errore 22**: numero intero (verde=1, giallo=0, rosso≥2) |
| Note | Motivo skip, errore/timeout, redirect, HTTP status, `appId` non definito |
| Tipo accesso | `semi_statico_desktop` / `semi_statico_mobile` |
| Nielsen Static URL | URL del file JS di mapping Nielsen (ultima colonna) |

**Logica Soluzione — Errore 21** (in ordine di priorità):
- `TLH in pagina = No` → "Inserire TLH in pagina"
- `TLH Sì, Config No` → "Aggiungere config TLH"
- `Config Sì, Mapping No` → "Aggiungere mapping Nielsen"
- `Mapping Sì, SDK No, appId undefined` → "AppId Nielsen non definito: aggiungere/correggere regexp nel mapping Nielsen"
- `Mapping Sì, SDK No` → "Aggiungere regexp nel mapping Nielsen"

**Logica Soluzione — Errore 22** (doppia inizializzazione):
- `ping_count ≥ 2` → "Doppia inizializzazione Nielsen: N ping rilevati in 30s. Verificare che lo snippet Nielsen non venga eseguito due volte"
- `ping_count = 0` → "Errore non riprodotto: nessun ping rilevato nella finestra di osservazione (30s)"
- `ping_count = 1` → nessuna soluzione (comportamento corretto)

**Formattazione celle:** bordi `thin` grigi su tutte le celle. Solo la colonna **Note**
va a capo automaticamente; tutte le altre rimangono su riga singola.

**Righe grigie:** URL da ignorare visivamente — `http://`, errori HTTP ≥ 400,
errori/timeout Playwright.

**N/A** nelle colonne Playwright: URL saltata per `http_to_https`, o errore/timeout
Playwright. Le URL in `skip_url_patterns` vengono comunque verificate da Playwright
e ricevono solo una nota nelle Note (non N/A).

#### `nielsen_manzoni_YYYYMMDD_HHMM.xlsx` — editori terzi Manzoni

| Colonna | Cosa mostra |
|---|---|
| URL | L'URL segnalata |
| Gruppo | Nome editore terzo |
| Testata | Testata(e) che ha segnalato quell'URL |
| Tipo accesso | `semi_statico_desktop` / `semi_statico_mobile` |
| SDK in pagina | Sì/No — verde/rosso; **N/A** se non verificabile |
| Ping inviato | Sì/No — verde/rosso; **N/A** se non verificabile |
| Soluzione | "SDK Nielsen non presente in pagina" / "SDK presente ma ping non inviato" / "SDK caricato con appId non definito" |
| Note | Motivo skip, errore/timeout, redirect, HTTP status |

Stessa URL segnalata da più testate → **una sola riga** con le testate concatenate.

---

### `src/mailer.py` — invio mail

**Input:** path dei file Excel generati + testo corpo + config
**Output:** mail con allegati

Usa SMTP con STARTTLS (Office 365 di default). La password si legge in questo ordine:
1. Variabile d'ambiente `SMTP_PASSWORD` (preferita per sicurezza)
2. Campo `smtp_password` in `config.yaml`

Il corpo della mail contiene un riepilogo testuale delle numeriche.

Se `--tipo` è specificato, solo il report corrispondente viene allegato.

---

## Dati di riferimento (giugno 2026 — primo run reale)

| Metrica | Valore |
|---|---|
| Excel Audicom trovati | 58 |
| Segnalazioni totali | 2218 |
| URL uniche | 849 |
| URL GEDI | 640 |
| URL terzi Manzoni | 209 |
| Testate coinvolte | 20 |
| Errore 21 (Zero page views) | 2170 segnalazioni |
| Errore 22 (Troppi page views) | 48 segnalazioni (solo Repubblica desktop) |
| TLH: URL con config trovata | 559 / 640 |
| TLH: URL con config Nielsen | 388 / 640 |

---

## URL chiave Nielsen da sapere

| Risorsa | URL |
|---|---|
| TLH produzione (non-minificato) | `https://tlh.gedidigital.it/tlh/js/adsetup_tlh.js` |
| Bundle Nielsen statico (esempio Repubblica) | `https://www.gedistatic.it/corporate/nielsen/nielsen_static_mapping_repubblica.min.js` |
| SDK Nielsen CDN | `https://cdn-gl.imrworldwide.com/conf/{appId}.js` |
| Ping Nielsen | `https://secure-it.imrworldwide.com/cgi-bin/gn?...` |

---

## Come si aggiorna ogni mese

1. Mettere la nuova cartella segnalazioni dentro `segnalazioni/`
2. Aggiornare `segnalazioni_path` in `config.yaml` con il nome della nuova sottocartella
3. Lanciare `python3 main.py`

---

## Cosa fare se qualcosa non funziona

**TLH matching non trova nulla:**
→ Il TLH potrebbe essere stato aggiornato e la sezione delle push si trova a righe diverse.
→ Aprire `tlh_helper/check_url.js`, cercare la funzione `matchUrl`, e verificare che
  `firstPushLine` venga trovato correttamente (aggiungere un `console.error` temporaneo).
→ Eliminare il file cache: `rm $TMPDIR/tlh_prod_deob_cached.js`

**ERR_CONNECTION_RESET su molte URL:**
→ Il sito ha protezioni anti-bot. Il checker usa già stealth di base (User-Agent realistico,
  `navigator.webdriver` rimosso, viewport, locale). Se il problema persiste, valutare
  `playwright-stealth` (pacchetto npm) per evasione più robusta.

**Playwright timeout su molte URL:**
→ Abbassare `playwright_concurrency` in `config.yaml` (da 6 a 3 o meno).
→ Aumentare `playwright_timeout` (da 30 a 45).

**URL senza www non trovate nel TLH:**
→ Alcune URL arrivano da Audicom senza `www` (es. `http://entietribunali.it`).
→ Il TLH usa regex che richiedono `www`. Aggiungere normalizzazione in `excel_parser.py`
  se il fenomeno è frequente.

**Mail non inviata:**
→ Verificare che `SMTP_PASSWORD` sia impostata come variabile d'ambiente, o aggiungere
  la password direttamente in `config.yaml`.
