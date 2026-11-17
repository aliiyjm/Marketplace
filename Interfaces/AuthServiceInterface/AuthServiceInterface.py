"""Contract for authentication."""
from abc import ABC, abstractmethod


class AuthServiceInterface(ABC):
    @abstractmethod
    def sign_in(self, form):
        """SignInForm -> SessionUserDTO. Raises ValidationError (generic message)."""

    @abstractmethod
    def register(self, form):
        """RegisterForm -> SessionUserDTO. Creates the seller profile when needed."""

    @abstractmethod
    def session_user(self, user_id):
        """Reload the user on every request (role changes / deletions apply at once)."""
