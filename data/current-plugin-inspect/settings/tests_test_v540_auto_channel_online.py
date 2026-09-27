import os
import unittest

ROOT = os.path.dirname(os.path.dirname(__file__))

class TestV540UX(unittest.TestCase):
    def test_channel_mapping_has_auto_channel(self):
        text = open(os.path.join(ROOT, 'ui', 'channel_mapping.py'), encoding='utf-8').read()
        self.assertIn('def auto_map_current_channel', text)
        self.assertIn('"green": self.auto_map_current_channel', text)
        self.assertIn('self._advance_to_next_channel()', text)

    def test_online_refresh_is_nonblocking_and_used_only(self):
        text = open(os.path.join(ROOT, 'ui', 'channel_mapping.py'), encoding='utf-8').read()
        self.assertIn('def _start_online_cache_refresh', text)
        self.assertIn('if os.path.exists(path)', text)
        self.assertIn('threading.Thread(target=worker, daemon=True).start()', text)
        self.assertIn('def _ensure_external_source_ready', text)

    def test_unmap_still_available(self):
        text = open(os.path.join(ROOT, 'ui', 'channel_mapping.py'), encoding='utf-8').read()
        self.assertIn('"6": self.unmap_current_channel', text)

if __name__ == '__main__':
    unittest.main()
