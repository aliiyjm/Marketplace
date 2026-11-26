"""scripts/aws_deploy.py with fake AWS clients: it never runs against AWS here."""
import io
import json
import zipfile

import pytest

from scripts.aws_deploy import (LAMBDA_FILE, LAMBDA_MEMORY, ROUTES, Deployer, api_from_url,
                                bucket_policy, images_url, lambda_name_from_uri, lambda_zip,
                                merged_environment, set_env_line)

FN_ARN = "arn:aws:lambda:us-east-1:123456789012:function:marketplace"


def test_api_from_url():
    assert api_from_url("https://i4rsywszmf.execute-api.us-east-1.amazonaws.com") == ("i4rsywszmf", "us-east-1")
    for bad in ("", "http://localhost:5000", "https://evil.com/i4rsywszmf.execute-api.us-east-1.amazonaws.com"):
        with pytest.raises(SystemExit):
            api_from_url(bad)


def test_lambda_package_is_the_generated_file():
    with zipfile.ZipFile(io.BytesIO(lambda_zip())) as zf:
        assert zf.namelist() == ["lambda_function.py"]
        assert zf.read("lambda_function.py") == LAMBDA_FILE.read_bytes()
    assert lambda_zip() == lambda_zip()          # deterministic: re-running uploads nothing new


def test_environment_keeps_every_variable():
    current = {"MARKETPLACE_API_KEY": "secret", "MARKETPLACE_TABLE": "MarketplaceDB"}
    assert merged_environment(current, "photos") == dict(current, MARKETPLACE_BUCKET="photos")
    assert current == {"MARKETPLACE_API_KEY": "secret", "MARKETPLACE_TABLE": "MarketplaceDB"}


def test_bucket_policy_only_allows_reading_photos():
    statement, = json.loads(bucket_policy("photos"))["Statement"]
    assert statement["Action"] == "s3:GetObject" and statement["Resource"] == "arn:aws:s3:::photos/products/*"
    assert images_url("photos", "us-east-1") == "https://photos.s3.us-east-1.amazonaws.com"


def test_integration_uri_to_function():
    assert lambda_name_from_uri(FN_ARN) == FN_ARN
    assert lambda_name_from_uri(f"arn:aws:apigateway:us-east-1:lambda:path/2015-03-31/functions/{FN_ARN}/invocations") == FN_ARN
    assert lambda_name_from_uri("https://example.com") is None


def test_env_line_is_replaced_or_appended():
    text = "API_URL=x\nPRODUCT_IMAGES_URL=\nSECRET_KEY=abc\n"
    out = set_env_line(text, "PRODUCT_IMAGES_URL", "https://p.s3.us-east-1.amazonaws.com")
    assert out.splitlines() == ["API_URL=x", "PRODUCT_IMAGES_URL=https://p.s3.us-east-1.amazonaws.com", "SECRET_KEY=abc"]
    assert set_env_line("A=1\n", "PRODUCT_IMAGES_URL", "u").splitlines()[-1] == "PRODUCT_IMAGES_URL=u"


# ---------------- Deployer with fake clients ----------------
class FakeGateway:
    def __init__(self, routes):
        self.routes = {k: {"RouteId": str(i), "RouteKey": k, "Target": "integrations/int1"}
                       for i, k in enumerate(routes)}
        self.created = []

    def get_api(self, ApiId):
        return {"Name": "marketplace", "ProtocolType": "HTTP"}

    def get_routes(self, ApiId):
        return {"Items": list(self.routes.values())}

    def get_integrations(self, ApiId):
        return {"Items": [{"IntegrationId": "int1", "IntegrationUri": FN_ARN}]}

    def create_route(self, ApiId, RouteKey, Target):
        self.created.append((RouteKey, Target))

    def get_stages(self, ApiId):
        return {"Items": [{"StageName": "$default", "AutoDeploy": True}]}


class FakeLambda:
    class exceptions:
        class ResourceNotFoundException(Exception):
            pass

    def __init__(self):
        self.conf = {"FunctionName": "marketplace", "Runtime": "python3.13", "Handler": "lambda_function.lambda_handler",
                     "Role": "arn:aws:iam::123456789012:role/LabRole", "MemorySize": 512, "Timeout": 30,
                     "CodeSize": 1, "CodeSha256": "old", "State": "Active", "LastUpdateStatus": "Successful",
                     "Environment": {"Variables": {"MARKETPLACE_API_KEY": "secret", "MARKETPLACE_GSI": "GSI1"}}}
        self.updates = []

    def get_function_configuration(self, FunctionName):
        return dict(self.conf)

    def update_function_code(self, FunctionName, ZipFile):
        self.updates.append("code")

    def update_function_configuration(self, FunctionName, **changes):
        self.updates.append(changes)


class FakeSession:
    def __init__(self, clients):
        self.clients = clients

    def client(self, name, region_name=None):
        return self.clients[name]


def deployer(apply, gw, lam):
    lines = []
    d = Deployer(FakeSession({"apigatewayv2": gw, "lambda": lam}), "us-east-1", apply, out=lines.append)
    return d, lines


def test_check_mode_changes_nothing():
    gw, lam = FakeGateway(ROUTES[:15]), FakeLambda()
    d, lines = deployer(False, gw, lam)
    gateway, routes, integration, function = d.api("abc")
    d.routes(gateway, "abc", routes, integration)
    d.lambda_function(function, "photos")
    assert gw.created == [] and lam.updates == []
    assert d.changes == 4 + 3                    # 4 missing routes + code, memory, environment
    assert not any("secret" in line for line in lines)     # values are never printed


def test_apply_creates_routes_on_the_same_integration_and_keeps_env():
    gw, lam = FakeGateway(ROUTES[:15]), FakeLambda()
    d, _ = deployer(True, gw, lam)
    gateway, routes, integration, function = d.api("abc")
    d.routes(gateway, "abc", routes, integration)
    d.lambda_function(function, "photos")
    assert sorted(k for k, _ in gw.created) == sorted(ROUTES[15:])
    assert {t for _, t in gw.created} == {"integrations/int1"}
    code, config = lam.updates
    assert code == "code" and config["MemorySize"] == LAMBDA_MEMORY
    assert config["Environment"]["Variables"] == {"MARKETPLACE_API_KEY": "secret", "MARKETPLACE_GSI": "GSI1",
                                                  "MARKETPLACE_BUCKET": "photos"}


def test_missing_api_key_in_the_lambda_is_reported():
    lam = FakeLambda()
    lam.conf["Environment"] = {"Variables": {}}
    d, lines = deployer(False, FakeGateway(ROUTES), lam)
    d.lambda_function(FN_ARN, "photos")
    assert d.problems == 1 and any("MARKETPLACE_API_KEY" in line for line in lines)
