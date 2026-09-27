from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]

def test_native_import_uses_unified_source_browser():
    text = (ROOT / 'ui' / 'native_import.py').read_text(encoding='utf-8')
    assert 'NativeSourcesScreen' in text
    assert 'Native + EPG-Importer + Online' in text

def test_unified_catalog_has_local_external_and_epgimport_discovery():
    text = (ROOT / 'core' / 'source_catalog.py').read_text(encoding='utf-8')
    assert 'LOCAL_SOURCES' in text
    assert 'discover_epgimport_sources' in text
    assert '/etc/epgimport/*.sources.xml' in text
    assert 'bundled_external_sources' in text

def test_custom_rows_are_explicitly_repainted():
    for rel in ('ui/native_import.py', 'ui/settings.py'):
        text = (ROOT / rel).read_text(encoding='utf-8')
        assert 'self._set_bg("rowlabel%d" % i, bg)' in text
        assert 'self._set_bg("rowvalue%d" % i, bg)' in text
