import copy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

# Offline environments can exercise pure logic without installing an HTTP client.
# Network behavior is always mocked in these tests.
try:
    import requests
except ImportError:
    class HTTPError(Exception):
        response = None
    sys.modules['requests'] = types.SimpleNamespace(HTTPError=HTTPError)

import paper_agent as agent
import options_research as options
import research_backtest as replay
import manual_replication


class LedgerTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(dir=Path(__file__).parent)
        self.old_path = agent.STATE_PATH
        agent.STATE_PATH = Path(self.directory.name) / 'state.json'
        self.state = agent.new_state()

    def tearDown(self):
        agent.STATE_PATH = self.old_path
        self.directory.cleanup()

    def test_partial_fill_then_cancel_keeps_filled_shares(self):
        pending = {'symbol': 'SPY', 'side': 'buy', 'asset_class': 'stock', 'notional': 20, 'stop_fraction': .1}
        order = {'id': 'one', 'status': 'partially_filled', 'filled_qty': '0.1', 'filled_avg_price': '100'}
        agent.apply_filled_order(self.state, pending, order)
        agent.apply_filled_order(self.state, pending, {**order, 'status': 'canceled'})
        self.assertEqual(self.state['cash'], 90)
        self.assertEqual(self.state['positions'][0]['qty'], .1)

    def test_repeated_cumulative_fills_are_not_double_counted(self):
        pending = {'symbol': 'SPY', 'side': 'buy', 'asset_class': 'stock', 'stop_fraction': .1}
        for qty in ('0.1', '0.2', '0.2'):
            agent.apply_filled_order(self.state, pending, {'id': 'one', 'filled_qty': qty, 'filled_avg_price': '100'})
        self.assertEqual(self.state['cash'], 80)
        self.assertAlmostEqual(self.state['positions'][0]['qty'], .2)

    def test_option_multiplier_and_sale(self):
        pending = {'symbol': 'SPY261120C00800000', 'side': 'buy', 'asset_class': 'option', 'notional': 5, 'stop_fraction': .5}
        agent.apply_filled_order(self.state, pending, {'id': 'one', 'filled_qty': '1', 'filled_avg_price': '.05'})
        self.assertEqual(self.state['cash'], 95)
        self.assertEqual(agent.invested_value(self.state), 5)
        sell = {'symbol': pending['symbol'], 'side': 'sell', 'asset_class': 'option'}
        agent.apply_filled_order(self.state, sell, {'id': 'two', 'filled_qty': '1', 'filled_avg_price': '.02'})
        self.assertEqual(self.state['cash'], 97)
        self.assertEqual(self.state['positions'], [])

    def test_partial_sell_keeps_remaining_position(self):
        self.state['positions'] = [{'symbol': 'SPY', 'qty': 2, 'last_price': 10, 'entry_price': 10}]
        agent.apply_filled_order(self.state, {'symbol': 'SPY', 'side': 'sell', 'asset_class': 'stock'},
                                 {'id': 'one', 'filled_qty': '.5', 'filled_avg_price': '12'})
        self.assertEqual(self.state['positions'][0]['qty'], 1.5)

    def test_order_intent_persisted_before_broker_call(self):
        class Broker:
            def trade(inner, method, path, **kwargs):
                saved = json.loads(agent.STATE_PATH.read_text())
                self.assertEqual(len(saved['pending']), 1)
                self.assertEqual(saved['pending'][0]['request']['qty'], '1')
                self.assertNotIn('notional', kwargs['json'])
                self.assertEqual(kwargs['json']['position_intent'], 'buy_to_open')
                raise TimeoutError('unknown broker response')
        with self.assertRaises(TimeoutError):
            agent.send_order(Broker(), self.state, {'symbol': 'SPY261120C00800000', 'asset_class': 'option', 'price': .05}, 'buy', 5, 1)
        self.assertEqual(agent.reserved_cash(self.state), 5)

    def test_uncertain_order_is_recovered_by_client_id(self):
        calls = []
        class Broker:
            def trade(inner, method, path, **kwargs):
                calls.append((method, path))
                return {'id': 'recovered', 'status': 'new'}
        pending = {'client_order_id': 'tl100-test', 'order_id': None}
        agent.filled_order(Broker(), pending)
        self.assertEqual(calls, [('GET', '/orders:by_client_order_id')])
        self.assertEqual(pending['order_id'], 'recovered')

    def test_foreign_positions_pause(self):
        class Broker:
            def trade(inner, method, path, **kwargs):
                return [{'symbol': 'CLAUDE', 'qty': '1'}] if path == '/positions' else []
        with self.assertRaisesRegex(RuntimeError, 'another bot'):
            agent.reconcile_foreign_activity(Broker(), self.state)

    def test_whole_option_contracts_required(self):
        with self.assertRaises(ValueError):
            agent.send_order(None, self.state, {'symbol': 'X', 'asset_class': 'option', 'price': .05}, 'buy', 5, .5)

    def test_option_budget_reserves_unfilled_premium(self):
        self.state['pending'] = [{'side': 'buy', 'notional': 5, 'accounted_value': .02, 'asset_class': 'option'}]
        self.assertEqual(agent.reserved_cash(self.state), 3)

    def test_option_limit_cannot_exceed_budget(self):
        with self.assertRaisesRegex(ValueError, 'premium budget'):
            agent.send_order(None, self.state, {'symbol': 'X', 'asset_class': 'option', 'price': .06}, 'buy', 6, 1)

    def test_full_paper_step_keeps_shared_ledger(self):
        stock_row = {'symbol': 'X', 'asset_class': 'stock', 'price': 10, 'stop_fraction': .1,
                     'bar_date': '2026-10-09', 'score': 1, 'signal': True}
        class Broker:
            def __init__(inner, *args):
                pass
            def trade(inner, method, path, **kwargs):
                if path == '/account':
                    return {'status': 'ACTIVE', 'equity': '300', 'options_trading_level': 2}
                if path in ('/positions', '/orders') and method == 'GET':
                    return []
                if path == '/clock':
                    return {'is_open': False}
                if path == '/orders' and method == 'POST':
                    self.assertEqual(kwargs['json']['symbol'], 'X')
                    return {'id': 'queued'}
                if path == '/orders/queued':
                    return {'id': 'queued', 'status': 'new', 'filled_qty': '0'}
                raise AssertionError(path)
        with patch.object(agent, 'credentials', return_value=('key', 'secret')), \
             patch.object(agent, 'Alpaca', Broker), \
             patch.object(agent, 'bar_candidates', return_value=([stock_row], {'latest_completed_bar_date': '2026-10-09'}, {'X': stock_row})), \
             patch.object(options, 'scan', return_value=([], {'feed': 'indicative', 'contracts': 0})), \
             patch.object(agent.time, 'sleep'), patch.object(agent, 'sys', types.SimpleNamespace(argv=['paper_agent.py'])), \
             patch.object(agent, 'Path', side_effect=lambda p: agent.STATE_PATH.parent / p if p == 'research' else Path(p)):
            agent.run()
        saved = json.loads(agent.STATE_PATH.read_text())
        self.assertEqual(saved['cash'], 100)
        self.assertEqual(len(saved['pending']), 1)
        self.assertEqual(saved['pending'][0]['notional'], 5)
        self.assertEqual(saved['broker_status'], 'connected_paper')

    def test_existing_ledger_is_preserved(self):
        self.state['cash'] = 73
        self.state['pending'] = [{'symbol': 'PLTR'}]
        agent.save_state(self.state)
        restored = agent.load_state()
        self.assertEqual(restored['cash'], 73)
        self.assertEqual(restored['pending'], [{'symbol': 'PLTR'}])


class BacktestTests(unittest.TestCase):
    def test_manual_ticket_retains_crypto_without_fake_proxy(self):
        state = {'pending': [{'symbol': 'BTC/USD', 'asset_class': 'crypto', 'side': 'buy', 'notional': 5}]}
        ticket = manual_replication.tickets(state)[0]
        self.assertEqual(ticket['symbol'], 'BTC/USD')
        self.assertEqual(ticket['thinkorswim_status'], 'NOT AN EXACT SPOT-CRYPTO MATCH')

    def test_manual_option_contract_is_unambiguous(self):
        ticket = manual_replication.tickets({'pending': [{'symbol': 'SPY261120C00800000', 'asset_class': 'option',
                                                         'side': 'buy', 'qty': 1, 'notional': 5,
                                                         'request': {'type': 'limit', 'limit_price': '0.05'}}]})[0]
        self.assertEqual((ticket['underlying'], ticket['expiration'], ticket['strike'], ticket['option_type']),
                         ('SPY', '2026-11-20', 800, 'CALL'))
        self.assertEqual(ticket['order_type'], 'LIMIT')

    def test_stock_fill_is_next_open_with_gap_and_cost(self):
        data = {'X': [{'t': '2026-01-01T00:00:00Z', 'o': 10, 'c': 10, 'v': 100},
                      {'t': '2026-01-02T00:00:00Z', 'o': 20, 'c': 20, 'v': 100}]}
        def signal(symbol, history, day):
            return {'symbol': symbol, 'signal': day == '2026-01-01', 'price': 10, 'atr20': 1, 'score': 1, 'stop_fraction': .1}
        run = replay.stock_replay(data, signal, '2026-01-01', '2026-01-02', .01)
        # $5 risk-sized notional is spent at the gapped $20.20 open, then liquidated
        # with sell slippage. A same-close fill would have manufactured a profit.
        self.assertLess(run['curve'][-1], 100)
        self.assertEqual(run['open_positions_at_end'], 1)

    def test_stock_signal_receives_only_past_bars(self):
        data = {'X': [{'t': f'2026-01-0{i}T00:00:00Z', 'o': 10, 'c': 10, 'v': 100} for i in range(1, 4)]}
        def signal(symbol, history, day):
            self.assertTrue(all(r['t'][:10] <= day for r in history))
            return None
        replay.stock_replay(data, signal, '2026-01-01', '2026-01-03')

    def quote(self, stamp, **overrides):
        return {'observed_at': stamp, 'symbol': 'SPY261120C00800000', 'feed': 'opra', 'bid': .04, 'ask': .045,
                'quote_time': stamp, 'bid_size': 10, 'ask_size': 10, 'expiration_date': '2026-11-20',
                'eligible': True, 'market_open': True, **overrides}

    def test_options_never_fill_same_snapshot(self):
        result = replay.option_replay([self.quote('2026-10-12T15:00:00+00:00')], fee=0)
        self.assertEqual(result['filled_exits'], 0)
        self.assertFalse(result['missing_exit_charged_full_premium'])

    def test_options_missing_exit_charged_full_premium(self):
        result = replay.option_replay([self.quote('2026-10-12T15:00:00+00:00'),
                                       self.quote('2026-10-12T16:00:00+00:00')], fee=0)
        self.assertTrue(result['missing_exit_charged_full_premium'])
        self.assertAlmostEqual(result['metrics']['return_pct'], -4.5)

    def test_indicative_quotes_not_used_as_fills(self):
        rows = [self.quote(f'2026-10-12T{hour}:00:00+00:00', feed='indicative') for hour in ('15', '16')]
        result = replay.option_replay(rows)
        self.assertEqual(result['skipped_quotes'], 2)
        self.assertEqual(result['metrics']['return_pct'], 0)

    def test_stale_crossed_and_zero_quotes_rejected(self):
        clock = datetime(2026, 10, 12, 15, tzinfo=timezone.utc)
        for bid, ask, stamp in ((0, .05, clock), (.06, .05, clock), (.04, .045, clock - timedelta(minutes=2))):
            valid, _, _ = options.quote_check({'bp': bid, 'ap': ask, 't': stamp.isoformat(), 'bs': 10, 'as': 10}, clock)
            self.assertFalse(valid)

    def test_scan_obeys_full_premium_and_delta(self):
        clock = datetime(2026, 10, 12, 15, tzinfo=timezone.utc)
        class Broker:
            def data(inner, path, params):
                return {'snapshots': {'SPY261106C00800000': {'latestQuote': {'bp': .037, 'ap': .04, 't': clock.isoformat(), 'bs': 1, 'as': 1},
                                                             'greeks': {'delta': .30}}}}
            def trade(inner, method, path, **kwargs):
                return {'option_contracts': [{'symbol': 'SPY261106C00800000', 'tradable': True, 'size': '100', 'type': 'call',
                                             'expiration_date': '2026-11-06', 'open_interest': '100', 'open_interest_date': '2026-10-12'}]}
        with patch.object(options, 'UNDERLYINGS', ('SPY',)):
            rows, summary = options.scan(Broker(), {'SPY': {'signal': True, 'bar_date': '2026-10-09'}}, clock, 100, 100)
            self.assertTrue(rows[0]['eligible'])
            self.assertEqual(rows[0]['premium_usd'], 4)
            rows, _ = options.scan(Broker(), {'SPY': {'signal': True}}, clock, 50, 100)
            self.assertFalse(rows[0]['eligible'])

    def test_paper_endpoint_and_dedicated_credentials(self):
        self.assertEqual(agent.TRADE_API, 'https://paper-api.alpaca.markets/v2')
        with patch.dict('os.environ', {'CHATGPT_APCA_API_KEY_ID': 'test', 'CHATGPT_APCA_API_SECRET_KEY': 'secret', 'ALPACA_KEY': 'claude'}):
            self.assertEqual(agent.credentials(), ('test', 'secret'))


if __name__ == '__main__':
    unittest.main()
