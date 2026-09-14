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
                    for category,text in [('notify','Переход в варп-режим по приказу Synthetic Commander'),('None','Synthetic Scoop I* отключается, теряя руду в пространстве, так как вы отдалились на 1600,00 м от цели, что превышает радиус действия в 1500,00 м.'),('info','<b>Synthetic private notification</b>'),('combat','Synthetic combat')]:
                        f.write(datetime.now(timezone.utc).strftime('[ %Y.%m.%d %H:%M:%S ] ') + f'({category}) {text}\n')
                self.assertEqual(tailer.poll(),2)
                payload = self.t.build_batch(pending,credentials)
                self.assertEqual(self.request('events',payload,token=legacy['access_token'])[0],403)
                uploader = self.t.Uploader(credentials)
                with patch.object(collector_api,'MAX_EVENTS',0):
                    self.assert_retry_scheduled(uploader,pending)
                self.assertEqual(pending.count(),2)
                uploader.next_try=0
                uploader.upload(pending)
                self.assertEqual(pending.count(),0)
                with self.api.transaction() as con:
                    self.assertEqual(con.execute('SELECT count(*) FROM collector_private_events').fetchone()[0],2)
                    self.assertEqual(con.execute('SELECT count(*) FROM collector_events').fetchone()[0],0)
                # Stored killmail anchors, real member HTTP, fixed private-safe projection.
                import collector_sorties, server, http.client, json
                from fixtures import attacker, killmail
                row = killmail(attackers=[attacker(character_id=p, corporation_id=123) for p in (42,43,44)])
                row.update(killmail_id=99001, killmail_time=payload['events'][0]['time'])
                self.store.upsert_rows(123, [row, dict(row,killmail_id=99002)])
                con = http.client.HTTPConnection('127.0.0.1', self.httpd.server_port, timeout=5)
                con.request('GET', '/api/activity-records', headers={'Cookie':server.SSO_COOKIE+'=synthetic-only'})
                response = con.getresponse(); raw = response.read().decode(); con.close()
                self.assertEqual(response.status,200)
                activity = json.loads(raw)
                events = [e for episode in activity['episodes'] for e in episode['events']]
                self.assertEqual({e['signal'] for e in events}, {'fleet_warp','module_range'})
                self.assertTrue(all(e['sortieKey'] for e in activity['episodes']))
                for secret in ('Synthetic Commander','Synthetic Scoop','Synthetic private notification','Synthetic combat'):
                    self.assertNotIn(secret,raw)
                self.assertNotIn('Synthetic',json.dumps(self.api.private_events(42)))
                self.assertEqual(self.api.private_events(999),[])
                if os.environ.get('SPHOL_BROWSER_GATE') == '1':
                    import re
                    from playwright.sync_api import sync_playwright
                    with sync_playwright() as p:
                        browser = p.chromium.launch(headless=True)
                        base = f'http://127.0.0.1:{self.httpd.server_port}'
                        for width in (1280,390,320):
                            context = browser.new_context(viewport={'width':width,'height':900})
                            context.add_cookies([{'name':server.SSO_COOKIE,'value':'synthetic-only','url':base}])
                            page = context.new_page()
                            html = re.sub(r'<script\b[^>]*>[\s\S]*?</script>', '', (Path(SERVER)/'index.html').read_text())
                            page.route(base+'/',lambda route:route.fulfill(content_type='text/html',body=html))
                            page.goto(base)
                            page.add_script_tag(content=(Path(SERVER)/'killboard.js').read_text())
                            page.evaluate("bindUi(); state.locked=false; state.session={loggedIn:true}; document.querySelector('[data-view=records]').hidden=false")
                            page.locator('[data-view=records]').click()
                            page.locator('#activityRecords summary').first.click()
                            rendered = page.locator('#activityRecords').inner_text()
                            self.assertIn('Выполняется варп флота.',rendered)
                            self.assertIn('Цель вне радиуса действия модуля.',rendered)
                            self.assertNotIn('Synthetic private notification',rendered)
                            self.assertTrue(page.locator('#activityRecords').evaluate('(e)=>e.scrollWidth<=e.clientWidth'))
                            context.close()
                        browser.close()
                # Existing v1 credential still captures and uploads independently.
                with closing(self.core.PendingQueue(root/'legacy.sqlite')) as old_queue:
                    old_tailer = self.core.Tailer(logs,old_queue,started=datetime.now(timezone.utc)-timedelta(seconds=2))
                    with source.open('a',encoding='utf-8') as f:
                        f.write(datetime.now(timezone.utc).strftime('[ %Y.%m.%d %H:%M:%S ] (combat) 100 damage to Synthetic Target\n'))
                    self.assertEqual(old_tailer.poll(),1)
                    self.t.Uploader(legacy).upload(old_queue)
                    self.assertEqual(old_queue.count(),0)
                # Client -> HTTP -> SQLite -> specific loss facts, without raw notify.
                from fixtures import victim
                loss = killmail(victim_data=victim(character_id=42, corporation_id=123),
                                attackers=[attacker(character_id=43)])
                loss.update(killmail_id=99003, killmail_time=collector_api.utc(__import__('time').time()+1))
                self.store.upsert_rows(123, [loss])
                self.store.remember_names({42:'Test Pilot', 43:'Synthetic Enemy'})
                with closing(self.core.PendingQueue(root/'facts.sqlite')) as facts_queue:
                    facts_tailer = self.core.Tailer(logs,facts_queue,started=datetime.now(timezone.utc)-timedelta(seconds=2))
                    with source.open('a',encoding='utf-8') as f:
                        for text in ('125 из Synthetic Enemy',
                                     '240 единиц запаса прочности щитов получено накачкой от Synthetic Support',
                                     'Попытка варп-глушения: источник Synthetic Enemy, цель Test Pilot!'):
                            f.write(datetime.now(timezone.utc).strftime('[ %Y.%m.%d %H:%M:%S ] (combat) ')+text+'\n')
                    self.assertEqual(facts_tailer.poll(),3)
                    self.t.Uploader(legacy).upload(facts_queue)
                    self.assertEqual(facts_queue.count(),0)
                con = http.client.HTTPConnection('127.0.0.1', self.httpd.server_port, timeout=5)
                con.request('GET','/api/killmail-logs?id=99003',headers={'Cookie':server.SSO_COOKIE+'=synthetic-only'})
                response=con.getresponse(); facts_raw=response.read().decode(); con.close()
                self.assertEqual(response.status,200)
                self.assertEqual([(f['kind'],f['amount']) for f in json.loads(facts_raw)['facts']],
                                 [('damage',125),('repair',240),('tackle_attempt',None)])
                self.assertNotIn('Synthetic Commander',facts_raw)
                # Bypass-client adversary: reject raw/destinations before persistence.
                for text in ('Переход в варп-режим по приказу Synthetic Commander',
                             'Warping to Private System', 'https://private.invalid',
                             '<b>private</b>', 'Contract accepted', 'Самоуничтожение через 120 секунд.'):
                    bad = dict(payload['events'][0],id='f'*64,text=text)
                    ack = self.request('events',{'schema':2,'events':[bad]},token=credentials['access_token'])[1]
                    self.assertEqual(ack['accepted_ids'],[])
                with self.api.transaction() as db:
                    saved = ' '.join(r[0] for r in db.execute('SELECT payload FROM collector_private_events'))
                    self.assertNotIn('Synthetic Commander',saved)
                    self.assertNotIn('private',saved)
                    self.assertEqual(db.execute('SELECT count(*) FROM collector_private_events').fetchone()[0],2)
                # Exact stable retry is acknowledged, not duplicated.
                self.assertEqual(self.request('events',payload,token=credentials['access_token'])[1]['accepted_ids'],[e['id'] for e in payload['events']])
else:
    class V2ContractIntegration(unittest.TestCase):
        @unittest.skip('Set SPHOL_SERVER_PATH for real client/server contract integration')
        def test_requires_server_checkout(self):
            pass
