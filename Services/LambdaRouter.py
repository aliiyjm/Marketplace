"""LambdaRouter: the single Lambda entry point (handler: lambda_function.lambda_handler).

    /productos/...                 -> ProductService      (catalog)
    /usuarios/...                  -> UserLambdaService   (accounts)
    /carrito/... and /favoritos/... -> CartLambdaService   (quick transactions)

Everything lives in the single DynamoDB table MarketplaceDB.
"""
from Services.CartService.CartLambdaService import handle_rapidas
from Services.LambdaCommon import parse_request, response
from Services.ProductService.ProductService import handle_productos
from Services.UserService.UserLambdaService import handle_usuarios


def lambda_handler(event, context=None, products=None, users=None, carts=None, favorites=None):
    method, path, _, _, _ = parse_request(event)
    if method == "OPTIONS":
        return {"statusCode": 204, "headers": {"Allow": "GET,POST,PUT,DELETE,OPTIONS"}, "body": ""}
    if "/productos/" in path:
        return handle_productos(event, context, service=products)
    if "/usuarios/" in path:
        return handle_usuarios(event, context, service=users)
    if "/carrito/" in path or "/favoritos/" in path:
        return handle_rapidas(event, context, carts=carts, favorites=favorites)
    return response(404, {"error": "Route not found."})
