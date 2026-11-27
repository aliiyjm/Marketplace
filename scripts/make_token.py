"""Print a JWT service token to try the API by hand (Postman, Swagger Editor, curl):

    python scripts/make_token.py                    read-only token, valid 15 minutes
    python scripts/make_token.py --scope "read write" --minutes 5

Use it in the header   Authorization: Bearer <token>
A "read" token can only call GET routes: any route that changes data answers
403, which shows the role-based permissions of the API. The token is signed
with MARKETPLACE_API_KEY from .env (the key itself is never printed) and
expires by itself, so it is safe to paste in Postman for a demo.
"""
import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from View.MainViews.Utils.env_file import load_env_file, utf8_console  # noqa: E402


def main(argv=None):
    import os

    from Interfaces.ServiceToken import MAX_LIFETIME, sign_token
    utf8_console()
    load_env_file()
    parser = argparse.ArgumentParser(description="Make a JWT service token for the marketplace API.")
    parser.add_argument("--scope", default="read", choices=["read", "write", "read write"],
                        help='permissions of the token (default: "read")')
    parser.add_argument("--minutes", type=int, default=15, help=f"1 to {MAX_LIFETIME // 60} (default 15)")
    args = parser.parse_args(argv)
    if not 1 <= args.minutes <= MAX_LIFETIME // 60:
        raise SystemExit(f"--minutes must be between 1 and {MAX_LIFETIME // 60}.")
    secret = os.environ.get("MARKETPLACE_API_KEY", "")
    if not secret:
        raise SystemExit("MARKETPLACE_API_KEY is not set in .env.")
    token = sign_token(secret, args.scope, subject="manual-test", lifetime=args.minutes * 60)
    print(f"# scope: {args.scope} · expires in {args.minutes} min · header: Authorization: Bearer <token>",
          file=sys.stderr)
    print(token)


if __name__ == "__main__":
    main()
