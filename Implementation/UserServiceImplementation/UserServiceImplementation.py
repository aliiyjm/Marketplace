"""User management (admin CRUD). Storage is delegated to a UserRepository."""
import uuid

from Interfaces.UserServiceInterface.UserDTO import UserDTO
from Interfaces.UserServiceInterface.UserServiceInterface import DuplicateId
from Interfaces.Validators import USER_ID_RE, ValidationError, normalize


def generate_id():
    """Automatic unique ID (10 hex characters). The user never types it."""
    return uuid.uuid4().hex[:10]


class UserServiceImplementation:
    def __init__(self, repo, store=None):
        self.repo = repo
        self.store = store       # to keep the seller profile in sync (PostgreSQL)

    def list(self, q="", role=""):
        users = [UserDTO.from_record(u) for u in self.repo.list_users() if u.get("id_user")]
        if role:
            users = [u for u in users if u.role == role]
        if q:
            n = normalize(q)
            users = [u for u in users if n in normalize(u.id_user) or n in normalize(u.name)
                     or n in normalize(u.email)]
        return sorted(users, key=lambda u: u.name.lower())

    def get(self, user_id):
        if not USER_ID_RE.fullmatch(user_id or ""):
            return None
        record = self.repo.get_user(user_id)
        return UserDTO.from_record(record) if record else None

    def get_record(self, user_id):
        """Full record (with password hash). Only for AuthService."""
        return self.repo.get_user(user_id) if USER_ID_RE.fullmatch(user_id or "") else None

    def get_by_email(self, email):
        return self.repo.get_by_email(email)

    def _email_taken(self, email, except_id=None):
        other = self.repo.get_by_email(email)
        return bool(other and other.get("id_user") != except_id)

    def create(self, form, password_hash="!", created_at=""):
        if self._email_taken(form.email):
            raise ValidationError("That email is already registered.")
        record = {"name": form.name, "email": form.email, "role": form.role,
                  "password_hash": password_hash, "created_at": created_at}
        for _ in range(3):   # a repeated random id is very rare: retry with a new one
            record["id_user"] = generate_id()
            try:
                self.repo.add_user(record)
                return record["id_user"]
            except DuplicateId:
                continue
        raise ValidationError("Could not generate a unique ID. Please try again.")

    def update(self, user_id, form):
        existing = self.repo.get_user(user_id)
        if existing is None:
            raise ValidationError("That user no longer exists.")
        if self._email_taken(form.email, except_id=user_id):
            raise ValidationError("That email is already registered.")
        # put_item replaces the whole record: start from the existing one so
        # fields the form does not show (password_hash, created_at) survive.
        existing.pop("nombre", None)   # migrate legacy field name
        existing.update(name=form.name, email=form.email, role=form.role)
        self.repo.update_user(existing)

    def set_password_hash(self, user_id, password_hash):
        """Replace only the password hash (used to upgrade scrypt -> Argon2id)."""
        existing = self.repo.get_user(user_id)
        if existing is None:
            raise ValidationError("That user no longer exists.")
        existing.pop("nombre", None)
        existing["password_hash"] = password_hash
        self.repo.update_user(existing)

    def delete(self, user_id):
        if self.repo.get_user(user_id) is None:
            raise ValidationError("That user no longer exists.")
        self.repo.delete_user(user_id)
        if self.store is not None:
            seller = self.store.seller_by_user(user_id)
            if seller:   # hide their products from the catalog (PostgreSQL sellers.status)
                self.store.ops.set_seller_status(seller["seller_id"], "suspended")
                self.store.reload_meta(force=True)
