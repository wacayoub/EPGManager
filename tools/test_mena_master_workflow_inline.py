#!/usr/bin/env python3
"""Offline regression for embedded Python in the MENA master workflow."""
import ast
import csv
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WORKFLOW = ROOT / ".github/workflows/refresh-mena-source-id-master.yml"
REQUIRED = (
    "id_group", "receiver_canonical_id", "now_status",
    "now_title", "now_desc", "epg_snapshot_utc",
)


def extract_live_column_script():
    content = WORKFLOW.read_text(encoding="utf-8")
    match = re.search(
        r"(?ms)^      - name: Verify required live columns\n"
        r".*?^          python - <<'PY'\n(?P<body>.*?)^          PY$",
        content,
    )
    if match is None:
        raise AssertionError("expected live-column embedded Python not found")
    lines = match.group("body").splitlines()
    if not all(line.startswith("          ") for line in lines if line.strip()):
        raise AssertionError("incorrect indentation in embedded Python")
    script = "\n".join(line[10:] for line in lines) + "\n"
    ast.parse(script, filename=str(WORKFLOW))
    return script


class EmbeddedMasterGateTests(unittest.TestCase):
    def run_gate(self, missing=()):
        script = extract_live_column_script()
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "docs" / "mena-source-id-master.csv"
            target.parent.mkdir(parents=True)
            fields = [x for x in REQUIRED if x not in missing]
            with target.open("w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=fields)
                writer.writeheader()
                writer.writerow({x: "NOW" if x == "now_status" else "BEIN"
                                 for x in fields})
            return subprocess.run(
                [sys.executable, "-c", script], cwd=tmp,
                text=True, capture_output=True, check=False,
            )

    def test_valid_current_rows(self):
        res = self.run_gate()
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertIn("MASTER_LIVE_COLUMNS PASS", res.stdout)

    def test_missing_live_column_blocks_release(self):
        res = self.run_gate(missing=("epg_snapshot_utc",))
        self.assertNotEqual(res.returncode, 0)


if __name__ == "__main__":
    unittest.main()
