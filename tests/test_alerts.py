from datetime import datetime, timezone, timedelta
import unittest
from unittest.mock import patch
import trade_alerts
import manual_replication


class AlertTests(unittest.TestCase):
    def setUp(self):
        self.state = {}
        self.time = datetime(2026, 10, 14, 15, tzinfo=timezone.utc)
        self.body = {'symbol': 'SPY', 'side': 'buy', 'type': 'market', 'notional': '5.00', 'client_order_id': 'one'}
        self.row = {'asset_class': 'stock', 'bar_date': '2026-10-13', 'price': 100, 'stop_fraction': .05}

    def test_publish_without_smtp_does_not_submit(self):
        with patch.dict('os.environ', {}, clear=True):
            allowed, pid = trade_alerts.gate(self.state, self.body, self.row, lambda s: None, self.time)
        self.assertFalse(allowed)
        self.assertEqual(self.state['alert_channel'], 'dashboard')
        self.assertEqual(self.state['trade_proposals'][pid]['status'], 'awaiting_approval')
        ticket = manual_replication.proposal_tickets(self.state, self.time)[0]
        self.assertEqual(ticket['dollar_amount'], 5)
        self.assertEqual(ticket['initial_reference_stop'], 95)
        self.assertIn('NOT SUBMITTED', ticket['alpaca_status'])

    def test_approval_required(self):
        allowed, pid = trade_alerts.gate(self.state, self.body, self.row, lambda s: None, self.time)
        self.assertFalse(allowed)
        trade_alerts.approve(self.state, pid, self.time)
        allowed, _ = trade_alerts.gate(self.state, {**self.body, 'client_order_id': 'two'}, self.row, lambda s: None, self.time)
        self.assertTrue(allowed)

    def test_changed_size_requires_new_approval(self):
        _, pid = trade_alerts.gate(self.state, self.body, self.row, lambda s: None, self.time)
        trade_alerts.approve(self.state, pid, self.time)
        allowed, second = trade_alerts.gate(self.state, {**self.body, 'notional': '6.00'}, self.row, lambda s: None, self.time)
        self.assertFalse(allowed)
        self.assertNotEqual(pid, second)

    def test_expired_or_consumed_cannot_submit_or_display(self):
        _, pid = trade_alerts.gate(self.state, self.body, self.row, lambda s: None, self.time)
        self.assertEqual(manual_replication.proposal_tickets(self.state, self.time + timedelta(days=2)), [])
        trade_alerts.approve(self.state, pid, self.time)
        self.state['trade_proposals'][pid]['consumed'] = True
        allowed, _ = trade_alerts.gate(self.state, self.body, self.row, lambda s: None, self.time)
        self.assertFalse(allowed)
        self.assertEqual(manual_replication.proposal_tickets(self.state, self.time), [])

    def test_legacy_email_failure_migrates_only_after_revalidation(self):
        _, pid = trade_alerts.gate(self.state, self.body, self.row, lambda s: None, self.time)
        proposal = self.state['trade_proposals'][pid]
        proposal.pop('published_utc')
        proposal['status'] = 'email_failed'
        self.assertEqual(manual_replication.proposal_tickets(self.state, self.time), [])
        trade_alerts.gate(self.state, self.body, self.row, lambda s: None, self.time)
        self.assertEqual(len(manual_replication.proposal_tickets(self.state, self.time)), 1)
