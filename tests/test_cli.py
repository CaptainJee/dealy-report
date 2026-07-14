import io
import json
import struct
import tempfile
import unittest
import zlib
from pathlib import Path
from types import SimpleNamespace

from dealy_report.config import ProfileConfig
from dealy_report.runner import RunOutcome


class FakeStore:
    def __init__(self, values=None, set_error=None):
        self.values = values
        self.set_error = set_error
        self.set_calls = []
        self.deleted = []

    def get(self, profile_id):
        return self.values

    def set(self, profile_id, **values):
        self.set_calls.append((profile_id, values))
        if self.set_error:
            raise self.set_error
        self.values = values

    def delete_profile(self, profile_id):
        self.deleted.append(profile_id)


def compatible_codex():
    return SimpleNamespace(
        executable=Path("local-codex.cmd"),
        version="codex-cli 0.144.4",
        logged_in=True,
        compatible=True,
        error=None,
    )


class CliTests(unittest.TestCase):
    def make_dependencies(self, cli, root, **overrides):
        defaults = dict(
            repo_path=root,
            config_root=root / "config",
            data_root=root / "data",
            python_path=root / ".runtime" / "venv" / "Scripts" / "python.exe",
            home=root / "home",
            input_func=lambda prompt: "",
            getpass_func=lambda prompt: "",
            stdout=io.StringIO(),
            stderr=io.StringIO(),
            secret_store_factory=lambda allow_file_fallback=False: FakeStore(),
            ensure_codex_func=lambda *args, **kwargs: compatible_codex(),
            install_scheduler_func=lambda *args, **kwargs: {"backend": "test"},
            remove_scheduler_func=lambda *args, **kwargs: {"backend": "test"},
            run_profile_func=lambda *args, **kwargs: RunOutcome("sent"),
            deliver_manifest_func=lambda *args, **kwargs: SimpleNamespace(delivered_cards=1, uploaded_images=1),
        )
        defaults.update(overrides)
        return cli.Dependencies(**defaults)

    def test_default_command_routes_to_setup_and_parser_supports_all_commands(self):
        from dealy_report import cli

        self.assertEqual(cli.parse_args([]).command, "setup")
        self.assertEqual(cli.parse_args(["setup"]).command, "setup")
        self.assertEqual(cli.parse_args(["doctor", "--profile", "daily-ai"]).command, "doctor")
        self.assertTrue(cli.parse_args(["doctor", "--profile", "daily-ai", "--live"]).live)
        self.assertTrue(cli.parse_args(["run", "--profile", "daily-ai", "--now"]).now)
        self.assertEqual(cli.parse_args(["dispatch", "--profile", "daily-ai"]).command, "dispatch")
        self.assertEqual(cli.parse_args(["list"]).command, "list")
        self.assertTrue(cli.parse_args(["remove", "--profile", "daily-ai", "--yes"]).yes)

    def test_setup_asks_before_secret_file_fallback_and_sends_image_by_default(self):
        from dealy_report import cli
        from dealy_report.secrets import SecretError

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            answers = iter(["", "", "", "", "", "", "AI, Agents", "", "", "", "", "2", "y", ""])
            secrets = iter(["hook-value", "app-value", "secret-value", "bot-value"])
            keyring_store = FakeStore(set_error=SecretError("unavailable"))
            file_store = FakeStore()
            factory_calls = []
            scheduler_calls = []
            deliveries = []

            def store_factory(allow_file_fallback=False):
                factory_calls.append(allow_file_fallback)
                return file_store if allow_file_fallback else keyring_store

            deps = self.make_dependencies(
                cli,
                root,
                input_func=lambda prompt: next(answers),
                getpass_func=lambda prompt: next(secrets),
                secret_store_factory=store_factory,
                install_scheduler_func=lambda *args, **kwargs: scheduler_calls.append((args, kwargs)) or {"backend": "test"},
                deliver_manifest_func=lambda *args, **kwargs: deliveries.append((args, kwargs)) or SimpleNamespace(delivered_cards=1, uploaded_images=1),
            )

            exit_code = cli.main([], deps=deps)

            self.assertEqual(exit_code, 0)
            saved = ProfileConfig.load(root / "config" / "profiles" / "daily-ai.json")
            self.assertEqual(saved.topics, ("AI", "Agents"))
            self.assertEqual(saved.audience, "developer/tech-lead")
            self.assertEqual(saved.max_cards, 2)
            self.assertEqual(factory_calls, [False, True])
            self.assertEqual(file_store.set_calls[0][1]["app_secret"], "secret-value")
            self.assertEqual(scheduler_calls[0][0][1], deps.python_path)
            marker = root / "data" / "schedulers" / "dealy-report-daily-ai.backend"
            self.assertEqual(marker.read_text(encoding="utf-8"), "test\n")
            manifest = deliveries[0][0][0]
            image_path = Path(manifest["images"]["connectivity"])
            self.assertTrue(image_path.is_file())
            self.assertIn("{{image:connectivity}}", json.dumps(manifest["cards"]))
            output = deps.stdout.getvalue() + deps.stderr.getvalue()
            for secret in ("hook-value", "app-value", "secret-value", "bot-value"):
                self.assertNotIn(secret, output)

    def test_setup_refuses_file_fallback_without_explicit_consent(self):
        from dealy_report import cli
        from dealy_report.secrets import SecretError

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            answers = iter(["", "", "", "", "", "", "", "", "", "", "", "", "n"])
            secrets = iter(["hook", "app", "secret", ""])
            fallback_created = []

            def store_factory(allow_file_fallback=False):
                if allow_file_fallback:
                    fallback_created.append(True)
                return FakeStore(set_error=None if allow_file_fallback else SecretError("unavailable"))

            deps = self.make_dependencies(
                cli,
                root,
                input_func=lambda prompt: next(answers),
                getpass_func=lambda prompt: next(secrets),
                secret_store_factory=store_factory,
            )

            self.assertNotEqual(cli.main(["setup"], deps=deps), 0)
            self.assertEqual(fallback_created, [])
            self.assertIn("not stored", deps.stderr.getvalue().lower())

    def test_setup_blank_values_preserve_an_existing_profile(self):
        from dealy_report import cli

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            existing = ProfileConfig(
                profile_id="team-report",
                task_name="Team Brief",
                schedule_time="09:15",
                timezone="Asia/Shanghai",
                language="en-US",
                audience="staff engineers",
                topics=("Agents", "Evaluation"),
                source_balance="global",
                model="gpt-5.5",
                reasoning_effort="xhigh",
                service_tier="fast",
            )
            path = root / "config" / "profiles" / "team-report.json"
            existing.save(path)
            answers = iter(["team-report", "", "", "", "", "", "", "", "", "", "", "", "n"])
            old_secrets = {"webhook": "old-hook", "app_id": "old-app", "app_secret": "old-secret", "bot_secret": ""}
            store = FakeStore(values=old_secrets)
            deps = self.make_dependencies(
                cli,
                root,
                input_func=lambda prompt: next(answers),
                getpass_func=lambda prompt: "",
                secret_store_factory=lambda allow_file_fallback=False: store,
            )

            self.assertEqual(cli.main(["setup"], deps=deps), 0)
            self.assertEqual(ProfileConfig.load(path), existing)
            self.assertEqual(store.set_calls[0][1], old_secrets)

    def test_setup_reads_existing_file_fallback_when_strict_keyring_is_empty(self):
        from dealy_report import cli

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            ProfileConfig().save(root / "config" / "profiles" / "daily-ai.json")
            fallback_path = root / "data" / "secrets.json"
            fallback_path.parent.mkdir(parents=True)
            fallback_path.write_text("{}\n", encoding="utf-8")
            old_values = {"webhook": "old-hook", "app_id": "old-app", "app_secret": "old-secret", "bot_secret": "old-bot"}
            strict_store = FakeStore(values=None)
            fallback_store = FakeStore(values=old_values)
            answers = iter(["", "", "", "", "", "", "", "", "", "", "", "", "n"])

            def store_factory(allow_file_fallback=False):
                return fallback_store if allow_file_fallback else strict_store

            deps = self.make_dependencies(
                cli,
                root,
                input_func=lambda prompt: next(answers),
                getpass_func=lambda prompt: "",
                secret_store_factory=store_factory,
            )

            self.assertEqual(cli.main(["setup"], deps=deps), 0)
            self.assertEqual(strict_store.set_calls[0][1], old_values)

    def test_setup_rejects_max_cards_outside_one_to_three(self):
        from dealy_report import cli

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            answers = iter(["", "", "", "", "", "", "", "", "", "", "", "4"])
            deps = self.make_dependencies(cli, root, input_func=lambda prompt: next(answers))

            self.assertNotEqual(cli.main(["setup"], deps=deps), 0)
            self.assertIn("max cards", deps.stderr.getvalue().lower())

    def test_run_now_forces_and_dispatch_uses_due_logic_without_printing_secrets(self):
        from dealy_report import cli

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            ProfileConfig().save(root / "config" / "profiles" / "daily-ai.json")
            values = {"webhook": "private-hook", "app_id": "private-app", "app_secret": "private-secret", "bot_secret": "private-bot"}
            store = FakeStore(values=values)
            forces = []

            def run_profile(*args, **kwargs):
                forces.append(kwargs["force"])
                return RunOutcome("failed", error="private-hook private-secret generation failed")

            deps = self.make_dependencies(
                cli,
                root,
                secret_store_factory=lambda allow_file_fallback=False: store,
                run_profile_func=run_profile,
            )

            self.assertEqual(cli.main(["run", "--profile", "daily-ai", "--now"], deps=deps), 1)
            self.assertEqual(cli.main(["dispatch", "--profile", "daily-ai"], deps=deps), 1)
            self.assertEqual(forces, [True, False])
            output = deps.stdout.getvalue() + deps.stderr.getvalue()
            self.assertNotIn("private-hook", output)
            self.assertNotIn("private-secret", output)
            self.assertIn("[redacted]", output)

    def test_doctor_reports_all_checks_and_live_delivery_is_opt_in(self):
        from dealy_report import cli

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            ProfileConfig().save(root / "config" / "profiles" / "daily-ai.json")
            store = FakeStore(values={"webhook": "h", "app_id": "a", "app_secret": "s", "bot_secret": ""})
            dry_runs = []
            deliveries = []
            deps = self.make_dependencies(
                cli,
                root,
                secret_store_factory=lambda allow_file_fallback=False: store,
                install_scheduler_func=lambda *args, **kwargs: dry_runs.append(kwargs) or {"backend": "windows-task"},
                deliver_manifest_func=lambda *args, **kwargs: deliveries.append(args) or SimpleNamespace(delivered_cards=1, uploaded_images=1),
            )
            marker = root / "data" / "schedulers" / "dealy-report-daily-ai.backend"
            marker.parent.mkdir(parents=True)
            marker.write_text("windows-task\n", encoding="utf-8")

            self.assertEqual(cli.main(["doctor", "--profile", "daily-ai"], deps=deps), 0)
            self.assertEqual(deliveries, [])
            self.assertTrue(dry_runs[0]["dry_run"])
            self.assertEqual(cli.main(["doctor", "--profile", "daily-ai", "--live"], deps=deps), 0)
            self.assertEqual(len(deliveries), 1)
            output = deps.stdout.getvalue().lower()
            for label in ("config", "secrets", "codex version", "codex login", "structured output", "scheduler"):
                self.assertIn(label, output)
            self.assertIn("registered backend: windows-task", output)

    def test_doctor_reports_missing_scheduler_registration(self):
        from dealy_report import cli

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            ProfileConfig().save(root / "config" / "profiles" / "daily-ai.json")
            store = FakeStore(values={"webhook": "h", "app_id": "a", "app_secret": "s", "bot_secret": ""})
            deps = self.make_dependencies(cli, root, secret_store_factory=lambda allow_file_fallback=False: store)

            self.assertEqual(cli.main(["doctor", "--profile", "daily-ai"], deps=deps), 1)
            self.assertIn("scheduler registration: missing", deps.stdout.getvalue().lower())

    def test_connectivity_feishu_error_is_controlled_and_redacted(self):
        from dealy_report import cli
        from scripts.feishu_card_sender import FeishuError

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            ProfileConfig().save(root / "config" / "profiles" / "daily-ai.json")
            values = {"webhook": "private-hook", "app_id": "private-app", "app_secret": "private-secret", "bot_secret": ""}
            store = FakeStore(values=values)
            marker = root / "data" / "schedulers" / "dealy-report-daily-ai.backend"
            marker.parent.mkdir(parents=True)
            marker.write_text("test\n", encoding="utf-8")

            def fail_delivery(*args, **kwargs):
                raise FeishuError("send failed for private-hook")

            deps = self.make_dependencies(
                cli,
                root,
                secret_store_factory=lambda allow_file_fallback=False: store,
                deliver_manifest_func=fail_delivery,
            )

            self.assertEqual(cli.main(["doctor", "--profile", "daily-ai", "--live"], deps=deps), 2)
            output = deps.stdout.getvalue() + deps.stderr.getvalue()
            self.assertIn("error:", output.lower())
            self.assertIn("[redacted]", output)
            self.assertNotIn("private-hook", output)
            self.assertNotIn("traceback", output.lower())

    def test_list_shows_last_state(self):
        from dealy_report import cli

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            ProfileConfig(profile_id="team-report", task_name="Team Brief").save(
                root / "config" / "profiles" / "team-report.json"
            )
            state_path = root / "data" / "profiles" / "team-report" / "state.json"
            state_path.parent.mkdir(parents=True)
            state_path.write_text('{"date":"2026-07-14","phase":"sent","attempts":1}\n', encoding="utf-8")
            deps = self.make_dependencies(cli, root)

            self.assertEqual(cli.main(["list"], deps=deps), 0)
            output = deps.stdout.getvalue()
            self.assertIn("team-report", output)
            self.assertIn("Team Brief", output)
            self.assertIn("sent", output)
            self.assertIn("2026-07-14", output)

    def test_remove_requires_confirmation_and_keeps_archived_reports(self):
        from dealy_report import cli

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config_path = root / "config" / "profiles" / "daily-ai.json"
            ProfileConfig().save(config_path)
            report_path = root / "data" / "profiles" / "daily-ai" / "reports" / "2026-07-14" / "report.md"
            report_path.parent.mkdir(parents=True)
            report_path.write_text("archive", encoding="utf-8")
            store = FakeStore()
            removals = []
            answers = iter(["n", "y"])
            deps = self.make_dependencies(
                cli,
                root,
                input_func=lambda prompt: next(answers),
                secret_store_factory=lambda allow_file_fallback=False: store,
                remove_scheduler_func=lambda *args, **kwargs: removals.append((args, kwargs)) or {"backend": "test"},
            )

            self.assertEqual(cli.main(["remove", "--profile", "daily-ai"], deps=deps), 0)
            self.assertTrue(config_path.exists())
            self.assertEqual(removals, [])
            self.assertEqual(cli.main(["remove", "--profile", "daily-ai"], deps=deps), 0)
            self.assertFalse(config_path.exists())
            self.assertEqual(store.deleted, ["daily-ai"])
            self.assertEqual(len(removals), 1)
            self.assertTrue(report_path.exists())

    def test_profile_id_is_validated_before_building_a_path(self):
        from dealy_report import cli

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            deps = self.make_dependencies(cli, root)

            self.assertEqual(cli.main(["doctor", "--profile", "../secrets"], deps=deps), 2)
            self.assertIn("profile id", deps.stderr.getvalue().lower())

    def test_warns_without_modifying_matching_legacy_automation(self):
        from dealy_report import cli

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            legacy = root / "home" / ".codex" / "automations" / "ai" / "automation.toml"
            legacy.parent.mkdir(parents=True)
            content = f'prompt = "run {root} daily"\n'
            legacy.write_text(content, encoding="utf-8")
            deps = self.make_dependencies(cli, root)

            self.assertEqual(cli.main(["list"], deps=deps), 0)
            self.assertIn("legacy", deps.stderr.getvalue().lower())
            self.assertEqual(legacy.read_text(encoding="utf-8"), content)


class ConnectivityPngTests(unittest.TestCase):
    def test_generator_writes_a_visible_valid_stdlib_png(self):
        from assets.connectivity_test import write_connectivity_png

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "connectivity.png"
            write_connectivity_png(path, width=320, height=120)
            payload = path.read_bytes()

        self.assertEqual(payload[:8], b"\x89PNG\r\n\x1a\n")
        offset = 8
        chunks = []
        while offset < len(payload):
            length = struct.unpack(">I", payload[offset : offset + 4])[0]
            chunk_type = payload[offset + 4 : offset + 8]
            data = payload[offset + 8 : offset + 8 + length]
            chunks.append((chunk_type, data))
            offset += 12 + length
        ihdr = next(data for chunk_type, data in chunks if chunk_type == b"IHDR")
        self.assertEqual(struct.unpack(">II", ihdr[:8]), (320, 120))
        raw = zlib.decompress(b"".join(data for chunk_type, data in chunks if chunk_type == b"IDAT"))
        self.assertGreater(len(set(raw)), 4)
        self.assertEqual(chunks[-1][0], b"IEND")


if __name__ == "__main__":
    unittest.main()
