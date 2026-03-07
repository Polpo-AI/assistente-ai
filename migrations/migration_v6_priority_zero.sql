-- Migration v6 — Supporto Priority 0 (No Reply)
-- Aggiornamento del vincolo su email_classifications

-- 1. Rimuoviamo il vecchio vincolo
alter table email_classifications
drop constraint if exists email_classifications_priority_check;

-- 2. Aggiungiamo il nuovo vincolo che include lo 0
alter table email_classifications
add constraint email_classifications_priority_check
check (priority in (0, 1, 2, 3));

-- 3. Nota: Non serve aggiornare i record esistenti, il vincolo si applica ai nuovi inserimenti.
