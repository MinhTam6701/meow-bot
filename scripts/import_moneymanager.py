"""Import your Money Manager history into M.E.O.W.

    python scripts/import_moneymanager.py EXPORT.xlsx BACKUP.mmbackup --telegram-id 123 --dry-run
    python scripts/import_moneymanager.py EXPORT.xlsx BACKUP.mmbackup --telegram-id 123
    python scripts/import_moneymanager.py EXPORT.xlsx BACKUP.mmbackup --telegram-id 123 --sql-dir out/

Send /start to the bot first. Re-running replaces the previous import. Bot entries up to the
last day in the export are removed (the old app is treated as the truth).
--sql-dir writes the SQL files instead of running them (for the Supabase SQL editor).
Your files are read locally and never committed: keep them out of the repo.
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from meow.config import get_settings  # noqa: E402
from meow.importer import build_plan, finalize_sql, staging_sql  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("xlsx")
    ap.add_argument("backup", nargs="?")
    ap.add_argument("--telegram-id", type=int, required=True)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--sql-dir")
    a = ap.parse_args()

    plan = build_plan(a.xlsx, a.backup)
    print(json.dumps(plan.summary(), indent=2, ensure_ascii=False))
    if a.dry_run:
        return
    stmts = staging_sql(plan) + [finalize_sql(a.telegram_id)]
    if a.sql_dir:
        os.makedirs(a.sql_dir, exist_ok=True)
        for i, sql in enumerate(stmts, 1):
            with open(os.path.join(a.sql_dir, f"{i:02d}.sql"), "w", encoding="utf-8") as f:
                f.write(sql)
        print(f"Wrote {len(stmts)} files to {a.sql_dir}. Run them in order.")
        return

    import psycopg

    with psycopg.connect(get_settings().database_url, prepare_threshold=None) as conn:
        with conn.transaction():
            for sql in stmts:
                conn.execute(sql)
    print("Imported.")


if __name__ == "__main__":
    main()
