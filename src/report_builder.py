"""
Costruisce il file Excel di report con i risultati dell'analisi.

Sheet prodotti:
  - "Riepilogo"          : numeriche aggregate
  - "Errore 21 - ..."    : dettaglio URL per errore (uno sheet per codice errore)
"""

import os
from datetime import datetime
import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter


# Palette colori
COL_HEADER_BG = "1F4E79"
COL_HEADER_FG = "FFFFFF"
COL_OK        = "C6EFCE"   # verde chiaro
COL_KO        = "FFC7CE"   # rosso chiaro
COL_WARN      = "FFEB9C"   # giallo
COL_NEUTRAL   = "FFFFFF"


def build_report(segnalazioni, tlh_results, playwright_results, output_path):
    """
    Costruisce il file Excel e lo salva in output_path.

    - segnalazioni     : lista di dict da excel_parser
    - tlh_results      : { url: dict } da tlh_matcher (solo GEDI)
    - playwright_results: { url: dict } da playwright_checker
    - output_path      : cartella dove salvare il file

    Restituisce il path del file creato.
    """
    os.makedirs(output_path, exist_ok=True)

    wb = openpyxl.Workbook()
    wb.remove(wb.active)  # rimuove il foglio di default

    # Raggruppa segnalazioni per codice errore
    by_errore = {}
    for s in segnalazioni:
        key = s['errore']
        if key not in by_errore:
            by_errore[key] = []
        by_errore[key].append(s)

    # Un sheet per ogni codice errore
    for errore_code, items in sorted(by_errore.items()):
        descr = items[0]['descr'] if items else ""
        sheet_name = f"{errore_code} - {descr}"[:31]  # Excel max 31 char
        ws = wb.create_sheet(title=sheet_name)
        _fill_errore_sheet(ws, errore_code, items, tlh_results, playwright_results)

    # Sheet riepilogo (in prima posizione)
    ws_riepilogo = wb.create_sheet(title="Riepilogo", index=0)
    _fill_riepilogo_sheet(ws_riepilogo, segnalazioni, tlh_results, playwright_results)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M")
    filename = f"nielsen_autodebug_{timestamp}.xlsx"
    filepath = os.path.join(output_path, filename)
    wb.save(filepath)
    print(f"\n[report] Salvato: {filepath}")
    return filepath


def _fill_errore_sheet(ws, errore_code, segnalazioni, tlh_results, playwright_results):
    """Riempie uno sheet con il dettaglio delle URL per un codice errore."""

    headers = [
        "URL",
        "Sito",
        "SDK in pagina",
        "Ping inviato",
        "Config TLH trovata",
        "Brand TLH",
        "Config Nielsen",
        "Nielsen Static URL",
        "Gruppo",
        "Testata",
        "Tipo accesso",
        "Note",
        "Soluzione",
    ]

    # Intestazione
    ws.append(headers)
    _style_header_row(ws, 1, len(headers))

    # Raccoglie URL uniche con le segnalazioni collegate
    url_map = {}
    for s in segnalazioni:
        url = s['url']
        if url not in url_map:
            url_map[url] = []
        url_map[url].append(s)

    for row_idx, (url, items) in enumerate(url_map.items(), start=2):
        # Aggrega le testate che hanno segnalato la stessa URL
        testate = sorted(set(i['testata'] for i in items))
        tipi    = sorted(set(i['tipo']    for i in items))
        gruppi  = sorted(set(i['gruppo']  for i in items))
        is_gedi = any(i['is_gedi'] for i in items)

        tlh  = tlh_results.get(url, {})
        pw   = playwright_results.get(url, {})

        tlh_matched  = tlh.get('matched',     False)
        has_nielsen  = tlh.get('has_nielsen', False)
        sdk_loaded   = pw.get('sdk_loaded')   # può essere True, False o None (skip)
        ping_sent    = pw.get('ping_sent')    # può essere True, False o None (skip)
        pw_skipped   = pw.get('skipped',      False)

        note_parts = []
        if tlh.get('error'):
            note_parts.append(f"TLH err: {tlh['error']}")
        if pw.get('error'):
            note_parts.append(f"PW err: {pw['error']}")
        if pw.get('final_url'):
            note_parts.append(f"Redirect -> {pw['final_url'][:80]}")
        if pw.get('http_status') and pw['http_status'] >= 400:
            note_parts.append(f"HTTP {pw['http_status']}")
        if pw.get('homepage_no_sdk'):
            note_parts.append("Homepage senza mapping Nielsen")

        # Soluzione: suggerisce l'azione correttiva per URL GEDI senza config Nielsen
        soluzione = ""
        if is_gedi:
            if not tlh_matched and not tlh.get('error'):
                soluzione = "Inserire config TLH"
            elif tlh_matched and not has_nielsen:
                soluzione = "Inserire config TLH"

        row = [
            url,
            "Interno GEDI" if is_gedi else "Terzo Manzoni",
            "N/A" if pw_skipped else _yesno(sdk_loaded),
            "N/A" if pw_skipped else _yesno(ping_sent),
            _yesno(tlh_matched) if is_gedi else "N/A",
            tlh.get('brand') or ("" if is_gedi else "N/A"),
            (_yesno(has_nielsen) if tlh_matched else ("" if is_gedi else "N/A")) if is_gedi else "N/A",
            tlh.get('nielsen_static') or ("" if is_gedi else "N/A"),
            ", ".join(gruppi),
            ", ".join(testate),
            ", ".join(tipi),
            " | ".join(note_parts),
            soluzione,
        ]
        ws.append(row)

        # Colori SDK/Ping: verde=sì, rosso=no, bianco=check non eseguito
        fill_sdk  = PatternFill("solid", fgColor=COL_OK if sdk_loaded else (COL_NEUTRAL if pw_skipped else COL_KO))
        fill_ping = PatternFill("solid", fgColor=COL_OK if ping_sent  else (COL_NEUTRAL if pw_skipped else COL_KO))

        col_sdk  = headers.index("SDK in pagina") + 1
        col_ping = headers.index("Ping inviato")  + 1
        ws.cell(row_idx, col_sdk).fill  = fill_sdk
        ws.cell(row_idx, col_ping).fill = fill_ping

        if is_gedi:
            fill_tlh     = PatternFill("solid", fgColor=COL_OK if tlh_matched else COL_KO)
            fill_nielsen = PatternFill("solid", fgColor=(COL_OK if has_nielsen else COL_WARN) if tlh_matched else COL_KO)
            col_tlh     = headers.index("Config TLH trovata") + 1
            col_nielsen = headers.index("Config Nielsen")     + 1
            ws.cell(row_idx, col_tlh).fill     = fill_tlh
            ws.cell(row_idx, col_nielsen).fill = fill_nielsen

        if soluzione:
            col_sol = headers.index("Soluzione") + 1
            ws.cell(row_idx, col_sol).fill = PatternFill("solid", fgColor=COL_WARN)

    _autofit_columns(ws)
    ws.freeze_panes = "A2"


def _fill_riepilogo_sheet(ws, segnalazioni, tlh_results, playwright_results):
    """Riempie il foglio Riepilogo con le numeriche aggregate."""

    # URL uniche totali
    all_urls   = set(s['url'] for s in segnalazioni)
    gedi_urls  = set(s['url'] for s in segnalazioni if s['is_gedi'])
    terzi_urls = all_urls - gedi_urls

    # Playwright
    sdk_ok  = sum(1 for u in all_urls if playwright_results.get(u, {}).get('sdk_loaded'))
    ping_ok = sum(1 for u in all_urls if playwright_results.get(u, {}).get('ping_sent'))
    pw_err  = sum(1 for u in all_urls if playwright_results.get(u, {}).get('error'))

    # TLH (solo GEDI)
    tlh_matched  = sum(1 for u in gedi_urls if tlh_results.get(u, {}).get('matched'))
    tlh_nielsen  = sum(1 for u in gedi_urls if tlh_results.get(u, {}).get('has_nielsen'))
    tlh_no_match = len(gedi_urls) - tlh_matched
    tlh_err      = sum(1 for u in gedi_urls if tlh_results.get(u, {}).get('error'))

    from collections import Counter
    by_testata = Counter(s['testata'] for s in segnalazioni)

    rows = [
        ["Auto Debug Nielsen - Riepilogo", ""],
        ["Data analisi", datetime.now().strftime("%d/%m/%Y %H:%M")],
        ["", ""],
        ["URL ANALIZZATE", ""],
        ["URL totali (uniche)", len(all_urls)],
        ["URL siti interni GEDI", len(gedi_urls)],
        ["URL editori terzi Manzoni", len(terzi_urls)],
        ["", ""],
        ["VERIFICA TLH (solo GEDI)", ""],
        ["Con config TLH trovata", tlh_matched],
        ["Con config Nielsen (nielsenStatic)", tlh_nielsen],
        ["Senza config TLH", tlh_no_match],
        ["Errori TLH check", tlh_err],
        ["", ""],
        ["VERIFICA PLAYWRIGHT (tutte le URL)", ""],
        ["SDK Nielsen caricato in pagina", sdk_ok],
        ["Ping Nielsen inviato", ping_ok],
        ["SDK non trovato", len(all_urls) - sdk_ok],
        ["Ping non trovato", len(all_urls) - ping_ok],
        ["Errori Playwright (pagina non caricata)", pw_err],
        ["", ""],
        ["SEGNALAZIONI PER TESTATA", ""],
    ]

    for row in rows:
        ws.append(row)

    for testata, count in by_testata.most_common():
        ws.append([testata, count])

    # Stile titolo
    ws["A1"].font = Font(bold=True, size=14)
    ws.column_dimensions["A"].width = 45
    ws.column_dimensions["B"].width = 20

    _SECTION_HEADERS = {
        "URL ANALIZZATE",
        "VERIFICA TLH (solo GEDI)",
        "VERIFICA PLAYWRIGHT (tutte le URL)",
        "SEGNALAZIONI PER TESTATA",
    }
    for cell in ws["A"]:
        if cell.value and str(cell.value) in _SECTION_HEADERS:
            cell.font = Font(bold=True)
            cell.fill = PatternFill("solid", fgColor="D9E1F2")


def _style_header_row(ws, row_num, num_cols):
    header_fill = PatternFill("solid", fgColor=COL_HEADER_BG)
    header_font = Font(bold=True, color=COL_HEADER_FG)
    for col in range(1, num_cols + 1):
        cell = ws.cell(row_num, col)
        cell.fill = header_fill
        cell.font = header_font
        cell.alignment = Alignment(horizontal="center", wrap_text=True)


def _autofit_columns(ws):
    for col in ws.columns:
        max_len = 0
        col_letter = get_column_letter(col[0].column)
        for cell in col:
            if cell.value:
                max_len = max(max_len, len(str(cell.value)))
        # Cap a 60 per non avere colonne enormi
        ws.column_dimensions[col_letter].width = min(max_len + 2, 60)


def _yesno(value):
    return "Sì" if value else "No"
