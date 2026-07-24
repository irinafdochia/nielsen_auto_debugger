"""
Invio mail con il report Excel in allegato.

Usa SMTP con STARTTLS (Office 365 di default).
Le credenziali si leggono da config.yaml oppure dalla variabile d'ambiente SMTP_PASSWORD.
"""

import os
import smtplib
from email.message import EmailMessage
from datetime import datetime


def invia_report(filepaths, testo_corpo, config):
    """
    Invia i file Excel come allegati.

    - filepaths   : path o lista di path dei file Excel generati
    - testo_corpo : testo della mail (numeriche aggregate)
    - config      : dict con le chiavi mail di config.yaml
    """
    if isinstance(filepaths, str):
        filepaths = [filepaths]
    mail_cfg = config.get('mail', {})

    mittente     = mail_cfg.get('mittente', '')
    destinatari  = mail_cfg.get('destinatari', [])
    oggetto      = mail_cfg.get('oggetto', 'Auto Debug Nielsen')
    smtp_host    = mail_cfg.get('smtp_host', 'smtp.office365.com')
    smtp_port    = int(mail_cfg.get('smtp_port', 587))
    smtp_user    = mail_cfg.get('smtp_user', mittente)
    smtp_password = os.environ.get('SMTP_PASSWORD') or mail_cfg.get('smtp_password', '')

    if not destinatari:
        print("[mail] Nessun destinatario configurato, skip invio mail.")
        return

    if not smtp_password:
        print("[mail] SMTP_PASSWORD non configurata. Aggiungila in config.yaml o come variabile d'ambiente.")
        print("[mail] Skip invio mail.")
        return

    # Sostituisce {mese} nell'oggetto con il mese corrente
    oggetto = oggetto.replace("{mese}", datetime.now().strftime("%m/%Y"))

    msg = EmailMessage()
    msg['From']    = mittente
    msg['To']      = ", ".join(destinatari)
    msg['Subject'] = oggetto
    msg.set_content(testo_corpo)

    # Allegati Excel
    for filepath in filepaths:
        with open(filepath, 'rb') as f:
            msg.add_attachment(
                f.read(),
                maintype='application',
                subtype='vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                filename=os.path.basename(filepath),
            )

    try:
        with smtplib.SMTP(smtp_host, smtp_port) as smtp:
            smtp.ehlo()
            smtp.starttls()
            smtp.login(smtp_user, smtp_password)
            smtp.send_message(msg)
        print(f"[mail] Inviata a: {', '.join(destinatari)}")
    except Exception as e:
        print(f"[mail] Errore invio: {e}")


def build_testo_mail(segnalazioni, tlh_results, playwright_results):
    """
    Costruisce il testo di riepilogo da usare come corpo della mail.
    """
    all_urls   = set(s['url'] for s in segnalazioni)
    gedi_urls  = set(s['url'] for s in segnalazioni if s['is_gedi'])
    terzi_urls = all_urls - gedi_urls

    sdk_ok   = sum(1 for u in all_urls if playwright_results.get(u, {}).get('sdk_loaded'))
    ping_ok  = sum(1 for u in all_urls if playwright_results.get(u, {}).get('ping_sent'))
    tlh_ok   = sum(1 for u in gedi_urls if tlh_results.get(u, {}).get('matched'))
    niel_ok  = sum(1 for u in gedi_urls if tlh_results.get(u, {}).get('has_nielsen'))

    mese = segnalazioni[0]['xlsx_path'].split(os.sep)[-4] if segnalazioni else "N/D"

    lines = [
        f"Auto Debug Nielsen - Segnalazioni Audicom",
        f"Periodo analizzato: {mese}",
        f"Data analisi: {datetime.now().strftime('%d/%m/%Y %H:%M')}",
        "",
        "--- RIEPILOGO ---",
        f"URL uniche analizzate : {len(all_urls)}",
        f"  di cui GEDI         : {len(gedi_urls)}",
        f"  di cui terzi Manzoni: {len(terzi_urls)}",
        "",
        "--- VERIFICA TLH (solo GEDI) ---",
        f"Config TLH trovata       : {tlh_ok} / {len(gedi_urls)}",
        f"Config Nielsen presente  : {niel_ok} / {len(gedi_urls)}",
        f"Senza config TLH         : {len(gedi_urls) - tlh_ok} / {len(gedi_urls)}",
        "",
        "--- VERIFICA PLAYWRIGHT ---",
        f"SDK Nielsen in pagina : {sdk_ok} / {len(all_urls)}",
        f"Ping Nielsen inviato  : {ping_ok} / {len(all_urls)}",
        f"SDK non trovato       : {len(all_urls) - sdk_ok} / {len(all_urls)}",
        f"Ping non trovato      : {len(all_urls) - ping_ok} / {len(all_urls)}",
        "",
        "Il dettaglio completo è nel file Excel allegato.",
    ]

    return "\n".join(lines)
