# Segnalazioni Audicom

Metti qui la cartella mensile ricevuta da Audicom.

## Struttura attesa

```
segnalazioni/
└── MM_YYYY_GEDI-MANZONI/          ← la cartella del mese (es. 06_2026_GEDI-MANZONI)
    ├── Apps_Report_GEDI.xlsx
    ├── 230427_Audicom_PwC report_guida.pdf
    ├── GEDI Gruppo Editoriale/
    │   └── {Testata}/
    │       ├── semi_statico_desktop/
    │       │   └── *.xlsx
    │       └── semi_statico_mobile/
    │           └── *.xlsx
    └── {GruppoManzoni}/
        └── {Testata}/
            └── semi_statico_desktop/
                └── *.xlsx
```

## Aggiornamento mensile

Dopo aver copiato la nuova cartella, aggiorna `segnalazioni_path` in `config.yaml`:

```yaml
segnalazioni_path: segnalazioni/06_2026_GEDI-MANZONI   # ← cambia questo
```

Poi lancia:

```bash
python3 main.py --no-mail
```

I report generati finiscono in `output/`.
