"""UserDTO: defines and validates the allowed user fields (id, name, email, role).
Any extra field is rejected before it reaches the database."""
from dataclasses import dataclass

from Interfaces.Validators import ValidationError, clean_text, valid_email

ROLES = ("buyer", "seller", "admin")


@dataclass(frozen=True)
class UserDTO:
    id_user: str
    name: str
    email: str
    role: str
    created_at: str = ""

    @classmethod
    def from_record(cls, record):
        # Records created before the English rename store the name as 'nombre'.
        return cls(id_user=str(record.get("id_user", "")),
                   name=record.get("name") or record.get("nombre", ""),
                   email=record.get("email", ""),
                   role=record.get("role") if record.get("role") in ROLES else "buyer",
                   created_at=str(record.get("created_at", ""))[:10])


@dataclass(frozen=True)
class UserForm:
    """Admin form. Only these three fields can be changed from the UI."""
    name: str
    email: str
    role: str

    @classmethod
    def from_form(cls, form):
        errors = []
        try:
            name = clean_text(form.get("name"), 80, field="Name", required=True)
        except ValidationError as e:
            errors += e.errors
            name = ""
        email = clean_text(form.get("email"), 120, field="Email").lower()
        if not valid_email(email):
            errors.append("Enter a valid email address.")
        role = form.get("role", "buyer")
        if role not in ROLES:
            errors.append("Choose a valid role.")
        if errors:
            raise ValidationError(errors)
        return cls(name=name, email=email, role=role)
