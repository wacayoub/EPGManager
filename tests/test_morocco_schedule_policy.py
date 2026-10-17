#!/usr/bin/env python3
"""Verify scheduled Morocco EPG runs survive delayed GitHub Actions queues."""
from __future__ import annotations
from datetime import datetime, timedelta
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest
from zoneinfo import ZoneInfo
import yaml

WORKFLOW = Path(__file__).resolve().parents[1] / ".github/workflows/morocco-epg.yml"
TZ = ZoneInfo("Africa/Casablanca")


def script(name):
    steps = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))["jobs"]["build"]["steps"]
    return next(step for step in steps if step["name"] == name)


def embedded_python(step):
    match = re.search(r"python - <<'PY'\n(.*?)\nPY(?:\n|$)", step["run"], re.S)
    if match is None:
        raise AssertionError("Missing Python heredoc in " + step["name"])
    code = match.group(1)
    return "\n".join(line[10:] if line.startswith("          ") else line for line in code.splitlines())


class MoroccoDelayedCronTests(unittest.TestCase):
    def test_workflow_syntax_and_schedule(self):
        self.assertIn("jobs", yaml.safe_load(WORKFLOW.read_text(encoding="utf-8")))
        generate = script("Generate Morocco XMLTV with final historical source logic")
        live = "\n".join(x for x in generate["run"].splitlines() if not x.strip().startswith("#"))
        self.assertNotIn("--scheduled", live)
        self.assertIn("steps.daily.outputs.run == 'true'", generate["if"])
        self.assertIn("steps.daily.outputs.run == 'true'",
                      script("Validate candidate and Moroccan source policies")["if"])
        self.assertIn("steps.daily.outputs.run == 'true'",
                      script("Keep endpoint healthy when candidate is rejected")["if"])
        self.assertIn("steps.daily.outputs.run == 'true'",
                      script("Fail loudly if published Morocco guide stays stale")["if"])

    def test_once_per_local_day_even_if_job_starts_late(self):
        code = embedded_python(script("Decide one refresh per Casablanca calendar day"))
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            (home / "previous").mkdir()
            today = datetime.now(TZ)
            for days_old, event, expected in [
                (0, "schedule", "false"),
                (1, "schedule", "true"),
                (28, "schedule", "true"),
                (0, "workflow_dispatch", "true"),
            ]:
                published = (today - timedelta(days=days_old)).isoformat()
                (home / "previous/manifest.json").write_text(json.dumps({
                    "generated": published, "programmes": 1771
                }))
                path = home / "github-output.txt"
                path.write_text("")
                env = {**os.environ, "EVENT_NAME": event, "GITHUB_OUTPUT": str(path)}
                p = subprocess.run([sys.executable, "-c", code], env=env, cwd=home,
                                   capture_output=True, text=True)
                self.assertEqual(p.returncode, 0, p.stderr)
                self.assertIn("run=" + expected, path.read_text())

    def test_stale_publication_never_looks_successful(self):
        code = embedded_python(script("Fail loudly if published Morocco guide stays stale"))
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "manifest.json"
            code = code.replace("/tmp/morocco-published-manifest.json", str(target))
            today = datetime.now(TZ)
            for age_days, expected_ok in [(0, True), (1, True), (2, False), (28, False)]:
                target.write_text(json.dumps({
                    "generated": (today - timedelta(days=age_days)).isoformat(),
                    "programmes": 1771
                }))
                p = subprocess.run([sys.executable, "-c", code],
                                   capture_output=True, text=True)
                self.assertEqual(p.returncode == 0, expected_ok, p.stdout + p.stderr)


if __name__ == "__main__":
    unittest.main()
