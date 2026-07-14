from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any, Callable

from .report import Report, report_json_schema, validate_report


class CodexError(RuntimeError):
    pass


def build_codex_command(
    executable: Path,
    repo_path: Path,
    model: str,
    reasoning_effort: str,
    service_tier: str,
    schema_path: Path,
    output_path: Path,
) -> list[str]:
    return [
        str(executable),
        "-c",
        f'service_tier="{service_tier}"',
        "-c",
        f'model_reasoning_effort="{reasoning_effort}"',
        "--search",
        "-a",
        "never",
        "exec",
        "-m",
        model,
        "--ephemeral",
        "--sandbox",
        "read-only",
        "--output-schema",
        str(schema_path),
        "--output-last-message",
        str(output_path),
        "--color",
        "never",
        "-C",
        str(repo_path),
        "-",
    ]


def generate_report(
    executable: Path,
    repo_path: Path,
    model: str,
    reasoning_effort: str,
    service_tier: str,
    prompt: str,
    work_dir: Path,
    run: Callable[..., Any] = subprocess.run,
    timeout_seconds: int = 1800,
) -> Report:
    work_dir.mkdir(parents=True, exist_ok=True)
    schema_path = work_dir / "report.schema.json"
    output_path = work_dir / "report.json"
    schema_path.write_text(json.dumps(report_json_schema(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    output_path.unlink(missing_ok=True)
    command = build_codex_command(
        executable=executable,
        repo_path=repo_path,
        model=model,
        reasoning_effort=reasoning_effort,
        service_tier=service_tier,
        schema_path=schema_path,
        output_path=output_path,
    )
    result = run(
        command,
        input=prompt,
        text=True,
        capture_output=True,
        timeout=timeout_seconds,
        shell=False,
        cwd=str(repo_path),
    )
    if result.returncode != 0:
        details = (result.stderr or result.stdout or "unknown Codex error").strip()[-2000:]
        raise CodexError(f"Codex report generation failed: {details}")
    if not output_path.is_file():
        raise CodexError("Codex completed without producing the structured report")
    try:
        payload = json.loads(output_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise CodexError("Codex produced invalid JSON") from error
    return validate_report(payload)

