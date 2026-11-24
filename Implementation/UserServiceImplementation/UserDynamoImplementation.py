"""DynamoDB access for users in MarketplaceDB (runs inside the Lambda).
This is the only layer that knows how users are stored in the table."""
from Implementation.ProductServiceImplementation.ProductServiceImplementation import \
    is_conditional_failure
from Interfaces.UserServiceInterface.UserItem import (EMAIL_PREFIX, USER_PK_PREFIX, USER_SK,
                                                      UserItem)
from Interfaces.Validators import ValidationError


class UserNotFound(ValidationError):
    """No user with that id."""


class UserExists(ValidationError):
    """A user with that id already exists."""


class EmailTaken(ValidationError):
    """Another user already has that email."""


class UserDynamoImplementation:
    def __init__(self, table, index_name="GSI1"):
        self.table = table
        self.index_name = index_name

    @staticmethod
    def _key(id_user):
        return {"PK": USER_PK_PREFIX + id_user, "SK": USER_SK}

    def get(self, id_user):
        item = self.table.get_item(Key=self._key(id_user)).get("Item")
        return UserItem.from_item(item) if item else None

    def by_email(self, email):
        resp = self.table.query(IndexName=self.index_name, KeyConditionExpression="GSI1PK = :c",
                                ExpressionAttributeValues={":c": EMAIL_PREFIX + email.strip().lower()},
                                Limit=10)
        items = resp.get("Items", [])
        return UserItem.from_item(items[0]) if items else None

    def list_all(self):
        users, start = [], None
        while True:
            kwargs = {"FilterExpression": "begins_with(PK, :p) AND SK = :sk",
                      "ExpressionAttributeValues": {":p": USER_PK_PREFIX, ":sk": USER_SK},
                      "Limit": 1000}
            if start:
                kwargs["ExclusiveStartKey"] = start
            resp = self.table.scan(**kwargs)
            users += [UserItem.from_item(i) for i in resp.get("Items", [])]
            start = resp.get("LastEvaluatedKey")
            if not start:
                return users

    def _check_email(self, user):
        other = self.by_email(user.email)
        if other and other.id_user != user.id_user:
            raise EmailTaken("That email is already registered.")

    def create(self, user):
        self._check_email(user)
        try:
            self.table.put_item(Item=user.to_item(), ConditionExpression="attribute_not_exists(PK)")
        except Exception as e:
            if is_conditional_failure(e):
                raise UserExists("A user with that ID already exists.") from None
            raise

    def replace(self, user):
        if self.get(user.id_user) is None:
            raise UserNotFound("User not found.")
        self._check_email(user)
        try:
            self.table.put_item(Item=user.to_item(), ConditionExpression="attribute_exists(PK)")
        except Exception as e:
            if is_conditional_failure(e):
                raise UserNotFound("User not found.") from None
            raise

    def delete(self, id_user):
        try:
            self.table.delete_item(Key=self._key(id_user), ConditionExpression="attribute_exists(PK)")
        except Exception as e:
            if is_conditional_failure(e):
                raise UserNotFound("User not found.") from None
            raise
