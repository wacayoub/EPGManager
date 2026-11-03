from copy import deepcopy
import json
from pathlib import Path
import unittest
from tools.source_schedule_order import validate
from tools.sport24_verified_donors import DONORS
from tools.osn_zero_epg_fallback import LOCAL_CANDIDATES as OSN
from tools.elcinema_zero_epg_fallback import LOCAL_CANDIDATES as ELCINEMA
from tools.dubaiplus_verified_osn_gap import OSN_SOURCE

class ScheduleOrder(unittest.TestCase):
    def setUp(self):
        self.policy=json.loads((Path(__file__).resolve().parents[1]/'config/source-schedules.json').read_text())

    def test_dependency_registry_matches_actual_local_fallbacks(self):
        def candidates(rows):
            return {Path(path).name.split('.')[0] for choices in rows.values() for path,*_ in choices}
        deps=self.policy['local_feed_dependencies']
        self.assertEqual(set(deps['sport24']),{source for source,_ in DONORS.values()})
        self.assertEqual(set(deps['osn']),candidates(OSN))
        self.assertEqual(set(deps['elcinema']),candidates(ELCINEMA))
        self.assertEqual(deps['dubaiplus'],[OSN_SOURCE])
        self.assertTrue(validate(self.policy))

    def test_previous_donor_orders_are_rejected(self):
        for source,time in [('alkass','06:30'),('sport24','03:05'),('starzplay','05:12')]:
            with self.subTest(source=source):
                policy=deepcopy(self.policy);policy['sources'][source]['time']=time
                with self.assertRaises(ValueError):validate(policy)

    def test_postprocess_cannot_race_last_source(self):
        policy=deepcopy(self.policy);policy['postprocess']['time']='07:15'
        with self.assertRaisesRegex(ValueError,'postprocessing'):validate(policy)
