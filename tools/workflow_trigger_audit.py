"""Read-only GitHub launch diagnostics; never dispatch, enable or cancel work."""
import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import re
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo
try:
    from tools.vuplus_clock import collection_gate, target
except ModuleNotFoundError:
    from vuplus_clock import collection_gate, target


class GitHub:
    def __init__(self, repository, token):
        if not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', repository):
            raise ValueError('Invalid repository')
        self.repository, self.token = repository, token

    def get(self, path, **params):
        url = 'https://api.github.com/repos/' + self.repository + '/' + path
        if params:
            url += '?' + urlencode(params)
        request = Request(url, headers={'Authorization': 'Bearer ' + self.token,
                          'Accept': 'application/vnd.github+json',
                          'X-GitHub-Api-Version': '2022-11-28'})
        try:
            with urlopen(request, timeout=30) as response:
                return json.load(response)
        except HTTPError as error:
            raise RuntimeError('GitHub API HTTP ' + str(error.code)) from None

    def pages(self, path, key):
        rows = []
        for page in range(1, 101):
            batch = self.get(path, per_page=100, page=page).get(key, [])
            rows.extend(batch)
            if len(batch) < 100:
                return rows
        raise RuntimeError('GitHub inventory pagination limit reached')


def collection_evidence(run, jobs):
    """A green workflow does not prove the collection step actually executed."""
    if not run:
        return 'NOT_OBSERVED'
    steps = [step for job in jobs for step in job.get('steps', [])
             if re.search(r'^(?:Refresh source guide\b|Refresh [a-z][a-z0-9]* for \d+h\b)', step.get('name', ''), re.I)]
    if any(step.get('conclusion') == 'success' for step in steps):
        return 'COLLECTION_STEP_SUCCEEDED'
    if steps and all(step.get('conclusion') == 'skipped' for step in steps):
        return 'COLLECTION_SKIPPED'
    return 'COLLECTION_NOT_PROVEN'


def delivery_timing(schedule, run, jobs, now):
    local = now.astimezone(ZoneInfo(schedule['timezone']))
    due = target(schedule, local.date())
    if due > local:
        due -= timedelta(days=1)
    created = datetime.fromisoformat(run['created_at'].replace('Z', '+00:00')) if run and run.get('created_at') else None
    if created is not None and created.tzinfo is None:
        raise ValueError('GitHub run creation has no timezone')
    run_due = target(schedule, created.astimezone(local.tzinfo).date()) if created else None
    if run_due and run_due > created:
        run_due -= timedelta(days=1)
    starts = [step['started_at'] for job in jobs for step in job.get('steps', [])
              if step.get('started_at') and step.get('name', '').startswith('Refresh source guide')]
    start = datetime.fromisoformat(min(starts).replace('Z', '+00:00')) if starts else None
    return {
        'latest_due_local': due.isoformat(),
        'run_created_local': created.astimezone(local.tzinfo).isoformat() if created else None,
        'trigger_delay_seconds': round((created-run_due).total_seconds()) if created else None,
        'creation_to_collection_seconds': round((start-created).total_seconds()) if start and created else None,
        'latest_due_unobserved': (now-due).total_seconds() > 900 and (created is None or created < due),
    }


def audit(root, client, now=None):
    root = Path(root)
    now = now or datetime.now(timezone.utc)
    policy = json.loads((root/'config/source-schedules.json').read_text(encoding='utf-8'))
    inventory = client.pages('actions/workflows', 'workflows')
    registered = {}
    for row in inventory:
        if row['path'] not in registered or row.get('state') == 'active':
            registered[row['path']] = row
    maintained = {'.github/workflows/' + p.name for p in (root/'.github/workflows').glob('*.yml')}
    errors, warnings = [], []
    sources = sorted(policy['sources'], key=lambda name: policy['sources'][name]['time'])
    driver = policy.get('schedule_driver') or {}
    clock_runs = []
    driver_error = None
    if driver.get('primary') == 'vuplus-clock':
        try:
            clock_runs = client.get('actions/workflows/'+driver['workflow']+'/runs',
                                    event='push', per_page=100).get('workflow_runs', [])
        except Exception as error:
            driver_error = type(error).__name__

    def source_row(source):
        if not re.fullmatch(r'[a-z][a-z0-9]{1,25}', source):
            raise ValueError('Invalid source name')
        filename = 'refresh-' + source + '.yml'
        path = '.github/workflows/' + filename
        entry = registered.get(path, {})
        row = {'source': source, 'workflow_path': path, 'workflow_id': entry.get('id'),
               'workflow_state': entry.get('state', 'NOT_REGISTERED'),
               'local_schedule': policy['sources'][source], 'issues': []}
        if path not in maintained:
            row['issues'].append('MISSING_DEFAULT_BRANCH_WORKFLOW')
        if row['workflow_state'] != 'active':
            row['issues'].append('WORKFLOW_NOT_ACTIVE')
        try:
            runs = client.get('actions/workflows/' + filename + '/runs', event='schedule', per_page=1).get('workflow_runs', [])
            native_run = runs[0] if runs else None
            run = native_run
            row['schedule_driver'] = driver.get('primary', 'github-schedule')
            if driver.get('primary') == 'vuplus-clock':
                row['last_native_scheduled_run'] = ({k: native_run.get(k) for k in
                    ('id', 'created_at', 'status', 'conclusion', 'html_url')} if native_run else None)
                run = next((r for r in clock_runs
                            if r.get('display_title', '').startswith('Vu+ clock '+source+' ')), None)
                if driver_error:
                    raise RuntimeError('External clock runs unavailable')
            row['last_scheduled_run'] = ({k: run.get(k) for k in
                ('id', 'created_at', 'status', 'conclusion', 'head_sha', 'html_url')} if run else None)
            jobs = client.pages('actions/runs/' + str(run['id']) + '/jobs', 'jobs') if run else []
            row['scheduled_collection_evidence'] = collection_evidence(run, jobs)
            if (row['scheduled_collection_evidence'] == 'COLLECTION_SKIPPED'
                    and not collection_gate(root, policy, source, 'schedule', '', now)[0]):
                row['scheduled_collection_evidence'] = 'DAILY_ATTEMPT_ALREADY_RECORDED'
            row['delivery_timing'] = delivery_timing(policy['sources'][source], run, jobs, now)
        except Exception as error:
            row['scheduled_collection_evidence'] = 'UNVERIFIED'
            row['api_error'] = type(error).__name__
        return row

    with ThreadPoolExecutor(max_workers=4) as pool:
        rows = list(pool.map(source_row, sources))
    for row in rows:
        errors.extend(row['source'] + ':' + issue for issue in row['issues'])
        if row['scheduled_collection_evidence'] not in ('COLLECTION_STEP_SUCCEEDED', 'DAILY_ATTEMPT_ALREADY_RECORDED'):
            warnings.append(row['source'] + ':' + row['scheduled_collection_evidence'])
        timing = row.get('delivery_timing') or {}
        if (timing.get('trigger_delay_seconds') or 0) > 900:
            warnings.append(row['source'] + ':TRIGGER_LATE_' + str(timing['trigger_delay_seconds']) + 's')
        if timing.get('latest_due_unobserved'):
            warnings.append(row['source'] + ':LATEST_DUE_RUN_NOT_OBSERVED')
        run = row.get('last_scheduled_run') or {}
        if run.get('conclusion') not in (None, 'success'):
            warnings.append(row['source'] + ':LAST_SCHEDULED_RUN_' + str(run['conclusion']).upper())
    retired_active = [{'id': row['id'], 'path': row['path']} for row in inventory
                      if row.get('state') == 'active' and row['path'] not in maintained]
    if retired_active:
        warnings.append('RETIRED_WORKFLOWS_STILL_ACTIVE:' + str(len(retired_active)))
    return {'checked_utc': now.isoformat(), 'scope': 'source_workflow_trigger_diagnostics',
            'status': 'BLOCKED' if errors else 'UNVERIFIED' if any('api_error' in row for row in rows) else 'REVIEW' if warnings else 'NO_TRIGGER_FLAGS',
            'sources': rows, 'errors': errors, 'warnings': warnings,
            'maintained_workflow_count': len(maintained),
            'inactive_retired_workflow_count': sum(row.get('state') != 'active' and row['path'] not in maintained for row in inventory),
            'retired_active_workflows': retired_active, 'mutations_performed': False,
            'automatic_schedule_delivery_certified': False, 'receiver_modified': False,
            'note': 'Missing scheduled runs are observations, not proof of a disabled trigger. GitHub may delay/drop schedule events. Historical successes do not certify the current code or the next scheduled run.'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', default='.')
    parser.add_argument('--repository', default=os.environ.get('GITHUB_REPOSITORY', ''))
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--github-output', type=Path)
    args = parser.parse_args()
    token = os.environ.get('GH_TOKEN') or os.environ.get('GITHUB_TOKEN')
    if not token:
        raise SystemExit('GitHub token unavailable')
    try:
        result = audit(args.root, GitHub(args.repository, token))
    except Exception as error:
        result = {'status': 'UNVERIFIED', 'errors': [type(error).__name__],
                  'mutations_performed': False, 'receiver_modified': False}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2)+'\n', encoding='utf-8')
    if args.github_output:
        with args.github_output.open('a', encoding='utf-8') as stream:
            stream.write('trigger_status=' + result['status'] + '\n')
    print('SOURCE_TRIGGER_AUDIT', result['status'])
    for issue in result.get('errors', []):
        print('::error::' + issue)
    for issue in result.get('warnings', []):
        print('::warning::' + issue)
    return 1 if result['status'] in ('BLOCKED', 'UNVERIFIED') else 0


if __name__ == '__main__':
    raise SystemExit(main())
