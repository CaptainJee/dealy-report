"""Credential storage kept separate from profile configuration."""

from __future__ import annotations

import importlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any

from dealy_report.config import data_dir as default_data_dir
from dealy_report.config import validate_profile_id


_SERVICE_NAME = "dealy-report"
_SECRET_NAMES = ("webhook", "app_id", "app_secret", "bot_secret")
_KEYRING_UNSET = object()


class SecretError(RuntimeError):
    """Raised when credentials cannot be stored or retrieved safely."""


class SecretStore:
    """Store per-profile credentials in keyring or an explicitly enabled file."""

    def __init__(
        self,
        *,
        data_dir: str | os.PathLike[str] | None = None,
        keyring_module: Any = _KEYRING_UNSET,
        allow_file_fallback: bool = False,
    ) -> None:
        self._data_dir = Path(data_dir) if data_dir is not None else default_data_dir()
        self._allow_file_fallback = allow_file_fallback
        self._keyring = _load_keyring() if keyring_module is _KEYRING_UNSET else keyring_module

    def set(
        self,
        profile_id: str,
        *,
        webhook: str,
        app_id: str,
        app_secret: str,
        bot_secret: str = "",
    ) -> None:
        validate_profile_id(profile_id)
        values = _validate_secret_values(webhook, app_id, app_secret, bot_secret)
        if self._keyring is not None:
            try:
                for name, value in values.items():
                    self._keyring.set_password(_SERVICE_NAME, f"{profile_id}:{name}", value)
                return
            except Exception:
                self._use_file_fallback_or_raise()
        else:
            self._use_file_fallback_or_raise()
        self._set_file_values(profile_id, values)

    def get(self, profile_id: str) -> dict[str, str] | None:
        validate_profile_id(profile_id)
        if self._keyring is not None:
            try:
                return self._get_keyring_values(profile_id)
            except Exception:
                self._use_file_fallback_or_raise()
        else:
            self._use_file_fallback_or_raise()
        return self._get_file_values(profile_id)

    def delete_profile(self, profile_id: str) -> None:
        validate_profile_id(profile_id)
        if self._keyring is not None:
            try:
                for name in _SECRET_NAMES:
                    username = f"{profile_id}:{name}"
                    if self._keyring.get_password(_SERVICE_NAME, username) is not None:
                        self._keyring.delete_password(_SERVICE_NAME, username)
                return
            except Exception:
                self._use_file_fallback_or_raise()
        else:
            self._use_file_fallback_or_raise()
        self._delete_file_values(profile_id)

    def _use_file_fallback_or_raise(self) -> None:
        if not self._allow_file_fallback:
            raise SecretError("Credential storage is unavailable.") from None

    def _get_keyring_values(self, profile_id: str) -> dict[str, str] | None:
        values = {
            name: self._keyring.get_password(_SERVICE_NAME, f"{profile_id}:{name}")
            for name in _SECRET_NAMES
        }
        if any(values[name] is None for name in _SECRET_NAMES[:-1]):
            return None
        return {
            "webhook": _required_string(values["webhook"]),
            "app_id": _required_string(values["app_id"]),
            "app_secret": _required_string(values["app_secret"]),
            "bot_secret": values["bot_secret"] if isinstance(values["bot_secret"], str) else "",
        }

    @property
    def _file_path(self) -> Path:
        return self._data_dir / "secrets.json"

    def _get_file_values(self, profile_id: str) -> dict[str, str] | None:
        profile_values = self._load_file().get(profile_id)
        if profile_values is None:
            return None
        if not isinstance(profile_values, dict):
            raise SecretError("Stored credentials are invalid.")
        try:
            return _validate_secret_values(
                profile_values["webhook"],
                profile_values["app_id"],
                profile_values["app_secret"],
                profile_values.get("bot_secret", ""),
            )
        except (KeyError, SecretError) as error:
            raise SecretError("Stored credentials are invalid.") from error

    def _set_file_values(self, profile_id: str, values: dict[str, str]) -> None:
        profiles = self._load_file()
        profiles[profile_id] = values
        self._write_file(profiles)

    def _delete_file_values(self, profile_id: str) -> None:
        profiles = self._load_file()
        if profile_id in profiles:
            del profiles[profile_id]
            self._write_file(profiles)

    def _load_file(self) -> dict[str, dict[str, str]]:
        path = self._file_path
        if not path.exists():
            return {}
        try:
            with path.open("r", encoding="utf-8") as secrets_file:
                profiles = json.load(secrets_file)
        except (OSError, json.JSONDecodeError) as error:
            raise SecretError("Stored credentials could not be read.") from error
        if not isinstance(profiles, dict):
            raise SecretError("Stored credentials are invalid.")
        return profiles

    def _write_file(self, profiles: dict[str, dict[str, str]]) -> None:
        self._data_dir.mkdir(parents=True, exist_ok=True)
        temporary_path: Path | None = None
        try:
            descriptor, temporary_name = tempfile.mkstemp(
                dir=self._data_dir,
                prefix=".secrets.",
                suffix=".tmp",
            )
            temporary_path = Path(temporary_name)
            os.chmod(temporary_path, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8") as secrets_file:
                json.dump(profiles, secrets_file, ensure_ascii=False, indent=2)
                secrets_file.write("\n")
                secrets_file.flush()
                os.fsync(secrets_file.fileno())
            os.replace(temporary_path, self._file_path)
            os.chmod(self._file_path, 0o600)
            temporary_path = None
        finally:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)


def _load_keyring() -> Any | None:
    try:
        keyring = importlib.import_module("keyring")
    except ImportError:
        return None
    if all(callable(getattr(keyring, method, None)) for method in ("set_password", "get_password", "delete_password")):
        return keyring
    return None


def _validate_secret_values(
    webhook: object,
    app_id: object,
    app_secret: object,
    bot_secret: object,
) -> dict[str, str]:
    required = {"webhook": webhook, "app_id": app_id, "app_secret": app_secret}
    if any(not isinstance(value, str) or not value for value in required.values()):
        raise SecretError("Required credentials must be non-empty text.")
    if not isinstance(bot_secret, str):
        raise SecretError("Bot secret must be text.")
    return {
        "webhook": webhook,
        "app_id": app_id,
        "app_secret": app_secret,
        "bot_secret": bot_secret,
    }


def _required_string(value: object) -> str:
    if not isinstance(value, str) or not value:
        raise SecretError("Stored credentials are invalid.")
    return value
