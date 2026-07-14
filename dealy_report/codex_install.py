"""Locate and validate Codex without changing a global installation."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .codex_runtime import build_codex_command


CODEX_PACKAGE = "@openai/codex@0.144.4"


@dataclass(frozen=True)
class CodexCheck:
    executable: Path | None
    version: str | None
    logged_in: bool
    compatible: bool
    error: str | None = None


def local_codex_path(repo_path: Path) -> Path:
    bin_dir = repo_path / ".runtime" / "codex" / "node_modules" / ".bin"
    command = bin_dir / "codex.cmd"
    executable = bin_dir / "codex"
    if command.is_file():
        return command
    if executable.is_file():
        return executable
    return command if os.name == "nt" else executable


def find_codex(repo_path: Path, *, which: Callable[[str], str | None] = shutil.which) -> Path | None:
    local = local_codex_path(repo_path)
    if local.is_file():
        return local
    global_path = which("codex")
    return Path(global_path) if global_path else None


def smoke_test(
    executable: Path,
    repo_path: Path,
    *,
    model: str,
    reasoning_effort: str,
    service_tier: str,
    run: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> bool:
    schema = {
        "type": "object",
        "properties": {"ok": {"type": "boolean"}},
        "required": ["ok"],
        "additionalProperties": False,
    }
    with tempfile.TemporaryDirectory(prefix="dealy-report-codex-") as directory:
        work_dir = Path(directory)
        schema_path = work_dir / "smoke.schema.json"
        output_path = work_dir / "smoke.json"
        schema_path.write_text(json.dumps(schema, separators=(",", ":")) + "\n", encoding="utf-8")
        command = build_codex_command(
            executable=executable,
            repo_path=repo_path,
            model=model,
            reasoning_effort=reasoning_effort,
            service_tier=service_tier,
            schema_path=schema_path,
            output_path=output_path,
        )
        completed = run(
            command,
            input='Return exactly {"ok": true}.',
            text=True,
            capture_output=True,
            timeout=120,
            shell=False,
            cwd=str(repo_path),
        )
        if completed.returncode != 0 or not output_path.is_file():
            return False
        try:
            return json.loads(output_path.read_text(encoding="utf-8")) == {"ok": True}
        except (OSError, json.JSONDecodeError):
            return False


def inspect_codex(
    executable: Path,
    *,
    repo_path: Path,
    model: str,
    reasoning_effort: str,
    service_tier: str,
    run: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
    result_type: type[CodexCheck] = CodexCheck,
) -> CodexCheck:
    try:
        version_result = run(
            [str(executable), "--version"],
            capture_output=True,
            text=True,
            timeout=30,
            shell=False,
        )
        version = (version_result.stdout or version_result.stderr or "").strip() or None
        if version_result.returncode != 0:
            return result_type(executable, version, False, False, "Codex version check failed")
        login_result = run(
            [str(executable), "login", "status"],
            capture_output=True,
            text=True,
            timeout=30,
            shell=False,
        )
        logged_in = login_result.returncode == 0
        if not logged_in:
            return result_type(executable, version, False, False, "Codex login is required")
        compatible = smoke_test(
            executable,
            repo_path,
            model=model,
            reasoning_effort=reasoning_effort,
            service_tier=service_tier,
            run=run,
        )
        return result_type(
            executable,
            version,
            True,
            compatible,
            None if compatible else "Codex structured-output smoke test failed",
        )
    except (OSError, subprocess.SubprocessError) as error:
        return result_type(executable, None, False, False, f"Codex check failed: {error}")


def ensure_codex(
    repo_path: Path,
    model: str,
    reasoning_effort: str,
    service_tier: str,
    *,
    run: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
    which: Callable[[str], str | None] = shutil.which,
    inspect: Callable[..., CodexCheck] = inspect_codex,
) -> CodexCheck:
    candidate = find_codex(repo_path, which=which)
    previous: CodexCheck | None = None
    if candidate is not None:
        previous = inspect(
            candidate,
            repo_path=repo_path,
            model=model,
            reasoning_effort=reasoning_effort,
            service_tier=service_tier,
            run=run,
            result_type=CodexCheck,
        )
        if previous.compatible:
            return previous

    npm = which("npm") or which("npm.cmd")
    if not npm:
        return previous or CodexCheck(None, None, False, False, "Codex is unavailable and npm was not found")

    prefix = repo_path / ".runtime" / "codex"
    prefix.mkdir(parents=True, exist_ok=True)
    try:
        run(
            [npm, "install", "--prefix", str(prefix), CODEX_PACKAGE, "--no-save"],
            check=True,
            capture_output=True,
            text=True,
            shell=False,
            cwd=str(repo_path),
        )
    except (OSError, subprocess.SubprocessError) as error:
        return CodexCheck(None, None, False, False, f"Project-local Codex install failed: {error}")

    local = local_codex_path(repo_path)
    if not local.is_file():
        return CodexCheck(None, None, False, False, "Project-local Codex executable was not installed")
    return inspect(
        local,
        repo_path=repo_path,
        model=model,
        reasoning_effort=reasoning_effort,
        service_tier=service_tier,
        run=run,
        result_type=CodexCheck,
    )
