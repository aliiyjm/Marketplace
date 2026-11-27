"""UserItem: one user as stored in DynamoDB MarketplaceDB (single-table design).

    PK     = USER#<id_user>       SK     = PROFILE
    GSI1PK = EMAIL#<email>        GSI1SK = USER#<id_user>   (find a user by email
             with one Query on GSI1 when signing in, instead of scanning the table)

Only these fields are stored (whitelist): anything else in the request is
dropped. The password is never stored in plain text: only a werkzeug hash
(scrypt:/pbkdf2:) or "!" (account that cannot sign in) is accepted.
"""
from dataclasses import dataclass

from Interfaces.UserServiceInterface.UserDTO import ROLES
from Interfaces.Validators import USER_ID_RE, ValidationError, clean_text, valid_email

USER_PK_PREFIX = "USER#"
USER_SK = "PROFILE"
EMAIL_PREFIX = "EMAIL#"
HASH_PREFIXES = ("$argon2id$", "scrypt:", "pbkdf2:", "!")   # Argon2id now; scrypt/pbkdf2 = older accounts


@dataclass(frozen=True)
class UserItem:
    id_user: str
    name: str
    email: str
    role: str
    password_hash: str = "!"
    created_at: str = ""

    @classmethod
    def from_payload(cls, data):
        if not isinstance(data, dict):
            raise ValidationError("User must be a JSON object.")
        errors = []
        id_user = str(data.get("id_user", ""))
        if not USER_ID_RE.fullmatch(id_user):
            errors.append("id_user is invalid.")
        try:   # records created before the English rename use 'nombre'
            name = clean_text(data.get("name") or data.get("nombre"), 80, field="name", required=True)
        except ValidationError as e:
            errors += e.errors
            name = ""
        email = str(data.get("email", "")).strip().lower()
        if not valid_email(email):
            errors.append("email is invalid.")
        role = data.get("role", "buyer")
        if role not in ROLES:
            errors.append("role is invalid.")
        password_hash = str(data.get("password_hash") or "!")
        if len(password_hash) > 300 or not password_hash.startswith(HASH_PREFIXES):
            errors.append("password_hash must be a password hash, never a plain password.")
        created_at = str(data.get("created_at") or "")[:32]
        if errors:
            raise ValidationError(errors)
        return cls(id_user=id_user, name=name, email=email, role=role,
                   password_hash=password_hash, created_at=created_at)

    def to_item(self):
        return {
            "PK": USER_PK_PREFIX + self.id_user, "SK": USER_SK, "entity": "USER",
            "GSI1PK": EMAIL_PREFIX + self.email, "GSI1SK": USER_PK_PREFIX + self.id_user,
            "id_user": self.id_user, "name": self.name, "email": self.email, "role": self.role,
            "password_hash": self.password_hash, "created_at": self.created_at,
        }

    @classmethod
    def from_item(cls, item):
        return cls(id_user=item["id_user"], name=item.get("name", ""), email=item.get("email", ""),
                   role=item.get("role", "buyer"), password_hash=item.get("password_hash", "!"),
                   created_at=item.get("created_at", ""))

    def to_payload(self):
        return {"id_user": self.id_user, "name": self.name, "email": self.email, "role": self.role,
                "password_hash": self.password_hash, "created_at": self.created_at}
