#!/usr/bin/env python3
import base64
import pathlib
import re
import sys

if len(sys.argv) != 3:
    raise SystemExit('Usage: restore_source.py INPUT OUTPUT')

src = pathlib.Path(sys.argv[1])
out = pathlib.Path(sys.argv[2])
raw = src.read_bytes()

# The source package may have been encoded more than once. Decode safely
# until a real ZIP signature is found.
for _ in range(6):
    if raw.startswith(b'PK'):
        out.write_bytes(raw)
        print('ZIP source restored:', out)
        raise SystemExit(0)
    clean = re.sub(rb'[^A-Za-z0-9+/=]', b'', raw)
    if not clean:
        break
    try:
        raw = base64.b64decode(clean, validate=False)
    except Exception as exc:
        raise SystemExit('Base64 decode failed: %s' % exc)

raise SystemExit('Source package could not be restored to a valid ZIP')
