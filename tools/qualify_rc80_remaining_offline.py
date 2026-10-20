"""Exercise SHA-verified receiver behavior in isolated workstation state.

No receiver connection, real eTimer, user mappings or EPG cache is used.
Source is supplied privately; only aggregate results may be published.
"""
import argparse
import base64
import copy
from datetime import datetime, timedelta, timezone
import hashlib
import json
import logging
from pathlib import Path
import sys
import tempfile
import types

PINS = {
    'core/mapping_store.py': '1d14fa0c1ec2bb08232388cb817439c6be94f455071e7ea13ea451929f9ae9cd',
    'core/scheduler.py': '29ac8ff2e95cd4ec5de34939881ecd789d66552be71c83c717976b11cba37594',
    'core/timezone_manager.py': '760994876c024723390a4b85aaf935c5288fe57379087c3a2fd864fc36384a15',
}


def qualify(snapshot, output):
    if Path('/proc/stb/info/model').exists():
        raise ValueError('workstation-only qualification')
    output = Path(output).resolve()
    if not output.parent.is_dir():
        raise ValueError('existing private output directory required')
    doc = json.loads(Path(snapshot).read_text(encoding='utf-8'))
    sources = {}
    for rel, pin in PINS.items():
        raw = base64.b64decode(doc['source_files'][rel], validate=True)
        if hashlib.sha256(raw).hexdigest() != pin:
            raise ValueError('receiver source drift: '+rel)
        sources[rel] = raw
    name = '_rc80_isolated_qualification'
    package = types.ModuleType(name)
    package.__path__ = []
    sys.modules[name] = package
    def stub(leaf, **values):
        module = types.ModuleType(name+'.'+leaf)
        module.__dict__.update(values)
        sys.modules[module.__name__] = module
        setattr(package, leaf, module)
        return module
    quiet_logger = logging.getLogger('rc80.offline')
    quiet_logger.addHandler(logging.NullHandler())
    quiet_logger.propagate = False
    stub('logger', get_logger=lambda key: quiet_logger)
    stub('json_atomic', read_json=lambda *a, **k: {}, mutate_json=lambda *a, **k: None)
    stub('source_catalog', by_mapping_id=lambda: {'fixture_a':{}, 'fixture_b':{}})
    stub('channel_registry', canonical_service_ref=lambda value: value.rstrip(':').lower())
    stub('srp_channel_map', load_index=lambda: {'sources':{}},
         apply_mapping_override=lambda *a, **k: None)
    class Timer:
        def __init__(self):
            self.callback = []
            self.started = None
        def start(self, ms, singleShot=False): self.started = (ms, singleShot)
        def stop(self): self.started = None
    previous_enigma = sys.modules.get('enigma')
    fake_enigma = types.ModuleType('enigma')
    fake_enigma.eTimer = Timer
    sys.modules['enigma'] = fake_enigma
    def load(rel):
        leaf = Path(rel).stem
        module = stub(leaf)
        exec(compile(sources[rel], '<verified-private>/'+rel, 'exec'), module.__dict__)
        return module
    checks = {}
    def check(label, condition):
        if not condition:
            raise AssertionError(label)
        checks[label] = True
    try:
        mapping = load('core/mapping_store.py')
        tz = load('core/timezone_manager.py')
        scheduler = load('core/scheduler.py')
        with tempfile.TemporaryDirectory(prefix='rc80-behavior-', dir=output.parent) as folder:
            path = Path(folder)/'synthetic-mappings.json'
            store = mapping.MappingStore(str(path))
            manual_ref = '1:0:1:1:2:3:400000:0:0:0:'
            auto_ref = '1:0:1:2:2:3:400000:0:0:0:'
            store.set('fixture_a','manual',[manual_ref],mode='manual')
            manual = copy.deepcopy(store.get('fixture_a','manual'))
            store.bulk_assign_auto_exclusive([
                {'source_id':'fixture_b','channel_id':'attempt','refs':[manual_ref],'confidence':100}])
            check('auto_does_not_replace_manual_owner', store.get('fixture_a','manual') == manual and store.get('fixture_b','attempt') is None)
            before = copy.deepcopy(store.all())
            store.bulk_assign_auto_exclusive([
                {'source_id':'fixture_a','channel_id':'auto','refs':[auto_ref],'confidence':100}],label='Auto Repair qualification')
            check('auto_save_preserves_manual',store.get('fixture_a','manual')==manual and store.get('fixture_a','auto')['mode']=='auto-repair')
            check('auto_undo_available',store.can_undo_last_auto_repair())
            check('auto_undo_restores_exact_mapping_dictionary',store.undo_last_auto_repair() and store.all()==before)
            store.set('fixture_b','manual-edit',[auto_ref],mode='manual')
            check('manual_undo_restores_dictionary',store.undo_last() and store.all()==before)
            mapping._STORE_CACHE.clear(); mapping._STORE_INSTANCES.clear()
            check('save_survives_fresh_store_load',mapping.MappingStore(str(path)).all()==before)
        class Clock:
            current = datetime(2026,10,20,10,0,0,tzinfo=timezone.utc)
        class FrozenDateTime(datetime):
            @classmethod
            def now(cls, zone=None):
                return Clock.current.astimezone(zone) if zone else Clock.current.replace(tzinfo=None)
        tz.datetime = FrozenDateTime
        scheduler.time = types.SimpleNamespace(time=lambda: Clock.current.timestamp())
        zone = tz.TimezoneManager(use_hijri_crosscheck=False)
        check('project_GMT0_now',zone.now().utcoffset()==timedelta(0))
        wall=datetime(2026,10,20,20,30,0)
        aware=zone.localize(wall)
        check('GMT0_XMLTV_format',zone.format_xmltv(aware)=='20261020203000 +0000')
        check('GMT0_epoch_roundtrip',zone.from_timestamp(aware.timestamp())==aware)
        class Config:
            enabled=True
            last=None
            def get_native_import_schedule_enabled(self): return self.enabled
            def get_native_import_time(self): return (10,0)
            def get_last_native_import(self): return self.last
            def set_last_native_import(self,value): self.last=value
        cfg=Config();calls=[]
        runner=types.SimpleNamespace(running=True,done=False,result=None)
        sch=scheduler.NativeImportScheduler(cfg,lambda: calls.append('start') or runner)
        sch.start()
        check('poll_timer_armed_without_duplicate_callbacks',sch._timer.started==(60000,False) and len(sch._timer.callback)==1)
        sch._tick()
        check('boot_grace_prevents_import',not calls)
        Clock.current+=timedelta(minutes=5,seconds=1)
        cfg.enabled=False;sch._tick()
        check('disabled_scheduler_does_not_import',not calls)
        cfg.enabled=True;sch._tick();sch._tick()
        check('due_scheduler_starts_once_while_runner_busy',len(calls)==1)
        runner.running=False;runner.done=True;runner.result={'ok':True}
        sch._tick();sch._tick()
        check('successful_slot_not_repeated',len(calls)==1 and cfg.last==Clock.current.timestamp())
        cfg=Config();busy_calls=[]
        busy=scheduler.NativeImportScheduler(cfg,lambda: busy_calls.append(1) or False)
        Clock.current+=timedelta(minutes=5,seconds=1)
        busy._tick();busy._tick()
        check('busy_scheduler_retries_without_marking_success',len(busy_calls)==2 and cfg.last is None)
        cfg=Config();fail_calls=[]
        failed=scheduler.NativeImportScheduler(cfg,lambda: fail_calls.append(1) or None)
        Clock.current+=timedelta(minutes=5,seconds=1)
        failed._tick();failed._tick()
        check('failed_slot_backoff_no_success_timestamp',len(fail_calls)==1 and cfg.last is None)
        Clock.current+=timedelta(minutes=10);failed._tick()
        Clock.current+=timedelta(minutes=10);failed._tick();failed._tick()
        check('failed_slot_bounded_to_three_attempts',len(fail_calls)==3 and failed._blocked_failure_slot is not None and cfg.last is None)
        Clock.current+=timedelta(days=1);failed._tick()
        check('next_day_slot_can_retry',len(fail_calls)==4)
        report={'status':'PASS_OFFLINE_ISOLATED_RECEIVER_SOURCE','checks':checks,
                'checks_passed':len(checks),'verified_source_sha256':PINS,
                'receiver_modified':False,'user_mapping_data_read':False,
                'real_enigma_timer_tested':False,'real_GUI_mapping_writes_tested':False,
                'timezone_scope':'project-requested GMT+0 policy, not legal tzdata certification'}
        output.write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
        return report
    finally:
        if previous_enigma is None: sys.modules.pop('enigma',None)
        else: sys.modules['enigma']=previous_enigma
        for key in list(sys.modules):
            if key==name or key.startswith(name+'.'): sys.modules.pop(key,None)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--snapshot',required=True)
    parser.add_argument('--output',required=True)
    args=parser.parse_args()
    print(json.dumps(qualify(args.snapshot,args.output),indent=2))
