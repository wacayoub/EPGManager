"""SHA-pinned UI-only correction; does not connect to a receiver."""
import ast
import hashlib

ORIGINAL = {'ui/main.py': 'acffe08939b96c8f8899adcaf231843fe284fff9933c280055dfc4b1b72a2008'}

GUARD = '''        # OpenATV rejects an external skin missing mandatory content and uses
        # our embedded layout. Keep complete third-party dashboard skins usable.
        self.mandatoryWidgets = (
            ["kpi%d_%s" % (i, part) for i in range(1, 8)
             for part in ("title", "value", "note")] +
            ["card%d_%s" % (i, part) for i in range(1, 9)
             for part in ("title", "status", "subtitle", "left", "right", "progress", "footer")] +
            ["key_" + color for color in ("red", "green", "yellow", "blue")]
        )
'''


def guard_dashboard(text):
    tree = ast.parse(text)
    dashboard = next(n for n in tree.body if isinstance(n, ast.ClassDef)
                     and n.name == 'EPGManagerMainScreen')
    init = next(n for n in dashboard.body if isinstance(n, ast.FunctionDef) and n.name == '__init__')
    first = init.body[0]
    if ast.unparse(first) != 'Screen.__init__(self, session)':
        raise ValueError('dashboard constructor drift')
    lines = text.splitlines(keepends=True)
    lines.insert(first.end_lineno, GUARD)
    out = ''.join(lines)
    # Prove the only semantic change is one assignment in this constructor.
    changed = ast.parse(out)
    target = next(n for n in changed.body if isinstance(n, ast.ClassDef) and n.name == dashboard.name)
    changed_init = next(n for n in target.body if isinstance(n, ast.FunctionDef) and n.name == '__init__')
    assignment = changed_init.body.pop(1)
    expected = ast.parse('class X:\n    def f(self):\n'+GUARD).body[0].body[0].body[0]
    if ast.dump(assignment) != ast.dump(expected) or ast.dump(tree) != ast.dump(changed):
        raise ValueError('nonpresentation change refused')
    return out


def corrected(source):
    if set(source) != set(ORIGINAL):
        raise ValueError('dashboard inventory drift')
    for name, pin in ORIGINAL.items():
        if hashlib.sha256(source[name]).hexdigest() != pin:
            raise ValueError('dashboard preimage drift: '+name)
    return {'ui/main.py': guard_dashboard(source['ui/main.py'].decode('utf-8')).encode('utf-8')}
