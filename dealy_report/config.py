"""Profile configuration without credential material."""

from __future__ import annotations

import json
import os
import re
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


SCHEMA_VERSION = 1
_PROFILE_ID_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_SECRET_FIELDS = frozenset({"webhook", "app_id", "app_secret", "bot_secret"})
_SOURCE_BALANCES = frozenset({"domestic", "global", "balanced"})
_REASONING_EFFORTS = frozenset({"low", "medium", "high", "xhigh"})
SECTION_LABELS = {
    "model-platform": "模型与平台更新",
    "developer-open-source": "开发者工具与开源",
    "agent-engineering": "Agent 工程实践",
    "benchmarks-evaluation": "Benchmark 与评测信号",
}


class ConfigError(ValueError):
    """Raised when profile configuration is unsafe or unsupported."""


def validate_profile_id(profile_id: str) -> str:
    """Validate a profile identifier safe for filenames and credential keys."""
    if not isinstance(profile_id, str) or not _PROFILE_ID_PATTERN.fullmatch(profile_id):
        raise ConfigError("Profile ID must be a lowercase hyphenated slug.")
    return profile_id


@dataclass(frozen=True)
class ProfileConfig:
    schema_version: int = SCHEMA_VERSION
    profile_id: str = "daily-ai"
    task_name: str = "Daily AI Report"
    schedule_time: str = "08:30"
    timezone: str = "Asia/Shanghai"
    language: str = "zh-CN"
    audience: str = "developer/tech-lead"
    topics: tuple[str, ...] = ("AI engineering",)
    sections: tuple[str, ...] = tuple(SECTION_LABELS)
    source_balance: str = "balanced"
    model: str = "gpt-5.5"
    reasoning_effort: str = "high"
    service_tier: str = "fast"
    max_cards: int = 3

    def __post_init__(self) -> None:
        if self.schema_version != SCHEMA_VERSION:
            if isinstance(self.schema_version, int) and self.schema_version > SCHEMA_VERSION:
                raise ConfigError("Configuration schema version is newer than this application.")
            raise ConfigError("Configuration schema version is unsupported.")
        validate_profile_id(self.profile_id)
        if not isinstance(self.task_name, str) or not self.task_name.strip():
            raise ConfigError("Task name must not be empty.")
        for field_name in ("language", "audience", "model", "service_tier"):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value.strip():
                raise ConfigError(f"{field_name.replace('_', ' ').title()} must not be empty.")
        _validate_schedule_time(self.schedule_time)
        _validate_timezone(self.timezone)
        topics = _normalize_topics(self.topics)
        object.__setattr__(self, "topics", topics)
        sections = _normalize_sections(self.sections)
        object.__setattr__(self, "sections", sections)
        if self.source_balance not in _SOURCE_BALANCES:
            raise ConfigError("Source balance is unsupported.")
        if self.reasoning_effort not in _REASONING_EFFORTS:
            raise ConfigError("Reasoning effort is unsupported.")
        if isinstance(self.max_cards, bool) or not isinstance(self.max_cards, int) or not 1 <= self.max_cards <= 3:
            raise ConfigError("Max cards must be between 1 and 3.")

    def to_dict(self) -> dict[str, Any]:
        """Return the complete, non-secret JSON representation."""
        return {
            "schema_version": self.schema_version,
            "profile_id": self.profile_id,
            "task_name": self.task_name,
            "schedule_time": self.schedule_time,
            "timezone": self.timezone,
            "language": self.language,
            "audience": self.audience,
            "topics": list(self.topics),
            "sections": list(self.sections),
            "source_balance": self.source_balance,
            "model": self.model,
            "reasoning_effort": self.reasoning_effort,
            "service_tier": self.service_tier,
            "max_cards": self.max_cards,
        }

    @classmethod
    def from_dict(cls, values: Mapping[str, Any]) -> "ProfileConfig":
        if not isinstance(values, Mapping):
            raise ConfigError("Configuration JSON must contain an object.")
        keys = set(values)
        if keys & _SECRET_FIELDS:
            raise ConfigError("Credential fields are not allowed in configuration.")
        allowed_fields = set(cls.__dataclass_fields__)
        if keys - allowed_fields:
            raise ConfigError("Configuration contains unsupported fields.")
        return cls(**dict(values))

    def save(self, path: str | os.PathLike[str]) -> None:
        """Write configuration as UTF-8 JSON and atomically replace ``path``."""
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=target.parent,
                prefix=f".{target.name}.",
                suffix=".tmp",
                delete=False,
            ) as temporary_file:
                temporary_path = Path(temporary_file.name)
                json.dump(self.to_dict(), temporary_file, ensure_ascii=False, indent=2)
                temporary_file.write("\n")
                temporary_file.flush()
                os.fsync(temporary_file.fileno())
            os.replace(temporary_path, target)
            temporary_path = None
        finally:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)

    @classmethod
    def load(cls, path: str | os.PathLike[str]) -> "ProfileConfig":
        try:
            with Path(path).open("r", encoding="utf-8") as config_file:
                values = json.load(config_file)
        except (OSError, json.JSONDecodeError) as error:
            raise ConfigError("Could not read configuration JSON.") from error
        return cls.from_dict(values)


def _validate_schedule_time(value: str) -> None:
    if not isinstance(value, str):
        raise ConfigError("Schedule time must use HH:MM format.")
    match = re.fullmatch(r"([01]\d|2[0-3]):([0-5]\d)", value)
    if match is None or int(match.group(2)) % 5:
        raise ConfigError("Schedule time must be in five-minute HH:MM increments.")


def _validate_timezone(value: str) -> None:
    if not isinstance(value, str) or not value:
        raise ConfigError("Timezone must be an IANA timezone name.")
    try:
        ZoneInfo(value)
    except ZoneInfoNotFoundError as error:
        # Some Windows Python installations have neither a system timezone
        # database nor the optional tzdata package. Keep the mandated default
        # usable in that constrained stdlib-only environment.
        if value == "Asia/Shanghai":
            return
        raise ConfigError("Timezone must be an IANA timezone name.") from error


def _normalize_topics(value: object) -> tuple[str, ...]:
    if isinstance(value, str):
        raise ConfigError("Topics must be a non-empty sequence of text.")
    try:
        topics = tuple(value)  # type: ignore[arg-type]
    except TypeError as error:
        raise ConfigError("Topics must be a non-empty sequence of text.") from error
    if not topics or any(not isinstance(topic, str) or not topic.strip() for topic in topics):
        raise ConfigError("Topics must be a non-empty sequence of text.")
    return topics


def _normalize_sections(value: object) -> tuple[str, ...]:
    if isinstance(value, str):
        raise ConfigError("Sections must be a non-empty sequence.")
    try:
        sections = tuple(value)  # type: ignore[arg-type]
    except TypeError as error:
        raise ConfigError("Sections must be a non-empty sequence.") from error
    if not sections or any(not isinstance(section, str) or section not in SECTION_LABELS for section in sections):
        raise ConfigError("Sections contain an unsupported value.")
    if len(set(sections)) != len(sections):
        raise ConfigError("Sections must not contain duplicates.")
    return sections


def config_dir(
    *,
    env: Mapping[str, str] | None = None,
    platform_name: str | None = None,
    home: Path | None = None,
) -> Path:
    """Return the per-user configuration directory without creating it."""
    environment = os.environ if env is None else env
    platform = sys.platform if platform_name is None else platform_name
    user_home = Path.home() if home is None else Path(home)
    if platform.startswith("win"):
        return Path(environment.get("APPDATA", user_home / "AppData" / "Roaming")) / "dealy-report"
    if platform.startswith("darwin"):
        return user_home / "Library" / "Application Support" / "dealy-report"
    return Path(environment.get("XDG_CONFIG_HOME", user_home / ".config")) / "dealy-report"


def data_dir(
    *,
    env: Mapping[str, str] | None = None,
    platform_name: str | None = None,
    home: Path | None = None,
) -> Path:
    """Return the per-user data directory without creating it."""
    environment = os.environ if env is None else env
    platform = sys.platform if platform_name is None else platform_name
    user_home = Path.home() if home is None else Path(home)
    if platform.startswith("win"):
        return Path(
            environment.get(
                "LOCALAPPDATA",
                environment.get("APPDATA", user_home / "AppData" / "Local"),
            )
        ) / "dealy-report"
    if platform.startswith("darwin"):
        return user_home / "Library" / "Application Support" / "dealy-report"
    return Path(environment.get("XDG_DATA_HOME", user_home / ".local" / "share")) / "dealy-report"


get_config_dir = config_dir
get_data_dir = data_dir


def save_profile_config(config: ProfileConfig, path: str | os.PathLike[str]) -> None:
    config.save(path)


def load_profile_config(path: str | os.PathLike[str]) -> ProfileConfig:
    return ProfileConfig.load(path)
