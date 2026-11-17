"""Sign-in, registration and session user.

Security notes:
- Passwords are hashed with werkzeug's scrypt (salted). Plain text never stored.
- 5 failed attempts lock the email for 15 minutes (slows down brute force).
- The same generic message is used for "unknown email" and "wrong password",
  and a dummy hash is checked for unknown emails so response time does not
  reveal which emails exist.
"""
from datetime import timedelta

from werkzeug.security import check_password_hash, generate_password_hash

from Interfaces.AuthServiceInterface.AuthDTO import SessionUserDTO
from Interfaces.AuthServiceInterface.AuthServiceInterface import AuthServiceInterface
from Interfaces.UserServiceInterface.UserDTO import UserForm
from Interfaces.Validators import ValidationError

MAX_ATTEMPTS = 5
LOCK_MINUTES = 15
GENERIC_ERROR = "Email or password is incorrect."
_DUMMY_HASH = generate_password_hash("not-a-real-password")


class AuthServiceImplementation(AuthServiceInterface):
    def __init__(self, store, users):
        self.store = store
        self.users = users          # UserServiceImplementation
        self._attempts = {}         # email -> (failures, locked_until)

    def _locked(self, email):
        failures, until = self._attempts.get(email, (0, None))
        return until is not None and until > self.store.now()

    def _fail(self, email):
        failures, _ = self._attempts.get(email, (0, None))
        failures += 1
        until = self.store.now() + timedelta(minutes=LOCK_MINUTES) if failures >= MAX_ATTEMPTS else None
        self._attempts[email] = (0 if until else failures, until)

    def sign_in(self, form):
        if self._locked(form.email):
            raise ValidationError(f"Too many failed attempts. Try again in {LOCK_MINUTES} minutes.")
        record = self.users.get_by_email(form.email)
        stored_hash = str((record or {}).get("password_hash", ""))
        if record and stored_hash.count("$") >= 2:
            ok = check_password_hash(stored_hash, form.password)
        else:
            check_password_hash(_DUMMY_HASH, form.password)   # same cost as a real check
            ok = False
        if not ok:
            self._fail(form.email)
            raise ValidationError(GENERIC_ERROR)
        self._attempts.pop(form.email, None)
        return self._to_session(record)

    def register(self, form):
        if self.users.get_by_email(form.email):
            raise ValidationError("That email is already registered. Sign in instead.")
        user_form = UserForm(name=form.name, email=form.email, role=form.role)
        user_id = self.users.create(user_form, password_hash=generate_password_hash(form.password),
                                    created_at=self.store.now().isoformat())
        if form.role == "seller":
            with self.store.lock:
                seller_no = self.store.next_id("sellers")
                seller_id = f"SEL-{seller_no:04d}"
                self.store.sellers[seller_id] = {
                    "seller_id": seller_id, "user_id": user_id, "store_name": form.store_name,
                    "tax_id": form.tax_id, "rating": 0.0, "status": "active",
                    "created_at": self.store.now().isoformat(), "_tops": [],
                }
        return self.session_user(user_id)

    def session_user(self, user_id):
        record = self.users.get_record(user_id)
        return self._to_session(record) if record else None

    def _to_session(self, record):
        seller = self.store.seller_by_user(record["id_user"])
        seller_id = seller["seller_id"] if seller and seller["status"] == "active" else None
        return SessionUserDTO(id_user=record["id_user"],
                              name=record.get("name") or record.get("nombre", ""),
                              email=record.get("email", ""), role=record.get("role", "buyer"),
                              seller_id=seller_id)

    @staticmethod
    def hash_password(password):
        return generate_password_hash(password)
