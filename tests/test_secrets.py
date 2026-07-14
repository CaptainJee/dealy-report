import os
import tempfile
import unittest
from pathlib import Path
from stat import S_IMODE
from unittest.mock import call, patch


class FakeKeyring:
    def __init__(self) -> None:
        self.values: dict[tuple[str, str], str] = {}

    def set_password(self, service: str, username: str, value: str) -> None:
        self.values[(service, username)] = value

    def get_password(self, service: str, username: str) -> str | None:
        return self.values.get((service, username))

    def delete_password(self, service: str, username: str) -> None:
        self.values.pop((service, username), None)


class UnusableKeyring:
    def set_password(self, service: str, username: str, value: str) -> None:
        raise RuntimeError("credential backend is unavailable")

    def get_password(self, service: str, username: str) -> str | None:
        raise RuntimeError("credential backend is unavailable")

    def delete_password(self, service: str, username: str) -> None:
        raise RuntimeError("credential backend is unavailable")


class SecretStoreKeyringTests(unittest.TestCase):
    def test_uses_keyring_with_profile_scoped_usernames(self) -> None:
        from dealy_report.secrets import SecretStore

        keyring = FakeKeyring()
        with tempfile.TemporaryDirectory() as directory:
            store = SecretStore(data_dir=Path(directory), keyring_module=keyring)
            store.set(
                "daily-ai",
                webhook="https://secret.example/hook",
                app_id="app-id",
                app_secret="app-secret",
            )

            self.assertEqual(
                {
                    "webhook": "https://secret.example/hook",
                    "app_id": "app-id",
                    "app_secret": "app-secret",
                    "bot_secret": "",
                },
                store.get("daily-ai"),
            )
            self.assertEqual(
                {
                    ("dealy-report", "daily-ai:webhook"),
                    ("dealy-report", "daily-ai:app_id"),
                    ("dealy-report", "daily-ai:app_secret"),
                    ("dealy-report", "daily-ai:bot_secret"),
                },
                set(keyring.values),
            )
            self.assertFalse((Path(directory) / "secrets.json").exists())

    def test_delete_profile_removes_all_keyring_values(self) -> None:
        from dealy_report.secrets import SecretStore

        keyring = FakeKeyring()
        with tempfile.TemporaryDirectory() as directory:
            store = SecretStore(data_dir=Path(directory), keyring_module=keyring)
            store.set("daily-ai", webhook="webhook", app_id="app-id", app_secret="app-secret")

            store.delete_profile("daily-ai")

            self.assertEqual({}, keyring.values)
            self.assertIsNone(store.get("daily-ai"))


class SecretStoreFallbackTests(unittest.TestCase):
    def test_rejects_unavailable_keyring_without_explicit_file_fallback(self) -> None:
        from dealy_report.secrets import SecretError, SecretStore

        secret_value = "do-not-expose-this-secret"
        with tempfile.TemporaryDirectory() as directory:
            store = SecretStore(data_dir=Path(directory), keyring_module=None)
            with self.assertRaises(SecretError) as raised:
                store.set(
                    "daily-ai",
                    webhook=secret_value,
                    app_id="app-id",
                    app_secret="app-secret",
                )

            self.assertNotIn(secret_value, str(raised.exception))
            self.assertNotIn(secret_value, repr(store))
            self.assertFalse((Path(directory) / "secrets.json").exists())

    def test_uses_a_mode_0600_file_only_when_explicitly_allowed(self) -> None:
        from dealy_report.secrets import SecretStore

        with tempfile.TemporaryDirectory() as directory:
            data_dir = Path(directory)
            store = SecretStore(
                data_dir=data_dir,
                keyring_module=UnusableKeyring(),
                allow_file_fallback=True,
            )
            with patch("dealy_report.secrets.os.chmod", wraps=os.chmod) as chmod:
                store.set(
                    "daily-ai",
                    webhook="https://secret.example/hook",
                    app_id="app-id",
                    app_secret="app-secret",
                    bot_secret="",
                )

            path = data_dir / "secrets.json"
            self.assertIn(call(path, 0o600), chmod.call_args_list)
            if os.name != "nt":
                self.assertEqual(0o600, S_IMODE(path.stat().st_mode))
            self.assertEqual(
                {
                    "webhook": "https://secret.example/hook",
                    "app_id": "app-id",
                    "app_secret": "app-secret",
                    "bot_secret": "",
                },
                store.get("daily-ai"),
            )
            self.assertFalse(list(data_dir.glob("*.tmp")))

            store.delete_profile("daily-ai")
            self.assertIsNone(store.get("daily-ai"))

    def test_reads_and_deletes_authorized_fallback_after_keyring_recovers(self) -> None:
        from dealy_report.secrets import SecretStore

        with tempfile.TemporaryDirectory() as directory:
            data_dir = Path(directory)
            fallback = SecretStore(data_dir=data_dir, keyring_module=None, allow_file_fallback=True)
            fallback.set("daily-ai", webhook="webhook", app_id="app-id", app_secret="app-secret")

            recovered = SecretStore(
                data_dir=data_dir,
                keyring_module=FakeKeyring(),
                allow_file_fallback=True,
            )
            self.assertEqual("webhook", recovered.get("daily-ai")["webhook"])
            recovered.delete_profile("daily-ai")

            self.assertIsNone(fallback.get("daily-ai"))

    def test_successful_keyring_write_removes_stale_file_fallback(self) -> None:
        from dealy_report.secrets import SecretStore

        with tempfile.TemporaryDirectory() as directory:
            data_dir = Path(directory)
            fallback = SecretStore(data_dir=data_dir, keyring_module=None, allow_file_fallback=True)
            fallback.set("daily-ai", webhook="old", app_id="old-app", app_secret="old-secret")

            recovered = SecretStore(
                data_dir=data_dir,
                keyring_module=FakeKeyring(),
                allow_file_fallback=True,
            )
            recovered.set("daily-ai", webhook="new", app_id="new-app", app_secret="new-secret")

            self.assertIsNone(fallback.get("daily-ai"))
            self.assertEqual("new", recovered.get("daily-ai")["webhook"])


if __name__ == "__main__":
    unittest.main()
