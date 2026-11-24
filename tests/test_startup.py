"""Startup on the user's computer (Windows + Git Bash): .env files saved by
Notepad, cp1252 consoles, and clear messages when a database is wrong."""
import os
import subprocess
import sys
from pathlib import Path

import pytest

from Implementation.DataSource.ProductRepositories import ApiProductRepository
from Implementation.DataSource.SqlConnection import SqlDatabase, explain
from Interfaces.DataInterfaces.RepositoryErrors import DatabaseUnavailable
from Interfaces.ProductServiceInterface.ProductServiceInterface import ProductStoreError
from View.MainViews.Utils.env_file import load_env_file

ROOT = Path(__file__).resolve().parents[1]
KEYS = ("T_FIRST", "T_SECOND", "T_QUOTED", "T_EXPORTED")


@pytest.fixture(autouse=True)
def clean_env():
    for k in KEYS:
        os.environ.pop(k, None)
    yield
    for k in KEYS:
        os.environ.pop(k, None)


TEXT = 'T_FIRST=mysql   # comment\nT_SECOND=abc\nT_QUOTED="p#ss word"\nexport T_EXPORTED=yes\n'


@pytest.mark.parametrize("encoding", ["utf-8", "utf-8-sig", "utf-16"])
def test_env_file_saved_by_notepad_is_read(tmp_path, encoding):
    env = tmp_path / ".env"
    env.write_bytes(TEXT.encode(encoding))
    assert load_env_file(env)
    # with a BOM the FIRST key used to be lost silently (FINANCE_BACKEND -> memory)
    assert os.environ["T_FIRST"] == "mysql"
    assert os.environ["T_SECOND"] == "abc"
    assert os.environ["T_QUOTED"] == "p#ss word"
    assert os.environ["T_EXPORTED"] == "yes"


def test_real_env_file_can_be_switched_off(tmp_path):
    os.environ["MARKETPLACE_ENV_FILE"] = "none"          # what conftest.py sets
    assert load_env_file() is False


def test_env_txt_is_reported(tmp_path, capsys):
    (tmp_path / ".env.txt").write_text("T_FIRST=x\n")
    assert load_env_file(tmp_path / ".env") is False
    assert "Rename it to .env" in capsys.readouterr().err


def test_terminal_variables_win(tmp_path):
    os.environ["T_FIRST"] = "from-terminal"
    env = tmp_path / ".env"
    env.write_text(TEXT)
    load_env_file(env)
    assert os.environ["T_FIRST"] == "from-terminal"


@pytest.mark.parametrize("message, dialect, hint", [
    ("(1045, \"Access denied for user 'root'@'localhost'\")", "mysql", "MYSQL_USER / MYSQL_PASSWORD"),
    ('FATAL:  password authentication failed for user "postgres"', "postgres", "PG_USER / PG_PASSWORD"),
    ("(1049, \"Unknown database 'finance_db'\")", "mysql", "MYSQL_DATABASE"),
    ('FATAL:  database "operations_db" does not exist', "postgres", "PG_DATABASE"),
    ("connection refused", "postgres", "PG_HOST / PG_PORT"),
])
def test_driver_errors_get_a_hint(message, dialect, hint):
    assert hint in explain(Exception(message), dialect)


def test_connection_failure_keeps_the_cause_out_of_the_web_message():
    def refuse():
        raise OSError('FATAL:  password authentication failed for user "postgres"')
    db = SqlDatabase(refuse, "postgres", "PostgreSQL operations_db")
    with pytest.raises(DatabaseUnavailable) as info:
        db.query("SELECT 1")
    assert "password" not in str(info.value)              # the page never shows it
    assert "PG_USER / PG_PASSWORD" in info.value.detail    # the console does


def test_product_api_error_at_startup_is_a_clear_message():
    class Error(Exception):
        status = 403

    class Client:
        def list_all(self):
            raise Error("Forbidden: wrong or missing x-api-key")
            yield  # pragma: no cover
    Client.Error = Error
    with pytest.raises(ProductStoreError, match="Could not read the products from DynamoDB"):
        list(ApiProductRepository(Client(), store=None).load_all())


def _run(args, env_text, tmp_path):
    """Run a script with a test .env (never the real one) and a cp1252 console (like Git Bash)."""
    env_file = tmp_path / ".env"
    env_file.write_text(env_text)
    env = dict(os.environ, PYTHONIOENCODING="cp1252", PYTHONPATH="", MARKETPLACE_ENV_FILE=str(env_file))
    return subprocess.run([sys.executable, *args], cwd=ROOT, env=env, capture_output=True,
                          text=True, encoding="utf-8", timeout=120)


def test_check_databases_does_not_crash_on_a_cp1252_console(tmp_path):
    result = _run(["scripts/check_databases.py"],
                  "FINANCE_BACKEND=mysql\nMYSQL_HOST=127.0.0.1\nMYSQL_PORT=1\n", tmp_path)
    assert "UnicodeEncodeError" not in result.stderr
    assert "Traceback" not in result.stderr
    assert "✗" in result.stdout and result.returncode == 1


def test_app_start_with_a_wrong_database_prints_one_clear_line(tmp_path):
    result = _run(["View/MainViews/PY/app.py"],
                  "OPERATIONS_BACKEND=postgres\nPG_HOST=127.0.0.1\nPG_PORT=1\nPRODUCT_COUNT=50\n", tmp_path)
    assert "Traceback" not in result.stderr + result.stdout
    assert result.returncode == 1
    # without the driver installed the message says so; with it, it names the database
    assert "pip install -r requirements.txt" in result.stdout or "PostgreSQL" in result.stdout
