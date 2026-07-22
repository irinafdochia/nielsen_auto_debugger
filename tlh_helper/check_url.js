#!/usr/bin/env node
/**
 * Dato un URL, scarica il TLH di produzione, lo esegue in un contesto simulato
 * e restituisce JSON con l'esito del matching TLH per Nielsen.
 *
 * Uso: node check_url.js <url>
 * Output stdout: JSON { matched, brand, activeHost, hasNielsenConfig, nielsenStaticUrl }
 */

const https = require('https');
const fs = require('fs');
const path = require('path');
const os = require('os');
const vm = require('vm');

const TLH_URL = 'https://tlh.gedidigital.it/tlh/js/adsetup_tlh.js';
const CACHE_PATH = path.join(os.tmpdir(), 'tlh_prod_deob_cached.js');
const CACHE_MAX_AGE_MS = 12 * 60 * 60 * 1000; // 12 ore

// Legge le URL da controllare:
// - Se passate come argomenti: node check_url.js <url1> <url2> ...
// - Se viene passato "--stdin": legge da stdin (una URL per riga)
// Output: JSON array [{ url, matched, brand, ... }, ...]
const args = process.argv.slice(2);
const readStdin = args.includes('--stdin');
const targetUrls = readStdin ? [] : args.filter(a => a !== '--stdin');

if (!readStdin && targetUrls.length === 0) {
    process.stderr.write('Usage: node check_url.js <url> [url2 ...]\n');
    process.stderr.write('   or: node check_url.js --stdin  (legge URL da stdin, una per riga)\n');
    process.exit(1);
}

// ----------------------------------------------------------------
// 1. Scarica (o usa cache) il TLH di produzione
// ----------------------------------------------------------------
function getTlhCode(callback) {
    const cacheValid = fs.existsSync(CACHE_PATH) &&
        (Date.now() - fs.statSync(CACHE_PATH).mtimeMs) < CACHE_MAX_AGE_MS;

    if (cacheValid) {
        return callback(null, fs.readFileSync(CACHE_PATH, 'utf8'));
    }

    process.stderr.write('[tlh_helper] Downloading TLH from ' + TLH_URL + '\n');
    let data = '';
    https.get(TLH_URL, res => {
        res.on('data', chunk => data += chunk);
        res.on('end', () => {
            fs.writeFileSync(CACHE_PATH, data);
            callback(null, data);
        });
    }).on('error', err => callback(err));
}

// ----------------------------------------------------------------
// 2. Estrai solo la sezione delle config e fai matching
//
// Nel TLH di produzione la struttura è:
//   - linee 1-14198  : setup + codice_applicativo.js (crasha in Node.js, non ci serve)
//   - linee 14199-~25706: tlh_configs.push(...) di tutte le testate
//   - linee 25707+   : window.TLH_UserStatus + resto (non ci serve)
//
// Estraiamo solo la sezione delle push e la eseguiamo con un contesto minimale.
// ----------------------------------------------------------------
function matchUrl(tlhCode, url) {

    // Trova la sezione delle push
    const lines = tlhCode.split('\n');
    const firstPushLine = lines.findIndex(l => l.includes('tlh_configs.push'));
    // Fine sezione: prima riga che inizia un nuovo blocco dopo le push
    const endMarkers = ['window.TLH_UserStatus', 'window.kw_configuration_handler'];
    let lastPushLine = lines.length - 1;
    for (let i = firstPushLine + 1; i < lines.length; i++) {
        if (endMarkers.some(m => lines[i].includes(m))) {
            lastPushLine = i - 1;
            break;
        }
    }

    // Costruisci il codice da eseguire: setup globals + push section
    const windowGlobals = `
var window = {
    kw_nlsnComponentBasicUrl: "https://www.gedistatic.it/corporate/nielsen/",
    kw_cmp_consentInvalidationDate: "2026/01/01",
    kw_cmp_consentInvalidationDateRepubblica: "2026/01/01",
    kw_cmp_consentInvalidationDateSae: "2026/01/01",
    kw_cmp_consentInvalidationDateSaeLas: "2026/01/01",
    kw_cmp_consentInvalidationDateBlue: "2026/01/01",
    kw_cmp_consentInvalidationDateDeejay: "2026/01/01",
    kw_cmp_consentInvalidationDateNatGeo: "2026/01/01",
    kw_cmp_consentInvalidationDate3nz: "2026/01/01",
    kw_cmp_consentInvalidationHuffPost: "2026/01/01",
    kw_cmp_consentInvalidationDateFP: "2026/01/01",
    kw_cmp_consentInvalidationDateLedi: "2026/01/01",
    kw_cmp_consentInvalidationDateRepubblicaEurope: "2026/01/01",
};
var tlh_configs = [];
`;
    const pushSection = lines.slice(firstPushLine, lastPushLine + 1).join('\n');
    const codeToRun = windowGlobals + pushSection;

    const context = vm.createContext({
        console: { log: () => {}, warn: () => {}, error: () => {} },
        Error, RegExp, Array, Object, JSON, Math, Date,
    });

    try {
        vm.runInContext(codeToRun, context, { timeout: 5000 });
    } catch (e) {
        process.stderr.write('[tlh_helper] Errore nell\'esecuzione delle config: ' + e.message + '\n');
    }

    const configs = context.tlh_configs || [];

    for (let i = 0; i < configs.length; i++) {
        const entry = configs[i];
        if (!entry || !entry[0]) continue;
        try {
            const matchesMain  = url.match(entry[0]);
            const matchesVideo = entry[1] !== false && entry[1] && url.match(entry[1]);
            if (matchesMain || matchesVideo) {
                const cfg = entry[2] || {};
                return {
                    matched: true,
                    brand: cfg.activeBrand || null,
                    activeHost: cfg.activeHost || null,
                    hasNielsenConfig: !!(cfg.nielsenStatic),
                    nielsenStaticUrl: cfg.nielsenStatic || null,
                };
            }
        } catch (_) { /* regex malformata, salta */ }
    }

    return { matched: false, brand: null, activeHost: null, hasNielsenConfig: false, nielsenStaticUrl: null };
}

function parseUrl(url) {
    try {
        const u = new URL(url);
        return { href: u.href, hostname: u.hostname, pathname: u.pathname, search: u.search, origin: u.origin, protocol: u.protocol };
    } catch (_) {
        return { href: url, hostname: '', pathname: '', search: '', origin: '', protocol: 'https:' };
    }
}

// ----------------------------------------------------------------
// Main
// ----------------------------------------------------------------
function runBatch(urls, tlhCode) {
    const results = {};
    for (const url of urls) {
        results[url] = matchUrl(tlhCode, url);
    }
    console.log(JSON.stringify(results));
}

if (readStdin) {
    // Legge tutte le URL da stdin, poi processa
    let stdinData = '';
    process.stdin.setEncoding('utf8');
    process.stdin.on('data', chunk => stdinData += chunk);
    process.stdin.on('end', () => {
        const urls = stdinData.split('\n').map(u => u.trim()).filter(Boolean);
        getTlhCode((err, code) => {
            if (err) { console.log(JSON.stringify({ error: String(err) })); process.exit(1); }
            runBatch(urls, code);
        });
    });
} else {
    getTlhCode((err, code) => {
        if (err) { console.log(JSON.stringify({ error: String(err) })); process.exit(1); }
        if (targetUrls.length === 1) {
            // Compatibilità con vecchio formato single-URL: output semplice
            console.log(JSON.stringify(matchUrl(code, targetUrls[0])));
        } else {
            runBatch(targetUrls, code);
        }
    });
}
