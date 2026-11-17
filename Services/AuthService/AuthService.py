"""AuthService: sign-up, login and session validation.
Passwords are stored hashed (scrypt via werkzeug), never in plain text."""
from Interfaces.AuthServiceInterface.AuthDTO import RegisterForm, SignInForm


class AuthService:
    def __init__(self, impl):
        self.impl = impl          # AuthServiceInterface

    def sign_in(self, form_data):
        form = SignInForm.from_form(form_data)
        return self.impl.sign_in(form), form.remember

    def register(self, form_data):
        return self.impl.register(RegisterForm.from_form(form_data))

    def current_user(self, user_id):
        return self.impl.session_user(user_id) if user_id else None
