# Open Points — Auto Debug Nielsen

---

## 1. Durata dello script

**Problema:** con 849 URL uniche e concorrenza 3, il tempo totale è elevato
(stima ~85 minuti: ogni pagina impiega ~35s tra caricamento + 5s attesa Nielsen).

**Leve disponibili:**

| Leva | Impatto | Rischio |
|---|---|---|
| Aumentare `playwright_concurrency` (da 3 a 6-10) | Alto | Crash per memoria/CPU; da testare |
| Ridurre l'attesa fissa post-caricamento (da 5s a 2-3s) | Medio | Si potrebbero perdere ping lenti |
| ~~Sostituire l'attesa fissa con listener evento Nielsen~~ | — | Implementato: `sdk_event` + `ping_event` con `asyncio.wait_for` |
| ~~Saltare Playwright per URL dove TLH dice `nielsen=False`~~ | — | Scartato: il browser segue redirect che il TLH non vede → falsi negativi |
| Eseguire TLH matching e Playwright in parallelo (oggi sono sequenziali) | Basso | Cambia l'architettura di `main.py`; poco guadagno perché TLH è già veloce |

**Prossimo passo consigliato:** testare valori più alti di `playwright_concurrency`
(da 3 a 6-8) su una macchina con RAM sufficiente, monitorando crash e durata.

---

## 2. Siti mobile — cosa verifichiamo davvero?

**Contesto:** negli Excel Audicom ci sono due tipologie mobile:
- `semi_statico_mobile` → pagine **web** servite su mobile (stesso URL, layout responsive o dedicato)
- `Apps_Report_GEDI.xlsx` → report separato per **app native** (già escluso dal parser)

**Per il web mobile:** il check SDK + ping funziona esattamente come per il
desktop. Playwright con user agent mobile (o con viewport 375px) simula
correttamente la visita. Al momento usiamo user agent desktop per tutti — va
bene per verificare l'esistenza del ping, ma potrebbe non replicare
esattamente il comportamento di un browser mobile (es. se il sito serve
contenuto diverso in base all'UA).

**Per le app native:** Playwright non può aiutare. Il tracking Nielsen nelle
app avviene via SDK nativo (iOS/Android), non via browser. Quelle segnalazioni
— se mai dovessero rientrare nel flusso — richiederebbero un approccio
completamente diverso (proxy di rete, Charles, o analisi dei log app).

**Decisione attuale:** le URL `semi_statico_mobile` vengono escluse a monte da
`main.py` perché il check browser non è applicabile (non si possono simulare
app native). Se in futuro si vuole coprire il web mobile (pagine responsive),
aggiungere un secondo profilo Playwright con UA mobile e viewport 375px.

---

## 3. Excel di output — avviso "problemi nel documento"

**Sintomo:** aprendo il file generato, Excel mostra un popup di recupero e poi
il file si apre normalmente.

**Fix già applicato:** le stringhe nelle sezioni Riepilogo non iniziano più con `=`
(es. `"=== URL ANALIZZATE ==="` → `"URL ANALIZZATE"`). Excel tentava di
interpretarle come formule — questo era il caso più comune e ora è risolto.

**Cause residue possibili (se il popup ricompare):**

1. **Caratteri non validi nelle celle** — URL o nomi di testate con caratteri
   speciali (es. `&`, `<`, `>`, caratteri Unicode non-BMP) che openpyxl non
   escapa correttamente. Da verificare: aggiungere un filtro `str(val).encode('utf-8', errors='replace').decode('utf-8')` prima di scrivere in cella.

2. **Larghezza colonne fuori range** — `_autofit_columns` usa `len(str(cell.value))`
   che non gestisce newline o testo lungo. Width troppo grande può corrompere il file.

3. **Sheet name troncato a metà di un carattere multi-byte** — troncamento con
   `[:31]` su nomi con caratteri non-ASCII può corrompere i metadati del workbook.

**Come diagnosticare:** rinominare `.xlsx` in `.zip`, estrarlo, aprire
`xl/worksheets/sheet1.xml` e cercare tag malformati o valori anomali.

---

## 4. Checkpoint intermedio — salvare il parsing prima di Playwright

**Idea:** dopo il parsing degli Excel e prima di avviare Playwright, salvare
in un file JSON (o CSV) tutte le URL estratte con i relativi metadati
(errore, testata, tipo, is_gedi).

**Perché ha senso:**
- Se Playwright crasha a metà (per timeout, OOM, interruzione manuale), si
  riparte dal JSON già calcolato senza rileggere tutti gli Excel Audicom.
- Permette di rivedere manualmente le URL prima di lanciare il browser.
- Utile anche per confrontare mese su mese (quali URL ricorrono?).

**Come implementare:**
```
untracked/output/
└── checkpoint_YYYYMMDD_HHMM/
    ├── urls.json        ← output del parser (segnalazioni + URL uniche)
    ├── tlh_results.json ← risultati TLH matching
    └── pw_results.json  ← risultati Playwright (scritto incrementalmente)
```

Aggiungere a `main.py` un flag `--from-checkpoint <path>` che salta parsing e
TLH e ricarica i JSON. Il Playwright potrebbe scrivere i risultati in append
così da essere riprendibile anche a metà.

---

## 5. Distinguere ping Nielsen video da ping Nielsen testo

**Problema:** il sistema intercetta qualsiasi request a `imrworldwide.com/cgi-bin/gn`
senza distinguere se si tratta di un ping di tracciamento pagina (testo/statico)
o di un ping video (eventi `loadMetadata`, `setPlayheadPosition`, ecc.).

**Perché potrebbe importare:** le segnalazioni Audicom per errore 21 (zero page views)
riguardano specificamente il tracking statico. Un ping video presente non implica
che il tracking statico funzioni.

**Come distinguere (da verificare sulle specifiche Nielsen):**
- Il ping statico ha parametri specifici nel query string (es. `prd=session`)
- Il ping video ha parametri diversi legati agli eventi del player

**Prossimo passo:** aprire un esempio reale di ping video e uno di ping testo e
confrontare i parametri per costruire un filtro affidabile. Aggiornare
`PING_PATTERN` in `playwright_checker.py` di conseguenza.
