"""Report observed delay from the unchanged 07:07 UTC target; no scraping."""
import argparse
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path


def observation(event, now):
    if now.tzinfo is None:
        raise ValueError('Timezone-aware observation required')
    now = now.astimezone(timezone.utc)
    target = now.replace(hour=7, minute=7, second=0, microsecond=0)
    if target > now:
        target -= timedelta(days=1)
    scheduled = event == 'schedule'
    delay = round((now-target).total_seconds()) if scheduled else None
    return dict(event=event, observed_utc=now.isoformat(), daily_target='07:07 GMT+0',
                scheduled_for_utc=target.isoformat() if scheduled else None,
                observed_delay_seconds=delay,
                delayed=scheduled and delay > 900,
                note='Observed at job setup; includes trigger, queue and setup delay. Manual retries are not scheduled runs.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--event', required=True)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    result = observation(args.event, datetime.now(timezone.utc))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(result))
    if result['delayed']:
        print('::warning::Morocco started late: observed delay %ss from 07:07 GMT+0' % result['observed_delay_seconds'])
    summary = os.environ.get('GITHUB_STEP_SUMMARY')
    if summary:
        with open(summary,'a',encoding='utf-8') as handle:
            handle.write('\n### Morocco scheduling\n\n```json\n'+json.dumps(result,indent=2)+'\n```\n')


if __name__ == '__main__': main()
