import hashlib
import importlib.util
import os
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def load_bootstrap():
    spec = importlib.util.spec_from_file_location("dealy_report_bootstrap", ROOT / "bootstrap.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class BootstrapTests(unittest.TestCase):
    def test_rejects_python_older_than_3_11_before_creating_runtime(self):
        bootstrap = load_bootstrap()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with self.assertRaisesRegex(RuntimeError, "Python 3.11"):
                bootstrap.ensure_runtime(root=root, version_info=(3, 10, 14))
            self.assertFalse((root / ".runtime").exists())

    def test_repository_lock_contains_only_exact_runtime_dependencies(self):
        lines = (ROOT / "requirements.lock").read_text(encoding="utf-8").splitlines()
        self.assertEqual(lines, ["keyring==25.7.0", "tzdata==2026.3"])

    def test_installs_lock_once_and_reinstalls_only_after_lock_changes(self):
        bootstrap = load_bootstrap()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            lock = root / "requirements.lock"
            lock.write_text("keyring==25.7.0\n", encoding="utf-8")
            calls = []

            def run(command, **kwargs):
                calls.append((command, kwargs))
                if command[1:3] == ["-m", "venv"]:
                    python_path = bootstrap.venv_python(root / ".runtime" / "venv")
                    python_path.parent.mkdir(parents=True, exist_ok=True)
                    python_path.touch()
                return subprocess.CompletedProcess(command, 0)

            first = bootstrap.ensure_runtime(root=root, python_executable="host-python", run=run)
            second = bootstrap.ensure_runtime(root=root, python_executable="host-python", run=run)

            self.assertEqual(first, second)
            install_calls = [call for call in calls if "pip" in call[0]]
            self.assertEqual(len(install_calls), 1)
            self.assertEqual(
                install_calls[0][0],
                [str(first), "-m", "pip", "install", "--requirement", str(lock)],
            )
            self.assertFalse(install_calls[0][1]["shell"])
            marker = root / ".runtime" / "requirements.sha256"
            self.assertEqual(marker.read_text(encoding="ascii"), hashlib.sha256(lock.read_bytes()).hexdigest() + "\n")

            lock.write_text("keyring==25.7.0\ntzdata==2026.3\n", encoding="utf-8")
            bootstrap.ensure_runtime(root=root, python_executable="host-python", run=run)
            self.assertEqual(len([call for call in calls if "pip" in call[0]]), 2)

    def test_marker_process_delegates_directly_to_cli_without_spawning(self):
        bootstrap = load_bootstrap()
        delegated = []

        class Cli:
            @staticmethod
            def main(argv):
                delegated.append(argv)
                return 7

        result = bootstrap.main(
            ["list"],
            environ={bootstrap.BOOTSTRAP_MARKER: "1"},
            cli_module=Cli,
            run=lambda *args, **kwargs: self.fail("must not spawn from marked process"),
        )

        self.assertEqual(result, 7)
        self.assertEqual(delegated, [["list"]])

    def test_unmarked_process_reexecutes_bootstrap_with_internal_marker(self):
        bootstrap = load_bootstrap()
        calls = []

        def run(command, **kwargs):
            calls.append((command, kwargs))
            return subprocess.CompletedProcess(command, 4)

        result = bootstrap.main(
            ["doctor", "--profile", "daily-ai"],
            environ={"PATH": os.environ.get("PATH", "")},
            ensure=lambda: Path("venv-python"),
            run=run,
        )

        self.assertEqual(result, 4)
        command, kwargs = calls[0]
        self.assertEqual(command[0], "venv-python")
        self.assertEqual(command[1], str(ROOT / "bootstrap.py"))
        self.assertEqual(command[2:], ["doctor", "--profile", "daily-ai"])
        self.assertEqual(kwargs["env"][bootstrap.BOOTSTRAP_MARKER], "1")
        self.assertFalse(kwargs["shell"])


if __name__ == "__main__":
    unittest.main()
