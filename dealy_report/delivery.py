from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from scripts.feishu_card_sender import (
    FeishuError,
    get_tenant_access_token,
    replace_image_placeholders,
    send_card,
    upload_image,
)


@dataclass(frozen=True)
class FeishuCredentials:
    webhook: str = field(repr=False)
    app_id: str | None = field(default=None, repr=False)
    app_secret: str | None = field(default=None, repr=False)
    bot_secret: str | None = field(default=None, repr=False)


@dataclass(frozen=True)
class DeliveryResult:
    delivered_cards: int
    uploaded_images: int


def deliver_manifest(
    manifest: dict[str, Any],
    credentials: FeishuCredentials,
    allowed_local_roots: list[Path] | None = None,
    start_card: int = 0,
    on_card_sent: Callable[[int], None] | None = None,
    token_getter: Callable[[str, str], str] = get_tenant_access_token,
    image_uploader: Callable[..., str] = upload_image,
    card_sender: Callable[[str, dict[str, Any], str | None], None] = send_card,
) -> DeliveryResult:
    images = manifest.get("images", {})
    cards = manifest.get("cards", [])
    if not isinstance(images, dict) or not isinstance(cards, list) or not cards:
        raise FeishuError("Manifest must contain an images object and a non-empty cards array")
    if not 0 <= start_card <= len(cards):
        raise FeishuError("Delivered card progress is outside the manifest")

    image_keys: dict[str, str] = {}
    if images:
        if not credentials.app_id or not credentials.app_secret:
            raise FeishuError("Feishu app credentials are required to upload images")
        token = token_getter(credentials.app_id, credentials.app_secret)
        for name, source in images.items():
            if not isinstance(name, str) or not isinstance(source, str):
                raise FeishuError("Image names and sources must be strings")
            image_keys[name] = image_uploader(
                token,
                source,
                allowed_local_roots=allowed_local_roots or [],
            )

    rendered_cards = replace_image_placeholders(cards, image_keys)
    delivered = start_card
    for index in range(start_card, len(rendered_cards)):
        card = rendered_cards[index]
        if not isinstance(card, dict):
            raise FeishuError("Each card must be a JSON object")
        card_sender(credentials.webhook, card, credentials.bot_secret)
        delivered = index + 1
        if on_card_sent:
            on_card_sent(delivered)
    return DeliveryResult(delivered_cards=delivered, uploaded_images=len(image_keys))

