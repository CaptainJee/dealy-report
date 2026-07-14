import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from dealy_report.codex_runtime import CodexError, build_codex_command, generate_report
from tests.test_report import valid_payload


class Completed:
    returncode = 0
    stdout = ""
    stderr = ""


class CodexRuntimeTests(unittest.TestCase):
    def test_command_is_ephemeral_read_only_and_uses_schema(self):
        command = build_codex_command(
            executable=Path("/tools/codex"),
            repo_path=Path("/repo"),
            model="gpt-5.5",
            reasoning_effort="high",
            service_tier="fast",
            schema_path=Path("/tmp/schema.json"),
            output_path=Path("/tmp/report.json"),
        )

        self.assertLess(command.index("--search"), command.index("exec"))
        self.assertIn("never", command)
        self.assertIn("read-only", command)
        self.assertIn("--ephemeral", command)
        self.assertEqual(command[-1], "-")
        self.assertNotIn("FEISHU", " ".join(command))

    def test_generate_report_writes_strict_schema_and_validates_output(self):
        calls = []

        def fake_run(command, **kwargs):
            calls.append((command, kwargs))
            output = Path(command[command.index("--output-last-message") + 1])
            output.write_text(json.dumps(valid_payload(), ensure_ascii=False), encoding="utf-8")
            return Completed()

        with tempfile.TemporaryDirectory() as directory:
            report = generate_report(
                executable=Path("codex"),
                repo_path=Path(directory),
                model="gpt-5.5",
                reasoning_effort="high",
                service_tier="fast",
                prompt="generate",
                work_dir=Path(directory),
                run=fake_run,
            )
            schema = json.loads((Path(directory) / "report.schema.json").read_text(encoding="utf-8"))

        self.assertEqual(report.title, valid_payload()["title"])
        self.assertFalse(schema["additionalProperties"])
        self.assertEqual(calls[0][1]["input"], "generate")
        self.assertFalse(calls[0][1]["shell"])

    def test_generate_report_surfaces_codex_failure_without_output(self):
        class Failed:
            returncode = 7
            stdout = ""
            stderr = "model unavailable"

        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(CodexError, "model unavailable"):
                generate_report(
                    executable=Path("codex"),
                    repo_path=Path(directory),
                    model="gpt-5.5",
                    reasoning_effort="high",
                    service_tier="fast",
                    prompt="generate",
                    work_dir=Path(directory),
                    run=lambda *args, **kwargs: Failed(),
                )


if __name__ == "__main__":
    unittest.main()

