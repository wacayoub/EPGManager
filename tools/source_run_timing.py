"""Observe each daily source target without rejecting delayed GitHub runs."""
import argparse
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
from zoneinfo import ZoneInfo


def observation(source, event, now, policy):
    if now.tzinfo is None:
        raise ValueError('Timezone-aware observation required')
    row = policy['sources'][source]
    local = now.astimezone(ZoneInfo(row['timezone']))
    hour, minute = map(int, row['time'].split(':'))
    target = local.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if target > local:
        target -= timedelta(days=1)
    scheduled = event in ('schedule', 'source-clock')
    delay = round((local-target).total_seconds()) if scheduled else None
    return dict(source=source, event=event, observed_utc=now.astimezone(timezone.utc).isoformat(),
                daily_target=row['time'], timezone=row['timezone'],
                scheduled_for_utc=target.astimezone(timezone.utc).isoformat() if scheduled else None,
                observed_delay_seconds=delay, delayed=scheduled and delay > 900,
                github_run_id=os.environ.get('GITHUB_RUN_ID',''),
                note='Observation at job setup includes delivery, queue and setup delay; a delay of more than one day is ambiguous. Manual runs have no scheduled delay.')


if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source',required=True)
    parser.add_argument('--event',required=True)
    parser.add_argument('--output',required=True,type=Path)
    args=parser.parse_args()
    policy=json.loads(Path('config/source-schedules.json').read_text(encoding='utf-8'))
    result=observation(args.source,args.event,datetime.now(timezone.utc),policy)
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(result,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(result))
    if result['delayed']:
        print(f"::warning::{args.source} observed late by {result['observed_delay_seconds']} seconds")
