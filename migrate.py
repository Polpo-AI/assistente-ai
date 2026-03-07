"""
migrate.py — Applica le migration SQL su Supabase in ordine

Tiene traccia delle migration già eseguite in una tabella `_migrations`.
Applica solo quelle mancanti.

Uso:
    python migrate.py            # applica migration mancanti
    python migrate.py --status   # mostra stato migration
    python migrate.py --dry-run  # mostra cosa verrebbe applicato senza eseguire
"""

import os
import sys
import argparse
import glob
import re
from dotenv import load_dotenv

load_dotenv()


def get_db():
    from supabase import create_client
    url = os.environ["SUPABASE_URL"]
    key = os.environ["SUPABASE_KEY"]
    return create_client(url, key)


def ensure_migrations_table(db):
    """Crea la tabella _migrations se non esiste."""
    db.rpc("create_migrations_table_if_not_exists", {}).execute()


def create_migrations_table_sql():
    """SQL per creare la tabella _migrations — da eseguire una volta su Supabase."""
    return """
CREATE TABLE IF NOT EXISTS _migrations (
    id          SERIAL PRIMARY KEY,
    name        TEXT UNIQUE NOT NULL,
    applied_at  TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);
"""


def get_applied_migrations(db) -> set:
    """Restituisce i nomi delle migration già applicate."""
    try:
        result = db.table("_migrations").select("name").execute()
        return {row["name"] for row in result.data}
    except Exception:
        return set()


def get_migration_files() -> list:
    """Restituisce i file SQL in ordine (prima le vecchie, poi le nuove)."""
    base = os.path.dirname(os.path.abspath(__file__))
    files = glob.glob(os.path.join(base, "migrations", "*.sql"))

    def sort_key(f):
        name = os.path.basename(f)
        # Estrai numero versione — es: migration_v9_xxx.sql → 9
        m = re.search(r'v(\d+)', name)
        if m:
            return int(m.group(1))
        # migration_add_email_credentials.sql → 0 (prima di tutte)
        return 0

    return sorted(files, key=sort_key)


def apply_migration(db, filepath: str, name: str) -> bool:
    """Applica una singola migration."""
    sql = open(filepath, encoding="utf-8").read()

    # Supabase non supporta esecuzione SQL diretta via client Python —
    # usiamo rpc o stampiamo per esecuzione manuale
    print(f"\n  📋 {name}")
    print(f"  {'─'*50}")
    print(sql.strip())
    print(f"  {'─'*50}")

    risposta = input("  Eseguita su Supabase? [s/n]: ").strip().lower()
    if risposta in ("s", "si", "y", "yes"):
        db.table("_migrations").insert({"name": name}).execute()
        print(f"  ✅ Registrata come applicata")
        return True
    else:
        print(f"  ⏭  Saltata")
        return False


def cmd_status(db):
    """Mostra stato di tutte le migration."""
    applied = get_applied_migrations(db)
    files   = get_migration_files()

    print(f"\n{'='*55}")
    print(f"  STATO MIGRATION — {len(applied)} applicate")
    print(f"{'='*55}\n")

    for f in files:
        name   = os.path.basename(f)
        status = "✅ applicata" if name in applied else "⏳ da applicare"
        print(f"  {status:<20} {name}")

    pending = [f for f in files if os.path.basename(f) not in applied]
    print(f"\n  Totale: {len(files)} migration, {len(pending)} da applicare\n")


def cmd_migrate(db, dry_run=False):
    """Applica le migration mancanti."""
    applied = get_applied_migrations(db)
    files   = get_migration_files()
    pending = [f for f in files if os.path.basename(f) not in applied]

    if not pending:
        print("\n  ✅ Tutte le migration sono già applicate.\n")
        return

    print(f"\n  Migration da applicare: {len(pending)}")
    for f in pending:
        print(f"    - {os.path.basename(f)}")

    if dry_run:
        print("\n  [DRY RUN] Nessuna migration applicata.")
        return

    print(f"\n  ⚠️  Vai su Supabase SQL Editor e copia/incolla le migration una alla volta.")
    print(f"  Dopo averle eseguite, conferma qui per registrarle come applicate.\n")

    for f in pending:
        name = os.path.basename(f)
        apply_migration(db, f, name)

    print(f"\n  ✅ Migration completate.\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Polpo AI — Migration manager")
    parser.add_argument("--status",  action="store_true", help="Mostra stato migration")
    parser.add_argument("--dry-run", action="store_true", help="Mostra cosa verrebbe applicato")
    args = parser.parse_args()

    print("\n  Connessione a Supabase...")
    db = get_db()

    # Prima esecuzione: crea tabella _migrations
    print(f"\n  ⚠️  Prima volta? Esegui questo SQL su Supabase:\n")
    print(create_migrations_table_sql())
    input("  Premi INVIO quando fatto (o se già esiste)... ")

    if args.status:
        cmd_status(db)
    else:
        cmd_migrate(db, dry_run=args.dry_run)
