import socket
import tempfile
import unittest
from pathlib import Path

from scripts.feishu_card_sender import (
    FeishuError,
    detect_image_content_type,
    fetch_remote_image,
    load_image,
    validate_remote_url,
)


def public_resolver(host, port, type=0):
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", port))]


def private_resolver(host, port, type=0):
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", port))]


class SenderSecurityTests(unittest.TestCase):
    def test_remote_images_require_https(self):
        with self.assertRaisesRegex(FeishuError, "HTTPS") as raised:
            validate_remote_url("http://example.com/image.png", resolver=public_resolver)
        self.assertNotIn("image.png", str(raised.exception))

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

    def test_remote_fetch_connects_to_the_validated_ip_without_second_dns_lookup(self):
        resolver_calls = []
        connections = []

        def resolver(host, port, type=0):
            resolver_calls.append(host)
            address = "93.184.216.34" if len(resolver_calls) == 1 else "127.0.0.1"
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, port))]

        class Response:
            status = 200

            def read(self, size):
                return b"\x89PNG\r\n\x1a\ncontent"

            def getheader(self, name):
                return None

        class Connection:
            def request(self, method, path, headers=None):
                pass

            def getresponse(self):
                return Response()

            def close(self):
                pass

        def factory(host, port, address, timeout):
            connections.append((host, address))
            return Connection()

        data, filename = fetch_remote_image(
            "https://example.com/image.png?token=sensitive",
            resolver=resolver,
            connection_factory=factory,
        )

        self.assertEqual(data[:8], b"\x89PNG\r\n\x1a\n")
        self.assertEqual(filename, "image.png")
        self.assertEqual(resolver_calls, ["example.com"])
        self.assertEqual(connections, [("example.com", "93.184.216.34")])

    def test_remote_fetch_revalidates_redirect_and_never_echoes_query_or_body(self):
        class RedirectResponse:
            status = 302

            def read(self, size):
                return b"upstream-secret-body"

            def getheader(self, name):
                return "https://internal.example/private.png?token=hidden" if name == "Location" else None

        class Connection:
            def request(self, method, path, headers=None):
                pass

            def getresponse(self):
                return RedirectResponse()

            def close(self):
                pass

        def resolver(host, port, type=0):
            address = "127.0.0.1" if host == "internal.example" else "93.184.216.34"
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, port))]

        with self.assertRaises(FeishuError) as raised:
            fetch_remote_image(
                "https://example.com/image.png?token=sensitive",
                resolver=resolver,
                connection_factory=lambda *args: Connection(),
            )

        message = str(raised.exception)
        self.assertNotIn("sensitive", message)
        self.assertNotIn("hidden", message)
        self.assertNotIn("upstream-secret-body", message)


if __name__ == "__main__":
    unittest.main()
