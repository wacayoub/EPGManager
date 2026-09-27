# -*- coding: utf-8 -*-
import json

from EPGManager.core.updater import is_newer_version, normalize_manifest, load_updater_settings, save_updater_settings


def test_version_comparison_numeric():
    assert is_newer_version("6.0.0", "5.9.0")
    assert is_newer_version("6.0.10", "6.0.9")
    assert not is_newer_version("6.0.0", "6.0.0")
    assert not is_newer_version("5.9.9", "6.0.0")


def test_manifest_relative_ipk_url():
    data = normalize_manifest({
        "version": "6.0.1",
        "url": "enigma2-plugin-extensions-epgmanager_6.0.1_all.ipk",
        "notes": "test",
    }, "https://example.com/epg/update.json")
    assert data["url"] == "https://example.com/epg/enigma2-plugin-extensions-epgmanager_6.0.1_all.ipk"
    assert data["version"] == "6.0.1"


def test_updater_settings_roundtrip(tmp_path):
    path = str(tmp_path / "online.json")
    save_updater_settings({"manifest_url": "https://example.com/update.json"}, path)
    loaded = load_updater_settings(path)
    assert loaded["manifest_url"] == "https://example.com/update.json"
