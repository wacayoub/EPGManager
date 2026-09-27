# -*- coding: utf-8 -*-
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

def test_page_navigation_and_remote_keys_are_wired():
    text = (ROOT / "ui" / "channel_mapping.py").read_text(encoding="utf-8")
    assert '"pageUp": self.page_up' in text
    assert '"pageDown": self.page_down' in text
    assert '"channelUp": self.page_up' in text
    assert '"channelDown": self.page_down' in text
    assert 'def _page_move' in text

def test_fuzzy_matching_is_deferred_until_selection_pane():
    text = (ROOT / "ui" / "channel_mapping.py").read_text(encoding="utf-8")
    assert 'self.refresh_selection()' in text
    assert 'if self.focus == 2 and new_focus == 3' in text
    assert 'self.refresh_selection()' in text

def test_local_source_update_uses_manager_directly():
    mapping = (ROOT / "ui" / "channel_mapping.py").read_text(encoding="utf-8")
    main = (ROOT / "ui" / "main.py").read_text(encoding="utf-8")
    assert 'self.manager.update_source_async(sid)' in mapping
    assert 'def _poll_local_update' in mapping
    assert 'ChannelMappingScreen, self.config, True, self.manager' in main

def test_manual_assignment_does_not_force_full_rescore():
    text = (ROOT / "ui" / "channel_mapping.py").read_text(encoding="utf-8")
    block = text.split('def assign_current(self):', 1)[1].split('def update_selected_source', 1)[0]
    assert 'if self.only_unmapped:' in block
    assert 'self.selection_rows[idx] = text + " [M]"' in block
