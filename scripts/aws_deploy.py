"""Deploy and check the AWS side of the marketplace from your computer, with the
TEMPORARY credentials of AWS Academy Learner Lab (no IAM changes needed).

    python scripts/aws_deploy.py                    only CHECKS and shows what would change
    python scripts/aws_deploy.py --apply            applies the changes
    python scripts/aws_deploy.py --apply --start-rds   also starts stopped RDS instances

What it does (all idempotent: running it again changes nothing that is already right):
  1. API Gateway (HTTP API of API_URL): finds the Lambda integration the routes use
     and creates the missing routes (19) on that same integration; deploys the stage
     if it has no auto-deploy.
  2. Lambda: uploads lambda_deploy/lambda_function.py, memory 256 MB (Learner Lab
     limit 128-256), timeout 30 s, adds MARKETPLACE_BUCKET to the environment and
     keeps every other variable (their values are never printed). Allows ONLY this
     API to invoke it.
  3. S3: creates the photo bucket, public READ of products/* only (bucket policy),
     ACLs blocked.
  4. .env: writes PRODUCT_IMAGES_URL (needed by the Content-Security-Policy).
  5. Reports DynamoDB capacity mode and the RDS class/storage/status (Learner Lab limits).

Credentials: boto3 reads them from ~/.aws/credentials ([default]) or from the variables
AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY / AWS_SESSION_TOKEN. In Learner Lab they change
every session: AWS Details -> AWS CLI -> Show, and paste them in that file again.
They are never written in the project.
"""
import argparse
import io
import json
import os
import re
import sys
import time
import zipfile
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from View.MainViews.Utils.env_file import ROOT as ENV_ROOT  # noqa: E402
from View.MainViews.Utils.env_file import _read_text, load_env_file, utf8_console  # noqa: E402

LAMBDA_FILE = ROOT / "lambda_deploy" / "lambda_function.py"
LAMBDA_MEMORY = 256          # Learner Lab rule: 128-256 MB
LAMBDA_TIMEOUT = 30
PERMISSION_ID = "marketplace-http-api"
ROUTES = (
    "GET /productos/consultar", "POST /productos/agregar", "PUT /productos/modificar",
    "DELETE /productos/eliminar", "POST /productos/stock", "POST /productos/cargar",
    "POST /productos/imagen", "DELETE /productos/imagen",
    "POST /productos/reservar", "POST /productos/liberar",
    "GET /usuarios/consultar", "POST /usuarios/agregar", "PUT /usuarios/modificar", "DELETE /usuarios/eliminar",
    "GET /carrito/consultar", "PUT /carrito/guardar", "DELETE /carrito/vaciar",
    "GET /favoritos/consultar", "PUT /favoritos/guardar",
)
API_HOST_RE = re.compile(r"([a-z0-9]{10})\.execute-api\.([a-z0-9-]+)\.amazonaws\.com")
BUCKET_RE = re.compile(r"[a-z0-9][a-z0-9-]{1,61}[a-z0-9]")
MICRO_CLASSES = ("db.t3.micro", "db.t4g.micro")


# ---------------- pure helpers (tested) ----------------
def api_from_url(url):
    """'https://abc123defg.execute-api.us-east-1.amazonaws.com' -> ('abc123defg', 'us-east-1')."""
    match = API_HOST_RE.fullmatch(urlsplit(url or "").netloc)
    if not match:
        raise SystemExit("✗ API_URL in .env must look like https://<id>.execute-api.<region>.amazonaws.com")
    return match.group(1), match.group(2)


def lambda_zip(path=LAMBDA_FILE):
    """The deployment package: one file, lambda_function.py (handler lambda_function.lambda_handler)."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        info = zipfile.ZipInfo("lambda_function.py", date_time=(2026, 1, 1, 0, 0, 0))
        info.external_attr = 0o644 << 16
        zf.writestr(info, Path(path).read_bytes())
    return buffer.getvalue()


def merged_environment(current, bucket):
    """Keep every variable (API key, table...) and add/replace MARKETPLACE_BUCKET."""
    env = dict(current or {})
    env["MARKETPLACE_BUCKET"] = bucket
    return env


def bucket_policy(bucket):
    return json.dumps({"Version": "2012-10-17", "Statement": [{
        "Sid": "PublicReadProductPhotos", "Effect": "Allow", "Principal": "*",
        "Action": "s3:GetObject", "Resource": f"arn:aws:s3:::{bucket}/products/*"}]})


def images_url(bucket, region):
    return f"https://{bucket}.s3.{region}.amazonaws.com"


def lambda_name_from_uri(uri):
    """Integration URI of an HTTP API -> Lambda function name (or ARN)."""
    match = re.search(r"(arn:aws:lambda:[a-z0-9-]+:\d{12}:function:[A-Za-z0-9_-]+)", uri or "")
    return match.group(1) if match else None


def set_env_line(text, key, value):
    """Replace `KEY=...` in the .env text (keeps everything else), or append it."""
    lines = text.splitlines()
    pattern = re.compile(rf"^\s*(export\s+)?{re.escape(key)}\s*=")
    for i, line in enumerate(lines):
        if pattern.match(line):
            lines[i] = f"{key}={value}"
            break
    else:
        lines += ["", "# Product photos (S3): written by scripts/aws_deploy.py", f"{key}={value}"]
    return "\n".join(lines) + "\n"


# ---------------- the deployer ----------------
class Deployer:
    def __init__(self, session, region, apply, out=print):
        self.session, self.region, self.apply, self.out = session, region, apply, out
        self.changes = 0
        self.problems = 0
        self._clients = {}

    def client(self, name):
        if name not in self._clients:
            self._clients[name] = self.session.client(name, region_name=self.region)
        return self._clients[name]

    def ok(self, text):
        self.out(f"  ✓ {text}")

    def todo(self, text):
        self.changes += 1
        self.out(f"  {'→' if self.apply else '•'} {text}{'' if self.apply else '  (will change with --apply)'}")

    def bad(self, text):
        self.problems += 1
        self.out(f"  ✗ {text}")

    # ---- account ----
    def account_id(self):
        return self.client("sts").get_caller_identity()["Account"]

    # ---- API Gateway ----
    def api(self, api_id):
        gw = self.client("apigatewayv2")
        api = gw.get_api(ApiId=api_id)
        self.out(f"API Gateway {api['Name']} ({api['ProtocolType']}, {api_id})")
        routes = {r["RouteKey"]: r for r in _all(gw.get_routes, ApiId=api_id)}
        integrations = {i["IntegrationId"]: i for i in _all(gw.get_integrations, ApiId=api_id)}
        votes = {}
        for route in routes.values():
            target = (route.get("Target") or "").split("/")[-1]
            if target in integrations and lambda_name_from_uri(integrations[target].get("IntegrationUri")):
                votes[target] = votes.get(target, 0) + 1
        if not votes:
            raise SystemExit("✗ No route of this API points to a Lambda integration: attach one in the console first.")
        integration_id = max(votes, key=votes.get)
        function = lambda_name_from_uri(integrations[integration_id]["IntegrationUri"])
        # Integrations that call the SAME function are equivalent (the console creates one per route).
        self._same_function = {f"integrations/{iid}" for iid, i in integrations.items()
                               if lambda_name_from_uri(i.get("IntegrationUri")) == function}
        self.ok(f"{len(routes)} routes; Lambda integration {integration_id} -> {function.split(':')[-1]}")
        return gw, routes, integration_id, function

    def routes(self, gw, api_id, routes, integration_id):
        target = f"integrations/{integration_id}"
        same = getattr(self, "_same_function", {target})
        pending = 0
        for key in ROUTES:
            route = routes.get(key) or routes.get(f"ANY {key.split(' ', 1)[1]}")   # ANY covers every method
            if route is None:
                pending += 1
                self.todo(f"create route {key}")
                if self.apply:
                    gw.create_route(ApiId=api_id, RouteKey=key, Target=target)
            elif route.get("Target") not in same:
                pending += 1
                self.todo(f"point {route['RouteKey']} to the marketplace Lambda (it calls another target)")
                if self.apply:
                    gw.update_route(ApiId=api_id, RouteId=route["RouteId"], Target=target)
        if not pending:
            self.ok(f"all {len(ROUTES)} routes call the marketplace Lambda")

    def stage(self, gw, api_id):
        stages = list(_all(gw.get_stages, ApiId=api_id))
        if any(s.get("AutoDeploy") for s in stages):
            self.ok("stage with auto-deploy: route changes are live")
            return
        if not stages:
            self.bad("the API has no stage: create one ($default with auto-deploy) in the console")
            return
        name = stages[0]["StageName"]
        self.todo(f"deploy stage {name}")
        if self.apply:
            gw.create_deployment(ApiId=api_id, StageName=name)

    # ---- Lambda ----
    def permission(self, function, api_id, account):
        lam = self.client("lambda")
        source = f"arn:aws:execute-api:{self.region}:{account}:{api_id}/*"
        try:
            policy = json.loads(lam.get_policy(FunctionName=function)["Policy"])
            statements = policy.get("Statement", [])
        except lam.exceptions.ResourceNotFoundException:
            statements = []
        covered = any(s.get("Sid") == PERMISSION_ID
                      or s.get("Condition", {}).get("ArnLike", {}).get("AWS:SourceArn") == source
                      for s in statements)
        if covered:
            self.ok("only this API can invoke the Lambda (resource policy)")
            return
        self.todo(f"allow API {api_id} (all its routes, nothing else) to invoke the Lambda")
        if self.apply:
            from botocore.exceptions import ClientError
            try:
                lam.add_permission(FunctionName=function, StatementId=PERMISSION_ID, Action="lambda:InvokeFunction",
                                   Principal="apigateway.amazonaws.com", SourceArn=source)
            except ClientError as e:
                self.bad(f"could not add the invoke permission ({e.response.get('Error', {}).get('Code')}): "
                         "in API Gateway open each new route -> Attach integration, the console adds it")

    def lambda_function(self, function, bucket):
        lam = self.client("lambda")
        conf = lam.get_function_configuration(FunctionName=function)
        name = conf["FunctionName"]
        env = conf.get("Environment", {}).get("Variables", {})
        self.out(f"Lambda {name} ({conf.get('Runtime')}, handler {conf.get('Handler')})")
        if conf.get("Handler") != "lambda_function.lambda_handler":
            self.bad("Handler must be lambda_function.lambda_handler (Runtime settings -> Edit)")
        if not conf.get("Role", "").endswith("/LabRole"):
            self.out("  ! the role is not LabRole: Learner Lab expects LabRole")
        for required in ("MARKETPLACE_API_KEY",):
            if not env.get(required):
                self.bad(f"{required} is not set in the Lambda: set it in Configuration -> Environment variables")
        package = lambda_zip()
        if conf.get("CodeSize") == len(package) and self._same_code(lam, name, package):
            self.ok("code is up to date")
        else:
            self.todo(f"upload lambda_function.py ({len(package) // 1024} KB zipped)")
            if self.apply:
                lam.update_function_code(FunctionName=name, ZipFile=package)
                _wait_updated(lam, name)
        wanted_env = merged_environment(env, bucket) if bucket else env
        changes = {}
        if conf.get("MemorySize") != LAMBDA_MEMORY:
            changes["MemorySize"] = LAMBDA_MEMORY
            self.todo(f"memory {conf.get('MemorySize')} MB -> {LAMBDA_MEMORY} MB (Learner Lab limit 128-256)")
        else:
            self.ok(f"memory {LAMBDA_MEMORY} MB")
        if conf.get("Timeout") != LAMBDA_TIMEOUT:
            changes["Timeout"] = LAMBDA_TIMEOUT
            self.todo(f"timeout {conf.get('Timeout')} s -> {LAMBDA_TIMEOUT} s")
        if wanted_env != env:
            changes["Environment"] = {"Variables": wanted_env}
            self.todo(f"environment: MARKETPLACE_BUCKET = {bucket} (other {len(env)} variables kept as they are)")
        else:
            self.ok(f"environment has {len(env)} variables (values not shown)")
        if changes and self.apply:
            lam.update_function_configuration(FunctionName=name, **changes)
            _wait_updated(lam, name)
        return name

    @staticmethod
    def _same_code(lam, name, package):
        import base64
        import hashlib
        sha = base64.b64encode(hashlib.sha256(package).digest()).decode()
        return lam.get_function_configuration(FunctionName=name).get("CodeSha256") == sha

    # ---- S3 ----
    def bucket(self, bucket):
        s3 = self.client("s3")
        self.out(f"S3 bucket {bucket}")
        from botocore.exceptions import ClientError
        try:
            s3.head_bucket(Bucket=bucket)
            self.ok("exists")
        except ClientError as e:
            code = e.response.get("Error", {}).get("Code")
            if code in ("403", "AccessDenied"):
                raise SystemExit(f"✗ The name {bucket} belongs to another AWS account: choose another with --bucket")
            self.todo("create the bucket (private, ACLs disabled)")
            if self.apply:
                kwargs = {} if self.region == "us-east-1" else {
                    "CreateBucketConfiguration": {"LocationConstraint": self.region}}
                s3.create_bucket(Bucket=bucket, ObjectOwnership="BucketOwnerEnforced", **kwargs)
        block = {"BlockPublicAcls": True, "IgnorePublicAcls": True,
                 "BlockPublicPolicy": False, "RestrictPublicBuckets": False}
        try:
            current = s3.get_public_access_block(Bucket=bucket)["PublicAccessBlockConfiguration"]
        except ClientError:
            current = None
        if current == block:
            self.ok("public access: ACLs blocked, bucket policy allowed")
        else:
            self.todo("public access block: keep ACLs blocked, allow the read-only bucket policy")
            if self.apply:
                s3.put_public_access_block(Bucket=bucket, PublicAccessBlockConfiguration=block)
        try:
            policy = json.loads(s3.get_bucket_policy(Bucket=bucket)["Policy"])
        except ClientError:
            policy = None
        if policy == json.loads(bucket_policy(bucket)):
            self.ok("bucket policy: public READ of products/* only")
        else:
            self.todo("bucket policy: public READ of products/* only (no list, no write)")
            if self.apply:
                s3.put_bucket_policy(Bucket=bucket, Policy=bucket_policy(bucket))

    # ---- .env ----
    def env_file(self, bucket):
        path = ENV_ROOT / ".env"
        url = images_url(bucket, self.region)
        if os.environ.get("PRODUCT_IMAGES_URL", "").rstrip("/") == url:
            self.ok(f".env PRODUCT_IMAGES_URL={url}")
            return
        self.todo(f".env PRODUCT_IMAGES_URL={url}")
        if self.apply and path.is_file():
            path.write_text(set_env_line(_read_text(path), "PRODUCT_IMAGES_URL", url), encoding="utf-8")

    # ---- reports ----
    def dynamodb(self, table):
        ddb = self.client("dynamodb")
        self.out(f"DynamoDB {table}")
        try:
            t = ddb.describe_table(TableName=table)["Table"]
        except ddb.exceptions.ResourceNotFoundException:
            self.bad("table not found (MARKETPLACE_TABLE)")
            return
        mode = t.get("BillingModeSummary", {}).get("BillingMode", "PROVISIONED")
        indexes = [i["IndexName"] for i in t.get("GlobalSecondaryIndexes", [])]
        count = t.get("ItemCount", 0)
        if mode == "PAY_PER_REQUEST":
            self.ok(f"On-Demand capacity; indexes {', '.join(indexes) or '-'}; ~{count:,} items")
        else:
            th = t.get("ProvisionedThroughput", {})
            self.out(f"  ! provisioned capacity (RCU {th.get('ReadCapacityUnits')}, WCU {th.get('WriteCapacityUnits')}):"
                     " Learner Lab asks for On-Demand or the minimum units")

    def rds(self, hosts, start):
        rds = self.client("rds")
        wanted = {h for h in hosts if h and h.endswith(".rds.amazonaws.com")}
        if not wanted:
            return
        for db in _all(rds.describe_db_instances, key="DBInstances"):
            host = db.get("Endpoint", {}).get("Address")
            if host not in wanted:
                continue
            ident, status = db["DBInstanceIdentifier"], db["DBInstanceStatus"]
            self.out(f"RDS {ident} ({db['Engine']} {db.get('EngineVersion')})")
            klass, storage = db["DBInstanceClass"], db["AllocatedStorage"]
            if klass in MICRO_CLASSES and storage <= 20:
                self.ok(f"{klass}, {storage} GB (Learner Lab limits)")
            else:
                self.bad(f"{klass}, {storage} GB: Learner Lab allows db.t3/t4g.micro with at most 20 GB")
            if status == "available":
                self.ok("running (stop it when you finish working to save credit)")
            elif status == "stopped" and start:
                self.todo("start the instance (takes about 5 minutes)")
                if self.apply:
                    rds.start_db_instance(DBInstanceIdentifier=ident)
            else:
                self.out(f"  ! status: {status}" + ("  (use --start-rds)" if status == "stopped" else ""))


def _all(method, key=None, **kwargs):
    """Every item of a paginated AWS list call (NextToken / Marker)."""
    token_names = ("NextToken", "Marker")
    while True:
        resp = method(**kwargs)
        items = resp[key] if key else resp.get("Items", [])
        yield from items
        next_token = next(((n, resp[n]) for n in token_names if resp.get(n)), None)
        if not next_token:
            return
        kwargs[next_token[0]] = next_token[1]


def _wait_updated(lam, name, seconds=120):
    deadline = time.time() + seconds
    while time.time() < deadline:
        conf = lam.get_function_configuration(FunctionName=name)
        if conf.get("LastUpdateStatus") in ("Successful", None) and conf.get("State") in ("Active", None):
            return
        if conf.get("LastUpdateStatus") == "Failed":
            raise SystemExit(f"✗ Lambda update failed: {conf.get('LastUpdateStatusReason')}")
        time.sleep(2)
    raise SystemExit("✗ The Lambda is still updating; run the script again in a minute.")


def main(argv=None):
    utf8_console()
    load_env_file()
    parser = argparse.ArgumentParser(description="Deploy/check the AWS side of the marketplace (Learner Lab).")
    parser.add_argument("--apply", action="store_true", help="apply the changes (default: only check)")
    parser.add_argument("--bucket", help="S3 bucket for photos (default: from PRODUCT_IMAGES_URL or "
                                         "marketplace-fotos-<account id>)")
    parser.add_argument("--start-rds", action="store_true", help="start stopped RDS instances")
    args = parser.parse_args(argv)
    try:
        import boto3
        from botocore.exceptions import ClientError, NoCredentialsError
    except ImportError:
        raise SystemExit("✗ boto3 is not installed. Run: pip install -r requirements.txt") from None

    api_id, region = api_from_url(os.environ.get("API_URL", ""))
    session = boto3.Session()
    d = Deployer(session, region, args.apply)
    try:
        account = d.account_id()
    except NoCredentialsError:
        raise SystemExit("✗ No AWS credentials. Learner Lab -> AWS Details -> AWS CLI -> Show, and paste them "
                         "in %USERPROFILE%\\.aws\\credentials (see docs/DESPLIEGUE_AWS.md).") from None
    except ClientError as e:
        if "ExpiredToken" in str(e) or "InvalidClientTokenId" in str(e):
            raise SystemExit("✗ The Learner Lab credentials expired: start the lab and copy them again.") from None
        raise
    print(f"{'APPLYING changes' if args.apply else 'CHECK only (nothing changes)'} · account {account[:4]}…, {region}")

    bucket = args.bucket
    if not bucket:
        match = re.match(r"https://([a-z0-9-]+)\.s3\.", os.environ.get("PRODUCT_IMAGES_URL", ""))
        bucket = match.group(1) if match else f"marketplace-fotos-{account}"
    if not BUCKET_RE.fullmatch(bucket):
        raise SystemExit("✗ Bucket names: lowercase letters, numbers and dashes, 3-63 characters, no dots.")

    try:
        gw, routes, integration_id, function = d.api(api_id)
        d.routes(gw, api_id, routes, integration_id)
        d.stage(gw, api_id)
        d.bucket(bucket)
        name = d.lambda_function(function, bucket)
        d.permission(name, api_id, account)
        d.env_file(bucket)
        d.dynamodb(os.environ.get("MARKETPLACE_TABLE", "MarketplaceDB"))
        d.rds([os.environ.get("MYSQL_HOST"), os.environ.get("PG_HOST")], args.start_rds)
    except ClientError as e:
        error = e.response.get("Error", {})
        raise SystemExit(f"✗ AWS refused {e.operation_name}: {error.get('Code')} - {error.get('Message')}") from None

    if d.problems:
        print(f"\n{d.problems} problem(s) need attention (see ✗ above).")
    if args.apply:
        print(f"\nDone: {d.changes} change(s) applied.")
    else:
        print(f"\n{d.changes} change(s) pending. Run again with --apply to make them.")
    sys.exit(1 if d.problems else 0)


if __name__ == "__main__":
    main()
