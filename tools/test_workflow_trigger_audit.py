"""Trigger evidence must distinguish active, unavailable and skipped collection."""
from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
import tempfile
import unittest
import yaml

from tools.workflow_trigger_audit import audit, collection_evidence, delivery_timing


class FakeGitHub:
    def __init__(self):
        self.workflows=[{'id':1,'path':'.github/workflows/refresh-tabie.yml','state':'active'}]
        self.runs=[];self.jobs=[];self.unavailable=False

    def pages(self,path,key):
        return deepcopy(self.workflows if key=='workflows' else self.jobs)

    def get(self,path,**params):
        if self.unavailable:raise RuntimeError('API unavailable')
        return {'workflow_runs':deepcopy(self.runs)}


class TriggerAuditTests(unittest.TestCase):
    def setUp(self):
        self.folder=tempfile.TemporaryDirectory();self.addCleanup(self.folder.cleanup)
        self.root=Path(self.folder.name)
        (self.root/'config').mkdir();(self.root/'.github/workflows').mkdir(parents=True)
        (self.root/'config/source-schedules.json').write_text(json.dumps({'sources':{'tabie':{'time':'00:30','timezone':'Africa/Casablanca'}}}))
        (self.root/'.github/workflows/refresh-tabie.yml').write_text('name: Tabie\n')
        self.client=FakeGitHub()

    def test_active_workflow_without_scheduled_run_is_not_claimed_certified(self):
        result=audit(self.root,self.client)
        self.assertEqual(result['status'],'REVIEW');self.assertEqual(result['errors'],[])
        self.assertEqual(result['sources'][0]['scheduled_collection_evidence'],'NOT_OBSERVED')
        self.assertFalse(result['automatic_schedule_delivery_certified'])
        self.assertFalse(result['mutations_performed'])

    def test_disabled_or_missing_required_workflow_blocks(self):
        self.client.workflows[0]['state']='disabled_inactivity'
        self.assertEqual(audit(self.root,self.client)['status'],'BLOCKED')
        self.client.workflows=[]
        self.assertEqual(audit(self.root,self.client)['status'],'BLOCKED')

    def test_missing_default_branch_file_blocks_even_when_registry_active(self):
        (self.root/'.github/workflows/refresh-tabie.yml').unlink()
        self.assertIn('tabie:MISSING_DEFAULT_BRANCH_WORKFLOW',audit(self.root,self.client)['errors'])

    def test_removed_active_registration_is_reported_without_mutation(self):
        self.client.workflows.append({'id':2,'path':'.github/workflows/retired-probe.yml','state':'active'})
        before=deepcopy(self.client.workflows)
        self.assertEqual(audit(self.root,self.client)['retired_active_workflows'],[{'id':2,'path':'.github/workflows/retired-probe.yml'}])
        self.assertEqual(self.client.workflows,before)

    def test_unknown_api_cannot_return_clean_status(self):
        self.client.unavailable=True
        self.assertEqual(audit(self.root,self.client)['status'],'UNVERIFIED')

    def test_disabled_historical_registration_does_not_shadow_active_one(self):
        self.client.workflows.append({'id':0,'path':'.github/workflows/refresh-tabie.yml','state':'disabled_manually'})
        self.assertEqual(audit(self.root,self.client)['errors'],[])

    def test_success_with_skipped_collection_is_distinct_from_actual_execution(self):
        run={'id':100,'conclusion':'success'}
        jobs=[{'steps':[{'name':'Refresh source guide (48h)','conclusion':'skipped'},
                         {'name':'Set up job','conclusion':'success'}]}]
        self.assertEqual(collection_evidence(run,jobs),'COLLECTION_SKIPPED')
        jobs[0]['steps'][0]['conclusion']='success'
        self.assertEqual(collection_evidence(run,jobs),'COLLECTION_STEP_SUCCEEDED')
        self.assertEqual(collection_evidence(run,[]),'COLLECTION_NOT_PROVEN')

    def test_failed_scheduled_run_is_flagged_even_when_collection_executed(self):
        self.client.runs=[{'id':100,'conclusion':'failure'}]
        self.client.jobs=[{'steps':[{'name':'Refresh source guide (48h)','conclusion':'success'}]}]
        self.assertIn('tabie:LAST_SCHEDULED_RUN_FAILURE',audit(self.root,self.client)['warnings'])

    def test_legacy_source_named_step_counts_but_catalogue_steps_do_not(self):
        run={'id':100,'conclusion':'success'}
        jobs=[{'steps':[{'name':'Refresh live OSN channel catalogue','conclusion':'success'},
                         {'name':'Refresh osn for 48h','conclusion':'skipped'}]}]
        self.assertEqual(collection_evidence(run,jobs),'COLLECTION_SKIPPED')
        jobs[0]['steps'][1]['conclusion']='success'
        self.assertEqual(collection_evidence(run,jobs),'COLLECTION_STEP_SUCCEEDED')

    def test_watchdog_audit_does_not_block_valid_rebuild_dispatch(self):
        root=Path(__file__).resolve().parents[1]
        workflow=yaml.load((root/'.github/workflows/epg-watchdog.yml').read_text(),Loader=yaml.BaseLoader)
        steps=workflow['jobs']['verify']['steps']
        self.assertEqual(workflow['jobs']['verify']['timeout-minutes'],'10')
        rebuild=next(i for i,s in enumerate(steps) if 'gh workflow run rebuild-mena-derived.yml' in s.get('run',''))
        trigger=next(i for i,s in enumerate(steps) if s.get('id')=='triggers')
        self.assertLess(rebuild,trigger)
        self.assertNotIn('continue-on-error',steps[trigger])
        self.assertNotIn('self-hosted','\n'.join(p.read_text() for p in (root/'.github/workflows').glob('*.yml')))

    def test_late_success_is_reported_and_queue_time_is_separate(self):
        now=datetime(2026,11,6,8,0,tzinfo=timezone.utc)
        self.client.runs=[{'id':100,'conclusion':'success','created_at':'2026-11-06T07:10:00Z'}]
        self.client.jobs=[{'steps':[{'name':'Refresh source guide (48h)',
                                    'conclusion':'success','started_at':'2026-11-06T07:10:20Z'}]}]
        result=audit(self.root,self.client,now)
        self.assertEqual(result['status'],'REVIEW')
        timing=result['sources'][0]['delivery_timing']
        self.assertEqual(timing['trigger_delay_seconds'],24000)
        self.assertEqual(timing['creation_to_collection_seconds'],20)
        self.assertIn('tabie:TRIGGER_LATE_24000s',result['warnings'])

    def test_yesterdays_success_does_not_hide_todays_missing_trigger(self):
        now=datetime(2026,11,6,8,0,tzinfo=timezone.utc)
        schedule={'time':'00:30','timezone':'Africa/Casablanca'}
        timing=delivery_timing(schedule,{'created_at':'2026-11-05T00:31:00Z'},[],now)
        self.assertTrue(timing['latest_due_unobserved'])
        self.assertEqual(timing['trigger_delay_seconds'],60)

    def test_clock_driver_is_used_instead_of_the_native_fallback(self):
        policy={'sources':{'tabie':{'time':'00:30','timezone':'Africa/Casablanca'}},
                'schedule_driver':{'primary':'vuplus-clock','workflow':'vuplus-clock.yml'}}
        (self.root/'config/source-schedules.json').write_text(json.dumps(policy))
        self.client.runs=[{'id':100,'conclusion':'success','created_at':'2026-11-06T00:30:10Z',
                           'display_title':'Vu+ clock tabie 2026-11-06'}]
        self.client.jobs=[{'steps':[{'name':'Refresh source guide (48h)','conclusion':'success',
                                    'started_at':'2026-11-06T00:30:25Z'}]}]
        now=datetime(2026,11,6,0,40,tzinfo=timezone.utc)
        result=audit(self.root,self.client,now)
        self.assertEqual(result['status'],'NO_TRIGGER_FLAGS')
        self.assertEqual(result['sources'][0]['schedule_driver'],'vuplus-clock')


if __name__=='__main__':unittest.main()
