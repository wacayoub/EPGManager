#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Small strict identity layer on top of mena_cloud_shards_safe."""
from __future__ import annotations

import mena_cloud_shards_safe as safe

_original_provider_group = safe.safe_provider_group


def strict_provider_group(cid, name, meta):
    probe = safe.compact("%s %s %s" % (cid or "", name or "", (meta or {}).get("name") or ""))

    # Majid Al Mohandis is an artist/music service, not Majid Kids / Abu Dhabi Media.
    if "majidalmohandis" in probe:
        return None

    return _original_provider_group(cid, name, meta)


safe.safe_provider_group = strict_provider_group


if __name__ == "__main__":
    raise SystemExit(safe.main())
