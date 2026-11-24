"""Upload the demo data to DynamoDB MarketplaceDB through the Lambda:
  1. users (Ana, Carlos, the admin and the demo customers/sellers) -> /usuarios/agregar
  2. the clothing catalog (17,000 products, 100 per call)            -> /productos/cargar

Before running, set (Git Bash):
    export API_URL="https://xxxx.execute-api.us-east-1.amazonaws.com"
    export MARKETPLACE_API_KEY="<the same MARKETPLACE_API_KEY you set in the Lambda>"

Usage (from the project root):
    python scripts/load_to_dynamo.py                   # users + 17,000 products
    python scripts/load_to_dynamo.py --dry-run         # only shows what would be sent
    python scripts/load_to_dynamo.py --skip-users --start 5200   # resume products after an error

Users that already exist are skipped (never overwritten). Products with the
same id ARE overwritten (their stock is reset). The data is generated with a
fixed seed, so ids always match the reviews and sales the app generates locally.
"""
import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from Implementation.DataSource.MemoryStore import MemoryStore  # noqa: E402
from Implementation.DataSource.Seed import seed  # noqa: E402
from Interfaces.ProductServiceInterface.ProductDTO import ProductItem  # noqa: E402
from View.MainViews.Utils import api_client  # noqa: E402
from View.MainViews.Utils.product_api import ProductApiClient, ProductApiError  # noqa: E402

USER_FIELDS = ("id_user", "name", "email", "role", "password_hash", "created_at")


def _seeded_store(count):
    store = MemoryStore()
    seed(store, product_count=count)
    return store


def build_payloads(count, store=None):
    store = store or _seeded_store(count)
    records = sorted(store.products.values(), key=lambda p: p["product_id"])
    return [ProductItem.from_record(p, store.categories[p["category_id"]]["slug"]).to_payload() for p in records]


def build_users(store):
    return [{k: u[k] for k in USER_FIELDS} for u in sorted(store.users.values(), key=lambda u: u["id_user"])]


def upload_users(api, users, out=print):
    added = skipped = 0
    for user in users:
        try:
            api.add_user(user)
            added += 1
        except api.DuplicateId:
            skipped += 1
        except api.ApiError as e:
            if "already registered" in str(e):
                skipped += 1
            else:
                raise SystemExit(f"Could not upload user {user['email']}: {e}")
    out(f"Users: {added} added, {skipped} already existed.")
    return added, skipped


def upload(client, payloads, batch=100, start=0, out=print):
    total = len(payloads)
    for i in range(start, total, batch):
        chunk = payloads[i:i + batch]
        for attempt in (1, 2):
            try:
                client.load(chunk)
                break
            except ProductApiError as e:
                if attempt == 2:
                    raise SystemExit(f"\nFailed at product #{i}: {e}\n"
                                     f"Fix the problem and resume with: --start {i}")
                time.sleep(3)
        out(f"\r{min(i + batch, total):>6} / {total} products uploaded", end="")
    out("\nDone.")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--count", type=int, default=17000)
    parser.add_argument("--batch", type=int, default=100, help="products per API call (max 100)")
    parser.add_argument("--start", type=int, default=0, help="index to resume from")
    parser.add_argument("--skip-users", action="store_true")
    parser.add_argument("--skip-products", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--yes", action="store_true", help="do not ask for confirmation")
    args = parser.parse_args(argv)

    store = _seeded_store(args.count)
    users = [] if args.skip_users else build_users(store)
    payloads = [] if args.skip_products else build_payloads(args.count, store)
    if args.dry_run:
        print(f"{len(users)} users and {len(payloads)} products would be uploaded.")
        if users:
            print("Users:", ", ".join(u["email"] for u in users[:5]), "…")
        if payloads:
            print("First product:")
            print(json.dumps(payloads[0], indent=2, ensure_ascii=False))
        return
    client = ProductApiClient()
    if not client.api_key:
        raise SystemExit("Set MARKETPLACE_API_KEY (same value as the Lambda's MARKETPLACE_API_KEY).")
    if not args.yes:
        answer = input(f"Upload {len(users)} users and {max(len(payloads) - args.start, 0)} products to "
                       f"{client.base_url} (existing product ids are overwritten)? [y/N] ")
        if answer.strip().lower() not in ("y", "yes", "s", "si", "sí"):
            print("Cancelled.")
            return
    if users:
        upload_users(api_client, users)
    if payloads:
        upload(client, payloads, batch=min(max(args.batch, 1), 100), start=args.start)


if __name__ == "__main__":
    main()
