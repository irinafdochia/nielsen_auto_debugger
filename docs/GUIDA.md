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
└── untracked/                 gitignored — metti qui gli Excel Audicom e i report generati
    ├── 06_2026_GEDI-MANZONI/  esempio: cartella segnalazioni giugno 2026
    └── output/                i report Excel generati finiscono qui
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
```

---

## Cosa fa ogni file

---

### `main.py` — entry point

Coordina l'intera esecuzione in quattro step sequenziali.

```
[1/4] Parsing Excel       → chiama excel_parser.find_segnalazioni()
[2/4] TLH matching        → chiama tlh_matcher.check_urls_batch()  (solo URL GEDI)
[3/4] Playwright check    → chiama playwright_checker.check_urls_batch()  (tutte le URL)
[4/4] Report + mail       → chiama report_builder.build_report() e mailer.invia_report()
```

Gestisce anche la modalità `--url` per testare una singola URL in debug.

---

### `config.yaml` — configurazione

Il file da modificare ogni mese quando cambia la cartella segnalazioni.

```yaml
segnalazioni_path: untracked/06_2026_GEDI-MANZONI   ← cambia questo ogni mese
output_path: untracked/output

playwright_concurrency: 3   ← quante pagine aprire in parallelo (abbassa se crasha)
playwright_timeout: 30      ← secondi di attesa per pagina

mail:
  mittente: nielsen-autodebug@gedidigital.it
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
│       └── {tipo}/                ← es. semi_statico_desktop
│           └── file.xlsx
└── {AltroGruppo}/                 ← editori terzi Manzoni
    └── stessa struttura
```

Le cartelle `dinamico` vengono ignorate (contengono PDF, non Excel di anomalie).
Il file `Apps_Report_GEDI.xlsx` nella root viene ignorato (struttura diversa).

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
`get_unique_urls()` la deduplicazione: restituisce `{ url: [lista_segnalazioni] }`.

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

**Input:** lista di URL (GEDI + terzi Manzoni)
**Output:** dict `{ url: result }` dove `result` ha questa forma:
```python
{
    'sdk_loaded': True,
    'ping_sent':  True,
    'sdk_url':    'https://cdn-gl.imrworldwide.com/conf/P78FA9AF2-....js',
    'ping_url':   'https://secure-it.imrworldwide.com/cgi-bin/gn?prd=session&...',
    'error':      None,
}
```

**Come funziona:** apre ogni URL con Chromium in modalità headless, intercetta tutte
le request di rete, e cerca:
- SDK Nielsen: request che contiene `imrworldwide.com/conf/`
- Ping Nielsen: request che contiene `imrworldwide.com/cgi-bin/gn`

Dopo il caricamento della pagina usa un listener a eventi invece di un'attesa fissa:
aspetta al massimo 5s che arrivi la request SDK; se non arriva esce subito (il ping
non può arrivare senza SDK); se l'SDK arriva, aspetta altri 5s per il ping.

**Il check avviene senza consensare la CMP.** Nielsen emette comunque un "session ping"
anche senza consenso, ma potrebbe non emettere tutti i ping "full". Questa è la
modalità scelta per semplicità e coerenza con le segnalazioni Audicom.

**Playwright gira su tutte le URL senza eccezioni.** Il TLH matching opera su URL
esatte, ma il browser segue redirect e normalizza schemi HTTP/HTTPS — i due non sono
equivalenti. Skippare in base al risultato TLH rischierebbe falsi negativi (es. una URL
senza `www` non matchata dal TLH potrebbe redirigere a una pagina con Nielsen attivo).

**Concorrenza:** usa `asyncio.Semaphore` per aprire al massimo N pagine in parallelo
(configurabile con `playwright_concurrency` in `config.yaml`).

**User agent:** simula Chrome su macOS per evitare blocchi bot.

---

### `src/report_builder.py` — generazione report Excel

**Input:** segnalazioni + risultati TLH + risultati Playwright
**Output:** file `nielsen_autodebug_YYYYMMDD_HHMM.xlsx` in `output_path`

Il report ha più sheet:

**Sheet "Riepilogo"** (sempre il primo):
- Totale URL analizzate, suddivise GEDI / terzi
- Statistiche TLH (quante con config trovata, quante con Nielsen attivo)
- Statistiche Playwright (SDK ok/ko, ping ok/ko)
- Tabella segnalazioni per testata

**Sheet per ogni codice errore** (es. `"Errore 21 - Zero page views"`):
Una riga per URL unica, con queste colonne:

| Colonna | Cosa mostra |
|---|---|
| URL | L'URL segnalata |
| Sito | `Interno GEDI` o `Terzo Manzoni` |
| SDK in pagina | Sì/No/N/A — verde/rosso/bianco |
| Ping inviato | Sì/No/N/A — verde/rosso/bianco |
| Config TLH trovata | Sì/No (solo GEDI) — verde/rosso |
| Brand TLH | es. `repubblica` |
| Config Nielsen | Sì/No — verde/giallo se TLH trovato senza Nielsen |
| Nielsen Static URL | URL del file JS di mapping Nielsen |
| Gruppo | GEDI o nome editore terzo |
| Testata | Testata(e) che ha segnalato quell'URL |
| Tipo accesso | `semi_statico_desktop` / `semi_statico_mobile` |
| Note | Eventuali errori TLH o Playwright |
| Soluzione | Azione correttiva suggerita (es. "Inserire config TLH") — giallo |

`N/A` nelle colonne SDK/Ping indica che il check Playwright è stato saltato perché
TLH ha confermato che Nielsen non è presente su quel dominio.

Stessa URL segnalata da più testate → **una sola riga** con le testate concatenate.

---

### `src/mailer.py` — invio mail

**Input:** path del file Excel + testo corpo + config
**Output:** mail con allegato

Usa SMTP con STARTTLS (Office 365 di default). La password si legge in questo ordine:
1. Variabile d'ambiente `SMTP_PASSWORD` (preferita per sicurezza)
2. Campo `smtp_password` in `config.yaml`

Il corpo della mail contiene un riepilogo testuale delle numeriche (stesso contenuto
del foglio Riepilogo).

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

1. Mettere la nuova cartella segnalazioni in `untracked/`
2. Aggiornare `segnalazioni_path` in `config.yaml`
3. Lanciare `python3 main.py`

---

## Cosa fare se qualcosa non funziona

**TLH matching non trova nulla:**
→ Il TLH potrebbe essere stato aggiornato e la sezione delle push si trova a righe diverse.
→ Aprire `tlh_helper/check_url.js`, cercare la funzione `matchUrl`, e verificare che
  `firstPushLine` venga trovato correttamente (aggiungere un `console.error` temporaneo).
→ Eliminare il file cache: `rm $TMPDIR/tlh_prod_deob_cached.js`

**Playwright timeout su molte URL:**
→ Abbassare `playwright_concurrency` in `config.yaml` (da 3 a 1 o 2).
→ Aumentare `playwright_timeout` (da 30 a 45).

**URL senza www non trovate nel TLH:**
→ Alcune URL arrivano da Audicom senza `www` (es. `http://entietribunali.it`).
→ Il TLH usa regex che richiedono `www`. Aggiungere normalizzazione in `excel_parser.py`
  se il fenomeno è frequente.

**Mail non inviata:**
→ Verificare che `SMTP_PASSWORD` sia impostata come variabile d'ambiente, o aggiungere
  la password direttamente in `config.yaml`.
