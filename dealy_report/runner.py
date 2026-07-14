from __future__ import annotations

import json
import os
from contextlib import contextmanager
from dataclasses import asdict, dataclass, replace
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Iterator
from zoneinfo import ZoneInfo

from .codex_runtime import CodexError, generate_report
from .config import ProfileConfig
from .delivery import DeliveryResult, FeishuCredentials, deliver_manifest
from .prompt import build_daily_prompt
from .renderer import render_feishu_manifest, render_markdown
from .report import Report, ReportValidationError
from .state import RunState, load_state, next_retry_at, save_state, should_run
from scripts.feishu_card_sender import FeishuDeliveryUncertain, FeishuError


class AlreadyRunningError(RuntimeError):
    pass


@dataclass(frozen=True)
class RunOutcome:
    status: str
    report_path: str | None = None
    delivered_cards: int = 0
    uploaded_images: int = 0
    error: str | None = None


def _atomic_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(content, encoding="utf-8")
    os.replace(temporary, path)


def _atomic_json(path: Path, value: Any) -> None:
    _atomic_text(path, json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def _clean_error(error: BaseException, credentials: FeishuCredentials) -> str:
    message = str(error)
    for value in (credentials.webhook, credentials.app_id, credentials.app_secret, credentials.bot_secret):
        if value:
            message = message.replace(value, "[redacted]")
    return message[-2000:]


@contextmanager
def profile_lock(path: Path) -> Iterator[None]:
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_file = path.open("a+b")
    try:
        if os.name == "nt":
            import msvcrt

            lock_file.seek(0, os.SEEK_END)
            if lock_file.tell() == 0:
                lock_file.write(b"0")
                lock_file.flush()
            lock_file.seek(0)
            try:
                msvcrt.locking(lock_file.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError as error:
                raise AlreadyRunningError("profile run is already active") from error
        else:
            import fcntl

            try:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as error:
                raise AlreadyRunningError("profile run is already active") from error
        yield
    finally:
        try:
            if os.name == "nt":
                import msvcrt

                lock_file.seek(0)
                msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
        except OSError:
            pass
        lock_file.close()


def run_profile(
    profile: ProfileConfig,
    repo_path: Path,
    data_root: Path,
    codex_path: Path,
    credentials: FeishuCredentials,
    now: datetime | None = None,
    force: bool = False,
    generate: Callable[..., Report] = generate_report,
    deliver: Callable[..., DeliveryResult] = deliver_manifest,
) -> RunOutcome:
    zone = ZoneInfo(profile.timezone)
    current = (now or datetime.now(zone)).astimezone(zone)
    today = current.date().isoformat()
    profile_root = data_root / "profiles" / profile.profile_id
    state_path = profile_root / "state.json"

    try:
        with profile_lock(profile_root / "run.lock"):
            state = load_state(state_path)
            if force:
                state = RunState(date=today)
            elif not should_run(state, profile.schedule_time, profile.timezone, current):
                return RunOutcome(status="skipped", report_path=state.report_path, delivered_cards=state.delivered_cards)
            elif state.date != today:
                state = RunState(date=today)

            manifest_path = Path(state.manifest_path) if state.manifest_path else None
            can_resume = state.phase in {"generated", "sending", "send_failed"} and manifest_path is not None and manifest_path.is_file()
            if can_resume:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            else:
                attempts = state.attempts + 1
                state = replace(
                    state,
                    date=today,
                    phase="generating",
                    attempts=attempts,
                    next_retry_at=None,
                    delivered_cards=0,
                    delivery_attempts=0,
                    report_path=None,
                    manifest_path=None,
                    error=None,
                )
                save_state(state_path, state)
                report_dir = profile_root / "reports" / today
                try:
                    report = generate(
                        executable=codex_path,
                        repo_path=repo_path,
                        model=profile.model,
                        reasoning_effort=profile.reasoning_effort,
                        service_tier=profile.service_tier,
                        prompt=build_daily_prompt(profile, today),
                        work_dir=report_dir,
                    )
                except (CodexError, ReportValidationError, OSError, ValueError) as error:
                    retry = next_retry_at(current, attempts)
                    state = replace(
                        state,
                        phase="failed",
                        next_retry_at=retry.isoformat() if retry else None,
                        error=_clean_error(error, credentials),
                    )
                    save_state(state_path, state)
                    return RunOutcome(status="failed", error=state.error)

                report_json_path = report_dir / "report.json"
                markdown_path = report_dir / "report.md"
                manifest_path = report_dir / "manifest.json"
                manifest = render_feishu_manifest(report, max_cards=profile.max_cards)
                _atomic_json(report_json_path, asdict(report))
                _atomic_text(markdown_path, render_markdown(report))
                _atomic_json(manifest_path, manifest)
                state = replace(
                    state,
                    phase="generated",
                    report_path=str(markdown_path),
                    manifest_path=str(manifest_path),
                    error=None,
                )
                save_state(state_path, state)

            delivery_attempts = state.delivery_attempts + 1
            state = replace(state, phase="sending", delivery_attempts=delivery_attempts, next_retry_at=None, error=None)
            save_state(state_path, state)

            def record_progress(delivered_cards: int) -> None:
                nonlocal state
                state = replace(state, delivered_cards=delivered_cards)
                save_state(state_path, state)

            try:
                result = deliver(
                    manifest,
                    credentials,
                    allowed_local_roots=[repo_path, profile_root],
                    start_card=state.delivered_cards,
                    on_card_sent=record_progress,
                )
            except FeishuDeliveryUncertain as error:
                state = replace(state, phase="uncertain", error=_clean_error(error, credentials))
                save_state(state_path, state)
                return RunOutcome(
                    status="uncertain",
                    report_path=state.report_path,
                    delivered_cards=state.delivered_cards,
                    error=state.error,
                )
            except FeishuError as error:
                retry = next_retry_at(current, delivery_attempts)
                state = replace(
                    state,
                    phase="send_failed",
                    next_retry_at=retry.isoformat() if retry else None,
                    error=_clean_error(error, credentials),
                )
                save_state(state_path, state)
                return RunOutcome(
                    status="send_failed",
                    report_path=state.report_path,
                    delivered_cards=state.delivered_cards,
                    error=state.error,
                )

            state = replace(
                state,
                phase="sent",
                delivered_cards=result.delivered_cards,
                next_retry_at=None,
                error=None,
            )
            save_state(state_path, state)
            return RunOutcome(
                status="sent",
                report_path=state.report_path,
                delivered_cards=result.delivered_cards,
                uploaded_images=result.uploaded_images,
            )
    except AlreadyRunningError:
        return RunOutcome(status="running")

