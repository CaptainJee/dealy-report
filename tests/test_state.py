import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from dealy_report.state import RunState, load_state, next_retry_at, save_state, should_run


SHANGHAI = ZoneInfo("Asia/Shanghai")


class StateTests(unittest.TestCase):
    def test_not_due_before_schedule_and_due_after_schedule(self):
        state = RunState()

        self.assertFalse(should_run(state, "08:30", "Asia/Shanghai", datetime(2026, 7, 14, 8, 29, tzinfo=SHANGHAI)))
        self.assertTrue(should_run(state, "08:30", "Asia/Shanghai", datetime(2026, 7, 14, 8, 30, tzinfo=SHANGHAI)))

    def test_success_or_uncertain_delivery_never_automatically_repeats(self):
        now = datetime(2026, 7, 14, 9, 0, tzinfo=SHANGHAI)
        self.assertFalse(should_run(RunState(date="2026-07-14", phase="sent"), "08:30", "Asia/Shanghai", now))
        self.assertFalse(should_run(RunState(date="2026-07-14", phase="uncertain"), "08:30", "Asia/Shanghai", now))

    def test_delivery_retries_stop_after_three_attempts(self):
        now = datetime(2026, 7, 14, 9, 0, tzinfo=SHANGHAI)
        exhausted = RunState(
            date="2026-07-14",
            phase="send_failed",
            attempts=1,
            delivery_attempts=3,
        )

        self.assertFalse(should_run(exhausted, "08:30", "Asia/Shanghai", now))

    def test_generation_retries_use_15_and_60_minute_backoff_then_stop(self):
        now = datetime(2026, 7, 14, 9, 0, tzinfo=SHANGHAI)

        self.assertEqual(next_retry_at(now, 1), now + timedelta(minutes=15))
        self.assertEqual(next_retry_at(now, 2), now + timedelta(minutes=60))
        self.assertIsNone(next_retry_at(now, 3))
        exhausted = RunState(date="2026-07-14", phase="failed", attempts=3)
        self.assertFalse(should_run(exhausted, "08:30", "Asia/Shanghai", now))

    def test_waits_until_next_retry_timestamp(self):
        now = datetime(2026, 7, 14, 9, 0, tzinfo=SHANGHAI)
        state = RunState(
            date="2026-07-14",
            phase="failed",
            attempts=1,
            next_retry_at=(now + timedelta(minutes=15)).isoformat(),
        )

        self.assertFalse(should_run(state, "08:30", "Asia/Shanghai", now))
        self.assertTrue(should_run(state, "08:30", "Asia/Shanghai", now + timedelta(minutes=15)))

    def test_state_round_trips_atomically(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            state = RunState(date="2026-07-14", phase="generated", attempts=1, delivered_cards=1)

            save_state(path, state)
            loaded = load_state(path)

        self.assertEqual(loaded, state)


if __name__ == "__main__":
    unittest.main()
