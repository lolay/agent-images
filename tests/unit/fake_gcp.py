"""A fake of the GCP endpoints the hook and the supervisor call, for the tests.

One HTTP server stands in for the metadata server (instance attributes and guest
attributes included), Cloud Storage's JSON API, the Cloud Run Admin API, Compute
Engine and Secret Manager. It records every request and answers
from simple in-memory state; a test can make any route fail with `fail`.
"""

import base64
import json
import threading
import time
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlsplit

TOKEN = "fake-token-1234"


@dataclass
class Request:
    method: str
    path: str
    query: dict
    headers: dict
    body: bytes

    def json(self):
        return json.loads(self.body)


@dataclass
class State:
    objects: dict = field(default_factory=dict)  # (bucket, name) -> bytes
    secrets: dict = field(default_factory=dict)  # name -> value
    jobs: dict = field(default_factory=dict)  # full job name -> Job body
    attributes: dict = field(default_factory=dict)  # instance metadata key -> value
    guest_attributes: dict = field(default_factory=dict)  # "namespace/key" -> value
    instances: dict = field(default_factory=dict)  # Compute Engine instance name -> body
    instance_guest_attributes: dict = field(default_factory=dict)  # name -> {"ns/key": value}
    images: dict = field(default_factory=dict)  # Compute Engine image name -> body
    # Compute Engine operations: name -> body. An operation listed in
    # operation_errors finishes with that error code; pending ones stay RUNNING.
    compute_operations: dict = field(default_factory=dict)
    operation_errors: dict = field(default_factory=dict)  # resource name -> error code
    pending_operations: bool = False
    operations: int = 0
    requests: list = field(default_factory=list)
    failures: dict = field(default_factory=dict)  # (method, path suffix) -> (status, body)
    delays: dict = field(default_factory=dict)  # path suffix -> seconds before answering
    lock: threading.Lock = field(default_factory=threading.Lock)


class FakeGcp:
    def __init__(self):
        self.state = State()
        state = self.state

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def handle_any(self):
                length = int(self.headers.get("Content-Length") or 0)
                body = self.rfile.read(length) if length else b""
                parts = urlsplit(self.path)
                request = Request(
                    self.command,
                    unquote(parts.path),
                    {key: values[0] for key, values in parse_qs(parts.query).items()},
                    {key.lower(): value for key, value in self.headers.items()},
                    body,
                )
                for suffix, seconds in list(state.delays.items()):
                    if request.path.endswith(suffix):
                        time.sleep(seconds)
                with state.lock:
                    state.requests.append(request)
                    status, payload = route(state, request)
                if isinstance(payload, RawText):
                    data = payload.encode()
                else:
                    data = json.dumps(payload).encode() if payload is not None else b""
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            do_GET = do_POST = do_DELETE = do_PATCH = do_PUT = handle_any

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self.server.shutdown()
        self.server.server_close()

    def env(self) -> dict:
        """The lib.sh overrides that point every call at this server."""
        return {
            "GCP_METADATA_URL": f"{self.url}/computeMetadata/v1",
            "GCP_STORAGE_URL": self.url,
            "GCP_RUN_URL": self.url,
            "GCP_SECRETS_URL": self.url,
            "GCP_COMPUTE_URL": self.url,
            "GCP_CURL_MAX_TIME": "5",
        }

    def fail(self, method: str, path_suffix: str, status: int, message: str = "fake failure"):
        self.state.failures[(method, path_suffix)] = (status, {"error": {"message": message}})

    def requests(self, method=None, contains=None):
        return [
            request
            for request in self.state.requests
            if (method is None or request.method == method)
            and (contains is None or contains in request.path)
        ]


def route(state: State, request: Request):
    for (method, suffix), response in state.failures.items():
        if request.method == method and request.path.endswith(suffix):
            return response
    path = request.path
    authorized = request.headers.get("authorization") == f"Bearer {TOKEN}"

    if path == "/computeMetadata/v1/instance/service-accounts/default/token":
        if request.headers.get("metadata-flavor") != "Google":
            return 403, {"error": "missing Metadata-Flavor"}
        return 200, {"access_token": TOKEN, "expires_in": 3599, "token_type": "Bearer"}

    if path.startswith("/computeMetadata/v1/instance/"):
        return route_metadata(state, request)

    if not authorized:
        return 401, {"error": {"message": "bad token"}}

    if request.method == "POST" and path.startswith("/upload/storage/v1/b/"):
        bucket = path.split("/")[5]
        name = request.query["name"]
        if request.query.get("ifGenerationMatch") == "0" and (bucket, name) in state.objects:
            return 412, {"error": {"message": "precondition failed"}}
        state.objects[(bucket, name)] = request.body
        return 200, {"bucket": bucket, "name": name, "generation": "1"}

    if request.method == "DELETE" and path.startswith("/storage/v1/b/"):
        segments = path.split("/", 6)
        bucket, name = segments[4], segments[6]
        if state.objects.pop((bucket, name), None) is None:
            return 404, {"error": {"message": "no such object"}}
        return 204, None

    if path.startswith("/compute/v1/"):
        return route_compute(state, request)

    if "/jobs" in path and path.startswith("/v2/") and not path.endswith(":run"):
        return route_jobs(state, request)

    if request.method == "GET" and "/operations/" in path and path.startswith("/v2/"):
        return 200, {"name": path.removeprefix("/v2/"), "done": True, "response": {}}

    if request.method == "POST" and path.endswith(":run") and "/jobs/" in path:
        job = path.rsplit("/", 1)[1].removesuffix(":run")
        parent = path.split("/jobs/")[0].removeprefix("/v2/")
        return 200, {
            "name": f"{parent}/operations/op-1",
            "metadata": {"name": f"{parent}/executions/{job}-abc12"},
        }

    if request.method == "GET" and path.endswith("/versions/latest:access"):
        name = path.split("/secrets/")[1].split("/")[0]
        if name not in state.secrets:
            return 404, {"error": {"message": f"secret {name} not found"}}
        data = base64.b64encode(state.secrets[name].encode()).decode()
        return 200, {"name": path, "payload": {"data": data}}

    return 404, {"error": {"message": f"no fake for {request.method} {path}"}}


def route_metadata(state: State, request: Request):
    """The metadata server's instance attributes (read) and guest attributes (write)."""
    if request.headers.get("metadata-flavor") != "Google":
        return 403, {"error": "missing Metadata-Flavor"}
    rest = request.path.removeprefix("/computeMetadata/v1/instance/")
    if request.method == "GET" and rest.startswith("attributes/"):
        key = rest.removeprefix("attributes/")
        if key not in state.attributes:
            return 404, None
        return 200, RawText(state.attributes[key])
    if request.method == "PUT" and rest.startswith("guest-attributes/"):
        state.guest_attributes[rest.removeprefix("guest-attributes/")] = request.body.decode()
        return 200, None
    return 404, None


def route_compute(state: State, request: Request):
    """Compute Engine v1: instances, their metadata and guest attributes, images, operations."""
    path = request.path.removeprefix("/compute/v1/")
    segments = path.split("/")
    # projects/<p>/zones/<z>/... or projects/<p>/global/...
    scope = "/".join(segments[:4]) if segments[2] == "zones" else "/".join(segments[:3])
    rest = segments[4:] if segments[2] == "zones" else segments[3:]

    def operation(target: str, kind: str):
        state.operations += 1
        name = f"operation-{state.operations}"
        body = {"name": name, "operationType": kind, "targetLink": target, "status": "DONE"}
        if state.pending_operations:
            body["status"] = "RUNNING"
        elif target in state.operation_errors:
            code = state.operation_errors[target]
            body["error"] = {"errors": [{"code": code, "message": f"fake {code}"}]}
        state.compute_operations[name] = body
        return 200, body

    if rest[:1] == ["operations"] and request.method == "GET":
        body = state.compute_operations.get(rest[1])
        return (200, body) if body else (404, {"error": {"message": "no such operation"}})

    if rest[:1] == ["instances"]:
        if len(rest) == 1 and request.method == "POST":
            body = request.json()
            name = body["name"]
            if name in state.instances:
                return 409, {"error": {"code": 409, "message": f"{name} already exists",
                                       "errors": [{"reason": "alreadyExists"}]}}
            if name not in state.operation_errors:
                state.instances[name] = {
                    **body,
                    "status": "RUNNING",
                    "creationTimestamp": "2026-11-06T10:00:00.000-08:00",
                    "metadata": {**body.get("metadata", {}), "fingerprint": "fp-1"},
                }
            return operation(name, "insert")
        if len(rest) == 1 and request.method == "GET":
            wanted = request.query.get("filter", "")
            items = [i for i in state.instances.values() if all(
                term.strip("()") in _filter_terms(i) for term in wanted.split(" AND ") if term)]
            return 200, {"items": items} if items else {}
        name = rest[1]
        if name not in state.instances:
            return 404, {"error": {"message": f"instance {name} not found"}}
        instance = state.instances[name]
        if len(rest) == 2 and request.method == "GET":
            return 200, instance
        if len(rest) == 2 and request.method == "DELETE":
            del state.instances[name]
            return operation(name, "delete")
        if rest[2:] == ["setMetadata"] and request.method == "POST":
            body = request.json()
            if body.get("fingerprint") != instance["metadata"].get("fingerprint"):
                return 412, {"error": {"message": "fingerprint mismatch"}}
            instance["metadata"] = {"items": body.get("items", []), "fingerprint": "fp-2"}
            return operation(name, "setMetadata")
        if rest[2:] == ["getGuestAttributes"] and request.method == "GET":
            prefix = request.query.get("queryPath", "")
            values = state.instance_guest_attributes.get(name, {})
            items = [{"namespace": k.split("/")[0], "key": k.split("/", 1)[1], "value": v}
                     for k, v in values.items() if k.startswith(prefix)]
            if not items:
                return 404, {"error": {"message": "no guest attributes"}}
            return 200, {"queryValue": {"items": items}}

    if rest[:1] == ["images"]:
        if len(rest) == 1 and request.method == "GET":
            wanted = request.query.get("filter", "")
            items = [i for i in state.images.values() if all(
                term in _filter_terms(i) for term in wanted.split(" AND ") if term)]
            return 200, {"items": items} if items else {}
        if rest[1] == "family" and request.method == "GET":
            family = [i for i in state.images.values() if i.get("family") == rest[2]]
            if not family:
                return 404, {"error": {"message": f"no image in family {rest[2]}"}}
            return 200, sorted(family, key=lambda i: i.get("creationTimestamp", ""))[-1]
        name = rest[1]
        if name not in state.images:
            return 404, {"error": {"message": f"image {name} not found"}}
        if request.method == "PATCH":
            state.images[name] = {**state.images[name], **request.json()}
            return operation(name, "patch")
        if request.method == "DELETE":
            del state.images[name]
            return operation(name, "delete")
        if request.method == "GET":
            return 200, state.images[name]

    return 404, {"error": {"message": f"no fake for {request.method} {scope} {rest}"}}


def _filter_terms(instance: dict) -> set:
    """The filter terms an instance matches, for the fake list call: labels.k=v and status=S."""
    terms = {f"labels.{key}={value}" for key, value in instance.get("labels", {}).items()}
    terms.add(f"status={instance.get('status')}")
    return terms


class RawText(str):
    """A body sent as is, not as JSON (the metadata server's attribute values)."""


def route_jobs(state: State, request: Request):
    """Cloud Run v2 jobs: PATCH (allowMissing), GET one, list, DELETE."""
    path = request.path.removeprefix("/v2/")
    parent = path.split("/jobs")[0]

    def operation():
        state.operations += 1
        # Not done yet, so the caller has to poll it.
        return {"name": f"{parent}/operations/op-{state.operations}", "done": False}

    if path.endswith("/jobs"):
        return 200, {"jobs": list(state.jobs.values())}
    if request.method == "PATCH":
        if request.query.get("allowMissing") != "true" and path not in state.jobs:
            return 404, {"error": {"message": f"{path} not found"}}
        job = request.json()
        if job.get("name") not in (None, path):
            return 400, {"error": {"message": "name in the body doesn't match the URL"}}
        # A GPU task over 1 hour is Preview: the real API refuses it at GA.
        task = job.get("template", {}).get("template", {})
        if (
            task.get("nodeSelector", {}).get("accelerator")
            and int(task.get("timeout", "0s").removesuffix("s")) > 3600
            and job.get("launchStage") not in ("ALPHA", "BETA")
        ):
            return 400, {"error": {"message": (
                "The feature 'Long running GPU jobs' is not supported in the declared launch stage "
                f"on resource {path.rsplit('/', 1)[1]}. The launch stage annotation should be "
                "specified at least as BETA."
            )}}
        state.jobs[path] = {**job, "name": path}
        return 200, operation()
    if request.method == "GET":
        if path not in state.jobs:
            return 404, {"error": {"message": f"{path} not found"}}
        return 200, state.jobs[path]
    if request.method == "DELETE":
        if state.jobs.pop(path, None) is None:
            return 404, {"error": {"message": f"{path} not found"}}
        return 200, operation()
    return 404, {"error": {"message": f"no fake for {request.method} {path}"}}
