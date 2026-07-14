"""Cross-platform installation helpers for the report dispatcher scheduler."""

from __future__ import annotations

import ntpath
import os
import platform
import plistlib
import posixpath
import re
import shlex
import shutil
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any


_PROFILE_SLUG = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*", re.ASCII)
_TASK_NAMESPACE = "http://schemas.microsoft.com/windows/2004/02/mit/task"


class SchedulerError(ValueError):
    """Raised when a scheduler cannot be configured safely."""


def scheduler_name(profile_id: str) -> str:
    """Return the stable, safe scheduler name for a profile."""
    if not isinstance(profile_id, str) or not _PROFILE_SLUG.fullmatch(profile_id):
        raise SchedulerError("profile_id must be a lowercase alphanumeric slug separated by single hyphens")
    return f"dealy-report-{profile_id}"


def build_windows_task_xml(profile_id: str, python_path: str | os.PathLike[str], repo_path: str | os.PathLike[str]) -> str:
    """Build an importable Task Scheduler task definition."""
    name = scheduler_name(profile_id)
    python_text = _absolute_path(python_path, "python_path")
    repo_text = _absolute_path(repo_path, "repo_path")
    bootstrap_path = ntpath.join(repo_text, "bootstrap.py")

    ET.register_namespace("", _TASK_NAMESPACE)
    task = ET.Element(_task_tag("Task"), {"version": "1.4"})
    registration = ET.SubElement(task, _task_tag("RegistrationInfo"))
    ET.SubElement(registration, _task_tag("Description")).text = f"Dispatch dealy report profile {profile_id} every five minutes."

    triggers = ET.SubElement(task, _task_tag("Triggers"))
    trigger = ET.SubElement(triggers, _task_tag("TimeTrigger"))
    ET.SubElement(trigger, _task_tag("StartBoundary")).text = "2000-01-01T00:00:00"
    repetition = ET.SubElement(trigger, _task_tag("Repetition"))
    ET.SubElement(repetition, _task_tag("Interval")).text = "PT5M"
    ET.SubElement(repetition, _task_tag("StopAtDurationEnd")).text = "false"

    principals = ET.SubElement(task, _task_tag("Principals"))
    principal = ET.SubElement(principals, _task_tag("Principal"), {"id": "CurrentUser"})
    ET.SubElement(principal, _task_tag("LogonType")).text = "InteractiveToken"
    ET.SubElement(principal, _task_tag("RunLevel")).text = "LeastPrivilege"

    settings = ET.SubElement(task, _task_tag("Settings"))
    ET.SubElement(settings, _task_tag("MultipleInstancesPolicy")).text = "IgnoreNew"
    ET.SubElement(settings, _task_tag("StartWhenAvailable")).text = "true"
    ET.SubElement(settings, _task_tag("RunOnlyIfNetworkAvailable")).text = "true"
    ET.SubElement(settings, _task_tag("Enabled")).text = "true"
    ET.SubElement(settings, _task_tag("Hidden")).text = "false"

    actions = ET.SubElement(task, _task_tag("Actions"), {"Context": "CurrentUser"})
    execute = ET.SubElement(actions, _task_tag("Exec"))
    ET.SubElement(execute, _task_tag("Command")).text = python_text
    ET.SubElement(execute, _task_tag("Arguments")).text = (
        f"{_windows_quote(bootstrap_path)} dispatch --profile {_windows_quote(profile_id)}"
    )

    return ET.tostring(task, encoding="unicode", xml_declaration=True)


def build_macos_plist(
    profile_id: str,
    python_path: str | os.PathLike[str],
    repo_path: str | os.PathLike[str],
    data_dir: str | os.PathLike[str] | None = None,
) -> bytes:
    """Build a LaunchAgent plist for a report profile."""
    name = scheduler_name(profile_id)
    label = _macos_label(profile_id)
    python_text = _absolute_path(python_path, "python_path")
    repo_text = _absolute_path(repo_path, "repo_path")
    data_text = _data_directory(data_dir, repo_text, posixpath)
    payload = {
        "Label": label,
        "ProgramArguments": [python_text, posixpath.join(repo_text, "bootstrap.py"), "dispatch", "--profile", profile_id],
        "RunAtLoad": True,
        "StartInterval": 300,
        "StandardOutPath": posixpath.join(data_text, "logs", f"{name}.out.log"),
        "StandardErrorPath": posixpath.join(data_text, "logs", f"{name}.err.log"),
    }
    return plistlib.dumps(payload, fmt=plistlib.FMT_XML, sort_keys=True)


def build_systemd_units(
    profile_id: str,
    python_path: str | os.PathLike[str],
    repo_path: str | os.PathLike[str],
    data_dir: str | os.PathLike[str] | None = None,
) -> tuple[str, str]:
    """Build the systemd user service and timer unit texts."""
    name = scheduler_name(profile_id)
    python_text = _absolute_path(python_path, "python_path")
    repo_text = _absolute_path(repo_path, "repo_path")
    _data_directory(data_dir, repo_text, posixpath)
    bootstrap_path = posixpath.join(repo_text, "bootstrap.py")
    service = "\n".join(
        (
            "[Unit]",
            f"Description=Dealy report dispatcher ({profile_id})",
            "",
            "[Service]",
            "Type=oneshot",
            f"WorkingDirectory={_systemd_quote(repo_text)}",
            f"ExecStart={_systemd_quote(python_text)} {_systemd_quote(bootstrap_path)} dispatch --profile {profile_id}",
            "",
        )
    )
    timer = "\n".join(
        (
            "[Unit]",
            f"Description=Run {name} every five minutes",
            "",
            "[Timer]",
            "OnBootSec=2m",
            "OnUnitActiveSec=5m",
            "Persistent=true",
            f"Unit={name}.service",
            "",
            "[Install]",
            "WantedBy=timers.target",
            "",
        )
    )
    return service, timer


def build_cron_line(
    profile_id: str,
    python_path: str | os.PathLike[str],
    repo_path: str | os.PathLike[str],
    data_dir: str | os.PathLike[str] | None = None,
) -> str:
    """Build the idempotently replaceable cron entry for a profile."""
    name = scheduler_name(profile_id)
    python_text = _absolute_path(python_path, "python_path")
    repo_text = _absolute_path(repo_path, "repo_path")
    data_text = _data_directory(data_dir, repo_text, posixpath)
    bootstrap_path = posixpath.join(repo_text, "bootstrap.py")
    stdout_path = posixpath.join(data_text, "logs", f"{name}.out.log")
    stderr_path = posixpath.join(data_text, "logs", f"{name}.err.log")
    command = " ".join(
        (
            shlex.quote(python_text),
            shlex.quote(bootstrap_path),
            "dispatch",
            "--profile",
            shlex.quote(profile_id),
            ">>",
            shlex.quote(stdout_path),
            "2>>",
            shlex.quote(stderr_path),
        )
    )
    return f"*/5 * * * * {command} # dealy-report:{profile_id}"


def install_scheduler(
    profile_id: str,
    python_path: str | os.PathLike[str],
    repo_path: str | os.PathLike[str],
    data_dir: str | os.PathLike[str],
    platform_name: str | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Install the best available scheduler for the current platform."""
    backend = _select_backend(platform_name)
    name = scheduler_name(profile_id)
    data_path = Path(data_dir)
    description = _description("install", backend, name, data_path)
    if dry_run:
        return description

    if backend == "windows-task":
        definition_path = data_path / "schedulers" / f"{name}.xml"
        definition_path.parent.mkdir(parents=True, exist_ok=True)
        definition_path.write_text(build_windows_task_xml(profile_id, python_path, repo_path), encoding="utf-8")
        command = ["schtasks", "/Create", "/TN", name, "/XML", str(definition_path), "/F"]
        subprocess.run(command, check=True)
    elif backend == "launchctl":
        plist_path = data_path / "schedulers" / f"{_macos_label(profile_id)}.plist"
        plist_path.parent.mkdir(parents=True, exist_ok=True)
        (data_path / "logs").mkdir(parents=True, exist_ok=True)
        plist_path.write_bytes(build_macos_plist(profile_id, python_path, repo_path, data_dir))
        command = ["launchctl", "bootstrap", _launchctl_domain(), str(plist_path)]
        subprocess.run(command, check=True)
    elif backend == "systemd":
        service_path, timer_path = _systemd_paths(name)
        service_path.parent.mkdir(parents=True, exist_ok=True)
        service_text, timer_text = build_systemd_units(profile_id, python_path, repo_path, data_dir)
        service_path.write_text(service_text, encoding="utf-8")
        timer_path.write_text(timer_text, encoding="utf-8")
        systemctl = _systemctl_path()
        subprocess.run([systemctl, "--user", "daemon-reload"], check=True)
        subprocess.run([systemctl, "--user", "enable", "--now", timer_path.name], check=True)
    else:
        (data_path / "logs").mkdir(parents=True, exist_ok=True)
        _replace_cron_entry(profile_id, build_cron_line(profile_id, python_path, repo_path, data_dir))

    return description


def remove_scheduler(
    profile_id: str,
    python_path_or_data_dir: str | os.PathLike[str] | None = None,
    repo_path: str | os.PathLike[str] | None = None,
    data_dir: str | os.PathLike[str] | None = None,
    platform_name: str | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Remove a profile scheduler without failing when it is already absent.

    The optional ``python_path_or_data_dir`` and ``repo_path`` retain a call shape
    parallel to :func:`install_scheduler`; removal only needs ``data_dir``.
    """
    name = scheduler_name(profile_id)
    resolved_data_dir = _resolve_remove_data_dir(python_path_or_data_dir, repo_path, data_dir)
    backend = _select_backend(platform_name)
    data_path = Path(resolved_data_dir)
    description = _description("remove", backend, name, data_path)
    if dry_run:
        return description

    if backend == "windows-task":
        completed = subprocess.run(
            ["schtasks", "/Delete", "/TN", name, "/F"],
            capture_output=True,
            text=True,
            check=False,
        )
        _raise_unexpected_removal_failure(completed, "schtasks")
        definition_path = data_path / "schedulers" / f"{name}.xml"
        if definition_path.exists():
            definition_path.unlink()
    elif backend == "launchctl":
        plist_path = data_path / "schedulers" / f"{_macos_label(profile_id)}.plist"
        if plist_path.exists():
            completed = subprocess.run(
                ["launchctl", "bootout", _launchctl_domain(), str(plist_path)],
                capture_output=True,
                text=True,
                check=False,
            )
            _raise_unexpected_removal_failure(completed, "launchctl")
            plist_path.unlink()
    elif backend == "systemd":
        service_path, timer_path = _systemd_paths(name)
        if service_path.exists() or timer_path.exists():
            systemctl = _systemctl_path()
            completed = subprocess.run(
                [systemctl, "--user", "disable", "--now", timer_path.name],
                capture_output=True,
                text=True,
                check=False,
            )
            _raise_unexpected_removal_failure(completed, "systemctl")
            if service_path.exists():
                service_path.unlink()
            if timer_path.exists():
                timer_path.unlink()
            subprocess.run([systemctl, "--user", "daemon-reload"], check=True)
    else:
        _remove_cron_entry(profile_id)

    return description


def _task_tag(name: str) -> str:
    return f"{{{_TASK_NAMESPACE}}}{name}"


def _macos_label(profile_id: str) -> str:
    scheduler_name(profile_id)
    return f"com.captainjee.dealy-report.{profile_id}"


def _absolute_path(value: str | os.PathLike[str], field_name: str) -> str:
    text = os.fspath(value)
    if not isinstance(text, str) or not text or any(character in text for character in ("\x00", "\r", "\n")):
        raise SchedulerError(f"{field_name} must be a non-empty path without control characters")
    if not PureWindowsPath(text).is_absolute() and not PurePosixPath(text).is_absolute():
        raise SchedulerError(f"{field_name} must be an absolute path")
    return text


def _data_directory(
    data_dir: str | os.PathLike[str] | None,
    repo_path: str,
    path_module: Any,
) -> str:
    if data_dir is None:
        return path_module.join(repo_path, ".dealy-report")
    return _absolute_path(data_dir, "data_dir")


def _windows_quote(value: str) -> str:
    escaped: list[str] = ['"']
    backslashes = 0
    for character in value:
        if character == "\\":
            backslashes += 1
        elif character == '"':
            escaped.append("\\" * (backslashes * 2 + 1))
            escaped.append('"')
            backslashes = 0
        else:
            escaped.append("\\" * backslashes)
            escaped.append(character)
            backslashes = 0
    escaped.append("\\" * (backslashes * 2))
    escaped.append('"')
    return "".join(escaped)


def _systemd_quote(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _select_backend(platform_name: str | None) -> str:
    normalized = (platform_name or platform.system()).strip().lower()
    if normalized in {"windows", "win32", "cygwin"}:
        return "windows-task"
    if normalized in {"darwin", "macos", "macosx"}:
        return "launchctl"
    if normalized in {"linux", "linux2"}:
        return "systemd" if shutil.which("systemctl") else "cron"
    raise SchedulerError(f"unsupported platform: {platform_name or platform.system()}")


def _description(operation: str, backend: str, name: str, data_dir: Path) -> dict[str, Any]:
    return {
        "operation": operation,
        "backend": backend,
        "scheduler_name": name,
        "data_dir": str(data_dir),
    }


def _launchctl_domain() -> str:
    try:
        user_id = os.getuid()
    except AttributeError as error:
        raise SchedulerError("launchctl requires a POSIX user identifier") from error
    return f"gui/{user_id}"


def _systemd_paths(name: str) -> tuple[Path, Path]:
    unit_directory = Path.home() / ".config" / "systemd" / "user"
    return unit_directory / f"{name}.service", unit_directory / f"{name}.timer"


def _systemctl_path() -> str:
    systemctl = shutil.which("systemctl")
    if not systemctl:
        raise SchedulerError("systemctl is not available")
    return systemctl


def _replace_cron_entry(profile_id: str, cron_line: str) -> None:
    current = _read_crontab()
    retained = [line for line in current.splitlines() if not _has_cron_marker(line, profile_id)]
    retained.append(cron_line)
    subprocess.run(["crontab", "-"], input="\n".join(retained) + "\n", text=True, check=True)


def _remove_cron_entry(profile_id: str) -> None:
    current = _read_crontab()
    retained = [line for line in current.splitlines() if not _has_cron_marker(line, profile_id)]
    if len(retained) != len(current.splitlines()):
        subprocess.run(["crontab", "-"], input="\n".join(retained) + "\n", text=True, check=True)


def _read_crontab() -> str:
    completed = subprocess.run(["crontab", "-l"], capture_output=True, text=True, check=False)
    if completed.returncode == 0:
        return completed.stdout
    if completed.returncode == 1:
        return ""
    detail = completed.stderr.strip() or completed.stdout.strip() or f"exit code {completed.returncode}"
    raise SchedulerError(f"could not read crontab: {detail}")


def _has_cron_marker(line: str, profile_id: str) -> bool:
    return line.rstrip().endswith(f"# dealy-report:{profile_id}")


def _raise_unexpected_removal_failure(completed: subprocess.CompletedProcess[str], command_name: str) -> None:
    if completed.returncode == 0:
        return
    detail = f"{completed.stdout}\n{completed.stderr}".lower()
    if any(fragment in detail for fragment in ("cannot find", "could not find", "not found", "does not exist", "not exist")):
        return
    raise SchedulerError(f"{command_name} removal failed: {detail.strip() or completed.returncode}")


def _resolve_remove_data_dir(
    python_path_or_data_dir: str | os.PathLike[str] | None,
    repo_path: str | os.PathLike[str] | None,
    data_dir: str | os.PathLike[str] | None,
) -> str | os.PathLike[str]:
    if data_dir is not None:
        return data_dir
    if repo_path is None and python_path_or_data_dir is not None:
        return python_path_or_data_dir
    raise SchedulerError("remove_scheduler requires data_dir")
