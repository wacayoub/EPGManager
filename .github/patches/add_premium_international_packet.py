from pathlib import Path


def replace(path, old, new, count=1):
    p = Path(path)
    text = p.read_text(encoding="utf-8")
    if old not in text:
        raise SystemExit(f"needle not found in {path}: {old[:120]!r}")
    p.write_text(text.replace(old, new, count), encoding="utf-8")


INTL_IDS = [
    "AnimalPlanetEurope.uk@SD",
    "DiscoveryChannelMiddleEastAfrica.us@SD",
    "InvestigationDiscovery.uk@SD",
    "HistoryMiddleEast.us@SD",
    "History2MiddleEast.us@SD",
    "TLCArabia.us@SD",
    "CartoonNetworkMENA.uk@SD",
    "NickelodeonArabia.ae@SD",
    "NickJrArabia.ae@SD",
    "NicktoonsArabia.ae@SD",
    "CartoonNetworkArabic.ae@SD",
]

set_block = "PREMIUM_INTERNATIONAL_IDS = {\n" + "".join(f'    "{cid}",\n' for cid in INTL_IDS) + "}\n\n"

# Dedicated receiver packet; these are verified regional MENA feeds, not generic Other.
p = "tools/mena_cloud_shards.py"
replace(
    p,
    '    ("starz", "STARZPLAY", "provider-starz"),\n]\n',
    '    ("starz", "STARZPLAY", "provider-starz"),\n    ("international", "Premium International", "provider-international"),\n]\n',
)
replace(
    p,
    "# Receiver-facing Rotana identities frozen after the provider audit. Alternate\n",
    set_block + "# Receiver-facing Rotana identities frozen after the provider audit. Alternate\n",
)
replace(
    p,
    'def provider_group(cid: str, name: str, meta: dict):\n    p = probe(cid, name, meta)\n',
    'def provider_group(cid: str, name: str, meta: dict):\n    if cid in PREMIUM_INTERNATIONAL_IDS:\n        return "international"\n    p = probe(cid, name, meta)\n',
)

# Accuracy wrapper must claim the same identities before country/Other routing.
p = "tools/mena_cloud_shards_safe.py"
replace(
    p,
    'def safe_provider_group(cid, name, meta):\n    p = identity_probe(cid, name, meta)\n',
    'def safe_provider_group(cid, name, meta):\n    if cid in base.PREMIUM_INTERNATIONAL_IDS:\n        return "international"\n    p = identity_probe(cid, name, meta)\n',
)

# Canonical publication must retain and account for the new provider shard.
p = "tools/canonical_publish_finalize.py"
replace(
    p,
    '    "starz": "provider-starz",\n}\n',
    '    "starz": "provider-starz",\n    "international": "provider-international",\n}\n',
)

# Hard release gate: require exactly the 11 verified identities, no extras.
p = "tools/receiver_release_gate.py"
release_block = "PREMIUM_INTERNATIONAL = {\n" + "".join(f'    "{cid}",\n' for cid in INTL_IDS) + "}\n"
replace(p, "PROVIDERS = {\n", release_block + "PROVIDERS = {\n")
replace(
    p,
    '    required_files = {"mena-arabic", "mena", "premium"} | set(PROVIDERS)\n',
    '    required_files = {"mena-arabic", "mena", "premium", "provider-international"} | set(PROVIDERS)\n',
)
anchor = '''    for stem, (prefix, required, optional) in PROVIDERS.items():
        if stem not in profiles:
            continue
        actual = profiles[stem]["ids"]
        missing = required - actual
        extra = actual - required - optional
        if missing:
            errors.append("%s: MISSING_CORE_IDS=%s" % (stem, sorted(missing)))
        if extra:
            errors.append("%s: UNREVIEWED_IDS=%s" % (stem, sorted(extra)))
        if any(not cid.startswith(prefix) for cid in actual):
            errors.append("%s: NAMESPACE_MISMATCH" % stem)

'''
replace(
    p,
    anchor,
    anchor + '''    if "provider-international" in profiles:
        actual = profiles["provider-international"]["ids"]
        missing = PREMIUM_INTERNATIONAL - actual
        extra = actual - PREMIUM_INTERNATIONAL
        if missing:
            errors.append("provider-international: MISSING_CORE_IDS=%s" % sorted(missing))
        if extra:
            errors.append("provider-international: UNREVIEWED_IDS=%s" % sorted(extra))

''',
)

print("PATCHED Premium International packet ids=11")
