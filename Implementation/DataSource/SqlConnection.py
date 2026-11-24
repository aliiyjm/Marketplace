"""Connections to the relational databases (MySQL finance_db, PostgreSQL).

- One short connection per operation (both databases are local, so it is cheap
  and thread-safe without a pool).
- Every query uses %s placeholders with parameters: values are NEVER pasted
  into the SQL text, so SQL injection is not possible.
- Driver errors become DatabaseUnavailable / DuplicateKey with a friendly
  message; the technical detail only goes to the server log.

Configuration (environment variables or the .env file):
    MYSQL_HOST, MYSQL_PORT (3306), MYSQL_USER, MYSQL_PASSWORD, MYSQL_DATABASE (finance_db)
    PG_HOST, PG_PORT (5432), PG_USER, PG_PASSWORD, PG_DATABASE (operations_db)
"""
import logging
import os
from contextlib import contextmanager
from datetime import date, datetime
from decimal import Decimal

from Interfaces.DataInterfaces.RepositoryErrors import DatabaseUnavailable, DuplicateKey
from Interfaces.Validators import ValidationError

log = logging.getLogger("sql")


def _is_integrity_error(error):
    return any(cls.__name__ in ("IntegrityError", "UniqueViolation") for cls in type(error).__mro__)


class Cursor:
    """Thin wrapper: rows as dicts, and %s placeholders for every driver."""

    def __init__(self, cursor, dialect):
        self._cursor = cursor
        self.dialect = dialect

    def execute(self, sql, params=()):
        if self.dialect == "sqlite":          # used by the automated tests
            sql = sql.replace("%s", "?")
        self._cursor.execute(sql, tuple(params))
        return self

    def executemany(self, sql, rows):
        if self.dialect == "sqlite":
            sql = sql.replace("%s", "?")
        self._cursor.executemany(sql, [tuple(r) for r in rows])
        return self

    def fetchall(self):
        if self._cursor.description is None:
            return []
        names = [d[0] for d in self._cursor.description]
        return [dict(zip(names, row)) for row in self._cursor.fetchall()]

    def fetchone(self):
        rows = self.fetchall()
        return rows[0] if rows else None

    @property
    def rowcount(self):
        return self._cursor.rowcount

    @property
    def lastrowid(self):
        return self._cursor.lastrowid


class SqlDatabase:
    def __init__(self, connect, dialect, label):
        self._connect = connect        # function that opens a new DB-API connection
        self.dialect = dialect         # mysql | postgres | sqlite
        self.label = label             # for messages: "MySQL finance_db"

    @contextmanager
    def transaction(self):
        """Everything inside runs in ONE transaction: commit at the end, rollback on any error."""
        try:
            conn = self._connect()
        except Exception as e:
            log.error("Cannot connect to %s: %r", self.label, e)
            raise DatabaseUnavailable(f"Could not connect to the database ({self.label}). Try again later.") from None
        try:
            cursor = Cursor(conn.cursor(), self.dialect)
            yield cursor
            conn.commit()
        except ValidationError:          # our own business errors (StaleWrite, DuplicateKey...)
            conn.rollback()
            raise
        except Exception as e:
            conn.rollback()
            if _is_integrity_error(e):
                log.warning("Integrity error on %s: %r", self.label, e)
                raise DuplicateKey("That record already exists.") from None
            log.exception("Error on %s", self.label)
            raise DatabaseUnavailable(f"The database ({self.label}) could not complete the operation.") from None
        finally:
            conn.close()

    def query(self, sql, params=()):
        with self.transaction() as cur:
            return cur.execute(sql, params).fetchall()

    def query_one(self, sql, params=()):
        rows = self.query(sql, params)
        return rows[0] if rows else None


# ---------- factories ----------
def _driver(module, package):
    try:
        return __import__(module)
    except ImportError:
        raise RuntimeError(f"The {package} driver is not installed. Run: pip install -r requirements.txt") from None


def mysql_from_env():
    pymysql = _driver("pymysql", "PyMySQL")

    settings = dict(host=os.environ.get("MYSQL_HOST", "localhost"),
                    port=int(os.environ.get("MYSQL_PORT", "3306")),
                    user=os.environ.get("MYSQL_USER", "root"),
                    password=os.environ.get("MYSQL_PASSWORD", ""),
                    database=os.environ.get("MYSQL_DATABASE", "finance_db"),
                    charset="utf8mb4", autocommit=False, connect_timeout=5)
    return SqlDatabase(lambda: pymysql.connect(**settings), "mysql", f"MySQL {settings['database']}")


def postgres_from_env():
    psycopg = _driver("psycopg", "psycopg (PostgreSQL)")

    settings = dict(host=os.environ.get("PG_HOST", "localhost"),
                    port=int(os.environ.get("PG_PORT", "5432")),
                    user=os.environ.get("PG_USER", "postgres"),
                    password=os.environ.get("PG_PASSWORD", ""),
                    dbname=os.environ.get("PG_DATABASE", "operations_db"),
                    connect_timeout=5)
    return SqlDatabase(lambda: psycopg.connect(**settings), "postgres", f"PostgreSQL {settings['dbname']}")


# ---------- value conversion (drivers return different types) ----------
def as_iso(value):
    """datetime/date/str -> ISO text (the app works with ISO strings)."""
    if value is None:
        return ""
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return str(value)


def as_datetime(value):
    """ISO text -> datetime for TIMESTAMP columns."""
    if value is None or value == "":
        return None
    return value if isinstance(value, datetime) else datetime.fromisoformat(str(value))


def as_date(value):
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10])


def as_money(value):
    return Decimal(str(value)).quantize(Decimal("0.01"))
