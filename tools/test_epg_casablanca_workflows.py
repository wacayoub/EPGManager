# -*- coding: utf-8 -*-
"""YAML and behavior checks: timezone-aware, targeted, and fail-closed EPG."""
from pathlib import Path
import unittest
import yaml

from tools import epg_schedule_policy

ROOT = Path(__file__).resolve().parents[1]
WF = ROOT / ".github/workflows"


def load(name):
    # BaseLoader preserves the literal GitHub Actions 'on:' key (PyYAML SafeLoader
    # otherwise uses the YAML 1.1 boolean representation of on).
    data = yaml.load((WF / name).read_text(encoding="utf-8"), Loader=yaml.BaseLoader)
    assert isinstance(data, dict) and "on" in data
    return data


class TestScheduledWorkflows(unittest.TestCase):
    def test_main_sequence_once_each_casablanca_morning(self):
        p = load("daily-epg-chain.yml")
        self.assertEqual(p["on"]["schedule"], [{
            "cron": "15 7 * * *", "timezone": "Etc/GMT",
        }])
        self.assertEqual(len([key for key in p["jobs"] if key != "finalize"]), 1)
        self.assertNotIn("osn", p["jobs"])
        self.assertNotIn("shahid", p["jobs"])
        self.assertNotIn("sport24", p["jobs"])
        self.assertNotIn("rotana", p["jobs"])
        self.assertNotIn("alkass", p["jobs"])
        self.assertNotIn("tabie", p["jobs"])
        self.assertNotIn("tunisiatv", p["jobs"])
        self.assertEqual(p["jobs"]["finalize"]["needs"], "elcinema_extend")
        self.assertNotIn("dubaiplus", p["jobs"])
        self.assertNotIn("aljazeera", p["jobs"])
        self.assertNotIn("starzplay", p["jobs"])
        self.assertNotIn("stctv", p["jobs"])
        self.assertEqual(p["jobs"]["elcinema_extend"]["uses"], "./.github/workflows/elcinema-daily-donors.yml")
        self.assertIsNone(p["jobs"]["elcinema_extend"].get("needs"))
        self.assertIn("epg-daily-chain", p["concurrency"]["group"])

    def test_1415_watchdog_is_not_full_chain_retry(self):
        p = load("epg-watchdog.yml")
        self.assertEqual(p["on"]["schedule"], [{
            "cron": "15 14 * * *", "timezone": "Etc/GMT",
        }])
        contents = (WF / "epg-watchdog.yml").read_text()
        self.assertNotIn("gh workflow run daily-epg-chain.yml", contents)
        self.assertIn("MENA", contents)

    def test_shahid_retry_is_manual_only_after_0339_schedule(self):
        p = load("epg-casablanca-targeted-retry.yml")
        self.assertNotIn("schedule", p["on"])
        self.assertIn("workflow_dispatch", p["on"])
        shahid = load("refresh-shahid.yml")
        self.assertEqual(shahid["on"]["schedule"], [{
            "cron": "39 3 * * *", "timezone": "Etc/GMT",
        }])
        self.assertIn("workflow_dispatch", shahid["on"])
        self.assertNotIn("push", shahid["on"])
        self.assertEqual(p["jobs"]["retry"]["with"]["source"], "${{ matrix.source }}")
        self.assertEqual(p["jobs"]["retry"]["strategy"]["max-parallel"], "1")
        self.assertEqual(p["jobs"]["retry"]["strategy"]["matrix"]["source"],
                         "${{ fromJSON(needs.decide.outputs.retry_sources) }}")
        self.assertEqual(tuple(epg_schedule_policy.TARGETS),
                         ("shahid",))
        self.assertIn("--active-chain", (WF / "epg-casablanca-targeted-retry.yml").read_text())
        self.assertIn("if ! gh run list", (WF / "epg-casablanca-targeted-retry.yml").read_text())

    def test_dubaiplus_exact_0444_gmt_only_one_source_schedule(self):
        p = load("refresh-dubaiplus.yml")
        self.assertEqual(p["on"]["schedule"], [{
            "cron": "44 4 * * *", "timezone": "Etc/GMT",
        }])
        self.assertIn("workflow_dispatch", p["on"])
        self.assertNotIn("push", p["on"])
        self.assertEqual(p["jobs"]["refresh"]["with"], {
            "source": "dubaiplus", "scheduled": "false",
        })
        chain = load("daily-epg-chain.yml")
        self.assertNotIn("dubaiplus", chain["jobs"])
        self.assertNotIn("aljazeera", chain["jobs"])
        self.assertNotIn("starzplay", chain["jobs"])
        self.assertNotIn("tunisiatv", chain["jobs"])
        self.assertEqual(set(chain["jobs"]), {"elcinema_extend","finalize"})

    def test_four_remaining_sources_daily_once_and_manual_preserved(self):
        slots = (("tabie","30 0 * * *"),("rotana","50 3 * * *"),
                 ("alkass","30 6 * * *"),("tunisiatv","35 6 * * *"))
        for source,cron in slots:
            with self.subTest(source=source):
                p=load(f"refresh-{source}.yml")
                self.assertEqual(p["on"]["schedule"], [
                    {"cron":cron,"timezone":"Etc/GMT"}])
                self.assertIn("workflow_dispatch",p["on"])
                self.assertNotIn("push",p["on"])
                self.assertEqual(p["jobs"]["refresh"]["with"], {
                    "source":source,"scheduled":"false"})
                self.assertNotIn(source,load("daily-epg-chain.yml")["jobs"])
        self.assertNotIn("30 19 * * *",
                         (WF/"epg-evening-casablanca.yml").read_text().split("  workflow_dispatch:")[0])

    def test_all_known_scheduled_workflows_are_fixed_gmt_zero(self):
        names = ("refresh-elcinema.yml", "refresh-osn.yml",
                 "refresh-bein.yml", "refresh-sport24.yml",
                 "refresh-shahid.yml", "refresh-dubaiplus.yml",
                 "refresh-aljazeera.yml", "refresh-starzplay.yml",
                 "refresh-stctv.yml", "refresh-alkass.yml",
                 "refresh-tunisiatv.yml", "refresh-rotana.yml", "refresh-tabie.yml",
                 "refresh-morocco.yml", "daily-epg-chain.yml", "epg-watchdog.yml")
        for name in names:
            with self.subTest(workflow=name):
                p=load(name)
                self.assertTrue(p["on"]["schedule"])
                for entry in p["on"]["schedule"]:
                    self.assertEqual(entry["timezone"], "Etc/GMT")

    def test_manual_legacy_group_sports_and_morocco_are_independent(self):
        p = load("epg-evening-casablanca.yml")
        self.assertNotIn("schedule",p["on"])
        self.assertIn("workflow_dispatch",p["on"])
        self.assertEqual(p["jobs"]["sports"]["strategy"]["matrix"]["source"],
                         ["alkass"])
        self.assertEqual(p["jobs"]["sports"]["strategy"]["max-parallel"], "1")
        self.assertEqual(p["jobs"]["morocco"]["with"]["source"], "morocco")
        self.assertIn("DISPATCH_GROUP", (WF / "epg-evening-casablanca.yml").read_text())
        morocco=load("refresh-morocco.yml")
        self.assertEqual(morocco["on"]["schedule"], [{"cron":"7 7 * * *","timezone":"Etc/GMT"}])
        self.assertIn("workflow_dispatch",morocco["on"])
        self.assertNotIn("push",morocco["on"])
        self.assertNotIn("morocco",load("daily-epg-chain.yml")["jobs"])

    def test_no_receiver_install_or_unbounded_recursion(self):
        names = ("daily-epg-chain.yml", "epg-watchdog.yml",
                 "epg-casablanca-targeted-retry.yml", "epg-evening-casablanca.yml")
        for name in names:
            src = (WF / name).read_text(encoding="utf-8")
            for command in ("opkg install", "ssh ", "reboot", "/etc/enigma2", "wget -q"):
                with self.subTest(filename=name, command=command):
                    self.assertNotIn(command, src)
        self.assertNotIn("gh workflow run daily-epg-chain", (WF / "epg-watchdog.yml").read_text())


if __name__ == "__main__":
    unittest.main()
