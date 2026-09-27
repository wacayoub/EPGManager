from EPGManager.core.updater import DEFAULT_SETTINGS, load_updater_settings

def test_github_manifest_is_default():
    assert DEFAULT_SETTINGS["manifest_url"] == "https://raw.githubusercontent.com/wacayoub/EPGManager/main/update.json"

def test_empty_legacy_manifest_falls_back(tmp_path):
    p = tmp_path / "settings.json"
    p.write_text('{"manifest_url":"", "channel":"stable"}')
    assert load_updater_settings(str(p))["manifest_url"] == DEFAULT_SETTINGS["manifest_url"]
