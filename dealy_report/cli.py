"""Command-line setup and operation for dealy-report."""

from __future__ import annotations

import argparse
import getpass
import json
import os
import subprocess
import sys
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Callable, Sequence, TextIO

from assets.connectivity_test import write_connectivity_png

from .codex_install import CodexCheck, ensure_codex
from .config import ConfigError, ProfileConfig, config_dir, data_dir, validate_profile_id
from .delivery import FeishuCredentials, deliver_manifest
from .runner import RunOutcome, run_profile
from .schedulers import SchedulerError, install_scheduler, remove_scheduler, scheduler_name
from .secrets import SecretError, SecretStore
from .state import RunState, load_state
from scripts.feishu_card_sender import FeishuError


class CliError(RuntimeError):
    pass


@dataclass
class Dependencies:
    repo_path: Path
    config_root: Path
    data_root: Path
    python_path: Path
    home: Path
    input_func: Callable[[str], str]
    getpass_func: Callable[[str], str]
    stdout: TextIO
    stderr: TextIO
    secret_store_factory: Callable[..., Any]
    ensure_codex_func: Callable[..., CodexCheck]
    install_scheduler_func: Callable[..., dict[str, Any]]
    remove_scheduler_func: Callable[..., dict[str, Any]]
    run_profile_func: Callable[..., RunOutcome]
    deliver_manifest_func: Callable[..., Any]


CliDependencies = Dependencies


def default_dependencies() -> Dependencies:
    root = Path(__file__).resolve().parents[1]
    user_data = data_dir()
    return Dependencies(
        repo_path=root,
        config_root=config_dir(),
        data_root=user_data,
        python_path=Path(sys.executable),
        home=Path.home(),
        input_func=input,
        getpass_func=getpass.getpass,
        stdout=sys.stdout,
        stderr=sys.stderr,
        secret_store_factory=lambda allow_file_fallback=False: SecretStore(
            data_dir=user_data,
            allow_file_fallback=allow_file_fallback,
        ),
        ensure_codex_func=ensure_codex,
        install_scheduler_func=install_scheduler,
        remove_scheduler_func=remove_scheduler,
        run_profile_func=run_profile,
        deliver_manifest_func=deliver_manifest,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="dealy-report")
    parser.set_defaults(command="setup")
    subparsers = parser.add_subparsers(dest="command")
    subparsers.add_parser("setup", help="configure or update a profile")

    doctor = subparsers.add_parser("doctor", help="check a profile installation")
    doctor.add_argument("--profile", required=True)
    doctor.add_argument("--live", action="store_true", help="send the connectivity card")

    run = subparsers.add_parser("run", help="run a profile immediately")
    run.add_argument("--profile", required=True)
    run.add_argument("--now", action="store_true", required=True)

    dispatch = subparsers.add_parser("dispatch", help="run a profile when due")
    dispatch.add_argument("--profile", required=True)

    subparsers.add_parser("list", help="list configured profiles")

    remove = subparsers.add_parser("remove", help="remove profile configuration")
    remove.add_argument("--profile", required=True)
    remove.add_argument("--yes", action="store_true")
    return parser


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    args = build_parser().parse_args(argv)
    if args.command is None:
        args.command = "setup"
    return args


def _profile_path(deps: Dependencies, profile_id: str) -> Path:
    validate_profile_id(profile_id)
    return deps.config_root / "profiles" / f"{profile_id}.json"


def _backend_marker_path(deps: Dependencies, profile_id: str) -> Path:
    return deps.data_root / "schedulers" / f"{scheduler_name(profile_id)}.backend"


def _write_backend_marker(deps: Dependencies, profile_id: str, backend: str) -> None:
    if not backend.strip() or any(character in backend for character in "\r\n"):
        raise CliError("scheduler did not report a valid backend")
    path = _backend_marker_path(deps, profile_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(backend + "\n", encoding="utf-8")
    os.replace(temporary, path)


def _registered_backend(deps: Dependencies, profile_id: str) -> str | None:
    path = _backend_marker_path(deps, profile_id)
    if not path.is_file():
        return None
    backend = path.read_text(encoding="utf-8").strip()
    return backend or None


def _load_profile(deps: Dependencies, profile_id: str) -> ProfileConfig:
    path = _profile_path(deps, profile_id)
    if not path.is_file():
        raise CliError(f"profile '{profile_id}' is not configured")
    return ProfileConfig.load(path)


def _ask(deps: Dependencies, label: str, current: str) -> str:
    answer = deps.input_func(f"{label} [{current}]: ").strip()
    return answer or current


def _confirm(deps: Dependencies, prompt: str, *, default: bool) -> bool:
    suffix = " [Y/n]: " if default else " [y/N]: "
    answer = deps.input_func(prompt + suffix).strip().lower()
    if not answer:
        return default
    return answer in {"y", "yes"}


def _codex_check(deps: Dependencies, profile: ProfileConfig) -> CodexCheck:
    return deps.ensure_codex_func(
        deps.repo_path,
        profile.model,
        profile.reasoning_effort,
        profile.service_tier,
    )


def _require_codex(deps: Dependencies, profile: ProfileConfig) -> CodexCheck:
    check = _codex_check(deps, profile)
    if check.executable is None or not check.logged_in or not check.compatible:
        raise CliError(check.error or "Codex is not ready")
    return check


def _fallback_file_exists(deps: Dependencies) -> bool:
    return (deps.data_root / "secrets.json").is_file()


def _store_for_read(deps: Dependencies) -> Any:
    return deps.secret_store_factory(allow_file_fallback=_fallback_file_exists(deps))


def _credential_values(deps: Dependencies, profile_id: str) -> dict[str, str]:
    values = _store_for_read(deps).get(profile_id)
    if values is None:
        raise CliError(f"credentials for profile '{profile_id}' are unavailable")
    return values


def _credentials(values: dict[str, str]) -> FeishuCredentials:
    return FeishuCredentials(
        webhook=values["webhook"],
        app_id=values["app_id"],
        app_secret=values["app_secret"],
        bot_secret=values.get("bot_secret") or None,
    )


def _connectivity_manifest(image_path: Path) -> dict[str, Any]:
    return {
        "images": {"connectivity": str(image_path)},
        "cards": [
            {
                "schema": "2.0",
                "config": {"wide_screen_mode": True},
                "header": {
                    "title": {"tag": "plain_text", "content": "dealy-report connectivity"},
                    "template": "green",
                },
                "body": {
                    "elements": [
                        {"tag": "img", "img_key": "{{image:connectivity}}", "alt": {"tag": "plain_text", "content": "Connectivity test"}},
                        {"tag": "markdown", "content": "Configuration, image upload, and card delivery are connected."},
                    ]
                },
            }
        ],
    }


def _send_connectivity(deps: Dependencies, profile_id: str, values: dict[str, str]) -> None:
    image_path = deps.data_root / "connectivity" / f"{profile_id}.png"
    write_connectivity_png(image_path)
    try:
        deps.deliver_manifest_func(
            _connectivity_manifest(image_path),
            _credentials(values),
            allowed_local_roots=[deps.data_root],
        )
    except FeishuError as error:
        raise CliError(_redact(str(error), values)) from None


def _setup(args: argparse.Namespace, deps: Dependencies) -> int:
    profile_id = _ask(deps, "Profile ID", "daily-ai")
    path = _profile_path(deps, profile_id)
    profile = ProfileConfig.load(path) if path.is_file() else replace(ProfileConfig(), profile_id=profile_id)
    task_name = _ask(deps, "Profile name", profile.task_name)
    schedule_time = _ask(deps, "Schedule", profile.schedule_time)
    timezone = _ask(deps, "Timezone", profile.timezone)
    language = _ask(deps, "Language", profile.language)
    audience = _ask(deps, "Audience", profile.audience)
    topics_text = deps.input_func(f"Topics, comma-separated [{', '.join(profile.topics)}]: ").strip()
    source_balance = _ask(deps, "Source balance", profile.source_balance)
    model = _ask(deps, "Model", profile.model)
    reasoning_effort = _ask(deps, "Reasoning effort", profile.reasoning_effort)
    service_tier = _ask(deps, "Service tier", profile.service_tier)
    max_cards = int(_ask(deps, "Max cards", str(profile.max_cards)))
    profile = ProfileConfig(
        schema_version=profile.schema_version,
        profile_id=profile.profile_id,
        task_name=task_name,
        schedule_time=schedule_time,
        timezone=timezone,
        language=language,
        audience=audience,
        topics=tuple(item.strip() for item in topics_text.split(",") if item.strip()) if topics_text else profile.topics,
        source_balance=source_balance,
        model=model,
        reasoning_effort=reasoning_effort,
        service_tier=service_tier,
        max_cards=max_cards,
    )

    strict_store = deps.secret_store_factory(allow_file_fallback=False)
    existing: dict[str, str] = {}
    try:
        existing = strict_store.get(profile.profile_id) or {}
    except SecretError:
        pass
    if not existing and _fallback_file_exists(deps):
        existing = deps.secret_store_factory(allow_file_fallback=True).get(profile.profile_id) or {}
    values = {
        "webhook": deps.getpass_func("Feishu webhook (blank keeps existing): ").strip() or existing.get("webhook", ""),
        "app_id": deps.getpass_func("Feishu app ID (blank keeps existing): ").strip() or existing.get("app_id", ""),
        "app_secret": deps.getpass_func("Feishu app secret (blank keeps existing): ").strip() or existing.get("app_secret", ""),
        "bot_secret": deps.getpass_func("Feishu bot secret, optional (blank keeps existing): ").strip() or existing.get("bot_secret", ""),
    }
    if not values["webhook"] or not values["app_id"] or not values["app_secret"]:
        raise CliError("webhook, app ID, and app secret are required")

    _require_codex(deps, profile)
    store = strict_store
    try:
        store.set(profile.profile_id, **values)
    except SecretError:
        if not _confirm(deps, "Keyring unavailable. Store credentials in a protected local file?", default=False):
            raise CliError("credentials were not stored") from None
        store = deps.secret_store_factory(allow_file_fallback=True)
        store.set(profile.profile_id, **values)

    profile.save(path)
    if _confirm(deps, "Send an embedded-image connectivity test card?", default=True):
        _send_connectivity(deps, profile.profile_id, values)
    scheduler = deps.install_scheduler_func(
        profile.profile_id,
        deps.python_path,
        deps.repo_path,
        deps.data_root,
    )
    backend = scheduler.get("backend")
    if not isinstance(backend, str):
        raise CliError("scheduler did not report a backend")
    _write_backend_marker(deps, profile.profile_id, backend)
    print(f"Configured {profile.profile_id}; scheduler={backend}", file=deps.stdout)
    return 0


def _doctor(args: argparse.Namespace, deps: Dependencies) -> int:
    profile = _load_profile(deps, args.profile)
    print(f"config: ok ({profile.profile_id})", file=deps.stdout)
    values: dict[str, str] | None = None
    try:
        values = _credential_values(deps, profile.profile_id)
        print("secrets: available", file=deps.stdout)
    except (CliError, SecretError):
        print("secrets: unavailable", file=deps.stdout)
    check = _codex_check(deps, profile)
    print(f"Codex version: {check.version or 'unavailable'}", file=deps.stdout)
    print(f"Codex login: {'ok' if check.logged_in else 'failed'}", file=deps.stdout)
    print(f"structured output: {'ok' if check.compatible else 'failed'}", file=deps.stdout)
    registered_backend = _registered_backend(deps, profile.profile_id)
    if registered_backend is None:
        print("scheduler registration: missing", file=deps.stdout)
    else:
        print(f"registered backend: {registered_backend}", file=deps.stdout)
    scheduler = deps.install_scheduler_func(
        profile.profile_id,
        deps.python_path,
        deps.repo_path,
        deps.data_root,
        dry_run=True,
    )
    print(f"scheduler: {scheduler.get('backend', 'unavailable')} dry-run ok", file=deps.stdout)
    if args.live:
        if values is None:
            raise CliError("live connectivity test requires credentials")
        _send_connectivity(deps, profile.profile_id, values)
        print("live connectivity: sent", file=deps.stdout)
    return 0 if values is not None and check.logged_in and check.compatible and registered_backend is not None else 1


def _redact(message: str, values: dict[str, str]) -> str:
    clean = message
    for value in values.values():
        if value:
            clean = clean.replace(value, "[redacted]")
    return clean


def _run(args: argparse.Namespace, deps: Dependencies, *, force: bool) -> int:
    profile = _load_profile(deps, args.profile)
    values = _credential_values(deps, profile.profile_id)
    check = _require_codex(deps, profile)
    outcome = deps.run_profile_func(
        profile=profile,
        repo_path=deps.repo_path,
        data_root=deps.data_root,
        codex_path=check.executable,
        credentials=_credentials(values),
        force=force,
    )
    fields = [f"status={outcome.status}"]
    if outcome.report_path:
        fields.append(f"report={outcome.report_path}")
    fields.extend((f"cards={outcome.delivered_cards}", f"images={outcome.uploaded_images}"))
    print(" ".join(fields), file=deps.stdout)
    if outcome.error:
        print(f"error: {_redact(outcome.error, values)}", file=deps.stderr)
    return 1 if outcome.status in {"failed", "send_failed", "uncertain"} else 0


def _list_profiles(args: argparse.Namespace, deps: Dependencies) -> int:
    profiles_dir = deps.config_root / "profiles"
    paths = sorted(profiles_dir.glob("*.json")) if profiles_dir.is_dir() else []
    if not paths:
        print("No profiles configured.", file=deps.stdout)
        return 0
    for path in paths:
        try:
            profile = ProfileConfig.load(path)
            state_path = deps.data_root / "profiles" / profile.profile_id / "state.json"
            state = load_state(state_path) if state_path.is_file() else RunState()
            print(
                f"{profile.profile_id}\t{profile.task_name}\t{profile.schedule_time} {profile.timezone}\t{state.phase}\t{state.date or '-'}",
                file=deps.stdout,
            )
        except (ConfigError, OSError, ValueError, json.JSONDecodeError) as error:
            print(f"warning: could not read {path.name}: {error}", file=deps.stderr)
    return 0


def _remove(args: argparse.Namespace, deps: Dependencies) -> int:
    path = _profile_path(deps, args.profile)
    if not path.is_file():
        raise CliError(f"profile '{args.profile}' is not configured")
    if not args.yes and not _confirm(deps, f"Remove profile '{args.profile}'?", default=False):
        print("Removal cancelled.", file=deps.stdout)
        return 0
    deps.remove_scheduler_func(args.profile, data_dir=deps.data_root)
    _store_for_read(deps).delete_profile(args.profile)
    path.unlink()
    _backend_marker_path(deps, args.profile).unlink(missing_ok=True)
    print(f"Removed {args.profile}; archived reports were kept.", file=deps.stdout)
    return 0


def _warn_legacy_automation(deps: Dependencies) -> None:
    path = deps.home / ".codex" / "automations" / "ai" / "automation.toml"
    if not path.is_file():
        return
    try:
        content = path.read_text(encoding="utf-8").casefold().replace("\\", "/")
    except OSError:
        return
    target = str(deps.repo_path.resolve()).casefold().replace("\\", "/")
    if target in content:
        print(f"warning: legacy Codex automation targets this repository: {path}", file=deps.stderr)


def main(argv: Sequence[str] | None = None, *, deps: Dependencies | None = None) -> int:
    dependencies = default_dependencies() if deps is None else deps
    args = parse_args(argv)
    _warn_legacy_automation(dependencies)
    handlers = {
        "setup": _setup,
        "doctor": _doctor,
        "list": _list_profiles,
        "remove": _remove,
    }
    try:
        if args.command == "run":
            return _run(args, dependencies, force=True)
        if args.command == "dispatch":
            return _run(args, dependencies, force=False)
        return handlers[args.command](args, dependencies)
    except (CliError, ConfigError, SecretError, SchedulerError, FeishuError, OSError, ValueError, subprocess.SubprocessError) as error:
        print(f"error: {error}", file=dependencies.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
