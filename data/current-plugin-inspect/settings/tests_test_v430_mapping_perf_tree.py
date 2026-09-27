# -*- coding: utf-8 -*-
from pathlib import Path

from ..core import channel_mapper

ROOT = Path(__file__).resolve().parents[1]


def test_bouquet_tree_has_parent_file_and_depth(tmp_path):
    (tmp_path / 'bouquets.tv').write_text('#NAME Bouquets\n#SERVICE 1:7:1:0:0:0:0:0:0:0:FROM BOUQUET "userbouquet.main.tv" ORDER BY bouquet\n')
    (tmp_path / 'userbouquet.main.tv').write_text('#NAME MAIN\n#SERVICE 1:7:1:0:0:0:0:0:0:0:FROM BOUQUET "userbouquet.sub.tv" ORDER BY bouquet\n')
    (tmp_path / 'userbouquet.sub.tv').write_text('#NAME SUB\n')
    rows = channel_mapper.list_bouquets(str(tmp_path))
    main = next(x for x in rows if x['bouquet_label'] == 'MAIN')
    sub = next(x for x in rows if x['bouquet_label'] == 'SUB')
    assert main['parent_file'] is None
    assert main['depth'] == 0
    assert sub['parent_file'] == 'userbouquet.main.tv'
    assert sub['depth'] == 1


def test_mapping_ui_highlights_only_active_column_and_collapses_trees():
    text = (ROOT / 'ui' / 'channel_mapping.py').read_text(encoding='utf-8')
    assert 'setSelectionEnable(i <= self.focus)' in text
    assert 'visited_focus' not in text
    assert 'self.expanded_bouquets = set()' in text
    assert 'self.expanded_source_groups = set()' in text
    assert 'Press OK to open %s sources' in text


def test_mapping_ui_uses_fast_indexes_and_cache():
    text = (ROOT / 'ui' / 'channel_mapping.py').read_text(encoding='utf-8')
    assert 'self.epg_by_source' in text
    assert 'self.selection_cache' in text
    assert 'self._mapped_refs' in text
    assert 'self.refresh_selection()' in text
