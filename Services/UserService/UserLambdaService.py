"""UserLambdaService: Lambda routes for users (same paths the Flask api_client uses).

    GET    /usuarios/consultar            all users
           /usuarios/consultar?id_user=   one user
           /usuarios/consultar?email=     one user by email (GSI1, used at sign-in)
    POST   /usuarios/agregar              create (409 if the id or the email exists)
    PUT    /usuarios/modificar            replace an existing user
    DELETE /usuarios/eliminar?id_user=    delete

Security: users are private data (emails, password hashes), so EVERY users
route requires the x-api-key header (MARKETPLACE_API_KEY). Only the Flask
backend has the key. Bodies are validated with UserItem (whitelisted fields,
no plain-text passwords).
"""
from Implementation.UserServiceImplementation.UserDynamoImplementation import (
    EmailTaken, UserDynamoImplementation, UserExists, UserNotFound)
from Interfaces.UserServiceInterface.UserItem import UserItem
from Interfaces.Validators import USER_ID_RE, ValidationError, valid_email
from Services.LambdaCommon import (api_key_configured, authorized, get_table, gsi_name,
                                   json_body, log, parse_request, response)

USER_QUERY = "/usuarios/consultar"
USER_ADD = "/usuarios/agregar"
USER_UPDATE = "/usuarios/modificar"
USER_DELETE = "/usuarios/eliminar"
USER_ROUTES = {(USER_QUERY, "GET"), (USER_ADD, "POST"), (USER_UPDATE, "PUT"), (USER_DELETE, "DELETE")}

_user_service = None


def default_user_service():
    global _user_service
    if _user_service is None:
        _user_service = UserDynamoImplementation(get_table(), gsi_name())
    return _user_service


def _id_user(value):
    value = str(value or "")
    if not USER_ID_RE.fullmatch(value):
        raise ValidationError("id_user is invalid.")
    return value


def handle_usuarios(event, context=None, service=None):
    method, path, headers, query, body = parse_request(event)
    route = next((r for r, _ in USER_ROUTES if path.endswith(r)), None)
    if route is None:
        return response(404, {"error": "Route not found."})
    if (route, method) not in USER_ROUTES:
        return response(405, {"error": "Method not allowed."})
    if not api_key_configured():
        return response(503, {"error": "MARKETPLACE_API_KEY is not configured in the Lambda."})
    if not authorized(headers):
        return response(403, {"error": "Forbidden."})

    svc = service or default_user_service()
    try:
        if route == USER_QUERY:
            if query.get("id_user"):
                user = svc.get(_id_user(query["id_user"]))
            elif query.get("email"):
                email = str(query["email"]).strip().lower()
                if not valid_email(email):
                    raise ValidationError("email is invalid.")
                user = svc.by_email(email)
            else:
                return response(200, [u.to_payload() for u in svc.list_all()])
            return response(200, user.to_payload()) if user else response(404, {"error": "User not found."})

        if route == USER_DELETE:
            svc.delete(_id_user(query.get("id_user")))
            return response(200, {"message": "User deleted"})

        user = UserItem.from_payload(json_body(body))
        if route == USER_ADD:
            svc.create(user)
            return response(200, {"message": "User added successfully", "id_user": user.id_user})
        svc.replace(user)
        return response(200, {"message": "User updated successfully", "id_user": user.id_user})
    except UserExists as e:
        return response(409, {"error": str(e), "code": "EXISTS"})
    except EmailTaken as e:
        return response(409, {"error": str(e), "code": "EMAIL_EXISTS"})
    except UserNotFound as e:
        return response(404, {"error": str(e), "code": "NOT_FOUND"})
    except ValidationError as e:
        return response(400, {"error": "; ".join(e.errors[:10])})
    except Exception:
        log.exception("Unhandled error on %s %s", method, route)
        return response(500, {"error": "Internal error."})
