"""Regression of the actual MetrixNEO missing-dashboard-content incident."""
import ast
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import types
import unittest
import xml.etree.ElementTree as ET

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'tools'))
import prepare_rc80_dashboard_skin_guard as fix
import build_rc80_dashboard_skin_guard_package as package
FIXTURES=ROOT/'tests/fixtures'
BASELINE=(FIXTURES/'rc80_dashboard_main_baseline.py').read_bytes()


def required_from_candidate():
    tree=ast.parse(fix.corrected({'ui/main.py':BASELINE})['ui/main.py'])
    cls=next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name=='EPGManagerMainScreen')
    init=next(n for n in cls.body if isinstance(n,ast.FunctionDef) and n.name=='__init__')
    obj=types.SimpleNamespace()
    exec(compile(ast.Module(body=[init.body[1]],type_ignores=[]),'<actual-candidate-assignment>','exec'),{'self':obj})
    return obj.mandatoryWidgets


class DashboardSkinGuard(unittest.TestCase):
    def test_actual_receiver_baseline_is_pinned_and_every_other_ast_node_is_unchanged(self):
        self.assertEqual(hashlib.sha256(BASELINE).hexdigest(),fix.ORIGINAL['ui/main.py'])
        new=ast.parse(fix.corrected({'ui/main.py':BASELINE})['ui/main.py'])
        cls=next(n for n in new.body if isinstance(n,ast.ClassDef) and n.name=='EPGManagerMainScreen')
        init=next(n for n in cls.body if isinstance(n,ast.FunctionDef) and n.name=='__init__')
        init.body.pop(1)
        self.assertEqual(ast.dump(new),ast.dump(ast.parse(BASELINE)))

    def test_actual_broken_metrixneo_override_fails_native_mandatory_contract(self):
        broken=json.loads((FIXTURES/'rc80_metrixneo_dashboard_widgets.json').read_text())
        required=required_from_candidate()
        self.assertFalse(all(n in broken for n in required))
        self.assertEqual(len(set(required)-set(broken)),81)

    def test_embedded_fallback_contains_all_required_content_and_visible_bounds(self):
        xml=ET.parse(FIXTURES/'rc80_dashboard_embedded.xml').getroot()
        by_name={w.get('name'):w for w in xml.iter('widget')}
        required=required_from_candidate()
        self.assertEqual(len(required),len(set(required)))
        self.assertEqual(len(required),81)
        for name in required:
            with self.subTest(widget=name):
                w=by_name[name];x,y=map(int,w.get('position').split(','));width,height=map(int,w.get('size').split(','))
                self.assertGreaterEqual(x,0);self.assertGreaterEqual(y,0)
                self.assertGreater(width,0);self.assertGreater(height,0)
                self.assertLessEqual(x+width,1920);self.assertLessEqual(y+height,1080)

    def test_complete_third_party_content_does_not_require_our_decoration(self):
        required=required_from_candidate()
        external=set(required)|{'brand','custom_background'}
        self.assertTrue(all(n in external for n in required))
        self.assertNotIn('card1_bg',required)
        self.assertNotIn('kpi1_rail',required)

    def test_inventory_and_unreviewed_baseline_are_rejected(self):
        with self.assertRaisesRegex(ValueError,'inventory drift'):fix.corrected({})
        with self.assertRaisesRegex(ValueError,'preimage drift'):fix.corrected({'ui/main.py':b'changed'})

    def test_unqualified_ipk_creates_no_candidate_artifact(self):
        with tempfile.TemporaryDirectory(dir=ROOT.parent) as d:
            out=Path(d);src=out/'unknown.ipk';src.write_bytes(b'unreviewed')
            with self.assertRaisesRegex(ValueError,'identity mismatch'):package.build(src,out)
            self.assertEqual(list(out.iterdir()),[src])

    def test_package_output_cannot_overwrite_the_repository(self):
        with self.assertRaisesRegex(ValueError,'private output'):package.build('not-read.ipk',ROOT)


if __name__=='__main__':unittest.main()
