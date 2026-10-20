"""Read-only offline checks of a SHA-pinned RC80 UI staging directory.

Usage: python tools/audit_rc80_readable_ui.py <private-staging-directory>
No Enigma2 imports, receiver access or file writes.
"""
import ast,types,runpy,json,time,xml.etree.ElementTree as ET
from pathlib import Path
import sys
import prepare_rc80_readable_ui as patch
root=Path(sys.argv[1]).resolve()
source={n:(root/'baseline'/n).read_bytes() for n in patch.ORIGINAL}
expected=patch.corrected(source)
for name, data in expected.items():
    if (root/'candidate'/name).read_bytes() != data:
        raise ValueError('candidate differs from pinned UI patch: '+name)
before={n:(root/'baseline/ui'/n).read_text(encoding='utf-8') for n in ('main.py','channel_mapping.py','theme.py')};after={n:(root/'candidate/ui'/n).read_text(encoding='utf-8') for n in before}
def funcs(s):
 t=ast.parse(s);return {n.name:ast.dump(n,include_attributes=False) for n in ast.walk(t) if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef))}
# Compare every top-level class's methods to avoid duplicate function names.
for file,allowed in [('main.py',{'EPGManagerMainScreen':{'__init__','_refresh','_import_note'}}),('channel_mapping.py',{'SafeMatchPaneList':{'__init__','_build_row'}})]:
 old,new=ast.parse(before[file]),ast.parse(after[file]);ocs={c.name:c for c in old.body if isinstance(c,ast.ClassDef)}
 for c in [c for c in new.body if isinstance(c,ast.ClassDef)]:
  previous={n.name:ast.dump(n,include_attributes=False) for n in ocs[c.name].body if isinstance(n,ast.FunctionDef)}
  for n in c.body:
   if isinstance(n,ast.FunctionDef) and n.name not in allowed.get(c.name,set()):assert previous[n.name]==ast.dump(n,include_attributes=False),(file,c.name,n.name)
ns=runpy.run_path(str(root/'candidate/ui/theme.py'));theme=types.SimpleNamespace(**ns)
t=ast.parse(after['channel_mapping.py']);c=next(n for n in t.body if isinstance(n,ast.ClassDef) and n.name=='SafeMatchPaneList');c.bases=[ast.Name(id='object',ctx=ast.Load())];c.body=[n for n in c.body if isinstance(n,ast.FunctionDef) and n.name in ('_flatten','_build_row')]
env={'theme':theme,'MultiContentEntryText':lambda **kw:kw,'RT_HALIGN_LEFT':1,'RT_VALIGN_CENTER':2,'RT_HALIGN_RIGHT':4};exec(compile(ast.fix_missing_locations(ast.Module(body=[c],type_ignores=[])),'<actual-candidate-pane>','exec'),env)
pane=env['SafeMatchPaneList']();pane._previews=['Current programme'];pane.health_colors=True
cases=[('EPG OK',False,False,'EPG OK'),('NO EPG',False,False,'NO EPG'),('GENERATED',False,False,'GEN EPG'),('REPEATED',False,False,'REPEAT?'),('EPG ?',False,False,'CHECK'),('EPG OK',True,False,'CHECK'),('EPG OK',True,True,'EPG OK')]
for health,review,mapped,label in cases:
 pane._meta=[dict(health=health,review=review,mapped=mapped)];row=pane._build_row(0,'Example ID');assert row[-1]['text']==label;assert row[2]['pos'][0]+row[2]['size'][0]<=row[-1]['pos'][0];assert row[0]=='Example ID'
 pane.health_colors=False;assert pane._build_row(0,'Example ID')[-1]['text']==label;pane.health_colors=True
m=next(n for n in ast.parse(after['main.py']).body if isinstance(n,ast.ClassDef) and n.name=='EPGManagerMainScreen');note=next(n for n in m.body if isinstance(n,ast.FunctionDef) and n.name=='_import_note');note.decorator_list=[];env={'time':time};exec(compile(ast.Module(body=[note],type_ignores=[]),'<actual-import-note>','exec'),env);f=env['_import_note']
assert f({})=='No import yet';assert f({'timestamp':'bad'})=='Time unknown';assert f({'timestamp':-1})=='Time unknown';assert f({'timestamp':1792485961})=='20/10 08:46 GMT+0';assert f({'timestamp':float('inf')})=='Time unknown'
# Evaluate actual dashboard skin definitions with inert Enigma2 UI imports removed.
t=ast.parse(after['main.py']);keep=[n for n in t.body if isinstance(n,(ast.FunctionDef,ast.Assign,ast.ClassDef))];env={'theme':theme,'Screen':object,'get_logger':lambda *a:None,'__name__':'preview'};exec(compile(ast.Module(body=keep,type_ignores=[]),'<actual-skins>','exec'),env)
for name in ('DuplicateAuditScreen','EPGManagerMainScreen'):
 skin=ET.fromstring(env[name].skin);assert skin.attrib['size']=='1920,1080'
 for w in skin.findall('widget'):
  x,y=map(int,w.attrib['position'].split(','));width,height=map(int,w.attrib['size'].split(','));assert x>=0 and y>=0 and x+width<=1920 and y+height<=1080,(name,w.attrib)
result={'receiver_changed':False,'candidate_python_modules':3,'syntax':'PASS','all_nonpresentation_methods_unchanged':True,'bestmatch_state_cases':14,'import_timestamp_cases':5,'actual_skin_xml_and_bounds':'PASS','hardware_gui':'NOT_TESTED'}
print(json.dumps(result))