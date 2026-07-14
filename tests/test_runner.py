import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from dealy_report.config import ProfileConfig
from dealy_report.delivery import DeliveryResult, FeishuCredentials
from dealy_report.report import validate_report
from dealy_report.runner import run_profile
from dealy_report.state import load_state
from scripts.feishu_card_sender import FeishuDeliveryUncertain, FeishuError
from tests.test_report import valid_payload


NOW = datetime(2026, 7, 14, 9, 0, tzinfo=ZoneInfo("Asia/Shanghai"))


class RunnerTests(unittest.TestCase):
    def setUp(self):
        self.profile = ProfileConfig(topics=("Agent", "模型平台"))
        self.credentials = FeishuCredentials("webhook-secret", "app", "app-secret", None)
        self.report = validate_report(valid_payload())

    def test_generates_archives_and_sends_a_due_report(self):
        progress = []

        def deliver(manifest, credentials, **kwargs):
            kwargs["on_card_sent"](1)
            kwargs["on_card_sent"](2)
            kwargs["on_card_sent"](3)
            progress.append(len(manifest["cards"]))
            return DeliveryResult(3, len(manifest["images"]))

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            outcome = run_profile(
                self.profile,
                repo_path=root,
                data_root=root / "data",
                codex_path=Path("codex"),
                credentials=self.credentials,
                now=NOW,
                generate=lambda **kwargs: self.report,
                deliver=deliver,
            )
            state = load_state(root / "data" / "profiles" / "daily-ai" / "state.json")
            report_dir = root / "data" / "profiles" / "daily-ai" / "reports" / "2026-07-14"

            self.assertTrue((report_dir / "report.json").is_file())
            self.assertIn("Agent 真实项目应用", (report_dir / "report.md").read_text(encoding="utf-8"))
            self.assertEqual(len(json.loads((report_dir / "manifest.json").read_text(encoding="utf-8"))["cards"]), 3)

        self.assertEqual(outcome.status, "sent")
        self.assertEqual(state.phase, "sent")
        self.assertEqual(state.delivered_cards, 3)
        self.assertEqual(progress, [3])

    def test_known_send_failure_reuses_manifest_without_regenerating(self):
        generate_calls = []
        delivery_calls = []

        def generate(**kwargs):
            generate_calls.append(1)
            return self.report

        def deliver(manifest, credentials, **kwargs):
            delivery_calls.append(kwargs["start_card"])
            if len(delivery_calls) == 1:
                kwargs["on_card_sent"](1)
                raise FeishuError("known failure")
            return DeliveryResult(3, 3)

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = run_profile(
                self.profile,
                repo_path=root,
                data_root=root / "data",
                codex_path=Path("codex"),
                credentials=self.credentials,
                now=NOW,
                generate=generate,
                deliver=deliver,
            )
            second = run_profile(
                self.profile,
                repo_path=root,
                data_root=root / "data",
                codex_path=Path("codex"),
                credentials=self.credentials,
                now=NOW.replace(minute=16),
                generate=generate,
                deliver=deliver,
            )

        self.assertEqual(first.status, "send_failed")
        self.assertEqual(second.status, "sent")
        self.assertEqual(generate_calls, [1])
        self.assertEqual(delivery_calls, [0, 1])

    def test_uncertain_delivery_is_not_automatically_retried(self):
        calls = []

        def deliver(*args, **kwargs):
            calls.append(1)
            raise FeishuDeliveryUncertain("timeout")

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = run_profile(
                self.profile,
                repo_path=root,
                data_root=root / "data",
                codex_path=Path("codex"),
                credentials=self.credentials,
                now=NOW,
                generate=lambda **kwargs: self.report,
                deliver=deliver,
            )
            second = run_profile(
                self.profile,
                repo_path=root,
                data_root=root / "data",
                codex_path=Path("codex"),
                credentials=self.credentials,
                now=NOW.replace(hour=10),
                generate=lambda **kwargs: self.report,
                deliver=deliver,
            )

        self.assertEqual(first.status, "uncertain")
        self.assertEqual(second.status, "skipped")
        self.assertEqual(calls, [1])

    def test_before_schedule_is_skipped_without_generation(self):
        with tempfile.TemporaryDirectory() as directory:
            outcome = run_profile(
                self.profile,
                repo_path=Path(directory),
                data_root=Path(directory) / "data",
                codex_path=Path("codex"),
                credentials=self.credentials,
                now=NOW.replace(hour=8, minute=25),
                generate=lambda **kwargs: self.fail("generation should not run"),
                deliver=lambda *args, **kwargs: self.fail("delivery should not run"),
            )

        self.assertEqual(outcome.status, "skipped")


if __name__ == "__main__":
    unittest.main()
