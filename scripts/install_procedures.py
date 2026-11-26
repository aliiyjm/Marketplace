"""Install (or update) the stored procedures and functions in your real databases:

    python scripts/install_procedures.py            asks before touching anything
    python scripts/install_procedures.py --yes      no question (for scripts)

- MySQL finance_db  <- database/finance_procedures.sql    (4 procedures)
- PostgreSQL        <- database/operations_procedures.sql (2 functions)

It only runs DROP PROCEDURE IF EXISTS / CREATE PROCEDURE / CREATE OR REPLACE
FUNCTION: tables and data are never touched, so it is safe to run again after
every change. Connection settings come from your .env (same as the app).
"""
import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from View.MainViews.Utils.env_file import load_env_file, utf8_console  # noqa: E402

MYSQL_FILE = ROOT / "database" / "finance_procedures.sql"
POSTGRES_FILE = ROOT / "database" / "operations_procedures.sql"
MYSQL_ROUTINES = ("sp_create_order", "sp_change_order_status", "sp_cancel_order", "sp_buyer_sales_history")
POSTGRES_ROUTINES = ("fn_seller_sales_history", "fn_seller_units_by_day")


def split_mysql_script(text):
    """Statements of a MySQL script that uses DELIMITER (a Workbench/CLI feature
    the server itself does not understand). Comment-only chunks are skipped."""
    statements, buffer, delimiter = [], [], ";"
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.upper().startswith("DELIMITER "):
            delimiter = stripped.split(None, 1)[1]
            continue
        if not buffer and (not stripped or stripped.startswith("--")):
            continue                      # comments between statements
        buffer.append(line)
        if not stripped.startswith("--") and stripped.endswith(delimiter):
            statement = "\n".join(buffer).rstrip()
            statements.append(statement[: len(statement) - len(delimiter)].rstrip())
            buffer = []
    if buffer:
        raise ValueError("The MySQL script ends without a delimiter.")
    return statements


def mysql_statements():
    statements = split_mysql_script(MYSQL_FILE.read_text(encoding="utf-8"))
    for statement in statements:            # never anything that could touch data or other databases
        head = " ".join(statement.split()[:4]).upper()
        if not (head.startswith("DROP PROCEDURE IF EXISTS") or head.startswith("CREATE PROCEDURE")):
            raise ValueError(f"Unexpected statement in {MYSQL_FILE.name}: {head}")
    return statements


def install_mysql(db, out=print):
    with db.transaction() as cur:
        version = cur.execute("SELECT VERSION() AS v").fetchone()["v"]
        major, minor, patch = (int("".join(ch for ch in part if ch.isdigit()) or 0)
                               for part in (version.split("-")[0].split(".") + ["0", "0"])[:3])
        if (major, minor, patch) < (8, 0, 4):
            raise RuntimeError(f"MySQL {version} is too old: the procedures need 8.0.4+ (JSON_TABLE).")
        for statement in mysql_statements():
            cur._cursor.execute(statement)       # no parameters: the text is our own file
    out(f"  ✓ {len(MYSQL_ROUTINES)} procedures installed (MySQL {version})")


def install_postgres(db, out=print):
    with db.transaction() as cur:
        cur._cursor.execute(POSTGRES_FILE.read_text(encoding="utf-8"))
    out(f"  ✓ {len(POSTGRES_ROUTINES)} functions installed")


def missing_routines(db):
    """Names of the expected routines that the database does not have."""
    with db.transaction() as cur:
        if db.dialect == "mysql":
            rows = cur.execute("SELECT ROUTINE_NAME AS name FROM information_schema.ROUTINES "
                               "WHERE ROUTINE_SCHEMA = DATABASE()").fetchall()
            expected = MYSQL_ROUTINES
        else:
            rows = cur.execute("SELECT p.proname AS name FROM pg_proc p "
                               "JOIN pg_namespace n ON n.oid = p.pronamespace "
                               "WHERE n.nspname = current_schema()").fetchall()
            expected = POSTGRES_ROUTINES
    have = {str(r["name"]).lower() for r in rows}
    return [name for name in expected if name not in have]


def main():
    utf8_console()
    load_env_file()
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--yes", action="store_true", help="do not ask for confirmation")
    args = parser.parse_args()
    from Implementation.DataSource.SqlConnection import mysql_from_env, postgres_from_env

    targets = []
    if os.environ.get("FINANCE_BACKEND") == "mysql":
        targets.append(("MySQL", f"{os.environ.get('MYSQL_DATABASE', 'finance_db')} @ "
                                 f"{os.environ.get('MYSQL_HOST', 'localhost')}", mysql_from_env, install_mysql))
    if os.environ.get("OPERATIONS_BACKEND") == "postgres":
        targets.append(("PostgreSQL", f"{os.environ.get('PG_DATABASE', 'operations_db')} @ "
                                      f"{os.environ.get('PG_HOST', 'localhost')}", postgres_from_env, install_postgres))
    if not targets:
        print("✗ FINANCE_BACKEND=mysql / OPERATIONS_BACKEND=postgres are not set in .env: nothing to install.")
        sys.exit(1)
    print("Stored procedures will be (re)created in:")
    for engine, where, _, _ in targets:
        print(f"  - {engine} {where}")
    print("Tables and data are not touched.")
    if not args.yes and input("Continue? [y/N] ").strip().lower() != "y":
        print("Cancelled.")
        return
    problems = 0
    for engine, where, connect, install in targets:
        print(f"{engine} {where}")
        try:
            db = connect()
            install(db)
            missing = missing_routines(db)
            if missing:
                print(f"  ✗ still missing: {', '.join(missing)}")
                problems += 1
        except Exception as e:
            print(f"  ✗ {e}  {getattr(e, 'detail', '')}")
            problems += 1
    print("\nDone." if not problems else f"\n{problems} problem(s).")
    sys.exit(1 if problems else 0)


if __name__ == "__main__":
    main()
