"""Zero-dependency launcher for the project-local dealy-report runtime."""

from __future__ import annotations

import hashlib
import os
import subprocess
import sys
from pathlib import Path
from typing import Callable, Mapping, Sequence


ROOT = Path(__file__).resolve().parent
BOOTSTRAP_MARKER = "_DEALY_REPORT_BOOTSTRAPPED"


def venv_python(venv_dir: Path) -> Path:
    if os.name == "nt":
        return venv_dir / "Scripts" / "python.exe"
    return venv_dir / "bin" / "python"


def _lock_hash(lock_path: Path) -> str:
    return hashlib.sha256(lock_path.read_bytes()).hexdigest()


def _write_marker(path: Path, value: str) -> None:
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(value + "\n", encoding="ascii")
    os.replace(temporary, path)


def ensure_runtime(
    *,
    root: Path = ROOT,
    python_executable: str = sys.executable,
    run: Callable[..., subprocess.CompletedProcess[object]] = subprocess.run,
    version_info: Sequence[int] = sys.version_info,
) -> Path:
    if tuple(version_info[:2]) < (3, 11):
        raise RuntimeError("Python 3.11 or newer is required")
    runtime_dir = root / ".runtime"
    venv_dir = runtime_dir / "venv"
    python_path = venv_python(venv_dir)
    lock_path = root / "requirements.lock"
    marker_path = runtime_dir / "requirements.sha256"

    if not lock_path.is_file():
        raise RuntimeError("requirements.lock is missing")
    runtime_dir.mkdir(parents=True, exist_ok=True)
    if not python_path.is_file():
        run([python_executable, "-m", "venv", str(venv_dir)], check=True, shell=False)
    if not python_path.is_file():
        raise RuntimeError("project-local Python environment was not created")

    digest = _lock_hash(lock_path)
    installed_digest = marker_path.read_text(encoding="ascii").strip() if marker_path.is_file() else ""
    if installed_digest != digest:
        run(
            [str(python_path), "-m", "pip", "install", "--requirement", str(lock_path)],
            check=True,
            shell=False,
        )
        _write_marker(marker_path, digest)
    return python_path


def main(
    argv: Sequence[str] | None = None,
    *,
    environ: Mapping[str, str] | None = None,
    ensure: Callable[[], Path] | None = None,
    run: Callable[..., subprocess.CompletedProcess[object]] = subprocess.run,
    cli_module: object | None = None,
) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    environment = dict(os.environ if environ is None else environ)
    if environment.get(BOOTSTRAP_MARKER) == "1":
        if cli_module is None:
            from dealy_report import cli as cli_module

        return int(cli_module.main(arguments))

    python_path = ensure_runtime() if ensure is None else ensure()
    environment[BOOTSTRAP_MARKER] = "1"
    completed = run(
        [str(python_path), str(ROOT / "bootstrap.py"), *arguments],
        env=environment,
        shell=False,
    )
    return int(completed.returncode)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, RuntimeError, subprocess.SubprocessError) as error:
        print(f"bootstrap error: {error}", file=sys.stderr)
        raise SystemExit(1) from None
