import unittest
from types import SimpleNamespace
from collector.modern_ui import ModernShell

class Label:
    def __init__(self): self.kw = {}
    def config(self, **kw): self.kw.update(kw)

class ModernDeliveryLabelTests(unittest.TestCase):
    def shell(self):
        s = ModernShell.__new__(ModernShell)
        s.screen = 'main'
        s.app = SimpleNamespace(
            connection_status=SimpleNamespace(state='connected'),
            delivery_check=SimpleNamespace(state='connected', ack_received_at='2026-01-01T00:00:00Z'),
            dashboard=SimpleNamespace(confirmed=0, ack_at=None),
            uploader=SimpleNamespace(failures=0), queue=SimpleNamespace(count=lambda: 0))
        s.pending_count = lambda: 0
        s.last_ack_text = lambda: 'Последняя отправка — 0 секунд назад'
        s.conn_label = Label(); s.delivery_label = Label(); s.send_label = Label(); s.detail_label = Label()
        s.update_counters = lambda: None
        return s

    def test_status_uses_exact_labels_and_ack_time(self):
        s = self.shell()
        color, conn, delivery, sending, detail = s.status_text('idle')
        self.assertEqual(conn, '● Подключено к SPHOL')
        self.assertRegex(delivery, r'^Проверка отправки: пройдена · подтверждено \d{2}:\d{2}:\d{2}$')
        self.assertEqual(sending, 'Отправка: выключена')
        s.update_dynamic('idle')
        self.assertEqual(s.delivery_label.kw['text'], delivery)

    def test_connection_color_tracks_actual_state_active_and_idle(self):
        expected = {
            'connected': '#8ad7aa',
            'denied': '#ef8791',
            'offline': '#ef8791',
            'checking': '#ffc36b',
            'unknown': '#ffc36b',
        }
        for state, color in expected.items():
            with self.subTest(state=state):
                s = self.shell()
                s.app.connection_status.state = state
                self.assertEqual(s.status_text('active')[0], color)
                self.assertEqual(s.status_text('idle')[0], color)
