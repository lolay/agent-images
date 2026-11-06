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
