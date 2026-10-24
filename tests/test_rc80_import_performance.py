"""Differential ownership tests against the qualified Rytec arbitration."""
from difflib import SequenceMatcher
from pathlib import Path
import random
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'tools'))
sys.path.insert(0, str(ROOT/'tests'))
import prepare_rc80_import_performance as perf
import prepare_rc80_rytec_native as rytec
import build_rc80_import_performance_package as package
from test_rc80_rytec_native import environment

BASELINE = rytec.correct_arbitration((ROOT/'tests/fixtures/rc80_rytec_arbitration_baseline.py').read_text())


def run(source, items, parsed, registry, persisted=None, displays=None, fail=False):
    ns = environment(persisted)
    calls = []
    def score(a, b):
        calls.append((a, b))
        if fail: raise ValueError('fixture scoring failure')
        return int(SequenceMatcher(None, a.lower(), b.lower()).ratio()*100)
    ns['channel_mapper']._token_score = score
    ns['source_channel_cache'].get = lambda sid: (displays or {}).get(sid, [])
    ns['source_priority'].ownership_rank = lambda item, svc: (item.get('rank', 0), ())
    exec(source, ns)
    out, stats = ns['_fast_single_owner_maps'](items, parsed, (registry, {}), store_rows={})
    return out, stats, calls


class PerformanceParityTests(unittest.TestCase):
    def compare(self, items, parsed, registry, **kw):
        old = run(BASELINE, items, parsed, registry, **kw)
        new = run(perf.optimize_arbitration(BASELINE), items, parsed, registry, **kw)
        self.assertEqual(old[0], new[0])
        for name in ('ownership_claims','single_owner_services','persisted_locked_services',
                     'suppressed_duplicate_refs','stale_persisted_fallback_services'):
            self.assertEqual(old[1][name], new[1][name], name)
        return old, new

    def test_different_source_priority_requires_no_fuzzy_name_scores(self):
        items = [{'id':str(i), 'rank':i} for i in range(54)]
        registry = {str(i):{'name':'Example '+str(i)} for i in range(40)}
        parsed = {x['id']:{'id.'+r:[r] for r in registry} for x in items}
        old, new = self.compare(items, parsed, registry)
        self.assertGreater(len(old[2]), 2000)
        self.assertEqual(len(new[2]), 0)

    def test_shared_rytec_maps_keep_every_native_id_with_reused_lexical_scores(self):
        items = [{'id':str(i),'rank':0,'provider':'RYTEC','channels_name':'shared'} for i in range(54)]
        registry = {'ref':{'name':'Alpha'}}
        parsed = {x['id']:{'alpha':['ref'],'alpha hd':['ref']} for x in items}
        old, new = self.compare(items, parsed, registry)
        self.assertEqual(len(new[0]), 54)
        self.assertEqual(len(new[2]), 2)
        self.assertGreater(len(old[2]), 100)

    def test_display_names_remain_source_specific_despite_shared_ids(self):
        items = [{'id':'a'},{'id':'b'}]
        displays = {'a':[{'channel_id':'same','display_name':'Wrong'}],
                    'b':[{'channel_id':'same','display_name':'Alpha'}]}
        old,new = self.compare(items, {'a':{'same':['r']},'b':{'same':['r']}},
                               {'r':{'name':'Alpha'}},displays=displays)
        self.assertEqual(set(new[0]), {'b'})

    def test_exceptions_zero_scores_stable_ties_and_job_local_cache(self):
        items=[{'id':'a'},{'id':'b'}]; parsed={'a':{'x':['r']},'b':{'y':['r']}}
        self.compare(items,parsed,{'r':{'name':'Alpha'}},fail=True)
        _,new=self.compare(items,parsed,{'r':{'name':'Y'}})
        self.assertEqual(set(new[0]),{'b'})
        _,new=self.compare(items,parsed,{'r':{'name':'X'}})
        self.assertEqual(set(new[0]),{'a'})

    def test_generated_priority_locks_stale_owners_and_aliases_match_baseline(self):
        rng=random.Random(20261024)
        for _ in range(60):
            items=[{'id':str(i),'rank':rng.randrange(3),
                    'provider':'RYTEC' if i<3 else 'GITHUB',
                    'channels_name':'shared' if i<2 else 'other'} for i in range(4)]
            refs=[str(i) for i in range(8)]
            registry={r:{'name':rng.choice(['Alpha','Beta','Gamma'])} for r in refs}
            parsed={x['id']:{c:[r for r in refs if rng.random()<.4]
                            for c in ('alpha','beta','gamma')} for x in items}
            persisted={r:[{'source_id':str(rng.randrange(6)),
                           'channel_id':rng.choice(['alpha','beta','stale']),
                           'mode':rng.choice(['manual','auto','smartmatch']),
                           'confidence':rng.randrange(100)} for _ in range(2)]
                       for r in refs if rng.random()<.5}
            self.compare(items,parsed,registry,persisted=persisted)

    def test_unreviewed_module_inventory_and_preimage_are_rejected(self):
        with self.assertRaisesRegex(ValueError,'two pinned'):perf.corrected({})
        with self.assertRaisesRegex(ValueError,'baseline drift'):
            perf.corrected({p:b'changed' for p in perf.ORIGINAL})

    def test_package_rejects_unknown_ipk_without_creating_candidate_artifacts(self):
        with tempfile.TemporaryDirectory(dir=ROOT.parent) as tmp:
            root=Path(tmp);source=root/'unqualified.ipk';source.write_bytes(b'wrong package')
            with self.assertRaisesRegex(ValueError,'identity mismatch'):
                package.build(source,root)
            self.assertEqual(list(root.iterdir()),[source])

    def test_package_refuses_repository_output_before_reading_an_ipk(self):
        with self.assertRaisesRegex(ValueError,'private output'):
            package.build('not-read.ipk',ROOT)


if __name__ == '__main__': unittest.main()
