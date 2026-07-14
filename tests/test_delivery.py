import unittest
from pathlib import Path

from dealy_report.delivery import FeishuCredentials, deliver_manifest
from scripts.feishu_card_sender import FeishuDeliveryUncertain


class DeliveryTests(unittest.TestCase):
    def test_credentials_never_reveal_values_in_repr(self):
        credentials = FeishuCredentials(
            webhook="https://open.feishu.cn/secret-hook",
            app_id="app-id",
            app_secret="app-secret",
            bot_secret="bot-secret",
        )

        rendered = repr(credentials)

        self.assertNotIn("secret-hook", rendered)
        self.assertNotIn("app-secret", rendered)
        self.assertNotIn("bot-secret", rendered)

    def test_uploads_images_and_resumes_after_delivered_cards(self):
        manifest = {
            "images": {"hero": "https://cdn.example.com/hero.png"},
            "cards": [
                {"elements": [{"img_key": "{{image:hero}}"}], "id": 1},
                {"elements": [{"img_key": "{{image:hero}}"}], "id": 2},
                {"elements": [{"img_key": "{{image:hero}}"}], "id": 3},
            ],
        }
        sent = []
        progress = []

        result = deliver_manifest(
            manifest,
            FeishuCredentials("webhook", "app", "secret", None),
            allowed_local_roots=[Path("/tmp")],
            start_card=1,
            on_card_sent=progress.append,
            token_getter=lambda app_id, app_secret: "token",
            image_uploader=lambda token, source, allowed_local_roots=None: "img-key",
            card_sender=lambda webhook, card, secret: sent.append(card),
        )

        self.assertEqual([card["id"] for card in sent], [2, 3])
        self.assertEqual(sent[0]["elements"][0]["img_key"], "img-key")
        self.assertEqual(progress, [2, 3])
        self.assertEqual(result.delivered_cards, 3)
        self.assertEqual(result.uploaded_images, 1)

    def test_uncertain_delivery_stops_without_marking_progress(self):
        manifest = {"images": {}, "cards": [{"id": 1}]}

        with self.assertRaises(FeishuDeliveryUncertain):
            deliver_manifest(
                manifest,
                FeishuCredentials("webhook", None, None, None),
                card_sender=lambda *args: (_ for _ in ()).throw(FeishuDeliveryUncertain("timeout")),
            )


if __name__ == "__main__":
    unittest.main()
