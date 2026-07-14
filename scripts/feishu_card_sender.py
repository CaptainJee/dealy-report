#!/usr/bin/env python3
"""Upload card images to Feishu and send interactive cards via a group webhook."""

from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import json
import mimetypes
import os
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


def open_request(request: urllib.request.Request, timeout: int = 45) -> Any:
    try:
        return urllib.request.urlopen(request, timeout=timeout)
    except urllib.error.HTTPError as error:
        details = error.read().decode("utf-8", errors="replace")[:1000]
        raise FeishuError(f"HTTP {error.code}: {details}") from error
    except urllib.error.URLError as error:
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


def load_image(source: str) -> tuple[bytes, str, str]:
    if source.startswith(("https://", "http://")):
        request = urllib.request.Request(source, headers={"User-Agent": "Codex-AI-Daily/1.0"})
        response = open_request(request)
        content_type = response.headers.get_content_type().lower()
        data = response.read(MAX_IMAGE_BYTES + 1)
        filename = Path(urllib.parse.urlparse(source).path).name or "image"
    else:
        path = Path(source).expanduser().resolve()
        if not path.is_file():
            raise FeishuError(f"Image file does not exist: {path}")
        data = path.read_bytes()
        content_type = (mimetypes.guess_type(path.name)[0] or "").lower()
        filename = path.name

    if len(data) == 0:
        raise FeishuError(f"Image is empty: {source}")
    if len(data) > MAX_IMAGE_BYTES:
        raise FeishuError(f"Image exceeds 10 MB: {source}")
    if content_type not in ALLOWED_IMAGE_TYPES:
        raise FeishuError(f"Unsupported image type {content_type or 'unknown'}: {source}")
    return data, content_type, filename


def upload_image(token: str, source: str) -> str:
    image, content_type, filename = load_image(source)
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
    decode_json_response(open_request(request))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("manifest", type=Path)
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
        for name, source in images.items():
            if not isinstance(name, str) or not isinstance(source, str):
                raise FeishuError("Image names and sources must be strings")
            image_keys[name] = upload_image(token, source)

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
