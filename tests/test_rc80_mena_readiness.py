"""Public synthetic MENA policy regressions; no receiver data or I/O."""
from copy import deepcopy
import ast
from pathlib import Path
import sys
import time
import types
import unittest
from test_rc80_verified_epg_match import bridge, fixture, NOW


def readiness():
    return {"schema": 1, "source": "mena", "checked_utc": NOW.isoformat(),
            "source_generated_utc": NOW.isoformat(), "structural_errors": [],
            "source_manifest_sha256_match": True, "verified_source_count": 14,
            "ids": [{"xmltv_id": "AlAoula.ma", "owner": "morocco",
                     "status": "QUALIFIED_AT_BUILD", "actual_future_programmes": 8,
                     "qualified_when_built": True, "best_match_green_allowed": True,
                     "selected_role": "MAIN"}]}


def snapshot(doc):
    return {"checked_utc": doc["checked_utc"], "ids": bridge.mena_index(doc, NOW.timestamp())}


class MenaReadinessTests(unittest.TestCase):
    def wrapper(self, status="EPG_OK"):
        tree = ast.parse((Path(__file__).resolve().parents[1] /
                          'tools/apply_rc80_private_functional_fixes.py').read_text())
        assignment = next(n for n in ast.walk(tree) if isinstance(n, ast.Assign) and
                          any(isinstance(t, ast.Name) and t.id == 'wrapper' for t in n.targets))
        package = types.ModuleType('mena_wrapper_test')
        package.__path__ = []
        sys.modules[package.__name__] = package
        sys.modules[package.__name__ + '.verified_epg_match'] = bridge
        namespace = {'__package__': package.__name__, 'time': time,
                     '_classify_source_report': lambda *args: status}
        exec(ast.literal_eval(assignment.value), namespace)
        return namespace['classify']

    def test_legacy_ui_health_requires_qualified_owner_before_green(self):
        classify = self.wrapper()
        report = {'source': 'morocco'}
        self.assertEqual(classify(report, 'AlAoula.ma', NOW.timestamp()), 'EPG_WARN')
        doc = readiness()
        report['mena_readiness'] = snapshot(doc)
        self.assertEqual(classify(report, 'AlAoula.ma', NOW.timestamp()), 'EPG_OK')
        doc['ids'][0].update(status='NO_FUTURE_EPG', actual_future_programmes=0)
        report['mena_readiness'] = snapshot(doc)
        self.assertEqual(classify(report, 'AlAoula.ma', NOW.timestamp()), 'NO_EPG')

    def test_missing_or_bad_legacy_quality_cannot_be_promoted_by_mena(self):
        report = {'source': 'morocco', 'mena_readiness': snapshot(readiness())}
        self.assertEqual(self.wrapper('UNKNOWN')(report, 'AlAoula.ma', NOW.timestamp()), 'UNKNOWN')

    def test_no_future_vetoes_even_hashed_current_and_main_role(self):
        doc = readiness()
        doc["ids"][0].update(status="NO_FUTURE_EPG", actual_future_programmes=0)
        report = fixture()
        report["sources"][0]["mena_readiness"] = snapshot(doc)
        result = bridge.quality_for_id("direct_morocco", "AlAoula.ma", report, NOW.timestamp())
        self.assertEqual(result["status"], "NO_FUTURE_EPG")
        self.assertFalse(result["verified_now"])

    def test_zero_count_overrides_contradictory_green_flag(self):
        doc = readiness()
        doc["ids"][0]["actual_future_programmes"] = 0
        self.assertEqual(snapshot(doc)["ids"]["AlAoula.ma"]["status"], "NO_FUTURE_EPG")

    def test_degraded_never_green_and_saved_mapping_stays_visible(self):
        doc = readiness()
        doc["ids"][0]["status"] = "DEGRADED_WITH_EPG"
        report = fixture()
        report["sources"][0]["mena_readiness"] = snapshot(doc)
        result = bridge.quality_for_id("direct_morocco", "AlAoula.ma", report, NOW.timestamp())
        self.assertEqual(result["status"], "DEGRADED_WITH_EPG")
        self.assertFalse(result["verified_now"])
        rows = [{"source_id": "direct_morocco", "channel_id": "AlAoula.ma", "_suggestion_current": True}]
        self.assertEqual(bridge.annotate_suggestions(rows, report, NOW.timestamp())[0]["_verified_epg_health"], "SAVED_OWNER")

    def test_qualified_metadata_cannot_promote_unverified_xml(self):
        report = fixture()
        report["sources"][0]["mena_readiness"] = snapshot(readiness())
        report["sources"][0]["xmltv_sha256_verified"] = False
        result = bridge.quality_for_id("direct_morocco", "AlAoula.ma", report, NOW.timestamp())
        self.assertFalse(result["verified_now"])

    def test_exact_owner_and_case_sensitive_identity(self):
        snap = snapshot(readiness())
        self.assertEqual(bridge.mena_status("osn", "AlAoula.ma", snap, NOW.timestamp()), "UNKNOWN")
        self.assertEqual(bridge.mena_status("morocco", "alaoula.ma", snap, NOW.timestamp()), "UNKNOWN")
        self.assertEqual(bridge.mena_status("morocco", "AlAoula.ma", snap, NOW.timestamp()), "QUALIFIED_AT_BUILD")

    def test_stale_duplicate_malformed_and_unverified_reports_fail_closed(self):
        good = readiness()
        variants = []
        for key, value in (("verified_source_count", 13), ("source_manifest_sha256_match", False),
                           ("checked_utc", "2020-01-01T00:00:00Z"), ("structural_errors", ["BAD"])):
            doc = deepcopy(good)
            doc[key] = value
            variants.append(doc)
        doc = deepcopy(good)
        doc["ids"].append(deepcopy(doc["ids"][0]))
        variants.append(doc)
        for key, value in (("owner", []), ("actual_future_programmes", True),
                           ("actual_future_programmes", "0"), ("status", "MAIN")):
            doc = deepcopy(good)
            doc["ids"][0][key] = value
            variants.append(doc)
        for doc in variants:
            with self.subTest(doc=doc):
                self.assertEqual(bridge.mena_index(doc, NOW.timestamp()), {})


if __name__ == "__main__":
    unittest.main()
