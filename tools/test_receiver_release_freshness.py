"""Expired XMLTV must fail even when an old manifest claims 48h coverage."""
from datetime import datetime, timedelta, timezone
import gzip
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET

from tools import receiver_release_gate as gate
from tools.receiver_contract import PROVIDERS, PREMIUM_INTERNATIONAL
from tools.test_mena_readiness_audit import fixture, check

ROOT=Path(__file__).resolve().parents[1]


def publication(folder, expired):
    """Synthetic canonical release fixture; never published or sent to a receiver."""
    now=datetime.now(timezone.utc).replace(microsecond=0)
    leaf={name:set(required) for name,(_,required,_) in PROVIDERS.items()}
    leaf['provider-international']=set(PREMIUM_INTERNATIONAL)
    leaf['mena-eg']={'News.eg'}
    owner={cid:name for name,ids in leaf.items() for cid in ids}
    premium=leaf['provider-bein']|leaf['provider-osn']
    combined=set(owner)
    groups=dict(leaf, **{'mena-arabic':combined, 'premium':premium, 'mena':combined-premium})
    profiles={}
    for name,ids in groups.items():
        root=ET.Element('tv')
        for cid in sorted(ids):
            ET.SubElement(root,'channel',id=cid)
        for cid in sorted(ids):
            start=now-timedelta(days=4) if expired else now-timedelta(hours=1)
            p=ET.SubElement(root,'programme',channel=cid,
                            start=start.strftime('%Y%m%d%H%M%S %z'),
                            stop=(start+timedelta(hours=48)).strftime('%Y%m%d%H%M%S %z'))
            title='Official title' if owner[cid] in ('provider-osn','provider-bein','provider-international') else 'برنامج رسمي'
            ET.SubElement(p,'title').text=title
            ET.SubElement(p,'desc').text='وصف رسمي باللغة العربية'
        raw=gzip.compress(ET.tostring(root,encoding='utf-8'),mtime=0)
        (folder/(name+'.xml.gz')).write_bytes(raw)
        profiles[name]={'channels':len(ids),'programmes':len(ids),
                        'size_bytes':len(raw),'sha256':hashlib.sha256(raw).hexdigest()}
    manifest=dict(profiles['mena-arabic'], coverage={'median_future_hours':999},
                  splits={name:profiles[stem] for name,stem in (
                      ('mena','mena'),('premium','premium'),('legacy_combined','mena-arabic'))})
    (folder/'manifest.json').write_text(json.dumps(manifest))
    (folder/'shards.json').write_text(json.dumps({'shards':{name:profiles[name] for name in leaf},
        'published_channels':len(combined),'input_channels':len(combined)}))
    (folder/'receiver-id-aliases.json').write_text(json.dumps({'canonical_ids':sorted(combined),'mapping':{}}))


class ReceiverFreshness(unittest.TestCase):
    def run_gate(self, folder, artifact_checks_only=False):
        args=[sys.executable,str(ROOT/'tools/receiver_release_gate.py'),
              '--dir',str(folder),'--json',str(folder/'result.json')]
        if artifact_checks_only:
            args.append('--artifact-checks-only')
        result=subprocess.run(args,
                              capture_output=True,timeout=30)
        self.assertTrue((folder/'result.json').exists(),result.stderr)
        return result,json.loads((folder/'result.json').read_text())

    def test_expired_compressed_programmes_override_positive_manifest(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);publication(root,expired=True)
            result,report=self.run_gate(root)
            self.assertEqual(result.returncode,1)
            self.assertEqual(report['status'],'FAIL')
            self.assertEqual(report['coverage']['median_future_hours'],0)
            self.assertEqual(report['manifest_declared_coverage']['median_future_hours'],999)
            self.assertTrue(any('NO_FUTURE_EPG_IDS' in e for e in report['errors']))
            self.assertEqual(report['receiver_release_status'],'NO_GO')

    def test_fresh_artifacts_do_not_claim_hardware_validation_or_authorization(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);publication(root,expired=False)
            result,report=self.run_gate(root,artifact_checks_only=True)
            self.assertEqual(result.returncode,0,report['errors'])
            self.assertEqual(report['status'],'PASS')
            self.assertGreater(report['coverage']['median_future_hours'],46)
            self.assertLess(report['coverage']['median_future_hours'],49)
            self.assertEqual(report['receiver_release_status'],'NO_GO')
            self.assertFalse(report['enigma2_receiver_validated'])
            self.assertFalse(report['release_authorized'])

    def test_default_release_mode_blocks_even_structurally_fresh_artifacts(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);publication(root,expired=False)
            result,report=self.run_gate(root)
            self.assertEqual(report['artifact_checks_status'],'PASS')
            self.assertEqual(result.returncode,1)
            self.assertEqual(report['status'],'FAIL')
            self.assertIn('ENIGMA2_RECEIVER_NOT_VALIDATED',report['errors'])
            self.assertFalse(report['release_authorized'])

    def test_invalid_intervals_cannot_count_as_future_epg(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);publication(root,expired=False)
            path=root/'mena-eg.xml.gz'
            tv=ET.fromstring(gzip.decompress(path.read_bytes()))
            tv.find('programme').set('start','invalid')
            path.write_bytes(gzip.compress(ET.tostring(tv)))
            errors=[];profile=gate.check_xml(path,errors)
            self.assertEqual(profile['future_programme_count'],0)
            self.assertTrue(any('INVALID_PROGRAMME_TIMES' in e for e in errors))

    def test_source_ready_status_is_still_receiver_no_go(self):
        with tempfile.TemporaryDirectory() as tmp:
            _,_,now=fixture(tmp)
            report=check(tmp,now)
            self.assertEqual(report['status'],'READY')
            self.assertEqual(report['scope'],'source_snapshot_diagnostic')
            self.assertEqual(report['receiver_release_status'],'NO_GO')
            self.assertFalse(report['receiver_release_eligible'])
            self.assertIn('ENIGMA2_RECEIVER_NOT_VALIDATED',report['receiver_release_blockers'])


if __name__=='__main__':
    unittest.main()
