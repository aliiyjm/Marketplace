"""UserService: admin CRUD of users (query, add, update, delete).
Receives raw input, validates it with UserDTO and delegates to UserServiceImplementation."""
from Implementation.AuthServiceImplementation.Passwords import hash_password

from Interfaces.AuthServiceInterface.AuthDTO import check_password_policy
from Interfaces.UserServiceInterface.UserDTO import ROLES, UserForm
from Interfaces.Validators import ValidationError, clean_text


class UserService:
    def __init__(self, impl):
        self.impl = impl          # UserServiceImplementation

    def list(self, args):
        q = clean_text(args.get("q", "")[:80], 80)
        role = args.get("role", "") if args.get("role") in ROLES else ""
        return self.impl.list(q, role), q, role

    def get(self, user_id):
        return self.impl.get(user_id)

    def create(self, form_data, created_at=""):
        form = UserForm.from_form(form_data)
        temp = str(form_data.get("temp_password", ""))
        if temp:
            problems = check_password_policy(temp)
            if problems:
                raise ValidationError(problems)
        password_hash = hash_password(temp) if temp else "!"   # Argon2id; "!" = cannot sign in
        return self.impl.create(form, password_hash=password_hash, created_at=created_at)

    def update(self, user_id, form_data):
        self.impl.update(user_id, UserForm.from_form(form_data))

    def delete(self, user_id):
        self.impl.delete(user_id)
