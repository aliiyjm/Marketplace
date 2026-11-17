"""Contract for the user storage. Two implementations exist:
MemoryUserRepository (local, default) and ApiUserRepository (API Gateway + Lambda + DynamoDB)."""
from abc import ABC, abstractmethod


class UserRepositoryInterface(ABC):
    @abstractmethod
    def list_users(self):
        """All user records (dicts)."""

    @abstractmethod
    def get_user(self, user_id):
        """User record or None."""

    @abstractmethod
    def get_by_email(self, email):
        """User record or None (email compared in lowercase)."""

    @abstractmethod
    def add_user(self, record):
        """Insert. Raises DuplicateId if the id exists."""

    @abstractmethod
    def update_user(self, record):
        """Replace the whole record (keeps fields the caller does not edit)."""

    @abstractmethod
    def delete_user(self, user_id):
        """Delete by id."""


class DuplicateId(Exception):
    """A user with that id already exists."""
