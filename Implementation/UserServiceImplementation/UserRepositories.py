"""User storage. This is the only layer that knows where users live.

- MemoryUserRepository: local data (default, works offline and in tests).
- ApiUserRepository: API Gateway -> Lambda -> DynamoDB MarketplaceDB
  (USER#<id> / PROFILE items; enable it with DATA_BACKEND=aws).
"""
from Interfaces.UserServiceInterface.UserServiceInterface import (DuplicateId,
                                                                   UserRepositoryInterface)
from Interfaces.Validators import ValidationError


class MemoryUserRepository(UserRepositoryInterface):
    def __init__(self, store):
        self.store = store

    def list_users(self):
        return [dict(u) for u in self.store.users.values()]

    def get_user(self, user_id):
        user = self.store.users.get(user_id)
        return dict(user) if user else None

    def get_by_email(self, email):
        email = email.lower()
        for user in self.store.users.values():
            if user.get("email", "").lower() == email:
                return dict(user)
        return None

    def add_user(self, record):
        with self.store.lock:
            if record["id_user"] in self.store.users:
                raise DuplicateId(record["id_user"])
            self.store.users[record["id_user"]] = dict(record, PK=f"USER#{record['id_user']}", SK="PROFILE")

    def update_user(self, record):
        with self.store.lock:
            self.store.users[record["id_user"]] = dict(record)

    def delete_user(self, user_id):
        with self.store.lock:
            self.store.users.pop(user_id, None)
            self.store.carts.pop(user_id, None)
            self.store.favorites.pop(user_id, None)


class ApiUserRepository(UserRepositoryInterface):
    """Adapter over View/MainViews/Utils/api_client.py (users routes of the Lambda,
    stored in MarketplaceDB). API errors become ValidationError so every screen
    shows a friendly message instead of crashing."""

    def __init__(self, api):
        self.api = api

    def _call(self, fn, *args):
        try:
            return fn(*args)
        except self.api.DuplicateId:
            raise DuplicateId(args[0]["id_user"] if args and isinstance(args[0], dict) else "") from None
        except self.api.ApiError as e:
            raise ValidationError(str(e)) from None

    def list_users(self):
        return [u for u in self._call(self.api.list_users) if u.get("id_user")]

    def get_user(self, user_id):
        return self._call(self.api.get_user, user_id)

    def get_by_email(self, email):
        return self._call(self.api.get_user_by_email, email.lower())

    def add_user(self, record):
        self._call(self.api.add_user, record)

    def update_user(self, record):
        self._call(self.api.update_user, record)

    def delete_user(self, user_id):
        self._call(self.api.delete_user, user_id)
