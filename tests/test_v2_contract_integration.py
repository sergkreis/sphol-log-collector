"""Optional real loopback server contract gate. Set SPHOL_SERVER_PATH explicitly."""
import os
import sys
import unittest
from contextlib import closing
from pathlib import Path
from datetime import datetime, timezone, timedelta
from unittest.mock import patch

SERVER = os.environ.get('SPHOL_SERVER_PATH')
if SERVER:
    sys.path.insert(0, SERVER)
    sys.path.insert(0, str(Path(SERVER) / 'tests'))
    os.environ['SPHOL_CLIENT_PATH'] = str(Path(__file__).resolve().parents[1])
    from test_collector_http_e2e import CollectorHTTPE2E
    import collector_api
    from collector.expanded import ExpandedQueue, capture, SCOPE

    class V2ContractIntegration(CollectorHTTPE2E):
        def test_v2_explicit_scope_private_storage_capacity_and_ack(self):
            legacy = self.pair()
            pairing = self.t.Pairing(scope=SCOPE)
            code = pairing.start()
            self.assertEqual(self.request('approval', {'user_code':code,'consent':True})[0],403)
            self.assertEqual(self.request('approval', {'user_code':code,'consent':True,'scope':SCOPE})[0],200)
            pairing.next_poll = 0
            credentials = pairing.poll()
            self.assertEqual(credentials['scope'], SCOPE)
            root = Path(self._store_tmp.name)
            logs = root/'SyntheticGamelogs'
            logs.mkdir()
            source = logs/'synthetic.txt'
            source.write_text('  Listener: Test Pilot\n---\n')
            with closing(ExpandedQueue(root)) as pending:
                tailer = capture(logs,pending,consent=True,credentials=credentials,started=datetime.now(timezone.utc)-timedelta(seconds=2))
                with source.open('a',encoding='utf-8') as f:
                    for category,text in [('notify','Fleet warp initiated.'),('None','Target destroyed.'),('info','<b>Synthetic private notification</b>'),('combat','Synthetic combat')]:
                        f.write(datetime.now(timezone.utc).strftime('[ %Y.%m.%d %H:%M:%S ] ') + f'({category}) {text}\n')
                self.assertEqual(tailer.poll(),4)
                payload = self.t.build_batch(pending,credentials)
                self.assertEqual(self.request('events',payload,token=legacy['access_token'])[0],403)
                uploader = self.t.Uploader(credentials)
                with patch.object(collector_api,'MAX_EVENTS',0):
                    self.assert_retry_scheduled(uploader,pending)
                self.assertEqual(pending.count(),4)
                uploader.next_try=0
                uploader.upload(pending)
                self.assertEqual(pending.count(),0)
                with self.api.transaction() as con:
                    self.assertEqual(con.execute('SELECT count(*) FROM collector_private_events').fetchone()[0],4)
                    self.assertEqual(con.execute('SELECT count(*) FROM collector_events').fetchone()[0],0)
                # Exact stable retry is acknowledged, not duplicated.
                self.assertEqual(self.request('events',payload,token=credentials['access_token'])[1]['accepted_ids'],[e['id'] for e in payload['events']])
else:
    class V2ContractIntegration(unittest.TestCase):
        @unittest.skip('Set SPHOL_SERVER_PATH for real client/server contract integration')
        def test_requires_server_checkout(self):
            pass
