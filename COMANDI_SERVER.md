# 🖥️ Guida Comandi Server — Polpo AI

## Connettersi al server
```bash
ssh root@46.225.212.159
cd /opt/polpo-ai && source venv/bin/activate
```

---

## Gestione Worker

### Avviare il worker (in background, sopravvive alla chiusura del terminale)
```bash
nohup python email_worker.py > /opt/polpo-ai/worker.log 2>&1 &
```

### Riavviare il worker (kill + pulizia lock + start)
```bash
pkill -f email_worker.py; rm -f /tmp/polpo_email_worker.lock && sleep 1 && nohup python email_worker.py > /opt/polpo-ai/worker.log 2>&1 &
```

### Verificare se il worker sta girando
```bash
ps aux | grep email_worker
```
Se vedi una riga con `/opt/polpo-ai/venv/bin/python email_worker.py` → sta girando.
Se vedi solo la riga `grep` → non sta girando.

### Vedere i log in tempo reale
```bash
tail -f /opt/polpo-ai/worker.log
```
Premi `Ctrl+C` per uscire dal log (il worker continua a girare).

### Killare il worker
```bash
pkill -f email_worker.py
```

### Lock file bloccato (worker non parte)
```bash
rm -f /tmp/polpo_email_worker.lock
```

---

## Deploy aggiornamenti

### Flusso completo (da fare ogni volta che puschi su GitHub)
```bash
pkill -f email_worker.py; rm -f /tmp/polpo_email_worker.lock && git pull && nohup python email_worker.py > /opt/polpo-ai/worker.log 2>&1 &
```

### Solo aggiornare il codice senza riavviare
```bash
git pull
```

---

## Diagnostica

### Controllare l'ultimo UID processato
```bash
python3 -c "
from database import get_client
db = get_client()
r = db.table('clients').select('imap_last_uid').eq('id', 'e15d59d6-24ec-44df-bc20-fe05a9dce8ba').execute()
print('imap_last_uid:', r.data[0]['imap_last_uid'])
"
```

### Vedere l'ultima email salvata nel DB
```bash
python3 -c "
from database import get_client
db = get_client()
r = db.table('emails').select('sender_email, subject').order('received_at', desc=True).limit(1).execute()
print(r.data[0])
"
```

### Testare il login IMAP manualmente
```bash
python3 -c "
import asyncio, aioimaplib
async def test():
    imap = aioimaplib.IMAP4_SSL('imap.gmail.com', 993)
    await imap.wait_hello_from_server()
    result = await imap.login('dcdavi9@gmail.com', 'PASSWORD_SENZA_SPAZI')
    print('Login:', result)
asyncio.run(test())
"
```

---

## Stato salute server

### Risorse sistema
```bash
htop        # CPU e RAM in tempo reale (esci con q)
df -h       # spazio disco
free -h     # memoria RAM
```

### Processi attivi
```bash
ps aux | grep python    # tutti i processi Python
```

---

## Note importanti

- **Password Gmail app**: vanno salvate nel DB con spazi (`kwxb aeom gpse erjv`), il codice le sanitizza automaticamente prima del login.
- **Lock file**: `/tmp/polpo_email_worker.lock` — viene creato all'avvio e rimosso allo stop. Se il worker crasha senza pulizia, va rimosso manualmente.
- **Log file**: `/opt/polpo-ai/worker.log` — viene sovrascritto ad ogni riavvio del worker.
- **imap_last_uid**: salvato nel DB nella tabella `clients`. Se si azzera o si abbassa, il worker riprocessa le email vecchie.
