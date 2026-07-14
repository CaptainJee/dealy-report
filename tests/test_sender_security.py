import io
import socket
import ssl
import tempfile
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest.mock import patch

from scripts.feishu_card_sender import (
    FeishuDeliveryUncertain,
    FeishuError,
    PinnedHTTPSConnection,
    detect_image_content_type,
    fetch_remote_image,
    load_image,
    open_request,
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

    def test_remote_size_errors_do_not_echo_a_signed_url(self):
        source = "https://cdn.example.com/image.png?token=sensitive"
        for data, expected in ((b"", "empty"), (b"too-large", "10 MB")):
            with self.subTest(expected=expected), patch(
                "scripts.feishu_card_sender.fetch_remote_image",
                return_value=(data, "image.png"),
            ), patch("scripts.feishu_card_sender.MAX_IMAGE_BYTES", 1):
                with self.assertRaisesRegex(FeishuError, expected) as raised:
                    load_image(source)
            self.assertNotIn("sensitive", str(raised.exception))
            self.assertNotIn("token", str(raised.exception))

    def test_feishu_http_error_does_not_echo_response_body_or_request_url(self):
        request = urllib.request.Request("https://open.feishu.cn/hook?token=sensitive")
        error = urllib.error.HTTPError(
            request.full_url,
            403,
            "Forbidden",
            {},
            io.BytesIO(b"upstream-secret-body"),
        )
        with patch("scripts.feishu_card_sender.urllib.request.urlopen", side_effect=error):
            with self.assertRaisesRegex(FeishuError, "HTTP 403") as raised:
                open_request(request)

        message = str(raised.exception)
        self.assertNotIn("sensitive", message)
        self.assertNotIn("upstream-secret-body", message)

    def test_delivery_http_5xx_is_uncertain_and_must_not_be_retried(self):
        request = urllib.request.Request("https://open.feishu.cn/hook?token=sensitive")
        error = urllib.error.HTTPError(
            request.full_url,
            502,
            "Bad Gateway",
            {},
            io.BytesIO(b"upstream-secret-body"),
        )
        with patch("scripts.feishu_card_sender.urllib.request.urlopen", side_effect=error):
            with self.assertRaises(FeishuDeliveryUncertain) as raised:
                open_request(request, delivery=True)

        self.assertNotIn("sensitive", str(raised.exception))
        self.assertNotIn("upstream-secret-body", str(raised.exception))

    def test_pinned_connection_uses_validated_ip_and_original_hostname_for_sni(self):
        connection = PinnedHTTPSConnection("images.example.com", 443, "93.184.216.34", 45)
        self.assertTrue(connection._context.check_hostname)
        self.assertEqual(connection._context.verify_mode, ssl.CERT_REQUIRED)

        class Context:
            def __init__(self):
                self.server_hostname = None

            def wrap_socket(self, sock, server_hostname=None):
                self.server_hostname = server_hostname
                return sock

        context = Context()
        connection._context = context
        raw_socket = object()
        with patch("scripts.feishu_card_sender.socket.create_connection", return_value=raw_socket) as create:
            connection.connect()

        create.assert_called_once_with(("93.184.216.34", 443), 45, None)
        self.assertEqual(context.server_hostname, "images.example.com")


if __name__ == "__main__":
    unittest.main()
