import unittest
from collector.combat_policy import is_combat

class CombatPolicyTests(unittest.TestCase):
    def test_local_capture_filters_and_advances(self):
        import tempfile,json
        from pathlib import Path
        from collector.local_capture import LocalCapture
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)/'Gamelogs'; root.mkdir()
            source=root/'synthetic.txt'; source.write_bytes(b'')
            capture=LocalCapture(root,Path(d)/'captures',consent=True)
            raw=b'[ 2026.09.18 20:00:00 ] (notify) private synthetic\n[ 2026.09.18 20:00:01 ] (combat) synthetic repair\n'
            source.write_bytes(raw); capture.poll(); capture.poll(); capture.close()
            rows=capture.path.read_text().splitlines()
            self.assertEqual(len(rows),1)
            self.assertIn('(combat)',json.loads(rows[0])['line'])
            self.assertNotIn('private synthetic',capture.path.read_text())
            self.assertEqual(next(iter(capture.files.values()))[0],len(raw))

    def test_existing_v1_combat(self):
        self.assertTrue(is_combat({'schema':1,'type':'combat'}))
    def test_explicit_v2_combat_only(self):
        self.assertTrue(is_combat({'schema':2,'type':'game-event','category':'combat'}))
        for category in ('notify','None','mining','route','Combat','',None):
            self.assertFalse(is_combat({'schema':2,'type':'game-event','category':category}))
    def test_no_silent_upgrade(self):
        for event in (None,{}, {'schema':True,'type':'combat'}, {'schema':'1','type':'combat'},
                      {'schema':1,'type':'combat','category':'notify'},
                      {'schema':2,'type':'combat'}, {'schema':1,'type':'unknown'}):
            self.assertFalse(is_combat(event))
    def test_no_mutation(self):
        event={'schema':1,'type':'combat','id':'a'*64,'text':'synthetic repair'}
        before=event.copy(); is_combat(event); self.assertEqual(event,before)
