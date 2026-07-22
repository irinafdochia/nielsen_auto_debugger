# Auto Debug Nielsen — Stato dello sviluppo

> Documento di continuità: chiunque legga questo file (o qualsiasi chat futura con Claudio)
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
├── PROGETTO.md                ← requisiti + documentazione tecnica flusso Nielsen
├── STATO_DEV.md               ← questo file
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
└── untracked/                 ← gitignored: Excel Audicom + output generati
    ├── 06_2026_GEDI-MANZONI/  ← esempio dati reali giugno 2026
    └── output/                ← report Excel generati
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
```

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
`Apps_Report_GEDI.xlsx` nella root viene skippato (struttura diversa).

**Struttura Excel anomalie:**
- Riga 1: descrizione errore (es. "Zero page views")
- Riga 2: vuota
- Righe 3+: URL, una per riga (possono esserci duplicati)
- Uno sheet per codice errore (`Errore 21`, `Errore 22`, ecc.)

**Correzione importante:** il codice "zero page views" è l'**Errore 21** (non 22 come
scritto nel task originale). Errore 22 = "Troppi page views".

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

Per tutte le URL (GEDI e terzi Manzoni). Usa Playwright in modalità headless.

**Cosa cerca:**
- SDK Nielsen caricato: request URL contiene `imrworldwide.com/conf/`
- Ping Nielsen inviato: request URL contiene `imrworldwide.com/cgi-bin/gn`

**URL esatte da prod (per riferimento):**
- SDK: `https://cdn-gl.imrworldwide.com/conf/{appId}.js`
- Ping: `https://secure-it.imrworldwide.com/cgi-bin/gn?prd=session&...`

Il check avviene **senza consensare la CMP** (come da specifiche). Nielsen in alcuni
casi pinga anche senza consenso (session ping).

**Homepage probe (ottimizzazione):** prima di girare su tutte le URL GEDI, si controlla
l'homepage di ogni dominio unico. Se non c'è SDK lì (senza errori né redirect), tutte
le URL anomale di quel dominio ereditano sdk=No, ping=No con la nota "Homepage senza
mapping Nielsen" — il browser non viene aperto per nessuna di esse.

**Redirect e HTTP error:** dopo `page.goto()` si verifica se `page.url` differisce
dall'URL richiesta (redirect) e se lo status HTTP è >= 400. Redirect e stato HTTP
vengono scritti nella colonna Note del report.

Impostazioni in `config.yaml`:
- `playwright_concurrency`: quante pagine aprire in parallelo (default 3)
- `playwright_timeout`: secondi attesa per pagina (default 30)

Dopo `domcontentloaded`, usa listener a eventi: aspetta max 5s per la request SDK;
se non arriva esce subito (SDK assente → ping certamente assente); se arriva,
aspetta altri 5s per il ping.

**Verificato su Repubblica.it:** rileva correttamente SDK e ping.

---

### 4. Report Excel (`report_builder.py`)

Genera un file `nielsen_autodebug_{YYYYMMDD_HHMM}.xlsx` in `output_path`.

**Sheet prodotti:**
- **"Riepilogo"** (prima sheet): numeriche aggregate (totale URL, SDK ok/ko, ping ok/ko,
  TLH matched, Nielsen config trovata, suddivisione per testata)
- **"Errore 21 - Zero page views"** (e altri sheet per errori futuri): tabella dettaglio
  con una riga per URL unica, colonne:
  - URL, Gruppo, Testata, Tipo accesso, Sito (GEDI/Terzo)
  - Config TLH trovata, Brand TLH, Config Nielsen, Nielsen Static URL
  - SDK in pagina, Ping inviato, Note

Le celle SDK/Ping sono colorate verde/rosso per lettura rapida.

---

### 5. Mail (`mailer.py`)

Invia il file Excel come allegato. Configurazione SMTP in `config.yaml`.
La password SMTP si legge dalla variabile d'ambiente `SMTP_PASSWORD` o da `config.yaml`.

Il corpo mail è testo plain con le numeriche aggregate (generato da `mailer.build_testo_mail()`).

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
| Un sheet per codice errore | Preparato per futuri errori oltre il 21. |
| Una riga per URL unica nel report | Deduplicazione: la stessa URL segnalata da più file Excel appare una volta sola. |

---

## Cosa manca / TODO futuri

- [ ] **Consenso CMP per Playwright**: oggi il check è senza consenso. Futuramente si
  potrebbe consensare la CMP (es. cliccando "Accetta tutto") per verificare l'erogazione
  completa. Da valutare caso per caso perché ogni sito ha il suo banner.

- [ ] **Gestione nuovi codici errore**: la struttura è già pronta (un sheet per errore).
  Per aggiungere logica specifica a un nuovo errore, si crea un handler dedicato in `src/`.

- [ ] **GUI**: vedi sezione sotto.

- [ ] **Configurazione mail**: la parte SMTP non è stata testata. Aggiungere
  `SMTP_PASSWORD` come env var o completare `config.yaml`.

- [ ] **Selenizzazione URL senza www**: alcune URL nelle segnalazioni Audicom sono in
  forma `http://entietribunali.it` (senza www) che il TLH non matcha. Valutare se
  aggiungere una normalizzazione automatica delle URL.

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
