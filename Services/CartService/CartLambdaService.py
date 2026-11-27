"""CartLambdaService / favorites: Lambda routes for the quick transactions.

    GET    /carrito/consultar?id_user=      the user's cart  {"id_user", "lines": [...]}
    PUT    /carrito/guardar                 replace the cart (body: {"id_user", "lines": [...]})
    DELETE /carrito/vaciar?id_user=         empty the cart (after checkout)
    GET    /favoritos/consultar?id_user=    {"id_user", "items": {product_id: added_at}}
    PUT    /favoritos/guardar               replace the favorites

Carts and favorites are private: every route requires a JWT service token
signed with MARKETPLACE_API_KEY ("read" for GET, "write" for the rest), which
only the Flask backend can make.
"""
from Implementation.CartServiceImplementation.QuickDynamoImplementation import (CartDynamoImplementation,
                                                                                FavDynamoImplementation)
from Interfaces.CartServiceInterface.CartItem import CartItem
from Interfaces.FavServiceInterface.FavItem import FavItem
from Interfaces.Validators import USER_ID_RE, ValidationError
from Services.LambdaCommon import (access_denied, get_table, json_body, log, parse_request,
                                   response)

QUICK_ROUTES = {("/carrito/consultar", "GET"), ("/carrito/guardar", "PUT"), ("/carrito/vaciar", "DELETE"),
                ("/favoritos/consultar", "GET"), ("/favoritos/guardar", "PUT")}

_quick_services = None


def default_quick_services():
    global _quick_services
    if _quick_services is None:
        table = get_table()
        _quick_services = (CartDynamoImplementation(table), FavDynamoImplementation(table))
    return _quick_services


def _uid(value):
    value = str(value or "")
    if not USER_ID_RE.fullmatch(value):
        raise ValidationError("id_user is invalid.")
    return value


def handle_rapidas(event, context=None, carts=None, favorites=None):
    method, path, headers, query, body = parse_request(event)
    route = next((r for r, _ in QUICK_ROUTES if path.endswith(r)), None)
    if route is None:
        return response(404, {"error": "Route not found."})
    if (route, method) not in QUICK_ROUTES:
        return response(405, {"error": "Method not allowed."})
    denied = access_denied(headers, "read" if method == "GET" else "write")
    if denied:
        return denied
    if carts is None or favorites is None:
        default_carts, default_favorites = default_quick_services()
        carts, favorites = carts or default_carts, favorites or default_favorites
    try:
        if route == "/carrito/consultar":
            return response(200, carts.get(_uid(query.get("id_user"))).to_payload())
        if route == "/carrito/guardar":
            cart = CartItem.from_payload(json_body(body))
            carts.save(cart)
            return response(200, {"id_user": cart.id_user, "lines": len(cart.lines)})
        if route == "/carrito/vaciar":
            carts.clear(_uid(query.get("id_user")))
            return response(200, {"message": "Cart emptied"})
        if route == "/favoritos/consultar":
            return response(200, favorites.get(_uid(query.get("id_user"))).to_payload())
        fav = FavItem.from_payload(json_body(body))
        favorites.save(fav)
        return response(200, {"id_user": fav.id_user, "items": len(fav.items)})
    except ValidationError as e:
        return response(400, {"error": "; ".join(e.errors[:10])})
    except Exception:
        log.exception("Unhandled error on %s %s", method, route)
        return response(500, {"error": "Internal error."})
