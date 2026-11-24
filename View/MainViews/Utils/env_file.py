"""Read the .env file of the project root into the environment (no extra dependency).

Lines look like KEY=value (an optional "export " in front is accepted). Variables
already set in the terminal win over the file. The .env file holds passwords and
keys: it is in .gitignore and must never be committed (copy .env.example and fill it in).

Windows notes: Notepad may save the file as UTF-8 with BOM or as UTF-16, or add
".txt" to the name; all of these are handled or reported here.
"""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]


def utf8_console():
    """Git Bash on Windows gives Python a cp1252 console: printing ✓ or accents
    would crash with UnicodeEncodeError. Switch stdout/stderr to UTF-8."""
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except (ValueError, OSError):
                pass


def _read_text(path):
    data = path.read_bytes()
    if data[:2] in (b"\xff\xfe", b"\xfe\xff"):      # UTF-16 (Notepad "Unicode")
        return data.decode("utf-16")
    return data.decode("utf-8-sig")                 # utf-8-sig also removes a UTF-8 BOM


def load_env_file(path=None):
    """MARKETPLACE_ENV_FILE can point to another file, or be "none" to read no
    file at all (the automated tests use it so they never touch real databases)."""
    if path is None:
        choice = os.environ.get("MARKETPLACE_ENV_FILE", "")
        if choice.lower() == "none":
            return False
        path = choice or ROOT / ".env"
    path = Path(path)
    if not path.is_file():
        if path.with_name(path.name + ".txt").is_file():
            print(f"WARNING: found {path.name}.txt but not {path.name}. Rename it to {path.name} "
                  "(Windows hides the .txt extension).", file=sys.stderr)
        return False
    try:
        text = _read_text(path)
    except UnicodeDecodeError:
        raise RuntimeError(f"{path} is not UTF-8 text. Save it again as UTF-8 "
                           "(VS Code: bottom bar -> encoding -> Save with Encoding -> UTF-8).") from None
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith("export "):
            line = line[len("export "):].strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key, value = key.strip(), value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]                      # quoted: keep everything inside
        elif " #" in value:
            value = value.split(" #", 1)[0].rstrip()  # KEY=value   # comment
        if key and key not in os.environ:
            os.environ[key] = value
    return True
