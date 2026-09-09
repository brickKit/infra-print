"""真实的本地 JWKS/bundle 服务器——移植自 be-sdk-python 自己的
``tests/helpers.py``（两边不能共享 import，分属两个仓库，逻辑逐字对应）。
真 RSA 密钥对、真 JWKS 端点、真 RS256 签名与验签，身份是测试用的，
判定链路本身是真的（同 be-sdk-python 的既有判据）。
"""

from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa
from jwt.algorithms import RSAAlgorithm


class FakeJWKSServer:
    def __init__(self) -> None:
        self._priv = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        self.kid = "test-key-1"
        jwk = RSAAlgorithm.to_jwk(self._priv.public_key(), as_dict=True)
        jwk["kid"] = self.kid
        jwk["use"] = "sig"
        jwk["alg"] = "RS256"
        body = json.dumps({"keys": [jwk]}).encode()

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args: object) -> None:
                pass

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    @property
    def url(self) -> str:
        host, port = self._server.server_address[:2]
        return f"http://{host}:{port}/.well-known/jwks.json"

    def close(self) -> None:
        self._server.shutdown()
        self._server.server_close()

    def sign(self, sub: str, roles: list[str] | None = None, dept_path: str = "") -> str:
        now = time.time()
        payload: dict[str, Any] = {
            "sub": sub,
            "iat": int(now),
            "exp": int(now) + 600,
            "roles": roles or [],
            "dept_path": dept_path,
            "org_id": "",
        }
        return jwt.encode(payload, self._priv, algorithm="RS256", headers={"kid": self.kid})


class FakeBundleServer:
    def __init__(self, roles: dict[str, list[str]]) -> None:
        body = json.dumps({"roles": roles, "stale_since": {}}).encode()

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("ETag", '"v1"')
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args: object) -> None:
                pass

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    @property
    def url(self) -> str:
        host, port = self._server.server_address[:2]
        return f"http://{host}:{port}/authz/bundle"

    def close(self) -> None:
        self._server.shutdown()
        self._server.server_close()
