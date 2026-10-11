from datetime import datetime, timezone, timedelta
import unittest
from unittest.mock import patch
import trade_alerts


class AlertTests(unittest.TestCase):
    def setUp(self):
        self.state = {}
        self.time = datetime(2026, 10, 12, 15, tzinfo=timezone.utc)
        self.body = {'symbol': 'SPY', 'side': 'buy', 'type': 'market', 'notional': '5.00', 'client_order_id': 'one'}
        self.row = {'asset_class': 'stock', 'bar_date': '2026-10-09'}

    def test_email_failure_blocks_order(self):
        with patch.object(trade_alerts, 'send_email', side_effect=RuntimeError('missing sender')):
            allowed, pid = trade_alerts.gate(self.state, self.body, self.row, lambda s: None, self.time)
        self.assertFalse(allowed)
        self.assertEqual(self.state['trade_proposals'][pid]['status'], 'email_failed')
        with self.assertRaises(ValueError):
            trade_alerts.approve(self.state, pid, self.time)

    def test_email_before_approval_and_no_duplicate_email(self):
        with patch.object(trade_alerts, 'send_email') as mail:
            allowed, pid = trade_alerts.gate(self.state, self.body, self.row, lambda s: None, self.time)
            self.assertFalse(allowed)
            trade_alerts.approve(self.state, pid, self.time)
            allowed, _ = trade_alerts.gate(self.state, {**self.body, 'client_order_id': 'two'}, self.row, lambda s: None, self.time)
            self.assertTrue(allowed)
            self.assertEqual(mail.call_count, 1)

    def test_changed_size_requires_new_approval(self):
        with patch.object(trade_alerts, 'send_email'):
            _, pid = trade_alerts.gate(self.state, self.body, self.row, lambda s: None, self.time)
            trade_alerts.approve(self.state, pid, self.time)
            allowed, second = trade_alerts.gate(self.state, {**self.body, 'notional': '6.00'}, self.row, lambda s: None, self.time)
        self.assertFalse(allowed)
        self.assertNotEqual(pid, second)

    def test_expired_or_consumed_proposal_cannot_submit(self):
        with patch.object(trade_alerts, 'send_email'):
            _, pid = trade_alerts.gate(self.state, self.body, self.row, lambda s: None, self.time)
            trade_alerts.approve(self.state, pid, self.time)
            self.state['trade_proposals'][pid]['consumed'] = True
            allowed, _ = trade_alerts.gate(self.state, self.body, self.row, lambda s: None, self.time)
            self.assertFalse(allowed)
            with self.assertRaises(ValueError):
                trade_alerts.approve(self.state, pid, self.time + timedelta(days=2))
