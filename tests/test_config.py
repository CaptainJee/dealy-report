import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


class ProfileConfigDefaultsTests(unittest.TestCase):
    def test_defaults_describe_the_daily_ai_profile(self) -> None:
        from dealy_report.config import ProfileConfig

        config = ProfileConfig()

        self.assertEqual(1, config.schema_version)
        self.assertEqual("daily-ai", config.profile_id)
        self.assertEqual("08:30", config.schedule_time)
        self.assertEqual("Asia/Shanghai", config.timezone)
        self.assertEqual("zh-CN", config.language)
        self.assertEqual("developer/tech-lead", config.audience)
        self.assertIsInstance(config.topics, tuple)
        self.assertTrue(config.topics)
        self.assertEqual("balanced", config.source_balance)
        self.assertEqual("gpt-5.5", config.model)
        self.assertEqual("high", config.reasoning_effort)
        self.assertEqual("fast", config.service_tier)
        self.assertEqual(3, config.max_cards)


class ProfileConfigValidationTests(unittest.TestCase):
    def test_rejects_invalid_profile_values(self) -> None:
        from dealy_report.config import ConfigError, ProfileConfig

        invalid_values = (
            {"profile_id": "Daily AI"},
            {"schedule_time": "08:32"},
            {"schedule_time": "25:00"},
            {"timezone": "Mars/Olympus"},
            {"topics": ()},
            {"topics": ("",)},
            {"source_balance": "regional"},
            {"reasoning_effort": "maximum"},
            {"max_cards": 0},
            {"max_cards": 4},
        )

        for values in invalid_values:
            with self.subTest(values=values):
                with self.assertRaises(ConfigError):
                    ProfileConfig(**values)

    def test_normalizes_topics_to_a_tuple(self) -> None:
        from dealy_report.config import ProfileConfig

        config = ProfileConfig(topics=["AI agents", "LLM evaluation"])

        self.assertEqual(("AI agents", "LLM evaluation"), config.topics)


class ProfileConfigJsonTests(unittest.TestCase):
    def test_save_and_load_round_trip_without_secret_fields(self) -> None:
        from dealy_report.config import ProfileConfig

        config = ProfileConfig(profile_id="team-report", topics=("AI agents",))
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "profile.json"

            config.save(path)

            serialized = json.loads(path.read_text(encoding="utf-8"))
            self.assertNotIn("webhook", serialized)
            self.assertNotIn("app_id", serialized)
            self.assertNotIn("app_secret", serialized)
            self.assertNotIn("bot_secret", serialized)
            self.assertEqual(config, ProfileConfig.load(path))

    def test_save_replaces_the_destination_atomically(self) -> None:
        from dealy_report.config import ProfileConfig

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "profile.json"
            with patch("dealy_report.config.os.replace", wraps=__import__("os").replace) as replace:
                ProfileConfig().save(path)

            replace.assert_called_once()
            self.assertEqual(path, Path(replace.call_args.args[1]))

    def test_rejects_future_schema_and_secret_json_fields(self) -> None:
        from dealy_report.config import ConfigError, ProfileConfig

        with tempfile.TemporaryDirectory() as directory:
            future_path = Path(directory) / "future.json"
            secret_path = Path(directory) / "secret.json"
            future_path.write_text('{"schema_version": 2}', encoding="utf-8")
            secret_path.write_text('{"webhook": "not-allowed"}', encoding="utf-8")

            with self.assertRaises(ConfigError):
                ProfileConfig.load(future_path)
            with self.assertRaises(ConfigError):
                ProfileConfig.load(secret_path)


class ConfigDirectoryTests(unittest.TestCase):
    def test_uses_xdg_and_windows_application_directories(self) -> None:
        from dealy_report.config import config_dir, data_dir

        root = Path("C:/profiles/example")
        self.assertEqual(
            root / "xdg-config" / "dealy-report",
            config_dir(
                env={"XDG_CONFIG_HOME": str(root / "xdg-config")},
                platform_name="linux",
                home=root,
            ),
        )
        self.assertEqual(
            root / "xdg-data" / "dealy-report",
            data_dir(
                env={"XDG_DATA_HOME": str(root / "xdg-data")},
                platform_name="linux",
                home=root,
            ),
        )
        self.assertEqual(
            root / "AppData" / "Roaming" / "dealy-report",
            config_dir(
                env={"APPDATA": str(root / "AppData" / "Roaming")},
                platform_name="win32",
                home=root,
            ),
        )
        self.assertEqual(
            root / "AppData" / "Local" / "dealy-report",
            data_dir(
                env={"LOCALAPPDATA": str(root / "AppData" / "Local")},
                platform_name="win32",
                home=root,
            ),
        )


if __name__ == "__main__":
    unittest.main()
