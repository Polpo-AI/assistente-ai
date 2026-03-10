# 🖥️ Guida Comandi Server — Polpo AI (v3 Systemd)

## Connettersi al server
```bash
ssh root@46.225.212.159
```
Le cartelle di lavoro sono:
- Produzione: `/opt/polpo-ai` (branch `main`)
- Staging: `/opt/polpo-staging` (branch `preview`)

---

## Gestione Servizi (Systemd)

Il sistema utilizza `systemd` per garantire che i processi siano sempre attivi e si riavviino in caso di crash.

### Nomi dei Servizi
- **Produzione**: `polpo-prod.service` & `polpo-worker.service`
- **Staging**: `polpo-staging.service` & `polpo-worker-staging.service`

### Comandi Comuni
Sostituisci `<servizio>` con uno dei nomi sopra (es. `polpo-worker`).

| Azione | Comando |
|---|---|
| Riavviare | `systemctl restart <servizio>` |
| Avviare | `systemctl start <servizio>` |
| Fermare | `systemctl stop <servizio>` |
| Stato | `systemctl status <servizio>` |
| Riavviare Tutti | `systemctl restart polpo-*` |

---

## Visualizzazione Log (Journalctl)

I log non sono più in file `.log` sparsi, ma gestiti dal sistema.

### Log in tempo reale (Follow)
```bash
journalctl -u polpo-worker -f
```

### Log recenti (Ultime 100 righe)
```bash
journalctl -u polpo-worker -n 100 --no-pager
```

### Log di una fascia oraria specifica
```bash
journalctl -u polpo-worker --since "15:00:00"
```

---

## Deploy Aggiornamenti

### Produzione
```bash
cd /opt/polpo-ai
git fetch origin main && git reset --hard origin/main
systemctl restart polpo-prod polpo-worker
```

### Staging
```bash
cd /opt/polpo-staging
git fetch origin preview && git reset --hard origin/preview
systemctl restart polpo-staging polpo-worker-staging
```

---

## Diagnostica Rapida

### Controllare se i processi sono attivi
```bash
systemctl list-units "polpo-*"
```

### Verificare l'uso delle risorse
```bash
htop
```

---

## Note Tecniche
- **Virtualenv**: Ogni ambiente ha il suo venv in `venv/`.
- **IMAP Last UID**: Salvato nel database Supabase. Abbassarlo per forzare un re-scan.
- **Locking**: Gestito internamente tramite database e flag di stato, non più tramite file `/tmp/`.
