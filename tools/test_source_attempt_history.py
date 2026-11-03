"""A later daily refresh must not erase proof of a successful source attempt."""
from pathlib import Path
import json
import shutil
import subprocess
import tempfile
import unittest

from tools.test_all_source_recovery import sample
from tools.verify_source_attempt import verify


class SourceAttemptHistory(unittest.TestCase):
    def git(self,root,*args):
        result=subprocess.run(['git','-c','user.name=Test','-c','user.email=test@example.invalid',*args],cwd=root,capture_output=True)
        self.assertEqual(result.returncode,0,result.stderr)
        return result.stdout.decode().strip()

    def setup_repo(self,root):
        if not shutil.which('git'):self.skipTest('git unavailable')
        self.git(root,'init','-b','main')
        (root/'reports').mkdir()

    def publish(self,root,run,published=True,corrupt=False):
        path=root/'feeds/osn.xml.gz';sample(path)
        meta=json.loads(path.with_name('osn.json').read_text())
        if corrupt:
            meta['sha256']='0'*64
            path.with_name('osn.json').write_text(json.dumps(meta))
        report={'github_run_id':run,'github_run_attempt':'1','sources':{'osn':{
            'published':published,'decision':'PUBLISH' if published else 'KEEP_LKG',
            'candidate_stats':{'channels':1,'programmes':1}}}}
        (root/'reports/osn-publish-lkg.json').write_text(json.dumps(report))
        self.git(root,'add','.');self.git(root,'commit','-m','source run '+run)
        return self.git(root,'rev-parse','HEAD')

    def test_original_success_verifies_after_other_run_overwrites_latest_report(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);self.setup_repo(root)
            original=self.publish(root,'111')
            self.publish(root,'222')
            row=verify(root,'osn','111')
            self.assertEqual(row['verified_publication_commit'],original)
            self.assertEqual(row['producer_run_id'],'111')
            self.assertEqual(row['receiver_release_status'],'NO_GO')

    def test_unknown_or_different_attempt_cannot_borrow_latest_success(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);self.setup_repo(root);self.publish(root,'222')
            with self.assertRaisesRegex(ValueError,'No publication evidence'):
                verify(root,'osn','111')
            with self.assertRaisesRegex(ValueError,'No publication evidence'):
                verify(root,'osn','222','2')

    def test_kept_lkg_is_not_current_attempt_publication(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);self.setup_repo(root);self.publish(root,'111',published=False)
            with self.assertRaisesRegex(ValueError,'LKG was retained'):
                verify(root,'osn','111')

    def test_bad_old_bytes_are_not_certified_by_a_later_valid_refresh(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);self.setup_repo(root)
            self.publish(root,'111',corrupt=True);self.publish(root,'222')
            with self.assertRaisesRegex(ValueError,'mismatched publication bundle'):
                verify(root,'osn','111')


if __name__=='__main__':unittest.main()
