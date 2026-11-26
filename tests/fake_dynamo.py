"""Test doubles: an in-memory DynamoDB table and an HTTP layer that turns
requests into API Gateway events for the products Lambda.

FakeTable supports exactly the expressions ProductServiceImplementation uses,
with DynamoDB semantics: conditional writes, Limit = items evaluated per call,
LastEvaluatedKey pagination, GSI1 queries sorted by GSI1SK and numbers
returned as Decimal (like boto3).
"""
import copy
import json
from decimal import Decimal
from urllib.parse import urlsplit

from Services.LambdaRouter import lambda_handler as router
from Services.ProductService.ProductService import handle_productos as lambda_handler


class ConditionalCheckFailed(Exception):
    def __init__(self):
        super().__init__("The conditional request failed")
        self.response = {"Error": {"Code": "ConditionalCheckFailedException"}}


def _as_dynamo(value):
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return Decimal(value)
    if isinstance(value, dict):
        return {k: _as_dynamo(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_as_dynamo(v) for v in value]
    return value


class TransactionCanceled(Exception):
    """Like botocore's ClientError for TransactWriteItems: one reason per action."""

    def __init__(self, codes):
        super().__init__("Transaction cancelled")
        self.response = {"Error": {"Code": "TransactionCanceledException"},
                         "CancellationReasons": [{"Code": c} for c in codes]}


def from_attribute(av):
    """DynamoDB low-level attribute -> Python value (numbers as Decimal, like boto3)."""
    (kind, value), = av.items()
    if kind == "S":
        return value
    if kind == "N":
        return Decimal(value)
    if kind == "BOOL":
        return value
    if kind == "NULL":
        return None
    if kind == "M":
        return {k: from_attribute(v) for k, v in value.items()}
    if kind == "L":
        return [from_attribute(v) for v in value]
    raise NotImplementedError(kind)


class _FakeClient:
    """table.meta.client: only transact_write_items, with all-or-nothing semantics."""

    def __init__(self, table):
        self.table = table
        self.transactions = 0

    def transact_write_items(self, TransactItems):
        if len(TransactItems) > 100:
            raise ValueError("Too many actions in one transaction")
        touched = set()
        plan, codes = [], []
        for action in TransactItems:
            (kind, body), = action.items()
            assert body["TableName"] == self.table.name
            if kind == "Put":
                item = {k: from_attribute(v) for k, v in body["Item"].items()}
                key = (item["PK"], item["SK"])
            else:
                key = tuple(from_attribute(body["Key"][k]) for k in ("PK", "SK"))
            if key in touched:                       # real DynamoDB rejects this too
                raise ValueError("Transaction touches the same item twice")
            touched.add(key)
            current = self.table.items.get(key)
            values = {k: from_attribute(v) for k, v in body.get("ExpressionAttributeValues", {}).items()}
            ok = self._condition(body.get("ConditionExpression"), current, values,
                                 body.get("ExpressionAttributeNames", {}))
            codes.append("None" if ok else "ConditionalCheckFailed")
            plan.append((kind, key, body, values, item if kind == "Put" else None))
        if "ConditionalCheckFailed" in codes:
            raise TransactionCanceled(codes)         # nothing was applied
        for kind, key, body, values, item in plan:
            if kind == "Put":
                self.table.items[key] = copy.deepcopy(item)
            else:
                self._apply(self.table.items[key], body["UpdateExpression"], values,
                            body.get("ExpressionAttributeNames", {}))
        self.transactions += 1
        return {}

    @staticmethod
    def _condition(expr, item, values, names):
        if expr is None:
            return True
        if expr == "attribute_not_exists(PK)":
            return item is None
        if expr == "attribute_exists(PK)":
            return item is not None
        if expr == "attribute_exists(PK) AND stock >= :q":
            return item is not None and item["stock"] >= values[":q"]
        if expr == "#st = :reserved":
            return item is not None and item.get(names["#st"]) == values[":reserved"]
        raise NotImplementedError(expr)

    @staticmethod
    def _apply(item, expr, values, names):
        if expr == "SET stock = stock - :q":
            item["stock"] -= values[":q"]
        elif expr == "SET stock = stock + :q":
            item["stock"] += values[":q"]
        elif expr == "SET #st = :released, released_at = :now":
            item[names["#st"]] = values[":released"]
            item["released_at"] = values[":now"]
        else:
            raise NotImplementedError(expr)


class _Meta:
    def __init__(self, table):
        self.client = _FakeClient(table)


class _BatchWriter:
    def __init__(self, table):
        self.table = table

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def put_item(self, Item):
        self.table.items[(Item["PK"], Item["SK"])] = _as_dynamo(copy.deepcopy(Item))
        self.table.writes += 1


class FakeTable:
    def __init__(self, name="MarketplaceDB"):
        self.name = name
        self.items = {}
        self.writes = 0
        self.meta = _Meta(self)

    # ---- single item ----
    def get_item(self, Key, ConsistentRead=False):
        item = self.items.get((Key["PK"], Key["SK"]))
        return {"Item": copy.deepcopy(item)} if item else {}

    def put_item(self, Item, ConditionExpression=None):
        key = (Item["PK"], Item["SK"])
        exists = key in self.items
        if ConditionExpression == "attribute_not_exists(PK)" and exists:
            raise ConditionalCheckFailed()
        if ConditionExpression == "attribute_exists(PK)" and not exists:
            raise ConditionalCheckFailed()
        self.items[key] = _as_dynamo(copy.deepcopy(Item))
        self.writes += 1

    def update_item(self, Key, UpdateExpression, ExpressionAttributeValues=None, ConditionExpression=None,
                    ExpressionAttributeNames=None, ReturnValues=None):
        item = self.items.get((Key["PK"], Key["SK"]))
        values = ExpressionAttributeValues or {}
        if item is None:
            raise ConditionalCheckFailed()
        if ConditionExpression and ":need" in ConditionExpression and item["stock"] < values[":need"]:
            raise ConditionalCheckFailed()
        if UpdateExpression == "SET stock = stock + :d":
            item["stock"] = item["stock"] + values[":d"]
            return {"Attributes": {"stock": Decimal(item["stock"])}}
        if UpdateExpression == "SET #s = :d":
            item[ExpressionAttributeNames["#s"]] = values[":d"]
            return {}
        if UpdateExpression == "SET image_url = :u":
            item["image_url"] = values[":u"]
            return {}
        if UpdateExpression == "REMOVE image_url":
            item.pop("image_url", None)
            return {}
        raise NotImplementedError(UpdateExpression)

    def delete_item(self, Key, ConditionExpression=None):
        key = (Key["PK"], Key["SK"])
        if ConditionExpression == "attribute_exists(PK)" and key not in self.items:
            raise ConditionalCheckFailed()
        self.items.pop(key, None)

    # ---- many items ----
    @staticmethod
    def _page(rows, key_of, Limit, ExclusiveStartKey):
        start = 0
        if ExclusiveStartKey:
            keys = [key_of(r) for r in rows]
            start = keys.index(ExclusiveStartKey) + 1
        evaluated = rows[start:start + Limit]
        last = key_of(evaluated[-1]) if evaluated and start + Limit < len(rows) else None
        return evaluated, last

    def scan(self, FilterExpression, ExpressionAttributeValues, Limit, ExclusiveStartKey=None):
        assert FilterExpression == "begins_with(PK, :p) AND SK = :sk"
        rows = [self.items[k] for k in sorted(self.items)]
        evaluated, last = self._page(rows, lambda r: {"PK": r["PK"], "SK": r["SK"]}, Limit, ExclusiveStartKey)
        p, sk = ExpressionAttributeValues[":p"], ExpressionAttributeValues[":sk"]
        found = [copy.deepcopy(r) for r in evaluated if r["PK"].startswith(p) and r["SK"] == sk]
        return {"Items": found, **({"LastEvaluatedKey": last} if last else {})}

    def query(self, IndexName, KeyConditionExpression, ExpressionAttributeValues, Limit, ExclusiveStartKey=None):
        assert IndexName == "GSI1" and KeyConditionExpression == "GSI1PK = :c"
        rows = sorted((r for r in self.items.values() if r.get("GSI1PK") == ExpressionAttributeValues[":c"]),
                      key=lambda r: (r["GSI1SK"], r["PK"]))
        key_of = lambda r: {"PK": r["PK"], "SK": r["SK"], "GSI1PK": r["GSI1PK"], "GSI1SK": r["GSI1SK"]}  # noqa: E731
        evaluated, last = self._page(rows, key_of, Limit, ExclusiveStartKey)
        return {"Items": [copy.deepcopy(r) for r in evaluated], **({"LastEvaluatedKey": last} if last else {})}

    def batch_writer(self, overwrite_by_pkeys=None):
        return _BatchWriter(self)


class _Response:
    def __init__(self, status, body):
        self.status_code = status
        self._body = body
        self.text = body or ""

    def json(self):
        return json.loads(self._body)


class LambdaHttp:
    """Pretends to be `requests`: builds an API Gateway HTTP API (v2) event and
    calls the real lambda_handler with the given service."""

    def __init__(self, service):
        self.service = service
        self.calls = []

    def request(self, method, url, headers=None, timeout=None, params=None, json=None):
        path = urlsplit(url).path
        self.calls.append((method, path))
        event = {
            "version": "2.0", "rawPath": path, "requestContext": {"http": {"method": method}},
            "headers": headers or {},
            "queryStringParameters": {k: str(v) for k, v in (params or {}).items()} or None,
            "body": None if json is None else _json_dumps(json), "isBase64Encoded": False,
        }
        handler = getattr(self.service, "_handler", lambda_handler)
        out = handler(event, service=self.service)
        return _Response(out["statusCode"], out["body"])


class RouterHttp:
    """Like LambdaHttp but through the single Lambda entry point (LambdaRouter),
    with users and products sharing one FakeTable."""

    def __init__(self, products, users, handler=None, carts=None, favorites=None):
        self.products, self.users = products, users
        self.carts, self.favorites = carts, favorites
        self.handler = handler or router
        self.calls = []

    def request(self, method, url, headers=None, timeout=None, params=None, json=None):
        path = urlsplit(url).path
        self.calls.append((method, path))
        event = {
            "version": "2.0", "rawPath": path, "requestContext": {"http": {"method": method}},
            "headers": headers or {},
            "queryStringParameters": {k: str(v) for k, v in (params or {}).items()} or None,
            "body": None if json is None else _json_dumps(json), "isBase64Encoded": False,
        }
        out = self.handler(event, None, products=self.products, users=self.users,
                           carts=self.carts, favorites=self.favorites)
        return _Response(out["statusCode"], out["body"])


def _json_dumps(data):
    return json.dumps(data)


class FakeS3Client:
    """The few boto3 S3 client calls the photo store uses."""

    def __init__(self, fail_put=False):
        self.objects = {}           # (bucket, key) -> {"Body", "ContentType", "CacheControl"}
        self.fail_put = fail_put

    def put_object(self, Bucket, Key, Body, ContentType, CacheControl):
        if self.fail_put:
            raise RuntimeError("S3 is down")
        self.objects[(Bucket, Key)] = {"Body": bytes(Body), "ContentType": ContentType,
                                       "CacheControl": CacheControl}

    def delete_object(self, Bucket, Key):
        self.objects.pop((Bucket, Key), None)
