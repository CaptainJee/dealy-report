from __future__ import annotations

import json
import tempfile
import threading
import unittest
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from dealy_report.delivery import FeishuCredentials, deliver_manifest


PNG = b"\x89PNG\r\n\x1a\n" + b"integration-image"


class FeishuHandler(BaseHTTPRequestHandler):
    requests: list[tuple[str, bytes, dict[str, str]]] = []

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length)
        self.__class__.requests.append((self.path, body, dict(self.headers)))
        if self.path == "/auth":
            response = {"code": 0, "tenant_access_token": "tenant-token"}
        elif self.path == "/images":
            if self.headers.get("Authorization") != "Bearer tenant-token" or PNG not in body:
                response = {"code": 40001, "msg": "invalid upload"}
            else:
                response = {"code": 0, "data": {"image_key": "img_integration"}}
        elif self.path == "/hook":
            response = {"code": 0}
        else:
            self.send_error(404)
            return
        payload = json.dumps(response).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, format: str, *args: object) -> None:
        return


class FeishuHttpIntegrationTests(unittest.TestCase):
    def test_auth_upload_placeholder_replacement_and_card_send(self) -> None:
        FeishuHandler.requests = []
        server = ThreadingHTTPServer(("127.0.0.1", 0), FeishuHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = f"http://127.0.0.1:{server.server_port}"
        try:
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                image = root / "connectivity.png"
                image.write_bytes(PNG)
                manifest = {
                    "images": {"connectivity": str(image)},
                    "cards": [{"elements": [{"tag": "img", "img_key": "{{image:connectivity}}"}]}],
                }
                direct_open = urllib.request.build_opener(urllib.request.ProxyHandler({})).open
                with patch("scripts.feishu_card_sender.FEISHU_TENANT_TOKEN_URL", f"{base}/auth"), patch(
                    "scripts.feishu_card_sender.FEISHU_IMAGE_UPLOAD_URL", f"{base}/images"
                ), patch(
                    "scripts.feishu_card_sender.urllib.request.urlopen", direct_open
                ):
                    result = deliver_manifest(
                        manifest,
                        FeishuCredentials(f"{base}/hook", "app-id", "app-secret"),
                        allowed_local_roots=[root],
                    )
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)

        self.assertEqual(result.delivered_cards, 1)
        self.assertEqual(result.uploaded_images, 1)
        self.assertEqual([request[0] for request in FeishuHandler.requests], ["/auth", "/images", "/hook"])
        hook_payload = json.loads(FeishuHandler.requests[-1][1])
        self.assertEqual(hook_payload["card"]["elements"][0]["img_key"], "img_integration")
        self.assertNotIn("app-secret", FeishuHandler.requests[-1][1].decode("utf-8"))


if __name__ == "__main__":
    unittest.main()
