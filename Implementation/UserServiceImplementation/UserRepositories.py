"""User storage. This is the only layer that knows where users live.

- MemoryUserRepository: local data (default, works offline and in tests).
- ApiUserRepository: the existing API Gateway -> Lambda -> DynamoDB CRUD
  (enable it with DATA_BACKEND=aws).
"""
from Interfaces.UserServiceInterface.UserServiceInterface import (DuplicateId,
                                                                   UserRepositoryInterface)


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
    """Adapter over View/MainViews/Utils/api_client.py (the current Lambda)."""

    def __init__(self, api):
        self.api = api

    def list_users(self):
        return [u for u in self.api.list_users() if u.get("id_user")]

    def get_user(self, user_id):
        return self.api.get_user(user_id)

    def get_by_email(self, email):
        # The Lambda has no email index yet, so this is a scan. Add a GSI on email
        # (GSI1PK = EMAIL#<email>) before going to production.
        email = email.lower()
        return next((u for u in self.list_users() if str(u.get("email", "")).lower() == email), None)

    def add_user(self, record):
        try:
            self.api.add_user(record)
        except self.api.DuplicateId:
            raise DuplicateId(record["id_user"]) from None

    def update_user(self, record):
        self.api.update_user(record)

    def delete_user(self, user_id):
        self.api.delete_user(user_id)
