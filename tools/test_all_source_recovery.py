"""Every provider must run independently and verify its own final publication."""
from datetime import datetime, timedelta, timezone
import gzip
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
import yaml

from tools.source_health_snapshot import inspect
from tools.test_bundle_fixture import write_bundle_metadata

ROOT=Path(__file__).resolve().parents[1]


def sample(path):
    now=datetime.now(timezone.utc)
    root=ET.Element('tv')
    ET.SubElement(ET.SubElement(root,'channel',id='real.channel'),'display-name').text='Real Channel'
    p=ET.SubElement(root,'programme',channel='real.channel',
                    start=(now-timedelta(minutes=1)).strftime('%Y%m%d%H%M%S %z'),
                    stop=(now+timedelta(hours=36)).strftime('%Y%m%d%H%M%S %z'))
    ET.SubElement(p,'title').text='Real programme'
    ET.SubElement(p,'desc').text='Real description'
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_bytes(gzip.compress(ET.tostring(root),mtime=0))
    write_bundle_metadata(path)


class AllSourceRecovery(unittest.TestCase):
    def test_batch_runs_all_sources_and_checks_same_workflow_receipts(self):
        doc=yaml.load((ROOT/'.github/workflows/recover-all-sources.yml').read_text(),Loader=yaml.BaseLoader)
        expected=json.loads((ROOT/'config/canonical-channel-policy.json').read_text())['default_source_order']
        self.assertNotIn('schedule',doc['on'])
        for name in ('refresh','verify'):
            self.assertEqual(doc['jobs'][name]['strategy']['matrix']['source'],expected)
            self.assertEqual(doc['jobs'][name]['strategy']['max-parallel'],'4')
            self.assertEqual(doc['jobs'][name]['strategy']['fail-fast'],'false')
        self.assertEqual(doc['jobs']['verify']['with']['expected_run_id'],'${{ github.run_id }}')
        self.assertEqual(doc['jobs']['source_aggregate']['uses'],'./.github/workflows/rebuild-mena-derived.yml')

    def test_bad_source_checksum_is_explicit_and_does_not_break_next_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            for name in ('osn','shahid'):
                sample(root/'feeds'/(name+'.xml.gz'))
            path=root/'feeds/osn.json'
            meta=json.loads(path.read_text());meta['sha256']='0'*64;path.write_text(json.dumps(meta))
            self.assertEqual(inspect(root,'osn')['status'],'INVALID_PUBLICATION')
            self.assertEqual(inspect(root,'shahid')['status'],'AVAILABLE')

    def test_corrupt_gzip_is_not_available_even_with_matching_metadata(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);path=root/'feeds/osn.xml.gz';sample(path)
            path.write_bytes(b'bad gzip')
            meta=json.loads(path.with_name('osn.json').read_text())
            meta.update(sha256=hashlib.sha256(path.read_bytes()).hexdigest(),size_bytes=8)
            path.with_name('osn.json').write_text(json.dumps(meta))
            self.assertEqual(inspect(root,'osn')['status'],'INVALID_PUBLICATION')

    def test_bein_arabization_without_english_still_repacks_sidecars(self):
        bash=shutil.which('bash')
        if not bash and Path('C:/Program Files/Git/bin/bash.exe').exists():
            bash='C:/Program Files/Git/bin/bash.exe'
        if not bash:self.skipTest('bash unavailable')
        source=(ROOT/'tools/refresh_one_source.sh').read_text()
        pack='pack_source() {'+source.split('pack_source() {',1)[1].split('\n}\n',1)[0]+'\n}\n'
        branch=source.split('  bein)\n',1)[1].split('\n  osn)',1)[0]
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            (root/'tools').mkdir()
            for name in ('source_feed_pack.py','programme_authenticity.py','source_timeline.py'):
                shutil.copyfile(ROOT/'tools'/name,root/'tools'/name)
            sample(root/'fixture.xml.gz')
            (root/'fixture.xml').write_bytes(gzip.decompress((root/'fixture.xml.gz').read_bytes()))
            (root/'fake_arabize.py').write_text(
                "import sys,gzip,xml.etree.ElementTree as E\n"
                "r=E.fromstring(gzip.decompress(open(sys.argv[sys.argv.index('--input')+1],'rb').read()))\n"
                "r.find('programme/desc').text='AR description changed'\n"
                "open(sys.argv[sys.argv.index('--output')+1],'wb').write(gzip.compress(E.tostring(r)))\n")
            script='''set -euo pipefail
mkdir -p output/source-build output/final
ROOT="$PWD";HOURS=48;SOURCE=bein
grab_upstream() { if [ "$1" = bein ]; then cp fixture.xml "$3"; else echo '<tv/>' > "$3"; fi; }
has_programmes() { grep -q '<programme ' "$1" 2>/dev/null; }
python() { if [ "$1" = tools/bein_arabize.py ]; then "$RECOVERY_PYTHON" fake_arabize.py "$@"; else "$RECOVERY_PYTHON" "$@"; fi; }
'''+pack+'\n'+branch.removesuffix('    ;;\n').removesuffix('    ;;')
            env=dict(os.environ,RECOVERY_PYTHON=sys.executable)
            result=subprocess.run([bash,'-c',script],cwd=root,env=env,capture_output=True,timeout=30)
            self.assertEqual(result.returncode,0,result.stderr)
            path=root/'output/final/bein.xml.gz';meta=json.loads(path.with_name('bein.json').read_text())
            self.assertEqual(meta['sha256'],hashlib.sha256(path.read_bytes()).hexdigest())
            self.assertEqual(meta['size_bytes'],len(path.read_bytes()))
            self.assertEqual(ET.fromstring(gzip.decompress(path.read_bytes())).findtext('programme/desc'),'AR description changed')


if __name__=='__main__':unittest.main()
