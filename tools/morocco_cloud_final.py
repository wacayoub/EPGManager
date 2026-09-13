#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Final Morocco Cloud master runner.

One GitHub Actions entry point for:
- SNRT historical logic
- Arryadia historical football logic
- 2M full-day Arabic/Darija logic + French-first evening policy
- Chada enrichment/fallback
- Medi1

The Vu+ receiver still downloads only epg-data/morocco.xml.gz.
"""
from __future__ import annotations

import morocco_cloud_legacy_logic as legacy
import morocco_cloud_runner_ar2 as final2m


def main():
    legacy.install()
    return final2m.main()


if __name__ == "__main__":
    raise SystemExit(main())
