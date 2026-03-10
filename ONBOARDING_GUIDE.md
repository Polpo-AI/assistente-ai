# 🐙 Guida all'Onboarding Polpo AI
Questo documento descrive passo-passo il processo di configurazione di un nuovo cliente su Polpo AI.
È strutturato per essere utilizzato durante la discovery call e ottimizzato per la generazione automatica di slide tramite IA.

---

## 📅 Fase 1: Profilo e Stile
**Obiettivo:** Definire l'identità del bot e il suo tono di voce.
**Durata stimata:** 1-5 minuti

| Campo | Descrizione | Esempio |
|---|---|---|
| Nome Azienda | Nome ufficiale dell'attività | Officina Rossi S.r.l. |
| Settore | Settore di riferimento | Automotive, Sanitario, Legale, Edilizia |
| Lingua | Lingua di risposta del bot | Italiano, Inglese, Bilingue |
| Tono di voce | Carattere comunicativo | Professionale, Formale, Friendly, Tecnico |
| Persona | Identità del bot | "Sei l'assistente virtuale dell'Officina Rossi" |
| Firma | Firma ufficiale in fondo alle email | "Cordiali saluti, Team Officina Rossi" |

---

## 📱 Fase 2: Connessioni Tecniche
**Obiettivo:** Collegare la casella email del cliente al sistema Polpo AI.
**Durata stimata:** 5 minuti

Polpo AI si connette direttamente alla casella email del cliente tramite due protocolli standard:
- **IMAP** per leggere le email in arrivo
- **SMTP** per inviare le risposte approvate

Non è necessario cambiare provider email. Il sistema funziona con qualsiasi casella esistente.

**Dati richiesti:**

| Campo | Descrizione |
|---|---|
| Telegram Chat ID | ID numerico per le notifiche (ottenibile via @userinfobot) |
| IMAP Host | Indirizzo server in entrata |
| IMAP Porta | Porta server in entrata (default: 993) |
| IMAP Utente | Indirizzo email completo |
| IMAP Password | Password applicazione (vedi Fase 2a) |
| SMTP Host | Indirizzo server in uscita |
| SMTP Porta | Porta server in uscita (default: 587) |
| SMTP Utente | Indirizzo email completo |
| SMTP Password | Password applicazione (vedi Fase 2a) |

---

## 🔑 Fase 2a: Generazione Password Applicazione
**Obiettivo:** Creare una password sicura dedicata a Polpo AI, separata dalla password principale.

> ⚠️ La password principale dell'email non viene mai utilizzata né salvata.
> Polpo AI usa una "Password Applicazione": una password separata, revocabile in qualsiasi momento.

### Gmail (@gmail.com o Google Workspace)

**Dati di connessione:**
```
IMAP Host: imap.gmail.com       Porta: 993
SMTP Host: smtp.gmail.com       Porta: 587
```

**Come generare la Password Applicazione:**
1. Accedere a myaccount.google.com
2. Andare su Sicurezza → Verifica in due passaggi (deve essere attiva)
3. Cercare "Password per le app" in fondo alla pagina
4. Selezionare "Posta" come app → cliccare Genera
5. Copiare la password di 16 caratteri generata
6. Consegnare quella password (non la password principale)

---

### Outlook / Microsoft 365 (@outlook.com, @hotmail.com, dominio aziendale Microsoft)

**Dati di connessione:**
```
IMAP Host: outlook.office365.com    Porta: 993
SMTP Host: smtp.office365.com       Porta: 587
```

**Come generare la Password Applicazione:**
1. Accedere a account.microsoft.com
2. Andare su Sicurezza → Opzioni di sicurezza avanzate
3. Nella sezione "Password per le app" → cliccare Crea nuova password app
4. Copiare la password generata
5. Consegnare quella password (non la password principale)

> ⚠️ Se l'account è aziendale Microsoft 365, l'amministratore IT potrebbe dover abilitare IMAP/SMTP prima dell'onboarding.

---

### Aruba (domini custom su Aruba)

**Dati di connessione:**
```
IMAP Host: imaps.aruba.it       Porta: 993
SMTP Host: smtps.aruba.it       Porta: 465
```

**Come configurare:**
1. Accedere al pannello admin.aruba.it
2. Verificare che IMAP sia abilitato: Gestione Email → Impostazioni → Accesso IMAP
3. Aruba non supporta Password Applicazione: si usa la password normale della casella email
4. Consegnare la password della casella email

> ℹ️ La password viene cifrata e salvata in modo sicuro nel sistema.

---

### Register.it e altri hosting italiani

**Dati di connessione (esempio Register.it):**
```
IMAP Host: imap.register.it     Porta: 993
SMTP Host: smtp.register.it     Porta: 587
```

**Come configurare:**
1. Accedere al pannello di controllo dell'hosting
2. Trovare i dati IMAP/SMTP nella sezione Gestione Email o Webmail
3. Usare la password normale della casella email
4. I dati esatti si trovano anche nell'email di benvenuto del provider

> ℹ️ Per tutti gli hosting italiani (Keliweb, Tophost, Serverplan ecc.) i dati IMAP/SMTP si trovano sempre nel pannello di controllo o nell'email di benvenuto iniziale.

---

## 📞 Fase 3: Informazioni Aziendali
**Obiettivo:** Fornire al bot i dati che userà nelle risposte.
**Durata stimata:** 5 minuti

| Categoria | Dati richiesti |
|---|---|
| Contatti | Telefono fisso, Cellulare/WhatsApp, Email di riferimento, Indirizzo fisico |
| Web | Sito web ufficiale, Link prenotazioni online |
| Orari | Orari per ogni giorno della settimana, Eventuale pausa pranzo |

---

## 🧠 Fase 4: Intelligenza e Logica
**Obiettivo:** Definire come il bot classifica le email e cosa fa con ciascuna.
**Durata stimata:** 10 minuti

### 4.1 — Tipi di Email (Intenti)
Quali sono i motivi principali per cui i clienti scrivono?

**Intenti suggeriti:** Preventivo (con PDF automatico) · Appuntamento · Informazione · Reclamo · Pagamento · Spam

Il sistema può generare automaticamente documenti PDF formali (preventivi, conferme d'ordine) basandosi sui listini prezzi configurati.

### 4.2 — Priorità e Azioni

| Priorità | Comportamento | Esempio d'uso |
|---|---|---|
| 🟢 BASSA | Il bot risponde e invia in autonomia | Informazioni generali, spam |
| 🟡 MEDIA | Il bot genera la bozza e notifica via Telegram per approvazione | Preventivi, appuntamenti |
| 🔴 URGENTE | Alert immediato al titolare, nessun invio automatico | Reclami, urgenze mediche |

### 4.3 — Istruzioni Personalizzate per Intento
Per ogni tipo di email è possibile definire un comportamento specifico del bot.

| Intento | Esempio di istruzione |
|---|---|
| Reclamo | "Chiedi scusa, non fornire spiegazioni tecniche, offri un controllo gratuito" |
| Preventivo | "Chiedi sempre il modello dell'auto e l'anno di immatricolazione" |
| Appuntamento | "Proponi sempre 2-3 fasce orarie basandoti sugli orari di apertura" |

---

## ❓ Fase 5: Base di Conoscenza (FAQ)
**Obiettivo:** Fornire al bot risposte ufficiali alle domande frequenti, senza inventare informazioni.
**Durata stimata:** Facoltativa, 5-10 minuti

| Domanda | Risposta |
|---|---|
| Accettate pagamenti rateali? | Sì, offriamo rateizzazione fino a 12 mesi senza interessi |
| Quanto dura un tagliando? | In media 2 ore, con appuntamento |

---

## 🛡️ Fase 6: Filtri Spam
**Obiettivo:** Bloccare immediatamente email indesiderate senza sprecare risorse AI.
**Durata stimata:** 2 minuti

Lista di parole chiave da bloccare automaticamente.

**Esempi comuni:** "proposta commerciale" · "investimenti" · "trading" · "collaborazione sponsorizzata"

Il cliente può aggiungere mittenti specifici da bloccare in qualsiasi momento tramite Telegram.

---

## ✅ Conclusione
Al termine del processo viene generato un **Client ID** unico per il cliente.
Il sistema è immediatamente operativo: scarica, classifica e notifica la prima email via Telegram.

**Riepilogo tempi:**

| Fase | Durata |
|---|---|
| Fase 1 — Profilo e Stile | 1-5 min |
| Fase 2 — Connessioni Tecniche | 5 min |
| Fase 2a — Password Applicazione | 5 min |
| Fase 3 — Informazioni Aziendali | 5 min |
| Fase 4 — Intelligenza e Logica | 10 min |
| Fase 5 — FAQ (facoltativa) | 5-10 min |
| Fase 6 — Filtri Spam | 2 min |
| **Totale** | **~35 min** |
