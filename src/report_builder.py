"""
Costruisce i file Excel di report.

Due file separati:
  - nielsen_gedi_{timestamp}.xlsx    : siti interni GEDI — verifica config TLH
  - nielsen_manzoni_{timestamp}.xlsx : editori terzi Manzoni — verifica SDK + ping
"""

import os
from datetime import datetime
from collections import Counter
import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment
from openpyxl.utils import get_column_letter


COL_HEADER_BG = "1F4E79"
COL_HEADER_FG = "FFFFFF"
COL_OK        = "C6EFCE"
COL_KO        = "FFC7CE"
COL_WARN      = "FFEB9C"


def build_reports(segnalazioni, tlh_results, playwright_results, output_path):
    """
    Genera i due file Excel e restituisce (gedi_path, manzoni_path).

    - segnalazioni      : lista di dict da excel_parser
    - tlh_results       : { url: dict } da tlh_matcher (solo GEDI)
    - playwright_results: { url: dict } da playwright_checker (solo Manzoni)
    - output_path       : cartella dove salvare i file
    """
    os.makedirs(output_path, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M")

    seg_gedi    = [s for s in segnalazioni if s['is_gedi']]
    seg_manzoni = [s for s in segnalazioni if not s['is_gedi']]

    gedi_path    = _build_gedi_report(seg_gedi, tlh_results, playwright_results, output_path, timestamp)
    manzoni_path = _build_manzoni_report(seg_manzoni, playwright_results, output_path, timestamp)

    return gedi_path, manzoni_path


# ─────────────────────────────────────────────
# File 1: GEDI — verifica config TLH
# ─────────────────────────────────────────────

def _build_gedi_report(segnalazioni, tlh_results, playwright_results, output_path, timestamp):
    wb = openpyxl.Workbook()
    wb.remove(wb.active)

    for errore_code, items in sorted(_group_by_errore(segnalazioni).items()):
        descr = items[0]['descr'] if items else ""
        ws = wb.create_sheet(title=f"{errore_code} - {descr}"[:31])
        _fill_gedi_sheet(ws, items, tlh_results, playwright_results)

    ws_rip = wb.create_sheet(title="Riepilogo", index=0)
    _fill_gedi_riepilogo(ws_rip, segnalazioni, tlh_results, playwright_results)

    filepath = os.path.join(output_path, f"nielsen_gedi_{timestamp}.xlsx")
    wb.save(filepath)
    print(f"[report] GEDI: {filepath}")
    return filepath


def _fill_gedi_sheet(ws, segnalazioni, tlh_results, playwright_results):
    headers = [
        "URL",
        "Testata",
        "TLH in pagina",
        "Config TLH trovata",
        "Mapping Nielsen",
        "Soluzione",
        "SDK in pagina",
        "Ping inviato",
        "Nielsen Static URL",
        "Note",
        "Tipo accesso",
    ]
    ws.append(headers)
    _style_header_row(ws, 1, len(headers))

    for row_idx, (url, items) in enumerate(_dedup_by_url(segnalazioni).items(), start=2):
        testate = sorted(set(i['testata'] for i in items))
        tipi    = sorted(set(i['tipo']    for i in items))

        tlh         = tlh_results.get(url, {})
        pw          = playwright_results.get(url, {})
        tlh_matched = tlh.get('matched',     False)
        has_nielsen = tlh.get('has_nielsen', False)
        tlh_in_page = pw.get('tlh_loaded',  False)
        sdk_loaded  = pw.get('sdk_loaded',  False)
        ping_sent   = pw.get('ping_sent',   False)
        pw_skipped  = bool(pw.get('skipped_reason') or pw.get('http_to_https'))

        # Soluzione: calcolata solo se l'analisi Playwright e' stata eseguita
        soluzione = ""
        if not pw_skipped and not tlh.get('error'):
            if not tlh_in_page:
                soluzione = "Inserire TLH in pagina"
            elif not tlh_matched:
                soluzione = "Aggiungere config TLH"
            elif not has_nielsen:
                soluzione = "Aggiungere mapping Nielsen"

        note_parts = []
        if pw.get('skipped_reason'):
            note_parts.append(pw['skipped_reason'])
        if pw.get('http_to_https'):
            note_parts.append("Redirect HTTP->HTTPS: escludere dalle segnalazioni Audicom")
        if tlh.get('error'):
            note_parts.append(f"TLH err: {tlh['error']}")
        if pw.get('error'):
            note_parts.append(f"PW err: {pw['error']}")
        if pw.get('final_url') and not pw.get('http_to_https'):
            note_parts.append(f"Redirect -> {pw['final_url'][:80]}")
        if pw.get('http_status') and pw['http_status'] >= 400:
            note_parts.append(f"HTTP {pw['http_status']}")

        row = [
            url,
            ", ".join(testate),
            "N/A" if pw_skipped else _yesno(tlh_in_page),
            _yesno(tlh_matched),
            _yesno(has_nielsen) if tlh_matched else "",
            soluzione,
            "N/A" if pw_skipped else _yesno(sdk_loaded),
            "N/A" if pw_skipped else _yesno(ping_sent),
            tlh.get('nielsen_static') or "",
            " | ".join(note_parts),
            ", ".join(tipi),
        ]
        ws.append(row)

        _fill_cell = lambda col_name, ok, skipped=False: ws.cell(
            row_idx, headers.index(col_name) + 1
        ).__setattr__('fill', PatternFill("solid", fgColor="FFFFFF" if skipped else (COL_OK if ok else COL_KO)))

        _fill_cell("TLH in pagina",    tlh_in_page, skipped=pw_skipped)
        _fill_cell("Config TLH trovata", tlh_matched)
        if tlh_matched:
            ws.cell(row_idx, headers.index("Mapping Nielsen") + 1).fill = PatternFill(
                "solid", fgColor=COL_OK if has_nielsen else COL_WARN)
        _fill_cell("SDK in pagina", sdk_loaded, skipped=pw_skipped)
        _fill_cell("Ping inviato",  ping_sent,  skipped=pw_skipped)
        if soluzione:
            ws.cell(row_idx, headers.index("Soluzione") + 1).fill = PatternFill(
                "solid", fgColor=COL_WARN)

    _autofit_columns(ws)
    ws.freeze_panes = "A2"


def _fill_gedi_riepilogo(ws, segnalazioni, tlh_results, playwright_results):
    all_urls    = set(s['url'] for s in segnalazioni)
    tlh_in_page = sum(1 for u in all_urls if playwright_results.get(u, {}).get('tlh_loaded'))
    tlh_matched = sum(1 for u in all_urls if tlh_results.get(u, {}).get('matched'))
    tlh_nielsen = sum(1 for u in all_urls if tlh_results.get(u, {}).get('has_nielsen'))
    tlh_err     = sum(1 for u in all_urls if tlh_results.get(u, {}).get('error'))
    sdk_ok      = sum(1 for u in all_urls if playwright_results.get(u, {}).get('sdk_loaded'))
    ping_ok     = sum(1 for u in all_urls if playwright_results.get(u, {}).get('ping_sent'))
    by_testata  = Counter(s['testata'] for s in segnalazioni)

    _SECTIONS = {"URL ANALIZZATE", "VERIFICA IN PAGINA (Playwright)", "VERIFICA CONFIG TLH", "SEGNALAZIONI PER TESTATA"}
    rows = [
        ["Auto Debug Nielsen - Siti interni GEDI", ""],
        ["Data analisi", datetime.now().strftime("%d/%m/%Y %H:%M")],
        ["", ""],
        ["URL ANALIZZATE", ""],
        ["URL totali (uniche)", len(all_urls)],
        ["", ""],
        ["VERIFICA IN PAGINA (Playwright)", ""],
        ["TLH presente in pagina",         tlh_in_page],
        ["SDK Nielsen caricato",           sdk_ok],
        ["Ping Nielsen inviato",           ping_ok],
        ["", ""],
        ["VERIFICA CONFIG TLH", ""],
        ["Con config TLH trovata",         tlh_matched],
        ["Con config Nielsen (mapping)",   tlh_nielsen],
        ["Senza config TLH",               len(all_urls) - tlh_matched],
        ["Errori TLH check",               tlh_err],
        ["", ""],
        ["SEGNALAZIONI PER TESTATA", ""],
    ]
    for row in rows:
        ws.append(row)
    for testata, count in by_testata.most_common():
        ws.append([testata, count])

    ws["A1"].font = Font(bold=True, size=14)
    ws.column_dimensions["A"].width = 45
    ws.column_dimensions["B"].width = 20
    for cell in ws["A"]:
        if cell.value and str(cell.value) in _SECTIONS:
            cell.font = Font(bold=True)
            cell.fill = PatternFill("solid", fgColor="D9E1F2")


# ─────────────────────────────────────────────
# File 2: Manzoni — verifica SDK + ping
# ─────────────────────────────────────────────

def _build_manzoni_report(segnalazioni, playwright_results, output_path, timestamp):
    wb = openpyxl.Workbook()
    wb.remove(wb.active)

    for errore_code, items in sorted(_group_by_errore(segnalazioni).items()):
        descr = items[0]['descr'] if items else ""
        ws = wb.create_sheet(title=f"{errore_code} - {descr}"[:31])
        _fill_manzoni_sheet(ws, items, playwright_results)

    ws_rip = wb.create_sheet(title="Riepilogo", index=0)
    _fill_manzoni_riepilogo(ws_rip, segnalazioni, playwright_results)

    filepath = os.path.join(output_path, f"nielsen_manzoni_{timestamp}.xlsx")
    wb.save(filepath)
    print(f"[report] Manzoni: {filepath}")
    return filepath


def _fill_manzoni_sheet(ws, segnalazioni, playwright_results):
    headers = [
        "URL",
        "Gruppo",
        "Testata",
        "Tipo accesso",
        "SDK in pagina",
        "Ping inviato",
        "Note",
    ]
    ws.append(headers)
    _style_header_row(ws, 1, len(headers))

    for row_idx, (url, items) in enumerate(_dedup_by_url(segnalazioni).items(), start=2):
        gruppi  = sorted(set(i['gruppo']  for i in items))
        testate = sorted(set(i['testata'] for i in items))
        tipi    = sorted(set(i['tipo']    for i in items))

        pw         = playwright_results.get(url, {})
        sdk_loaded = pw.get('sdk_loaded', False)
        ping_sent  = pw.get('ping_sent',  False)
        pw_skipped = bool(pw.get('skipped_reason') or pw.get('http_to_https'))

        note_parts = []
        if pw.get('skipped_reason'):
            note_parts.append(pw['skipped_reason'])
        elif pw.get('http_to_https'):
            note_parts.append("URL HTTP con redirect HTTPS: da escludere dalle segnalazioni (non gestibile lato TLH)")
        if pw.get('error'):
            note_parts.append(f"PW err: {pw['error']}")
        if pw.get('final_url') and not pw.get('http_to_https'):
            note_parts.append(f"Redirect -> {pw['final_url'][:80]}")
        if pw.get('http_status') and pw['http_status'] >= 400:
            note_parts.append(f"HTTP {pw['http_status']}")

        row = [
            url,
            ", ".join(gruppi),
            ", ".join(testate),
            ", ".join(tipi),
            "N/A" if pw_skipped else _yesno(sdk_loaded),
            "N/A" if pw_skipped else _yesno(ping_sent),
            " | ".join(note_parts),
        ]
        ws.append(row)

        sdk_col = headers.index("SDK in pagina") + 1
        ping_col = headers.index("Ping inviato") + 1
        if pw_skipped:
            ws.cell(row_idx, sdk_col).fill  = PatternFill("solid", fgColor="FFFFFF")
            ws.cell(row_idx, ping_col).fill = PatternFill("solid", fgColor="FFFFFF")
        else:
            ws.cell(row_idx, sdk_col).fill  = PatternFill("solid", fgColor=COL_OK if sdk_loaded else COL_KO)
            ws.cell(row_idx, ping_col).fill = PatternFill("solid", fgColor=COL_OK if ping_sent  else COL_KO)

    _autofit_columns(ws)
    ws.freeze_panes = "A2"


def _fill_manzoni_riepilogo(ws, segnalazioni, playwright_results):
    all_urls   = set(s['url'] for s in segnalazioni)
    sdk_ok     = sum(1 for u in all_urls if playwright_results.get(u, {}).get('sdk_loaded'))
    ping_ok    = sum(1 for u in all_urls if playwright_results.get(u, {}).get('ping_sent'))
    pw_err     = sum(1 for u in all_urls if playwright_results.get(u, {}).get('error'))
    by_gruppo  = Counter(s['gruppo'] for s in segnalazioni)

    _SECTIONS = {"URL ANALIZZATE", "VERIFICA PLAYWRIGHT", "SEGNALAZIONI PER GRUPPO"}
    rows = [
        ["Auto Debug Nielsen - Editori terzi Manzoni", ""],
        ["Data analisi", datetime.now().strftime("%d/%m/%Y %H:%M")],
        ["", ""],
        ["URL ANALIZZATE", ""],
        ["URL totali (uniche)", len(all_urls)],
        ["", ""],
        ["VERIFICA PLAYWRIGHT", ""],
        ["SDK Nielsen caricato in pagina",    sdk_ok],
        ["Ping Nielsen inviato",              ping_ok],
        ["SDK non trovato",                   len(all_urls) - sdk_ok],
        ["Ping non trovato",                  len(all_urls) - ping_ok],
        ["Errori Playwright",                 pw_err],
        ["", ""],
        ["SEGNALAZIONI PER GRUPPO", ""],
    ]
    for row in rows:
        ws.append(row)
    for gruppo, count in by_gruppo.most_common():
        ws.append([gruppo, count])

    ws["A1"].font = Font(bold=True, size=14)
    ws.column_dimensions["A"].width = 45
    ws.column_dimensions["B"].width = 20
    for cell in ws["A"]:
        if cell.value and str(cell.value) in _SECTIONS:
            cell.font = Font(bold=True)
            cell.fill = PatternFill("solid", fgColor="D9E1F2")


# ─────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────

def _group_by_errore(segnalazioni):
    result = {}
    for s in segnalazioni:
        result.setdefault(s['errore'], []).append(s)
    return result


def _dedup_by_url(segnalazioni):
    result = {}
    for s in segnalazioni:
        result.setdefault(s['url'], []).append(s)
    return result


def _style_header_row(ws, row_num, num_cols):
    fill = PatternFill("solid", fgColor=COL_HEADER_BG)
    font = Font(bold=True, color=COL_HEADER_FG)
    for col in range(1, num_cols + 1):
        cell = ws.cell(row_num, col)
        cell.fill = fill
        cell.font = font
        cell.alignment = Alignment(horizontal="center", wrap_text=True)


def _autofit_columns(ws):
    for col in ws.columns:
        max_len    = 0
        col_letter = get_column_letter(col[0].column)
        for cell in col:
            if cell.value:
                max_len = max(max_len, len(str(cell.value)))
        ws.column_dimensions[col_letter].width = min(max_len + 2, 60)


def _yesno(value):
    return "Sì" if value else "No"
