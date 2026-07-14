import socket
import tempfile
import unittest
from pathlib import Path

from scripts.feishu_card_sender import (
    FeishuError,
    detect_image_content_type,
    load_image,
    validate_remote_url,
)


def public_resolver(host, port, type=0):
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", port))]


def private_resolver(host, port, type=0):
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", port))]


class SenderSecurityTests(unittest.TestCase):
    def test_remote_images_require_https(self):
        with self.assertRaisesRegex(FeishuError, "HTTPS"):
            validate_remote_url("http://example.com/image.png", resolver=public_resolver)

    def test_remote_images_reject_private_dns_results(self):
        with self.assertRaisesRegex(FeishuError, "public address"):
            validate_remote_url("https://internal.example/image.png", resolver=private_resolver)

    def test_remote_images_accept_public_https(self):
        self.assertEqual(
            validate_remote_url("https://example.com/image.png", resolver=public_resolver),
            "https://example.com/image.png",
        )

    def test_local_image_must_be_inside_an_allowed_root(self):
        png = b"\x89PNG\r\n\x1a\n" + b"payload"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            image = root / "cover.png"
            image.write_bytes(png)

            with self.assertRaisesRegex(FeishuError, "allowed root"):
                load_image(str(image), allowed_local_roots=[])

            data, content_type, filename = load_image(str(image), allowed_local_roots=[root])

        self.assertEqual(data, png)
        self.assertEqual(content_type, "image/png")
        self.assertEqual(filename, "cover.png")

    def test_detects_supported_image_signatures(self):
        self.assertEqual(detect_image_content_type(b"\x89PNG\r\n\x1a\nrest"), "image/png")
        self.assertEqual(detect_image_content_type(b"\xff\xd8\xffrest"), "image/jpeg")
        self.assertEqual(detect_image_content_type(b"GIF89arest"), "image/gif")
        self.assertEqual(detect_image_content_type(b"RIFFxxxxWEBPrest"), "image/webp")
        with self.assertRaisesRegex(FeishuError, "signature"):
            detect_image_content_type(b"not-an-image")


if __name__ == "__main__":
    unittest.main()
