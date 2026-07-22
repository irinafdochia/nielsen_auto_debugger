# Auto Debug - Segnalazioni Mensili Audicom (NIE)

## Contesto

Ogni mese Audicom invia una mail con il resoconto delle anomalie di funzionamento dell'SDK Nielsen del mese precedente. Le segnalazioni arrivano come file Excel organizzati in una struttura a cartelle. Non tutte le cartelle contengono anomalie: la presenza di un Excel in una cartella foglia indica che ci sono anomalie da verificare.

---

## Struttura dei dati in ingresso

La cartella root arriva da Audicom con questa struttura (esempio reale: `06_2026_GEDI-MANZONI`):

```
{root}/
├── Apps_Report_GEDI.xlsx                   ← report separato per app mobile (struttura diversa, non fa parte del flusso)
├── GEDI Gruppo Editoriale/                 ← siti INTERNI GEDI
│   ├── La Repubblica/
│   │   ├── dinamico/                       ← solo PDF, nessuna anomalia
│   │   ├── semi_statico_desktop/
│   │   │   └── La Repubblica_Semi_Static_Desktop_2026-06.xlsx  ← anomalie desktop
│   │   └── semi_statico_mobile/
│   │       ├── La Repubblica_Semi_Static_Mobile_2026-06.xlsx   ← anomalie mobile
│   │       └── La Repubblica_Semi_Static_Mobile_Agent_from_Desktop_2026-06.xlsx
│   ├── La Stampa.it/
│   ├── Huffington Post Italia/
│   └── ... (altre testate GEDI)
├── AutoXY/                                 ← editori TERZI Manzoni
├── Caffeina Media/
├── Gruppo Sae - Sapere Aude Editori/
├── UNIMEDIA SRL/
├── Unibeta/
└── ilMeteo/
```

**Regola classificazione siti:**
- Cartella `GEDI Gruppo Editoriale` → **sito interno GEDI** (analisi TLH + Playwright)
- Tutte le altre cartelle primo livello → **editore terzo Manzoni** (solo Playwright)

**Struttura Excel anomalie** (tutte le cartelle foglia con Excel, verificato su campione reale):
- Una sola colonna
- Riga 1: descrizione testuale dell'errore (es. "Zero page views")
- Riga 2: vuota
- Righe 3+: URL, una per riga (possono esserci duplicati)
- Uno sheet per codice errore riscontrato

**Numeriche campione giugno 2026:**
- 57 Excel con Errore 21 (Zero page views) → 2170 URL totali
- 1 Excel con Errore 22 (Troppi page views) → 48 URL (solo La Repubblica desktop)

**Nota:** le cartelle `dinamico` contengono solo PDF senza anomalie da verificare → ignorarle.

---

## Cosa deve fare il sistema

### 1. Parser cartelle + estrazione URL

- Scandire ricorsivamente la struttura di cartelle
- In ogni cartella foglia, cercare file Excel
- Per ogni Excel trovato, leggere tutti i sheet
- Da ogni sheet estrarre le URL segnalate + il codice errore dello sheet
- Classificare ogni URL come **sito interno GEDI** o **editore terzo Manzoni**

### 2. Analisi per errore 21 (Zero Page Views)

> **Nota:** nel task originale era indicato "errore 22" ma dagli Excel reali Audicom **Errore 21 = Zero page views** e Errore 22 = Troppi page views. Il sistema deve gestire l'Errore 21 come priorità.

#### Siti interni GEDI

**Step A - Verifica configurazione TLH:**
- Scaricare il TLH di produzione da `https://tlh.gedidigital.it/tlh/js/adsetup_tlh.js` (cachato 12h)
- Fare matching dell'URL con le regex presenti nelle configurazioni TLH
- Determinare se esiste una configurazione Nielsen per quell'URL (campo `nielsenStatic` nel config object)
- Risultato: `config presente` / `config assente` / `config con nielsenStatic vuoto`

**Step B - Verifica con Playwright:**
- Se `matched=False` senza errori TLH → skip: Nielsen certamente assente su quel dominio
- Se `matched=True` (con o senza `nielsenStatic`) → aprire la pagina con Playwright
- Verificare la presenza dell'SDK Nielsen in pagina (request verso `cdn-gl.imrworldwide.com/conf/`)
- Verificare l'uscita del ping Nielsen (request verso `imrworldwide.com/cgi-bin/gn`)
- **Nota**: SDK assente implica ping assente — il ping richiede che l'SDK sia caricato e inizializzato
- **Nota**: il ping può essere rilevato anche senza consensare la CMP (session ping)
- **Futuro**: valutare la possibilità di consensare la CMP per verifica completa

#### Editori terzi Manzoni

**Solo Playwright:**
- Aprire la pagina con Playwright
- Verificare l'uscita del ping Nielsen (network request verso `*.imrworldwide.com`)
- Verificare la presenza dell'SDK in pagina

### 3. Output

**File Excel di report:**
- Una o più cartelle Excel con sheet dedicati
- Per ogni URL analizzata:
  - Presenza configurazione TLH (solo siti interni)
  - Erogazione del ping Nielsen
  - Presenza SDK in pagina
  - Tipo sito (interno / terzo Manzoni)
  - Codice errore segnalato da Audicom

**Corpo mail (testo):**
- Numeriche aggregate: quante URL analizzate, quante senza config, quante senza ping, ecc.

**Invio mail:**
- Lista destinatari configurabile (file di config esterno, non hardcoded)

---

## Requisiti tecnici

- **Nessun over-engineering**: codice leggibile e modificabile senza essere esperti
- **Predisposto per nuovi codici errore**: struttura a handler per errore separata
- **Linguaggio**: Python (librerie: openpyxl/pandas per Excel, Playwright per browser)
- **Config esterna**: destinatari mail e parametri in un file separato (YAML/JSON)

---

## Repo di riferimento

| Repo | Path locale | Scopo |
|---|---|---|
| `Tracing/nielsen` | `/Users/irina.f.dochia/.../Tracing/nielsen` | Bundle JS Nielsen (mapping URL → config → SDK) |
| `Tlh` | `/Users/irina.f.dochia/.../Tlh` | Tag Loader Hub: matching URL → config testata |
| `Nielsen Legacy Video` | `/Users/irina.f.dochia/.../Nielsen Legacy Video` | Plugin video.js per tracking Nielsen sui player |

---

---

# Flusso Nielsen - Documentazione tecnica

## Overview

Nielsen viene integrato su tutti i siti GEDI tramite un sistema a tre livelli:

1. **TLH (Tag Loader Hub)** - determina quale bundle Nielsen caricare in base all'URL
2. **Bundle Nielsen statico** (da repo `Tracing/nielsen`) - determina l'`appId` e la `section` Nielsen esatta
3. **SDK Nielsen** (CDN esterno `cdn-gl.imrworldwide.com`) - esegue il tracciamento reale

---

## Livello 1: TLH - Matching URL → Config testata

**File chiave:** `Tlh/app/configurations_handler.js`, `Tlh/app/setup.js`

Ogni file in `Tlh/app/configurations/` registra una o più configurazioni con questa struttura:

```js
tlh_configs.push([
    /^https?:\/\/(www)\\.repubblica\.it/,   // regex URL principale
    false,                                   // regex URL video (o false)
    {
        "activeBrand": "repubblica",
        "activeHost":  "repubblica",
        "nielsenStatic": window.kw_nlsnComponentBasicUrl + "nielsen_static_mapping_repubblica.min.js",
        // ...altri campi
    }
]);
```

`configurations_handler.js` itera `tlh_configs` e al primo match assegna `window.kw_tlh_activeConfig`.

Il campo chiave per Nielsen è:
```js
window.kw_nlsnComponentBasicUrl = "https://www.gedistatic.it/corporate/nielsen/";
"nielsenStatic": window.kw_nlsnComponentBasicUrl + "nielsen_static_mapping_{brand}.min.js"
```

**Casi in cui Nielsen viene disabilitato dal TLH:**
- Pagine in webview → `kw_tlh_activeConfig.nielsenStatic = false`
- Pagine 404 → il componente `nielsenStaticMapping` non viene creato
- Config senza campo `nielsenStatic` (pagine funnel di pagamento, login, ecc.)

---

## Livello 2: Bundle Nielsen statico - Mapping URL → appId + section

**Repo:** `Tracing/nielsen` | **File chiave:** `app/main.js`, `app/mapping/*.js`

Una volta che TLH ha determinato il bundle da caricare, il bundle viene eseguito e:

1. `setup.js` inizializza `window.nielsenPageConfigs = {}` e `window.nielsenVideoConfigs = {}`
2. Il file di mapping del brand popola queste strutture
3. `main.js` fa il matching finale URL → appId + section

### Formato mapping semplice (brand monocanale)

```js
window.nielsenPageConfigs.deejay = ["P91A2EBCB-3AA3-4F25-B4C6-A8CFDBD8380F", "RadioDeejay"];
// [appId, section]
```

### Formato mapping complesso (brand multicanale - es. Repubblica, La Stampa)

```js
window.nielsenPageConfigs.repubblica = [];
window.nielsenPageConfigs.repubblica.push([
    "repubblicahomepage",                           // nome canale
    [/^https?:\/\/www\.repubblica\.it($|\/.*)/],    // array di RegExp URL
    "P67593295-06EE-437F-9AD1-6E7B1F267613",        // appId Nielsen
    "LaRepubblicaHomepage_BRW"                      // section Nielsen
]);
// Il primo match vince
```

### Calcolo `nie_assetid` (identificatore univoco pagina)

Formato: `{activeHost}_{tipoPagina}_{md5(url)}`
- homepage → `_home_`
- section → `_section_`
- pagine con "ricerca" → `_ricerca_`
- pagine con "dettaglio" → `_detail_`
- altri → usa `kw_tlh_pageType`

### Consenso CMP (Iubenda)

Solo se `window.kw_mfaList.includes("tcap")`:
- `sdktesto === false` → Nielsen istanziato con `optout: "true"`
- `rilevazionevolumetrica === false` → Nielsen non istanziato

---

## Livello 3: SDK Nielsen - Caricamento effettivo

**Stub NOLBUNDLE** (in `app/sdk_initializer.js` della repo `Tracing/nielsen`):

```js
NOLBUNDLE.nlsQ(appId, "kw_nSdkPageInstance", { optout: "false" })
```

Questa chiamata:
1. Inserisce in pagina `<script async src="https://cdn-gl.imrworldwide.com/conf/{appId}.js#name=kw_nSdkPageInstance&ns=NOLBUNDLE">`
2. Mette in coda l'evento: `kw_nSdkPageInstance.ggPM("staticstart", { type: "static", assetid, section })`

**Il ping Nielsen** è una richiesta HTTP verso `*.imrworldwide.com` generata dall'SDK dopo il `ggPM("staticstart", ...)`.

---

## Mappa brand → file di mapping Nielsen

| Brand/testata | File mapping Nielsen | Note |
|---|---|---|
| repubblica.it | `nielsen_static_mapping_repubblica.min.js` | Mapping complesso, ~15 canali |
| lastampa.it | `nielsen_static_mapping_lastampa.min.js` | |
| huffingtonpost.it | `nielsen_static_mapping_huffingtonpost.min.js` | Video tracking disabilitato |
| ilsecoloxix.it | `nielsen_static_mapping_ilsecoloxix.min.js` | |
| nationalgeographic.it | `nielsen_static_mapping_nationalgeographic.min.js` | |
| lescienze.it | `nielsen_static_mapping_lescienze.min.js` | |
| limesonline.com | `nielsen_static_mapping_limesonline.min.js` | |
| gelocal.it | `nielsen_static_mapping_gelocal.min.js` | Condivide appId con edicola/eventi (GNN) |
| deejay.it | `nielsen_static_mapping_deejay.min.js` | |
| capital.it | `nielsen_static_mapping_capital.min.js` | |
| m2o.it | `nielsen_static_mapping_m2o.min.js` | |
| italian.tech | `nielsen_static_mapping_italiantech.min.js` | |
| greenandblue.it | `nielsen_static_mapping_greenandblue.min.js` | appId vuoto = Nielsen non attivo |
| ilgusto.it | `nielsen_static_mapping_ilgusto.min.js` | appId vuoto = Nielsen non attivo |
| lazampa.it | `nielsen_static_mapping_lazampa.min.js` | appId vuoto = Nielsen non attivo |
| salute.eu | `nielsen_static_mapping_salute.min.js` | appId vuoto = Nielsen non attivo |
| alfemminile.com | `nielsen_static_mapping_alfemminile.min.js` | |
| sport.it | `nielsen_static_mapping_sport.min.js` | |
| stile.it | `nielsen_static_mapping_stile.min.js` | |
| turismo.it, film.it, moda.it | `nielsen_static_mapping_itedisitiesterni.min.js` | Ereditano mapping La Stampa |
| formulapassion.it | `nielsen_static_mapping_formulapassion.min.js` | |
| consigli.it | `nielsen_static_mapping_consigli.min.js` | |
| ilmiolibro.it | `nielsen_static_mapping_ilmiolibro.min.js` | |
| entietribunali.it | `nielsen_static_mapping_entietribunali.min.js` | |

URL base dei file: `https://www.gedistatic.it/corporate/nielsen/`

---

## Come verificare Nielsen su una pagina (riferimento per Playwright)

**URL esatte da intercettare:**
- Caricamento SDK: `https://cdn-gl.imrworldwide.com/conf/{appId}.js`
  - Esempio: `https://cdn-gl.imrworldwide.com/conf/P78FA9AF2-E29A-4C33-86B5-2D800CA27182.js`
- Ping di tracciamento: `https://secure-it.imrworldwide.com/cgi-bin/gn?...`
  - Esempio: `https://secure-it.imrworldwide.com/cgi-bin/gn?prd=session&c13=asid,P78FA9AF2-...`
  - Pattern da intercettare: `*.imrworldwide.com` (copre sia `secure-it` che eventuali altri host)

**Cosa cercare nel network (filtro Playwright):**
- SDK caricato: URL contiene `imrworldwide.com/conf/`
- Ping inviato: URL contiene `imrworldwide.com/cgi-bin/gn`

**Cosa cercare nel DOM/JS:**
- `window.NOLBUNDLE` deve esistere
- `window.kw_nSdkPageInstance` deve esistere dopo l'init
- `window.nie_section` deve avere un valore (non `undefined`, non stringa vuota)

**Valori sentinel di `gediDataLayer.nielsenChannel`:**
- `"nostaticsdk"` → SDK non caricato o URL non riconosciuta dal mapping
- `"nomappingsdk"` → URL riconosciuta ma senza sezione Nielsen associata
- Qualsiasi altro valore → sezione Nielsen assegnata correttamente

---

## Nielsen Legacy Video (plugin video)

**Repo:** `Nielsen Legacy Video` | Versione plugin: 3.0.1

Plugin video.js che gestisce il tracking Nielsen per i contenuti video (VOD e live). Non rilevante per l'errore 21 (zero page views) ma utile per capire come funziona il tracking video sui player Brightcove.

Il plugin ascolta gli eventi del player (play, pause, timeupdate, ads-*) e invia i comandi `ggPM()` all'SDK Nielsen:
- `loadMetadata` → inizio contenuto/ad
- `setPlayheadPosition` → aggiornamento posizione (throttled a 1s)
- `stop` → pausa o fine ad
- `end` → fine contenuto

Gestione consenso analoga a quella del bundle statico (controlla `kw_mfaList`, `TLH_CmpHelpers.getIubConsent()`).

---

## Variabili globali rilevanti (impostate dal TLH prima del bundle Nielsen)

| Variabile | Esempio | Impostata da |
|---|---|---|
| `window.kw_tlh_activeHost` | `"repubblica"` | TLH configurations_handler |
| `window.kw_tlh_activeBrand` | `"repubblica"` | TLH configurations_handler |
| `window.kw_tlh_pageType` | `"dettaglio"` | TLH |
| `window.kw_mfaList` | `["tcap", ...]` | TLH setup |
| `window.kw_nlsnComponentBasicUrl` | `"https://www.gedistatic.it/corporate/nielsen/"` | TLH setup |
| `window.nie_section` | `"LaRepubblicaEconomia_BRW"` | Bundle Nielsen statico |
| `window.kw_nielsenAppId` | `"P67593295-..."` | Bundle Nielsen statico (main.js) |
| `window.NOLBUNDLE` | oggetto SDK stub | sdk_initializer.js |
| `window.gediDataLayer.nielsenChannel` | `"LaRepubblicaEconomia_BRW"` | TLH setter post-mapping |