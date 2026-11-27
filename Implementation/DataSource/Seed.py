"""Deterministic demo data for a clothing marketplace (17,000 products by default).

The same seed always produces the same catalog, so screenshots and tests are
repeatable. Demo accounts (password Demo1234):
    ana@example.com         buyer
    carlos@urbanthread.mx   seller (Urban Thread)
    admin@marketplace.mx    admin
"""
import random
import secrets
from datetime import timedelta
from decimal import Decimal

from Implementation.AuthServiceImplementation.Passwords import hash_password

from Implementation.DataSource.Inventory import refresh_alert
from Implementation.DataSource.Mappers import refresh_rating, search_text
from Interfaces.MainServiceInterface.MainDTO import COLOR_PALETTE
from Interfaces.Validators import normalize

DEMO_PASSWORD = "Demo1234"

COLORS = list(COLOR_PALETTE.items())   # (name, css key)
COLOR_KEYS = {name.lower(): key for name, key in COLORS}

# category_id, parent_id, name, slug
CATEGORIES = [
    (1, None, "Women", "women"), (2, None, "Men", "men"), (3, None, "Kids", "kids"),
    (4, None, "Shoes", "shoes"), (5, None, "Accessories", "accessories"),
    (6, None, "Sportswear", "sportswear"),
    (11, 1, "Dresses", "women-dresses"), (12, 1, "Tops & Blouses", "women-tops"),
    (13, 1, "Jeans", "women-jeans"), (14, 1, "Jackets & Coats", "women-jackets"),
    (21, 2, "T-shirts", "men-tshirts"), (22, 2, "Shirts", "men-shirts"),
    (23, 2, "Jeans", "men-jeans"), (24, 2, "Jackets", "men-jackets"),
    (25, 2, "Hoodies", "men-hoodies"),
    (31, 3, "Girls", "kids-girls"), (32, 3, "Boys", "kids-boys"), (33, 3, "Baby", "kids-baby"),
    (41, 4, "Sneakers", "shoes-sneakers"), (42, 4, "Boots", "shoes-boots"),
    (43, 4, "Sandals", "shoes-sandals"), (44, 4, "Formal shoes", "shoes-formal"),
    (51, 5, "Bags", "acc-bags"), (52, 5, "Hats & Caps", "acc-hats"),
    (53, 5, "Belts", "acc-belts"), (54, 5, "Sunglasses", "acc-sunglasses"),
    (61, 6, "Leggings", "sport-leggings"), (62, 6, "Sports bras", "sport-bras"),
    (63, 6, "Tracksuits", "sport-tracksuits"),
]

SIZES = {
    "alpha": ["XS", "S", "M", "L", "XL"],
    "alpha_plus": ["S", "M", "L", "XL", "XXL"],
    "waist_w": ["24", "26", "28", "30", "32"],
    "waist_m": ["28", "30", "32", "34", "36", "38"],
    "kids": ["4", "6", "8", "10", "12", "14"],
    "baby": ["0-3M", "3-6M", "6-12M", "12-18M", "18-24M"],
    "shoes": ["22", "23", "24", "25", "26", "27", "28", "29"],
    "one": ["One size"],
    "belt": ["S", "M", "L"],
}
FABRICS = ["Cotton", "Organic cotton", "Linen", "Viscose", "Polyester", "Cotton blend"]
CARE = ["Machine wash cold", "Hand wash", "Dry clean only", "Machine wash warm"]
ORIGIN = ["Made in Mexico", "Made in Mexico", "Imported"]


def _clothes(r):
    return {"Material": r.choice(FABRICS), "Fit": r.choice(["Slim", "Regular", "Relaxed", "Oversized"]),
            "Care": r.choice(CARE), "Origin": r.choice(ORIGIN)}


def _denim(r):
    return {"Material": r.choice(["Denim 98% cotton", "Rigid denim", "Stretch denim"]),
            "Rise": r.choice(["High", "Mid", "Low"]), "Stretch": r.choice(["Yes", "No"]),
            "Care": "Machine wash cold, inside out", "Origin": r.choice(ORIGIN)}


def _dress(r):
    return {"Material": r.choice(FABRICS), "Length": r.choice(["Mini", "Midi", "Maxi"]),
            "Neckline": r.choice(["V-neck", "Round", "Square", "Halter"]), "Care": r.choice(CARE)}


def _outer(r):
    return {"Material": r.choice(["Nylon", "Wool blend", "Denim", "Cotton twill", "Faux leather"]),
            "Lining": r.choice(["Yes", "No"]), "Pockets": str(r.randint(2, 5)),
            "Water resistant": r.choice(["Yes", "No"]), "Care": r.choice(CARE)}


def _shoes(r):
    return {"Upper": r.choice(["Leather", "Canvas", "Suede", "Mesh", "Synthetic"]),
            "Sole": r.choice(["Rubber", "EVA", "Leather"]),
            "Closure": r.choice(["Laces", "Slip-on", "Buckle", "Zipper"]), "Origin": r.choice(ORIGIN)}


def _bag(r):
    return {"Material": r.choice(["Leather", "Canvas", "Woven palm", "Nylon"]),
            "Dimensions": f"{r.randint(20, 45)} × {r.randint(15, 35)} × {r.randint(5, 18)} cm",
            "Closure": r.choice(["Zipper", "Magnet", "Drawstring"]), "Origin": r.choice(ORIGIN)}


def _hat(r):
    return {"Material": r.choice(["Cotton twill", "Straw", "Wool", "Acrylic knit"]),
            "Adjustable": r.choice(["Yes", "No"]), "Origin": r.choice(ORIGIN)}


def _belt(r):
    return {"Material": r.choice(["Leather", "Braided leather", "Canvas"]),
            "Width": f"{r.choice([3, 3.5, 4])} cm", "Buckle": r.choice(["Silver", "Brass", "Black"])}


def _glasses(r):
    return {"UV protection": "UV400", "Frame": r.choice(["Metal", "Acetate", "Recycled plastic"]),
            "Polarized": r.choice(["Yes", "No"]), "Includes": "Case and cloth"}


def _sport(r):
    return {"Material": r.choice(["Polyester / elastane", "Nylon / spandex", "Recycled polyester"]),
            "Quick dry": "Yes", "Support": r.choice(["Light", "Medium", "High"]), "Care": "Machine wash cold"}


# slug -> (kind, product types, (min price, max price), size set, attribute builder)
TEMPLATES = {
    "women-dresses": ("dress", ["Midi Dress", "Wrap Dress", "Maxi Dress", "Shirt Dress", "Slip Dress"], (499, 1499), "alpha", _dress),
    "women-tops": ("tshirt", ["Ruffle Blouse", "Basic Tee", "Crop Top", "Satin Blouse", "Knit Sweater"], (249, 899), "alpha", _clothes),
    "women-jeans": ("pants", ["Mom Jeans", "Skinny Jeans", "Wide Leg Jeans", "Straight Jeans"], (549, 1199), "waist_w", _denim),
    "women-jackets": ("jacket", ["Denim Jacket", "Trench Coat", "Puffer Jacket", "Blazer"], (799, 2499), "alpha", _outer),
    "men-tshirts": ("tshirt", ["Crew Neck Tee", "Graphic Tee", "Pocket Tee", "V-Neck Tee"], (199, 599), "alpha_plus", _clothes),
    "men-shirts": ("tshirt", ["Oxford Shirt", "Linen Shirt", "Flannel Shirt", "Guayabera"], (449, 1199), "alpha_plus", _clothes),
    "men-jeans": ("pants", ["Slim Fit Jeans", "Regular Fit Jeans", "Relaxed Jeans", "Cargo Pants"], (549, 1299), "waist_m", _denim),
    "men-jackets": ("jacket", ["Bomber Jacket", "Denim Jacket", "Rain Jacket", "Field Jacket"], (899, 2799), "alpha_plus", _outer),
    "men-hoodies": ("jacket", ["Pullover Hoodie", "Zip Hoodie", "Crewneck Sweatshirt"], (499, 1099), "alpha_plus", _clothes),
    "kids-girls": ("dress", ["Girls Dress", "Girls Skirt Set", "Girls Tee"], (199, 699), "kids", _clothes),
    "kids-boys": ("tshirt", ["Boys Tee", "Boys Joggers", "Boys Hoodie"], (199, 649), "kids", _clothes),
    "kids-baby": ("tshirt", ["Baby Bodysuit", "Baby Romper", "Baby Pajama Set"], (149, 449), "baby", _clothes),
    "shoes-sneakers": ("shoe", ["Canvas Sneakers", "Running Sneakers", "Leather Sneakers", "High-Top Sneakers"], (699, 2499), "shoes", _shoes),
    "shoes-boots": ("shoe", ["Chelsea Boots", "Ankle Boots", "Work Boots"], (999, 2999), "shoes", _shoes),
    "shoes-sandals": ("shoe", ["Leather Sandals", "Slide Sandals", "Huaraches"], (299, 999), "shoes", _shoes),
    "shoes-formal": ("shoe", ["Oxford Shoes", "Loafers", "Derby Shoes"], (999, 2499), "shoes", _shoes),
    "acc-bags": ("bag", ["Tote Bag", "Crossbody Bag", "Backpack", "Clutch"], (349, 1899), "one", _bag),
    "acc-hats": ("cap", ["Baseball Cap", "Bucket Hat", "Beanie", "Straw Hat"], (199, 699), "one", _hat),
    "acc-belts": ("belt", ["Leather Belt", "Braided Belt", "Canvas Belt"], (249, 799), "belt", _belt),
    "acc-sunglasses": ("glasses", ["Aviator Sunglasses", "Round Sunglasses", "Cat-Eye Sunglasses"], (299, 1299), "one", _glasses),
    "sport-leggings": ("pants", ["High-Waist Leggings", "Running Tights", "Bike Shorts"], (349, 899), "alpha", _sport),
    "sport-bras": ("tshirt", ["Sports Bra", "Seamless Sports Bra"], (299, 699), "alpha", _sport),
    "sport-tracksuits": ("jacket", ["Tracksuit Set", "Training Joggers", "Windbreaker"], (599, 1599), "alpha_plus", _sport),
}
STYLES = ["Classic", "Essential", "Vintage", "Relaxed", "Premium", "Everyday", "Urban",
          "Organic", "Soft", "Tailored", "Coastal", "Weekend"]

# seller_id, store, top-level categories it sells, rating, RFC
SELLERS = [
    ("SEL-0001", "Urban Thread", ["women", "men"], 4.8, "UTH200314AB1"),
    ("SEL-0002", "Casa Moda", ["women", "kids"], 4.6, "CMO190822KL3"),
    ("SEL-0003", "Denim Co. MX", ["women", "men"], 4.5, "DCM180105PQ2"),
    ("SEL-0004", "Norte Sportswear", ["sportswear", "men"], 4.7, "NSP210611RT8"),
    ("SEL-0005", "Pasos Shoes", ["shoes"], 4.6, "PSH170930MN4"),
    ("SEL-0006", "Alma Boutique", ["women", "accessories"], 4.9, "ABO220217XY5"),
    ("SEL-0007", "Little Steps", ["kids"], 4.7, "LST200801GH6"),
    ("SEL-0008", "Raíz Artesanal", ["accessories", "women"], 4.8, "RAR160412CD7"),
    ("SEL-0009", "Luna Accessories", ["accessories"], 4.4, "LAC230120EF9"),
    ("SEL-0010", "Monterrey Outfitters", ["men", "shoes"], 4.3, "MOU190715JK0"),
    ("SEL-0011", "Costa Linen", ["women", "men"], 4.5, "CLI210309LM1"),
    ("SEL-0012", "Estilo Urbano", ["sportswear", "shoes", "men"], 4.2, "EUR200427NP2"),
]
BUYER_NAMES = ["Mariana G.", "Luis R.", "Sofía P.", "Andrés M.", "Valeria C.", "Diego H.", "Fernanda L.",
               "Jorge P.", "Camila S.", "Ricardo V.", "Paola N.", "Emilio T.", "Regina F.", "Héctor D.",
               "Ximena A.", "Pablo O.", "Natalia B.", "Iván Q.", "Daniela E.", "Óscar Z."]
COMMENTS = {
    5: ["Fits perfectly and the fabric feels great.", "Exactly like the photos, I already ordered another color.",
        "Great quality for the price. Arrived in two days.", "Very comfortable, I wear it every week.",
        "The size guide was accurate. Highly recommended."],
    4: ["Nice quality, runs a little small.", "Good product, the color is slightly darker in person.",
        "Comfortable and well made. Shipping took a bit longer.", "Looks good, I would buy again."],
    3: ["It is okay, the fabric is thinner than expected.", "Average quality, fits as expected.",
        "Decent for the price but the seams could be better."],
    2: ["The size was too big and the color faded after washing.", "Not what I expected from the description."],
    1: ["Poor quality, I requested a return."],
}


def _user(store, id_user, name, email, role, password=None, created=None):
    store.users[id_user] = {
        "PK": f"USER#{id_user}", "SK": "PROFILE",
        "id_user": id_user, "name": name, "email": email, "role": role,
        # Unusable hash for seeded customers: nobody can sign in as them.
        "password_hash": hash_password(password) if password else "!" + secrets.token_hex(8),
        "created_at": (created or store.now()).isoformat(),
    }


def seed(store, product_count=17000, rng_seed=42):
    r = random.Random(rng_seed)
    now = store.now()

    for cid, parent, name, slug in CATEGORIES:
        store.categories[cid] = {"category_id": cid, "parent_id": parent, "name": name,
                                 "slug": slug, "is_active": True}
    store._counters["categories"] = 100

    _user(store, "u-ana00001", "Ana Torres", "ana@example.com", "buyer", DEMO_PASSWORD, now - timedelta(days=60))
    _user(store, "u-carlos001", "Carlos Méndez", "carlos@urbanthread.mx", "seller", DEMO_PASSWORD, now - timedelta(days=400))
    _user(store, "u-admin0001", "Diego Salgado", "admin@marketplace.mx", "admin", DEMO_PASSWORD, now - timedelta(days=500))
    buyers = []
    for i, name in enumerate(BUYER_NAMES, start=1):
        uid = f"u-buyer{i:04d}"
        email = normalize(name.replace(".", "").replace(" ", ".")) + f"{i}@example.com"
        _user(store, uid, name, email, "buyer", created=now - timedelta(days=r.randint(30, 700)))
        buyers.append(uid)

    for i, (sid, store_name, tops, rating, rfc) in enumerate(SELLERS, start=1):
        if sid == "SEL-0001":
            user_id = "u-carlos001"
        else:
            user_id = f"u-seller{i:03d}"
            _user(store, user_id, f"{store_name} owner", f"owner{i}@{normalize(store_name).replace(' ', '').replace('.', '')}.mx",
                  "seller", created=now - timedelta(days=r.randint(200, 900)))
        store.sellers[sid] = {"seller_id": sid, "user_id": user_id, "store_name": store_name,
                              "tax_id": rfc, "rating": rating, "status": "active",
                              "created_at": (now - timedelta(days=r.randint(200, 900))).isoformat(),
                              "_tops": tops}
    store._counters["sellers"] = len(SELLERS)

    by_top = {}
    for sid, _, tops, _, _ in SELLERS:
        for top in tops:
            by_top.setdefault(top, []).append(sid)
    leaves = [c for c in CATEGORIES if c[1] is not None]
    top_slug = {c[0]: c[3] for c in CATEGORIES if c[1] is None}

    review_id = 0
    for n in range(1, product_count + 1):
        cid, parent, _, slug = leaves[(n - 1) % len(leaves)] if n <= len(leaves) else r.choice(leaves)
        kind, types, (lo, hi), size_set, attrs = TEMPLATES[slug]
        color_name, color_key = r.choice(COLORS)
        extra = r.sample([c for c in COLORS if c[0] != color_name], r.randint(0, 2))
        style, ptype = r.choice(STYLES), r.choice(types)
        sizes = list(SIZES[size_set])
        if len(sizes) > 3 and r.random() < 0.3:
            sizes.remove(r.choice(sizes[1:-1]))
        price = Decimal(r.randrange(lo, hi + 1, 50)).quantize(Decimal("0.01"))  # 499.00, 549.00…
        out_of_stock = r.random() < 0.015
        pid = f"PRD-{n:06d}"
        attributes = attrs(r)
        record = {
            "PK": f"PRODUCT#{pid}", "SK": "METADATA",
            "GSI1PK": f"CATEGORY#{slug}", "GSI1SK": f"PRICE#{price:010.2f}",
            "product_id": pid, "name": f"{style} {ptype}",
            "description": (f"{style} {ptype.lower()} in {color_name.lower()}. "
                            f"{attributes.get('Material', attributes.get('Upper', 'Quality materials'))} "
                            f"for everyday comfort. {attributes.get('Care', 'Easy to care for')}."),
            "category_id": cid, "seller_id": r.choice(by_top[top_slug[parent]]),
            "price": price, "stock": 0 if out_of_stock else r.randint(6, 150),
            "status": "active", "sizes": sizes, "colors": [color_name] + [c[0] for c in extra],
            "color_key": color_key, "kind": kind, "attributes": attributes,
            "threshold_limit": 0 if out_of_stock else 5,
            "created_at": (now - timedelta(days=r.randint(0, 365), minutes=r.randint(0, 1440))).isoformat(),
            "rating": None, "review_count": 0,
        }
        store.products[pid] = record

        reviewers = set()
        for _ in range(r.choice([0, 0, 1, 2, 3, 4])):
            stars = r.choice([5, 5, 5, 5, 4, 4, 4, 3, 2, 1])
            buyer = r.choice(buyers)
            comment = r.choice(COMMENTS[stars])
            created = (now - timedelta(days=r.randint(1, 120))).isoformat()
            # One review per buyer and product (UNIQUE in PostgreSQL). The random
            # draws above still happen, so the rest of the catalog stays identical.
            if buyer in reviewers:
                continue
            reviewers.add(buyer)
            review_id += 1
            review = {"review_id": review_id, "product_id": pid, "buyer_id": buyer,
                      "rating": stars, "comment": comment, "created_at": created}
            store.reviews[review_id] = review
            store.reviews_by_product[pid].append(review)
        refresh_rating(store, pid)
    store._counters["reviews"] = review_id

    for record in store.products.values():
        record["_search"] = search_text(store, record)

    _seed_seller_demo(store, r, now)


def _seed_seller_demo(store, r, now):
    """Sales history (POSTGRES_ANALYTICS) and stock alerts for Urban Thread."""
    own = [p for p in store.products.values() if p["seller_id"] == "SEL-0001" and p["stock"] > 0]
    if not own:
        return
    best = own[:40]
    sale_id = 0
    for days_ago in range(29, -1, -1):
        day = (now - timedelta(days=days_ago)).date()
        target = r.randint(28, 64)
        while target > 0:
            product = r.choice(best[:8]) if r.random() < 0.55 else r.choice(best)
            qty = min(target, r.randint(1, 3))
            sale_id += 1
            store.analytics.append({"sale_id": sale_id, "product_id": product["product_id"],
                                    "category_id": product["category_id"], "seller_id": "SEL-0001",
                                    "quantity_sold": qty, "sale_date": day.isoformat()})
            target -= qty
    store._counters["analytics"] = sale_id

    for product, stock, limit in zip(own[40:44], [2, 0, 4, 25], [10, 5, 8, 10]):
        product["stock"], product["threshold_limit"] = stock, limit
        if stock >= limit:   # a resolved alert: it was low, then restocked
            product["stock"] = 1
            refresh_alert(store, product)
            product["stock"] = stock
        refresh_alert(store, product)
