"""Read the .env file of the project root into the environment (no extra dependency).

Lines look like KEY=value. Variables already set in the terminal win over the
file. The .env file holds passwords and keys: it is in .gitignore and must
never be committed (copy .env.example and fill it in).
"""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]


def load_env_file(path=None):
    path = Path(path) if path else ROOT / ".env"
    if not path.is_file():
        return False
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
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
