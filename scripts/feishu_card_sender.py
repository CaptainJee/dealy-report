#!/usr/bin/env python3
"""Upload card images to Feishu and send interactive cards via a group webhook."""

from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import http.client
import ipaddress
import json
import os
import socket
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path
from typing import Any

try:
    import winreg
except ImportError:  # pragma: no cover - Windows is the production environment.
    winreg = None


MAX_IMAGE_BYTES = 10 * 1024 * 1024
ALLOWED_IMAGE_TYPES = {
    "image/gif",
    "image/jpeg",
    "image/png",
    "image/webp",
}


class FeishuError(RuntimeError):
    pass


class FeishuDeliveryUncertain(FeishuError):
    """The webhook request may have reached Feishu but no response was received."""


def get_setting(name: str) -> str | None:
    # User-level settings are authoritative on Windows. Long-running parent
    # processes can otherwise pass a stale webhook value to scheduled jobs.
    if winreg is not None:
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as key:
                value, _ = winreg.QueryValueEx(key, name)
                if value:
                    return str(value)
        except FileNotFoundError:
            pass
    return os.environ.get(name) or None


def decode_json_response(response: Any) -> dict[str, Any]:
    raw = response.read().decode("utf-8", errors="replace")
    try:
        result = json.loads(raw)
    except json.JSONDecodeError as error:
        raise FeishuError(f"Feishu returned non-JSON data (HTTP {response.status})") from error
    if result.get("code") not in (None, 0):
        raise FeishuError(f"Feishu error {result.get('code')}: {result.get('msg', 'unknown error')}")
    return result


def open_request(request: urllib.request.Request, timeout: int = 45, delivery: bool = False) -> Any:
    try:
        return urllib.request.urlopen(request, timeout=timeout)
    except urllib.error.HTTPError as error:
        details = error.read().decode("utf-8", errors="replace")[:1000]
        raise FeishuError(f"HTTP {error.code}: {details}") from error
    except urllib.error.URLError as error:
        if delivery:
            raise FeishuDeliveryUncertain("Feishu delivery result is uncertain after a network failure") from error
        raise FeishuError(f"Network request failed: {error.reason}") from error


def get_tenant_access_token(app_id: str, app_secret: str) -> str:
    payload = json.dumps({"app_id": app_id, "app_secret": app_secret}).encode("utf-8")
    request = urllib.request.Request(
        "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
        data=payload,
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    result = decode_json_response(open_request(request))
    token = result.get("tenant_access_token")
    if not token:
        raise FeishuError("Feishu did not return tenant_access_token")
    return str(token)


def _resolve_remote_url(source: str, resolver: Any) -> tuple[urllib.parse.SplitResult, list[str]]:
    parsed = urllib.parse.urlsplit(source)
    if parsed.scheme.lower() != "https":
        raise FeishuError("Remote images must use HTTPS")
    if not parsed.hostname or parsed.username or parsed.password:
        raise FeishuError("Remote image URL is invalid")
    try:
        addresses = resolver(parsed.hostname, parsed.port or 443, type=socket.SOCK_STREAM)
    except socket.gaierror as error:
        raise FeishuError(f"Image host could not be resolved: {parsed.hostname}") from error
    if not addresses:
        raise FeishuError(f"Image host could not be resolved: {parsed.hostname}")
    public_addresses: list[str] = []
    for address in addresses:
        raw_address = address[4][0].split("%", 1)[0]
        try:
            ip = ipaddress.ip_address(raw_address)
        except ValueError as error:
            raise FeishuError(f"Image host returned an invalid address: {parsed.hostname}") from error
        if not ip.is_global:
            raise FeishuError(f"Image host must resolve to a public address: {parsed.hostname}")
        public_addresses.append(raw_address)
    return parsed, public_addresses


def validate_remote_url(source: str, resolver: Any = socket.getaddrinfo) -> str:
    _resolve_remote_url(source, resolver)
    return source


class PinnedHTTPSConnection(http.client.HTTPSConnection):
    def __init__(self, host: str, port: int, address: str, timeout: int) -> None:
        super().__init__(host, port=port, timeout=timeout, context=ssl.create_default_context())
        self._pinned_address = address

    def connect(self) -> None:
        self.sock = socket.create_connection(
            (self._pinned_address, self.port),
            self.timeout,
            self.source_address,
        )
        if self._tunnel_host:
            self._tunnel()
        self.sock = self._context.wrap_socket(self.sock, server_hostname=self.host)


def _default_connection_factory(host: str, port: int, address: str, timeout: int) -> PinnedHTTPSConnection:
    return PinnedHTTPSConnection(host, port, address, timeout)


def fetch_remote_image(
    source: str,
    resolver: Any = socket.getaddrinfo,
    connection_factory: Any = _default_connection_factory,
    max_redirects: int = 5,
) -> tuple[bytes, str]:
    current = source
    for _ in range(max_redirects + 1):
        parsed, addresses = _resolve_remote_url(current, resolver)
        host = parsed.hostname or "unknown"
        port = parsed.port or 443
        path = parsed.path or "/"
        if parsed.query:
            path += f"?{parsed.query}"
        connection = connection_factory(host, port, addresses[0], 45)
        try:
            connection.request("GET", path, headers={"User-Agent": "Codex-AI-Daily/1.0", "Accept": "image/*"})
            response = connection.getresponse()
            if response.status in {301, 302, 303, 307, 308}:
                location = response.getheader("Location")
                if not location:
                    raise FeishuError(f"Image redirect from {host} has no destination")
                current = urllib.parse.urljoin(current, location)
                continue
            if not 200 <= response.status < 300:
                raise FeishuError(f"Image download failed with HTTP {response.status} from {host}")
            data = response.read(MAX_IMAGE_BYTES + 1)
            filename = Path(parsed.path).name or "image"
            return data, filename
        except FeishuError:
            raise
        except (OSError, http.client.HTTPException) as error:
            raise FeishuError(f"Image download failed for host {host}") from error
        finally:
            connection.close()
    raise FeishuError("Image download exceeded the redirect limit")


def detect_image_content_type(data: bytes) -> str:
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if len(data) >= 12 and data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return "image/webp"
    raise FeishuError("Image signature is not a supported PNG, JPEG, GIF, or WebP")


def _is_inside(path: Path, roots: list[Path]) -> bool:
    return any(path == root or path.is_relative_to(root) for root in roots)


def load_image(source: str, allowed_local_roots: list[Path] | None = None) -> tuple[bytes, str, str]:
    if source.startswith(("https://", "http://")):
        data, filename = fetch_remote_image(source)
    else:
        path = Path(source).expanduser().resolve()
        if not path.is_file():
            raise FeishuError(f"Image file does not exist: {path}")
        roots = [root.expanduser().resolve() for root in (allowed_local_roots or [])]
        if not _is_inside(path, roots):
            raise FeishuError(f"Local image is outside an allowed root: {path}")
        data = path.read_bytes()
        filename = path.name

    if len(data) == 0:
        raise FeishuError(f"Image is empty: {source}")
    if len(data) > MAX_IMAGE_BYTES:
        raise FeishuError(f"Image exceeds 10 MB: {source}")
    content_type = detect_image_content_type(data)
    return data, content_type, filename


def upload_image(token: str, source: str, allowed_local_roots: list[Path] | None = None) -> str:
    image, content_type, filename = load_image(source, allowed_local_roots=allowed_local_roots)
    boundary = f"----CodexFeishu{uuid.uuid4().hex}"
    delimiter = f"--{boundary}\r\n".encode("ascii")
    body = b"".join(
        [
            delimiter,
            b'Content-Disposition: form-data; name="image_type"\r\n\r\n',
            b"message\r\n",
            delimiter,
            (
                f'Content-Disposition: form-data; name="image"; filename="{filename}"\r\n'
                f"Content-Type: {content_type}\r\n\r\n"
            ).encode("utf-8"),
            image,
            b"\r\n",
            f"--{boundary}--\r\n".encode("ascii"),
        ]
    )
    request = urllib.request.Request(
        "https://open.feishu.cn/open-apis/im/v1/images",
        data=body,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": f"multipart/form-data; boundary={boundary}",
        },
        method="POST",
    )
    result = decode_json_response(open_request(request))
    image_key = result.get("data", {}).get("image_key")
    if not image_key:
        raise FeishuError("Feishu did not return image_key")
    return str(image_key)


def replace_image_placeholders(value: Any, image_keys: dict[str, str]) -> Any:
    if isinstance(value, dict):
        return {key: replace_image_placeholders(child, image_keys) for key, child in value.items()}
    if isinstance(value, list):
        return [replace_image_placeholders(child, image_keys) for child in value]
    if isinstance(value, str) and value.startswith("{{image:") and value.endswith("}}"):
        name = value[8:-2]
        if name not in image_keys:
            raise FeishuError(f"Unknown image placeholder: {name}")
        return image_keys[name]
    return value


def add_signature(payload: dict[str, Any], secret: str | None) -> None:
    if not secret:
        return
    timestamp = str(int(time.time()))
    string_to_sign = f"{timestamp}\n{secret}".encode("utf-8")
    signature = base64.b64encode(
        hmac.new(string_to_sign, digestmod=hashlib.sha256).digest()
    ).decode("ascii")
    payload["timestamp"] = timestamp
    payload["sign"] = signature


def send_card(webhook: str, card: dict[str, Any], signing_secret: str | None) -> None:
    payload: dict[str, Any] = {"msg_type": "interactive", "card": card}
    add_signature(payload, signing_secret)
    request = urllib.request.Request(
        webhook,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    decode_json_response(open_request(request, delivery=True))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--allow-local-image-root", action="append", type=Path, default=[])
    args = parser.parse_args()

    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    images = manifest.get("images", {})
    cards = manifest.get("cards", [])
    if not isinstance(images, dict) or not isinstance(cards, list) or not cards:
        raise FeishuError("Manifest must contain an images object and a non-empty cards array")

    webhook = get_setting("FEISHU_AI_DAILY_WEBHOOK_URL")
    if not webhook:
        raise FeishuError("FEISHU_AI_DAILY_WEBHOOK_URL is not configured")

    image_keys: dict[str, str] = {}
    if images:
        app_id = get_setting("FEISHU_AI_DAILY_APP_ID")
        app_secret = get_setting("FEISHU_AI_DAILY_APP_SECRET")
        if not app_id or not app_secret:
            raise FeishuError("Feishu app credentials are required to upload images")
        token = get_tenant_access_token(app_id, app_secret)
        allowed_roots = [args.manifest.resolve().parent, *args.allow_local_image_root]
        for name, source in images.items():
            if not isinstance(name, str) or not isinstance(source, str):
                raise FeishuError("Image names and sources must be strings")
            image_keys[name] = upload_image(token, source, allowed_local_roots=allowed_roots)

    signing_secret = get_setting("FEISHU_AI_DAILY_BOT_SECRET")
    for card in cards:
        if not isinstance(card, dict):
            raise FeishuError("Each card must be a JSON object")
        send_card(webhook, replace_image_placeholders(card, image_keys), signing_secret)

    print(f"FEISHU_SEND_SUCCESS cards={len(cards)} images={len(image_keys)}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (FeishuError, OSError, ValueError, json.JSONDecodeError) as error:
        print(f"FEISHU_SEND_FAILED {error}", file=sys.stderr)
        raise SystemExit(1)
