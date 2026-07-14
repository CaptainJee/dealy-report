import json
import subprocess
import tempfile
import unittest
from pathlib import Path


class CodexInstallTests(unittest.TestCase):
    def test_prefers_existing_project_local_codex(self):
        from dealy_report.codex_install import find_codex

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            local = root / ".runtime" / "codex" / "node_modules" / ".bin" / "codex.cmd"
            local.parent.mkdir(parents=True)
            local.touch()

            found = find_codex(root, which=lambda name: self.fail(f"unexpected global lookup: {name}"))

            self.assertEqual(found, local)

    def test_smoke_test_uses_strict_schema_and_safe_selected_options(self):
        from dealy_report.codex_install import smoke_test

        calls = []

        def run(command, **kwargs):
            calls.append((command, kwargs))
            schema_path = Path(command[command.index("--output-schema") + 1])
            output_path = Path(command[command.index("--output-last-message") + 1])
            schema = json.loads(schema_path.read_text(encoding="utf-8"))
            self.assertEqual(schema["required"], ["ok"])
            self.assertFalse(schema["additionalProperties"])
            output_path.write_text('{"ok":true}\n', encoding="utf-8")
            return subprocess.CompletedProcess(command, 0, "", "")

        with tempfile.TemporaryDirectory() as directory:
            result = smoke_test(
                Path("codex"),
                Path(directory),
                model="gpt-5.5",
                reasoning_effort="high",
                service_tier="fast",
                run=run,
            )

        self.assertTrue(result)
        command, kwargs = calls[0]
        self.assertIn("--ephemeral", command)
        self.assertEqual(command[command.index("--sandbox") + 1], "read-only")
        self.assertEqual(command[command.index("-m") + 1], "gpt-5.5")
        self.assertIn('service_tier="fast"', command)
        self.assertIn('model_reasoning_effort="high"', command)
        self.assertFalse(kwargs["shell"])

    def test_incompatible_global_codex_installs_exact_project_local_version(self):
        from dealy_report.codex_install import ensure_codex, local_codex_path

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            global_codex = Path(directory) / "global-codex.cmd"
            global_codex.touch()
            calls = []

            def which(name):
                return {"codex": str(global_codex), "npm": "npm.cmd", "npm.cmd": "npm.cmd"}.get(name)

            def inspect(executable, **kwargs):
                if Path(executable) == global_codex:
                    return kwargs["result_type"](Path(executable), "codex 0.100.0", True, False, "structured output failed")
                return kwargs["result_type"](Path(executable), "codex 0.144.4", True, True, None)

            def run(command, **kwargs):
                calls.append((command, kwargs))
                local = local_codex_path(root)
                local.parent.mkdir(parents=True, exist_ok=True)
                local.touch()
                return subprocess.CompletedProcess(command, 0, "", "")

            result = ensure_codex(root, "gpt-5.5", "high", "fast", run=run, which=which, inspect=inspect)

            self.assertTrue(result.compatible)
            self.assertEqual(result.executable, local_codex_path(root))
            command, kwargs = calls[0]
            self.assertEqual(command[:3], ["npm.cmd", "install", "--prefix"])
            self.assertIn("@openai/codex@0.144.4", command)
            self.assertNotIn("--global", command)
            self.assertNotIn("-g", command)
            self.assertFalse(kwargs["shell"])


if __name__ == "__main__":
    unittest.main()
