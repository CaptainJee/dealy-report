from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo


@dataclass(frozen=True)
class RunState:
    date: str | None = None
    phase: str = "idle"
    attempts: int = 0
    next_retry_at: str | None = None
    delivered_cards: int = 0
    delivery_attempts: int = 0
    report_path: str | None = None
    manifest_path: str | None = None
    error: str | None = None


def load_state(path: Path) -> RunState:
    if not path.is_file():
        return RunState()
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("run state must be a JSON object")
    return RunState(**data)


def save_state(path: Path, state: RunState) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(asdict(state), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def next_retry_at(now: datetime, attempts: int) -> datetime | None:
    if attempts == 1:
        return now + timedelta(minutes=15)
    if attempts == 2:
        return now + timedelta(minutes=60)
    return None


def should_run(state: RunState, schedule_time: str, timezone: str, now: datetime | None = None) -> bool:
    zone = ZoneInfo(timezone)
    current = (now or datetime.now(zone)).astimezone(zone)
    hour, minute = (int(part) for part in schedule_time.split(":", 1))
    scheduled = current.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if current < scheduled:
        return False
    today = current.date().isoformat()
    if state.date != today:
        return True
    if state.phase in {"sent", "uncertain"} or state.attempts >= 3:
        return False
    if state.phase == "send_failed" and state.delivery_attempts >= 3:
        return False
    if state.next_retry_at and current < datetime.fromisoformat(state.next_retry_at).astimezone(zone):
        return False
    return True
