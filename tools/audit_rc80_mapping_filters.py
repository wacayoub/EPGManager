"""Offline actual-code filter checks; reads private staging, never writes.

Usage: python tools/audit_rc80_mapping_filters.py <staging-directory>
"""
from pathlib import Path
import ast,json,time,types,hashlib
import sys
import prepare_rc80_mapping_filters as fix
root=Path(sys.argv[1]).resolve()
expected=fix.corrected({'ui/channel_mapping.py':(root/'baseline/ui/channel_mapping.py').read_bytes()})
if (root/'candidate/ui/channel_mapping.py').read_bytes()!=expected['ui/channel_mapping.py']:
 raise ValueError('candidate does not match pinned filter patch')
old=(root/'baseline/ui/channel_mapping.py').read_text(encoding='utf-8');new=(root/'candidate/ui/channel_mapping.py').read_text(encoding='utf-8')
def members(text):return {c.name+'.'+n.name:ast.dump(n,include_attributes=False) for c in ast.parse(text).body if isinstance(c,ast.ClassDef) for n in c.body if isinstance(n,ast.FunctionDef)}
a,b=members(old),members(new);changed={n.split('.')[-1] for n in a if a[n]!=b[n]};assert changed=={'_timer_tick','_paint_channel_rows_cached','refresh_channels','update_focus','open_mapping_options','_mapping_option_selected','set_channel_filter'}
cls=next(n for n in ast.parse(new).body if isinstance(n,ast.ClassDef) and n.name=='ChannelMappingScreen');names={'_cached_primary_epg_health','_service_has_no_future_epg','_service_needs_review','refresh_channels','set_channel_filter'};nodes=[n for n in cls.body if isinstance(n,ast.FunctionDef) and n.name in names]
health={'healthy':'EPG_OK','absent':'NO_EPG','unknown':'UNKNOWN','manual':'EPG_OK','unmapped':'UNKNOWN','conflict':'EPG_OK','ignored':'EPG_OK'};env={'time':time,'perf_profiler':types.SimpleNamespace(record=lambda *a:None),'github_match_health':types.SimpleNamespace(health=lambda sid,cid:health.get(cid))}
module=ast.fix_missing_locations(ast.Module(body=[ast.ClassDef(name='Harness',bases=[],keywords=[],decorator_list=[],body=nodes)],type_ignores=[]));exec(compile(module,'<actual-filter-integration>','exec'),env)
class Label:
 def setText(self,s):self.text=s
class Screen(env['Harness']):
 def __getitem__(self,key):return self.labels.setdefault(key,Label())
s=Screen();rows=[dict(ref=k,name=k) for k in health];original=json.dumps(rows,sort_keys=True);s.bouquet_records=[];s.bouquet_services=list(rows);s._sat_sorted_services_by_reception={'fixture':list(rows)};s._visible_service_by_ref={};s._bouquet_service_cache={};s.index=0;s.labels={};s.selection_cache={'old':1};s.focus=0
s._current=lambda key,records: ({'virtual':'reception','reception_list':'fixture'} if key=='bouquets' else (records[min(s.index,len(records)-1)] if records else None));s._paint_channel_rows_cached=lambda:None;s._paint_is_mapped=lambda ref:ref!='unmapped';s._service_has_conflict=lambda row:row['ref']=='conflict';s._service_is_ignored=lambda row:row['ref']=='ignored';s._service_is_manual_locked=lambda row:row['ref']=='manual';s._service_is_auto_repaired=lambda row:False;s._primary_mapping_info_for_ref=lambda ref:dict(source_id='fixture',channel_id=ref);s._set_index=lambda key,index:setattr(s,'index',index);s.refresh_selection=lambda:None;s._set_list=lambda *a:None;s.update_focus=lambda:None;s.update_source_title=lambda:None;s._update_action_labels=lambda:None
expected={'no_future':['absent'],'review':['absent','unknown','unmapped','conflict','ignored'],'locked':['manual'],'all':list(health)};result={}
for mode,want in expected.items():
 s.set_channel_filter(mode);actual=[x['ref'] for x in s.bouquet_services];assert actual==want,(mode,actual);result[mode]=len(actual)
# Returning to all preserves the selected manual service instead of jumping.
assert s.bouquet_services[s.index]['ref']=='manual'
health['absent']='EPG_OK';s.set_channel_filter('no_future');assert s.bouquet_services==[] and s.selection_entries==[];s.set_channel_filter('all');assert len(s.bouquet_services)==len(rows);assert json.dumps(rows,sort_keys=True)==original
report={'receiver_changed':False,'candidate_syntax':'PASS','changed_existing_methods':sorted(changed),'all_other_methods_unchanged':True,'actual_refresh_and_filter_modes':result,'selection_preserved_when_still_visible':True,'empty_filter_and_return_all':'PASS','synthetic_mapping_input_unchanged':True,'new_filter_data_IO':'NONE; existing RAM health and owner lookups only','real_GUI':'NOT_TESTED'};print(json.dumps(report))
