from __future__ import annotations

import os
import plistlib
import subprocess
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from unittest.mock import patch

from dealy_report.schedulers import (
    SchedulerError,
    build_cron_line,
    build_macos_plist,
    build_systemd_units,
    build_windows_task_xml,
    install_scheduler,
    remove_scheduler,
    scheduler_name,
)


class SchedulerBuildersTests(unittest.TestCase):
    def test_scheduler_name_accepts_a_strict_safe_slug(self) -> None:
        self.assertEqual(scheduler_name("weekday-report"), "dealy-report-weekday-report")

    def test_scheduler_name_rejects_unsafe_slugs(self) -> None:
        for value in ("", "Weekday", "two words", "../escape", "double--dash", "trailing-"):
            with self.subTest(value=value):
                with self.assertRaises(SchedulerError):
                    scheduler_name(value)

    def test_windows_task_xml_escapes_dynamic_values_and_sets_required_policy(self) -> None:
        xml_text = build_windows_task_xml(
            "weekday-report",
            r"C:\Program Files\Python & Tools\python.exe",
            r"C:\reports & <daily>",
        )

        self.assertIn("Python &amp; Tools", xml_text)
        self.assertIn("&lt;daily&gt;", xml_text)
        root = ET.fromstring(xml_text)
        namespace = {"task": "http://schemas.microsoft.com/windows/2004/02/mit/task"}
        self.assertEqual(root.findtext(".//task:Interval", namespaces=namespace), "PT5M")
        self.assertEqual(root.findtext(".//task:RunOnlyIfNetworkAvailable", namespaces=namespace), "true")
        self.assertEqual(root.findtext(".//task:StartWhenAvailable", namespaces=namespace), "true")
        self.assertEqual(root.findtext(".//task:MultipleInstancesPolicy", namespaces=namespace), "IgnoreNew")
        self.assertEqual(root.findtext(".//task:LogonType", namespaces=namespace), "InteractiveToken")
        self.assertEqual(root.findtext(".//task:RunLevel", namespaces=namespace), "LeastPrivilege")
        self.assertEqual(
            root.findtext(".//task:Command", namespaces=namespace),
            r"C:\Program Files\Python & Tools\python.exe",
        )
        self.assertEqual(
            root.findtext(".//task:Arguments", namespaces=namespace),
            r'"C:\reports & <daily>\bootstrap.py" dispatch --profile "weekday-report"',
        )

    def test_macos_plist_has_absolute_dispatch_arguments_and_log_paths(self) -> None:
        plist_bytes = build_macos_plist(
            "weekday-report",
            "/Applications/Python 3/bin/python3",
            "/Users/me/dealy report",
            "/Users/me/.local/share/dealy-report",
        )

        payload = plistlib.loads(plist_bytes)
        self.assertEqual(payload["Label"], "com.captainjee.dealy-report.weekday-report")
        self.assertEqual(payload["StartInterval"], 300)
        self.assertTrue(payload["RunAtLoad"])
        self.assertEqual(
            payload["ProgramArguments"],
            [
                "/Applications/Python 3/bin/python3",
                "/Users/me/dealy report/bootstrap.py",
                "dispatch",
                "--profile",
                "weekday-report",
            ],
        )
        self.assertEqual(
            payload["StandardOutPath"],
            "/Users/me/.local/share/dealy-report/logs/dealy-report-weekday-report.out.log",
        )
        self.assertEqual(
            payload["StandardErrorPath"],
            "/Users/me/.local/share/dealy-report/logs/dealy-report-weekday-report.err.log",
        )

    def test_systemd_units_quote_paths_and_define_persistent_timer(self) -> None:
        service, timer = build_systemd_units(
            "weekday-report",
            "/opt/Python 3/bin/python3",
            "/srv/dealy report",
            "/var/lib/dealy-report",
        )

        self.assertIn('ExecStart="/opt/Python 3/bin/python3" "/srv/dealy report/bootstrap.py" dispatch --profile weekday-report', service)
        self.assertIn('WorkingDirectory="/srv/dealy report"', service)
        self.assertIn("OnBootSec=2m", timer)
        self.assertIn("OnUnitActiveSec=5m", timer)
        self.assertIn("Persistent=true", timer)
        self.assertIn("Unit=dealy-report-weekday-report.service", timer)

    def test_cron_line_uses_shell_quoting_and_a_profile_marker(self) -> None:
        line = build_cron_line(
            "weekday-report",
            "/opt/Python 3/bin/python3",
            "/srv/dealy report",
            "/var/lib/dealy-report",
        )

        self.assertTrue(line.startswith("*/5 * * * * "))
        self.assertIn("'/opt/Python 3/bin/python3'", line)
        self.assertIn("'/srv/dealy report/bootstrap.py'", line)
        self.assertIn("# dealy-report:weekday-report", line)


class SchedulerInstallationTests(unittest.TestCase):
    def test_install_dry_run_selects_the_expected_platform_backend(self) -> None:
        common = {
            "profile_id": "weekday-report",
            "python_path": "/usr/bin/python3",
            "repo_path": "/srv/dealy-report",
            "data_dir": "/var/lib/dealy-report",
            "dry_run": True,
        }
        with patch("dealy_report.schedulers.shutil.which", return_value="/usr/bin/systemctl"):
            self.assertEqual(install_scheduler(platform_name="Linux", **common)["backend"], "systemd")
        self.assertEqual(install_scheduler(platform_name="Windows", **common)["backend"], "windows-task")
        self.assertEqual(install_scheduler(platform_name="Darwin", **common)["backend"], "launchctl")
        with patch("dealy_report.schedulers.shutil.which", return_value=None):
            self.assertEqual(install_scheduler(platform_name="Linux", **common)["backend"], "cron")

    def test_cron_install_replaces_an_existing_marked_entry(self) -> None:
        previous = (
            "0 8 * * * /old-command # dealy-report:weekday-report\n"
            "*/5 * * * * /other # dealy-report:other\n"
            "*/5 * * * * /longer # dealy-report:weekday-report-extra\n"
        )
        completed = [
            subprocess.CompletedProcess(["crontab", "-l"], 0, previous, ""),
            subprocess.CompletedProcess(["crontab", "-"], 0, "", ""),
        ]
        with tempfile.TemporaryDirectory() as temporary_dir:
            with patch("dealy_report.schedulers.shutil.which", return_value=None), patch(
                "dealy_report.schedulers.subprocess.run", side_effect=completed
            ) as run:
                result = install_scheduler(
                    "weekday-report",
                    "/usr/bin/python3",
                    "/srv/dealy-report",
                    temporary_dir,
                    platform_name="Linux",
                )

        self.assertEqual(result["backend"], "cron")
        self.assertEqual(run.call_count, 2)
        installed_crontab = run.call_args_list[1].kwargs["input"]
        self.assertNotIn("/old-command", installed_crontab)
        self.assertEqual(
            sum(line.rstrip().endswith("# dealy-report:weekday-report") for line in installed_crontab.splitlines()),
            1,
        )
        self.assertIn("# dealy-report:other", installed_crontab)
        self.assertIn("# dealy-report:weekday-report-extra", installed_crontab)

    def test_dry_runs_do_not_call_subprocess_or_create_files(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            data_dir = Path(temporary_dir) / "new-data"
            with patch("dealy_report.schedulers.subprocess.run") as run:
                install_result = install_scheduler(
                    "weekday-report",
                    "/usr/bin/python3",
                    "/srv/dealy-report",
                    data_dir,
                    platform_name="Windows",
                    dry_run=True,
                )
                remove_result = remove_scheduler(
                    "weekday-report",
                    data_dir,
                    platform_name="Windows",
                    dry_run=True,
                )

        self.assertEqual(install_result["operation"], "install")
        self.assertEqual(remove_result["operation"], "remove")
        run.assert_not_called()
        self.assertFalse(data_dir.exists())

    def test_macos_remove_tolerates_an_unloaded_existing_plist(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            plist_path = Path(temporary_dir) / "schedulers" / "com.captainjee.dealy-report.weekday-report.plist"
            plist_path.parent.mkdir()
            plist_path.write_text("placeholder", encoding="utf-8")

            def launchctl_not_loaded(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
                if kwargs["check"]:
                    raise subprocess.CalledProcessError(3, command, stderr="Could not find service")
                return subprocess.CompletedProcess(command, 3, "", "Could not find service")

            with patch("dealy_report.schedulers.os.getuid", return_value=501, create=True), patch(
                "dealy_report.schedulers.subprocess.run", side_effect=launchctl_not_loaded
            ):
                remove_scheduler("weekday-report", plist_path.parents[1], platform_name="Darwin")

            self.assertFalse(plist_path.exists())


if __name__ == "__main__":
    unittest.main()
