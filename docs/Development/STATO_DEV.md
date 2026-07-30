# Auto Debug Nielsen — Stato dello sviluppo

> Documento di continuità: chiunque legga questo file (o qualsiasi chat futura con Claude)
> può riprendere esattamente da dove siamo arrivati.

---

## Cos'è il progetto

Sistema Python che automatizza il processo mensile di verifica delle anomalie Nielsen
segnalate da Audicom. Ogni mese Audicom manda una cartella con Excel organizzati per
editore/testata/tipo, contenenti URL con anomalie SDK. Il sistema le processa, verifica
se Nielsen funziona correttamente su quelle pagine, e produce un report.

---

## Struttura cartelle del progetto

```
Auto Debug NIELSEN/
├── main.py                    ← entry point CLI
├── config.yaml                ← configurazione (paths, mail, timeout Playwright)
├── requirements.txt           ← dipendenze Python
├── .gitignore
│
├── tlh_helper/
│   └── check_url.js           ← helper Node.js per il matching TLH
│
├── src/
│   ├── excel_parser.py        ← parser cartelle + estrazione URL dagli Excel Audicom
│   ├── tlh_matcher.py         ← wrapper Python che chiama il helper Node.js
│   ├── playwright_checker.py  ← verifica Nielsen con Playwright (async + concorrenza)
│   ├── report_builder.py      ← genera il file Excel di output
│   └── mailer.py              ← invio mail con allegato
│
├── segnalazioni/              ← gitignored (tranne README.md)
│   └── 06_2026_GEDI-MANZONI/  ← esempio dati reali giugno 2026
│
├── output/                    ← gitignored (tranne README.md) — report Excel generati
│
└── docs/
    ├── GUIDA.md               ← guida utente
    └── Development/
        └── STATO_DEV.md       ← questo file
```

---

## Come si usa

```bash
# Installazione dipendenze (una volta sola)
pip3 install -r requirements.txt
python3 -m playwright install chromium

# Run completo
python3 main.py

# Senza invio mail
python3 main.py --no-mail

# Debug su una singola URL (TLH + Playwright, stampa tutto a console)
python3 main.py --url "https://www.repubblica.it/economia/test.html"

# Config diversa dal default
python3 main.py --config altro_config.yaml

# Solo URL di un certo dominio
python3 main.py --domain "repubblica.it" --no-mail

# Solo le prime N URL uniche
python3 main.py --limit 20 --no-mail

# Solo report siti interni GEDI
python3 main.py --tipo gedi --no-mail

# Solo report editori terzi Manzoni
python3 main.py --tipo manzoni --no-mail
```

### Flag CLI complete

| Flag | Tipo | Descrizione |
|---|---|---|
| `--no-mail` | bool | Non invia la mail finale |
| `--url` | str | Testa una singola URL in modalità debug |
| `--config` | str | Path del file config (default: `config.yaml`) |
| `--domain` | str | Filtra le URL che contengono questo dominio |
| `--limit` | int | Analizza solo le prime N URL uniche (dopo gli altri filtri) |
| `--tipo` | `gedi`\|`manzoni` | Genera solo il report per siti interni o editori terzi |

I filtri si applicano nell'ordine: `--tipo` → `--domain` → `--limit`.

---

## Cosa fa il flusso (step by step)

### 1. Parsing Excel Audicom (`excel_parser.py`)

Scansiona ricorsivamente la cartella `segnalazioni_path` (configurabile in `config.yaml`).

**Struttura cartelle Audicom:**
```
root/
├── GEDI Gruppo Editoriale/     ← siti INTERNI GEDI → analisi TLH + Playwright
│   └── {Testata}/{tipo}/       ← tipo = semi_statico_desktop / semi_statico_mobile
│       └── *.xlsx              ← Excel con anomalie (se assente = nessuna anomalia)
└── {AltroGruppo}/              ← editori TERZI Manzoni → solo Playwright
    └── stessa struttura
```

Le cartelle `dinamico` vengono skippate (contengono solo PDF).
`Apps_Report_GEDI.xlsx` nella root viene skippato: la funzione `read_app_report()`
è disponibile in `excel_parser.py` ma non viene chiamata automaticamente — da allegare
manualmente alla mail se necessario.

**Nota su semi_statico_mobile:** le URL mobile sono pagine web, non app native.
Il tracciamento Nielsen è URL-based, quindi vengono analizzate con la stessa logica
del desktop. Il filtro che le escludeva è stato rimosso.

**Struttura Excel anomalie:**
- Riga 1: descrizione errore (es. "Zero page views")
- Riga 2: vuota
- Righe 3+: URL, una per riga (possono esserci duplicati)
- Uno sheet per codice errore (`Errore 21`, `Errore 22`, ecc.)

**Errore 21 = Zero page views; Errore 22 = Troppi page views** (doppia inizializzazione).

Output: lista di dict `{url, errore, descr, gruppo, testata, tipo, is_gedi, xlsx_path}`.

---

### 2. TLH Matching (`tlh_matcher.py` + `tlh_helper/check_url.js`)

Solo per le URL classificate come siti interni GEDI (cartella `GEDI Gruppo Editoriale`).

**Approccio:** un helper Node.js scarica il TLH di produzione non-minificato
(`https://tlh.gedidigital.it/tlh/js/adsetup_tlh.js`), ne estrae la sezione delle
configurazioni (righe 14199-~25706 nella versione corrente), e la esegue in un
`vm.Context` Node.js con i global necessari mockati.

Il file TLH viene **cachato 12 ore** in `$TMPDIR/tlh_prod_deob_cached.js` per non
scaricarlo ogni volta.

**Perché solo la sezione delle config e non tutto il TLH:**
Il TLH di produzione crasha in Node.js alla riga ~7041 (codice_applicativo.js usa
`window.TLH_Utils.customLib` e altre API browser che non esistono fuori dal browser).
Le `tlh_configs.push(...)` di tutte le testate sono nella sezione 14199-25706 e sono
del tutto safe da eseguire con un mock minimale di `window`.

**Modalità batch:** tutte le URL GEDI vengono passate in una sola chiamata Node.js
(stdin), e il risultato è un JSON `{ url: { matched, brand, ... } }`. Questo è molto
più veloce che una chiamata per URL.

**Risultati possibili per ogni URL:**
- `matched=false` → nessuna config TLH trovata per questa URL
- `matched=true, has_nielsen=false` → config TLH trovata ma senza campo `nielsenStatic`
  (es. pagine funnel/pagamento/login che non tracciano Nielsen)
- `matched=true, has_nielsen=true` → config Nielsen presente, Nielsen dovrebbe essere attivo

**Esempi verificati su dati reali:**
```
https://www.repubblica.it/economia/test.html  → matched, nielsen=True
https://www.repubblica.it/codici-sconto       → matched, nielsen=False (pagina funnel)
https://www.lastampa.it/politica/test.html    → matched, nielsen=True
http://entietribunali.it                      → NOT matched (URL senza www, regex TLH richiede www)
https://www.entietribunali.it/test            → matched, nielsen=True
```

---

### 3. Playwright Check (`playwright_checker.py`)

Per **tutte** le URL (GEDI e terzi Manzoni, nessuna esclusa). Usa Playwright in modalità headless.

**Cosa cerca:**
- TLH in pagina: DOM inspection dei tag `<head><script>` (funziona anche con script cachati)
- SDK Nielsen caricato: request URL contiene `imrworldwide.com/conf/`
- Ping Nielsen inviato: request URL contiene `imrworldwide.com/cgi-bin/gn`

**Casistica sdk_appid_invalid:** se l'URL dell'SDK è `conf/undefined.js`, il campo
`sdk_appid_invalid=True` viene impostato. Significa che il mapping Nielsen non ha trovato
l'`appId` per questa URL (mappatura incompleta o regexp errata).

**Finestra di osservazione (parametro `observation_sec`):**
- **URL Errore 22** (troppe pageview = doppia inizializzazione): `observation_sec=30` —
  aspetta l'intera finestra di 30 secondi raccogliendo tutti i ping, come da metodologia
  PwC (apre la pagina, attende 30s, conta le occorrenze SDK/ping).
  Il `timeout_sec` è aumentato di +30s per non andare in timeout durante l'attesa.
- **Tutte le altre URL** (Errore 21): fast path event-driven — esce appena riceve
  SDK + primo ping (max ~5s per evento, molto più veloce per l'analisi batch).

In `main.py` le due liste vengono separate e lanciate in due coroutine async distinte
all'interno dello stesso `asyncio.run()`.

**Stealth anti-bot** (per ridurre `ERR_CONNECTION_RESET`):
- `--disable-blink-features=AutomationControlled`
- rimozione `navigator.webdriver` via `add_init_script` (principale segnale anti-bot)
- User-Agent Chrome su macOS (Chrome 124)
- Viewport 1280×800, locale `it-IT`, header `Accept-Language: it-IT`

**Redirect e HTTP error:** dopo `page.goto()` si verifica se `page.url` differisce
dall'URL richiesta (redirect) e se lo status HTTP è >= 400. Scritti nella colonna Note.

**URL di servizio** (pattern da `skip_url_patterns` in `config.yaml`): non vengono
escluse da Playwright. Vengono verificate normalmente; se corrispondono a un pattern
ricevono solo una `service_note` nelle Note del report, senza modificare i risultati
TLH/SDK/ping.

Impostazioni in `config.yaml`:
- `playwright_concurrency`: quante pagine aprire in parallelo (default 6)
- `playwright_timeout`: secondi attesa per pagina (default 30)

**Verificato su Repubblica.it:** rileva correttamente SDK e ping.

---

### 4. Report Excel (`report_builder.py`)

Genera uno o due file in `output_path` a seconda del flag `--tipo`.

**`build_reports(segnalazioni, tlh_results, playwright_results, output_path, tipo=None)`**
- `tipo=None` → genera entrambi i report
- `tipo="gedi"` → solo `nielsen_gedi_*.xlsx` (manzoni_path=None)
- `tipo="manzoni"` → solo `nielsen_manzoni_*.xlsx` (gedi_path=None)

#### Sheet GEDI — colonne (in ordine)

```
URL | Testata | TLH in pagina | Config TLH trovata | Mapping Nielsen |
Soluzione | SDK in pagina | Ping inviato | Note | Tipo accesso | Nielsen Static URL
```

Nielsen Static URL è sempre l'ultima colonna.

**Colori notevoli:**
- SDK in pagina giallo: `sdk_appid_invalid=True` (SDK caricato con `appId` non definito)
- Ping inviato Errore 22: numero intero (verde=1, giallo=0, rosso≥2) invece di Sì/No
- Soluzione: cella gialla quando presente

**Logica Soluzione Errore 21** (in ordine di priorità):
1. `TLH in pagina = No` → "Inserire TLH in pagina"
2. `TLH Sì, Config No` → "Aggiungere config TLH"
3. `Config Sì, Mapping No` → "Aggiungere mapping Nielsen"
4. `Mapping Sì, SDK No, appid_invalid` → "AppId Nielsen non definito: aggiungere/correggere regexp nel mapping Nielsen"
5. `Mapping Sì, SDK No` → "Aggiungere regexp nel mapping Nielsen"

**Logica Soluzione Errore 22** (doppia inizializzazione):
- `ping_count ≥ 2` → "Doppia inizializzazione Nielsen: N ping rilevati in 30s. Verificare che lo snippet Nielsen non venga eseguito due volte (TLH, template, tag manager)"
- `ping_count = 0` → "Errore non riprodotto: nessun ping rilevato nella finestra di osservazione (30s)"
- `ping_count = 1` → OK, nessuna soluzione

**Riepilogo GEDI:** tabella a 3 colonne (Metrica | Con ✓ | Senza ✗), verde per i
conteggi positivi, rosso per quelli negativi — lettura immediata delle lacune.

#### Sheet Manzoni — colonne

```
URL | Gruppo | Testata | Tipo accesso | SDK in pagina | Ping inviato | Soluzione | Note
```

**Logica Soluzione Manzoni:**
- SDK assente → "SDK Nielsen non presente in pagina"
- SDK presente, ping assente → "SDK presente ma ping non inviato"
- `sdk_appid_invalid` → "SDK caricato con appId non definito"

---

### 5. Mail (`mailer.py`)

Invia il file Excel come allegato. Configurazione SMTP in `config.yaml`.
La password SMTP si legge dalla variabile d'ambiente `SMTP_PASSWORD` o da `config.yaml`.

Il corpo mail è testo plain con le numeriche aggregate (generato da `mailer.build_testo_mail()`).
Gli allegati sono filtrati per escludere i `None` (quando `--tipo` genera solo un report).

---

## Numeriche su dati reali (giugno 2026)

| Metrica | Valore |
|---|---|
| Excel trovati | 58 |
| Segnalazioni totali | 2218 |
| URL uniche | 849 |
| URL GEDI (per TLH) | 640 |
| URL terzi Manzoni | 209 |
| Testate coinvolte | 20 |
| Errore 21 (Zero page views) | 2170 segnalazioni |
| Errore 22 (Troppi page views) | 48 segnalazioni (solo Repubblica desktop) |
| TLH: con config trovata | 559/640 |
| TLH: con config Nielsen | 388/640 |

---

## Decisioni tecniche prese

| Decisione | Perché |
|---|---|
| Node.js per TLH matching | Le config TLH sono JS con regex JS. Eseguire il codice originale è più sicuro che riscrivere il matching in Python. |
| Batch (stdin) per TLH | Evita di avviare 640+ processi Node.js. Una sola chiamata per tutto il batch. |
| Solo sezione push del TLH | Il resto del TLH crasha in Node.js per dipendenze browser. Le push sono self-contained. |
| URL TLH non-minificato | Il minificato aveva struttura diversa e crash più difficili da debuggare. |
| Cache 12h del TLH | Non riscaricare ad ogni run. Invalidare manualmente eliminando `$TMPDIR/tlh_prod_deob_cached.js`. |
| asyncio + semaphore Playwright | Parallelizzare le richieste senza aprire troppe pagine insieme. |
| Un sheet per codice errore | Preparato per futuri errori oltre il 21/22. |
| Una riga per URL unica nel report | Deduplicazione: la stessa URL segnalata da più file Excel appare una volta sola. |
| service_note invece di skipped_reason | Le URL in skip_url_patterns vengono comunque verificate da Playwright; la nota appare nelle Note senza mettere N/A nelle colonne TLH/SDK/ping. |
| Due batch Playwright separati | Le URL Errore 22 richiedono 30s di osservazione (metodologia PwC); le altre usano il fast path event-driven. Lanciarle in batch separati evita di rallentare l'intero run. |

---

## Comportamenti implementati (luglio 2026)

| Comportamento | File | Dettaglio |
|---|---|---|
| Errori Playwright puliti | `playwright_checker.py` | `str(e).split('\n')[0]` — elimina la verbosa sezione "Call log:" dal messaggio d'errore |
| Riga grigia | `report_builder.py` | Sfondo grigio (`D0D0D0`) per: `http://`, HTTP ≥ 400, errori/timeout Playwright |
| N/A per errori/timeout | `report_builder.py` | Colonne TLH/SDK/Ping mostrano "N/A" (non "No") quando Playwright non ha potuto verificare la pagina (errore, timeout, http_to_https) |
| Note errori leggibili | `report_builder.py` | Timeout → "Timeout di navigazione..."; ERR_CONNECTION_RESET → "Errore di connessione: server non raggiungibile" |
| Soluzione: regexp mapping | `report_builder.py` | Quando TLH Sì + Config Sì + Mapping Sì + SDK No → "Aggiungere regexp nel mapping Nielsen" |
| Wrap solo colonna Note (GEDI) | `report_builder.py` | Solo la colonna "Note" va a capo; tutte le altre su riga singola. Bordi `thin` grigi su ogni cella. |
| Riepilogo a 3 colonne | `report_builder.py` | Metrica \| Con ✓ \| Senza ✗ — verde/rosso per visibilità immediata delle lacune |
| Nielsen Static URL ultima colonna | `report_builder.py` | Spostata alla fine per non interrompere il flusso di lettura principale |
| sdk_appid_invalid | `playwright_checker.py` + `report_builder.py` | Rileva `conf/undefined.js`; cella SDK gialla; nota specifica; Soluzione specifica |
| Errore 22 — finestra 30s | `playwright_checker.py` + `main.py` | `observation_sec=30`; attesa intera finestra; `ping_count` raccoglie tutti i ping; batch separato da Errore 21 |
| Errore 22 — ping count nel report | `report_builder.py` | Cella "Ping inviato" mostra numero intero; verde=1, giallo=0, rosso≥2; Soluzione descrive il numero di ping |
| Manzoni: colonna Soluzione | `report_builder.py` | SDK assente / SDK senza ping / appId non definito |
| Flag --tipo | `main.py` + `report_builder.py` | Genera solo il report richiesto; allegato mail filtrato di conseguenza |
| Flag --domain | `main.py` | Filtra le segnalazioni per dominio prima del Playwright check |
| Flag --limit | `main.py` | Tronca le URL uniche alle prime N (utile per test veloci) |
| semi_statico_mobile incluso | `main.py` | Rimosso il filtro che escludeva le URL mobile; analizzate con la stessa logica desktop |
| Stealth anti-bot | `playwright_checker.py` | `--disable-blink-features=AutomationControlled`, rimozione `navigator.webdriver`, UA realistico, viewport, locale |
| Cartelle segnalazioni/output | `.gitignore` + README | `segnalazioni/` e `output/` a root, gitignored; README.md tracciati per istruzioni ai colleghi |

---

## Cosa manca / TODO futuri

- [ ] **Consenso CMP per Playwright**: oggi il check è senza consenso. Futuramente si
  potrebbe consensare la CMP (es. cliccando "Accetta tutto") per verificare l'erogazione
  completa. Da valutare caso per caso perché ogni sito ha il suo banner.

- [ ] **Gestione nuovi codici errore**: la struttura è già pronta (un sheet per errore).
  Per aggiungere logica specifica a un nuovo errore, si crea un handler dedicato in `src/`.

- [ ] **Configurazione mail**: la parte SMTP non è stata testata. Aggiungere
  `SMTP_PASSWORD` come env var o completare `config.yaml`.

- [ ] **Selenizzazione URL senza www**: alcune URL nelle segnalazioni Audicom sono in
  forma `http://entietribunali.it` (senza www) che il TLH non matcha. Valutare se
  aggiungere una normalizzazione automatica delle URL.

- [ ] **playwright-stealth**: se `ERR_CONNECTION_RESET` persiste su siti specifici,
  valutare l'adozione del pacchetto `playwright-stealth` per evasione anti-bot più robusta.

---

## Dipendenze e versioni

| Tool | Versione usata |
|---|---|
| Python | 3.9.6 |
| Node.js | 16.20.2 |
| openpyxl | >= 3.1 |
| playwright | >= 1.40 |
| pyyaml | >= 6.0 |

```bash
# Setup completo da zero
pip3 install -r requirements.txt
python3 -m playwright install chromium
```

---

## URL di riferimento

| Cosa | URL |
|---|---|
| TLH produzione (non-minificato) | `https://tlh.gedidigital.it/tlh/js/adsetup_tlh.js` |
| TLH produzione (minificato, non usato) | `https://tlh.gedidigital.it/tlh.js` |
| Bundle Nielsen statico (es. Repubblica) | `https://www.gedistatic.it/corporate/nielsen/nielsen_static_mapping_repubblica.min.js` |
| SDK Nielsen CDN | `https://cdn-gl.imrworldwide.com/conf/{appId}.js` |
| Ping Nielsen | `https://secure-it.imrworldwide.com/cgi-bin/gn?...` |
