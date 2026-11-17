"""AuthDTO: sign-in / registration forms and the user kept in the session."""
from dataclasses import dataclass

from Interfaces.Validators import RFC_RE, ValidationError, clean_text, valid_email

MIN_PASSWORD = 8
MAX_PASSWORD = 128   # caps hashing cost for absurdly long inputs


def check_password_policy(password):
    problems = []
    if len(password) < MIN_PASSWORD:
        problems.append(f"Password must have at least {MIN_PASSWORD} characters.")
    if len(password) > MAX_PASSWORD:
        problems.append(f"Password must have at most {MAX_PASSWORD} characters.")
    if not any(c.isupper() for c in password):
        problems.append("Password needs at least one uppercase letter.")
    if not any(c.isdigit() for c in password):
        problems.append("Password needs at least one number.")
    return problems


@dataclass(frozen=True)
class SignInForm:
    email: str
    password: str
    remember: bool

    def __repr__(self):  # never print passwords in logs or tracebacks
        return f"SignInForm(email={self.email!r}, password='***')"

    @classmethod
    def from_form(cls, form):
        email = clean_text(form.get("email"), 120, field="Email").lower()
        password = str(form.get("password", ""))[:MAX_PASSWORD]
        if not email or not password:
            raise ValidationError("Enter your email and password.")
        return cls(email=email, password=password, remember=form.get("remember") == "1")


@dataclass(frozen=True)
class RegisterForm:
    name: str
    email: str
    password: str
    role: str                # "buyer" or "seller"
    store_name: str
    tax_id: str

    def __repr__(self):
        return f"RegisterForm(email={self.email!r}, role={self.role!r}, password='***')"

    @classmethod
    def from_form(cls, form):
        errors = []
        try:
            name = clean_text(form.get("name"), 80, field="Full name", required=True)
        except ValidationError as e:
            errors += e.errors
            name = ""
        email = clean_text(form.get("email"), 120, field="Email").lower()
        if not valid_email(email):
            errors.append("Enter a valid email address.")
        password = str(form.get("password", ""))
        errors += check_password_policy(password)
        if password != str(form.get("confirm", "")):
            errors.append("Passwords do not match.")
        role = "seller" if form.get("role") == "seller" else "buyer"
        store_name = tax_id = ""
        if role == "seller":
            try:
                store_name = clean_text(form.get("store_name"), 60, field="Store name", required=True)
            except ValidationError as e:
                errors += e.errors
            tax_id = clean_text(form.get("tax_id"), 13, field="RFC").upper()
            if not RFC_RE.fullmatch(tax_id):
                errors.append("Enter a valid RFC (12 or 13 characters).")
        if form.get("terms") != "1":
            errors.append("You must accept the Terms and the Privacy notice.")
        if errors:
            raise ValidationError(errors)
        return cls(name=name, email=email, password=password, role=role,
                   store_name=store_name, tax_id=tax_id)


@dataclass(frozen=True)
class SessionUserDTO:
    """What the View knows about the signed-in user. No password hash here."""
    id_user: str
    name: str
    email: str
    role: str                 # buyer | seller | admin
    seller_id: str | None = None

    @property
    def first_name(self):
        return self.name.split(" ")[0] if self.name else "Account"

    @property
    def initial(self):
        return (self.name[:1] or "?").upper()

    @property
    def is_seller(self):
        return self.seller_id is not None

    @property
    def is_admin(self):
        return self.role == "admin"
