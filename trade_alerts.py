"""Dashboard proposals before paper orders. No email service or credentials."""
from datetime import datetime, timedelta
import hashlib
import json
import os


def gate(state, body, row, save, as_of):
    request = {k: v for k, v in body.items() if k != 'client_order_id'}
    signature = {'request': request, 'signal_date': row.get('bar_date') or as_of.date().isoformat()}
    proposal_id = hashlib.sha256(json.dumps(signature, sort_keys=True).encode()).hexdigest()[:16]
    proposals = state.setdefault('trade_proposals', {})
    proposal = proposals.setdefault(proposal_id, {'id': proposal_id, **signature,
        'created_utc': as_of.isoformat(), 'expires_utc': (as_of + timedelta(hours=24)).isoformat(),
        'asset_class': row['asset_class'], 'reference_price': row.get('price'),
        'stop_fraction': row.get('stop_fraction')})
    if proposal.get('consumed') or datetime.fromisoformat(proposal['expires_utc']) <= as_of:
        proposal['status'] = 'expired_or_consumed'
        save(state)
        return False, proposal_id
    proposal['published_utc'] = proposal.get('published_utc') or as_of.isoformat()
    proposal['last_revalidated_utc'] = as_of.isoformat()
    proposal['status'] = 'approved_pending_revalidation' if proposal.get('approved_utc') else 'awaiting_approval'
    state['alert_channel'] = 'dashboard'
    state['email_alert_status'] = 'disabled_by_user'
    save(state)
    return bool(proposal.get('approved_utc')), proposal_id


def approve(state, proposal_id, as_of):
    proposal = state.get('trade_proposals', {}).get(proposal_id)
    if not proposal or not proposal.get('published_utc') or proposal.get('consumed'):
        raise ValueError('Approval requires a published, unconsumed proposal')
    if datetime.fromisoformat(proposal['expires_utc']) <= as_of:
        raise ValueError('Proposal expired; refresh current signals')
    proposal['approved_utc'] = as_of.isoformat()
    proposal['approved_by'] = os.getenv('GITHUB_ACTOR', 'local')
    proposal['status'] = 'approved_pending_revalidation'
