"""Email proposals before paper orders. SMTP credentials never enter ledger files."""
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
import hashlib
import json
import os
import smtplib
import ssl


def send_email(subject, body):
    recipient, sender, password = (os.getenv(k, '').strip() for k in ('EMAIL_TO', 'SMTP_USER', 'SMTP_PASS'))
    if not all((recipient, sender, password)):
        raise RuntimeError('Email sender not configured: set EMAIL_TO, SMTP_USER and SMTP_PASS repository secrets')
    message = EmailMessage()
    message['To'], message['From'], message['Subject'] = recipient, sender, subject
    message.set_content(body)
    with smtplib.SMTP_SSL(os.getenv('SMTP_HOST') or 'smtp.gmail.com', 465, timeout=30, context=ssl.create_default_context()) as smtp:
        smtp.login(sender, password)
        refused = smtp.send_message(message)
        if refused:
            raise RuntimeError('SMTP rejected the alert recipient')


def gate(state, body, row, save, as_of):
    request = {k: v for k, v in body.items() if k != 'client_order_id'}
    signature = {'request': request, 'signal_date': row.get('bar_date') or as_of.date().isoformat()}
    proposal_id = hashlib.sha256(json.dumps(signature, sort_keys=True).encode()).hexdigest()[:16]
    proposals = state.setdefault('trade_proposals', {})
    proposal = proposals.setdefault(proposal_id, {'id': proposal_id, **signature, 'status': 'awaiting_email',
                                                  'created_utc': as_of.isoformat(), 'expires_utc': (as_of + timedelta(hours=24)).isoformat(),
                                                  'asset_class': row['asset_class'], 'reference_price': row.get('price'),
                                                  'stop_fraction': row.get('stop_fraction')})
    if proposal.get('consumed') or datetime.fromisoformat(proposal['expires_utc']) <= as_of:
        proposal['status'] = 'expired_or_consumed'
        save(state)
        return False, proposal_id
    if not proposal.get('email_sent_utc'):
        save(state)
        repo = os.getenv('GITHUB_REPOSITORY', 'deantil/three-lens-market-dashboard')
        instructions = (f'PAPER TRADE PROPOSAL {proposal_id}\n\n{json.dumps(request, indent=2)}\n'
                        f"Reference price: {row.get('price')}; stop fraction: {row.get('stop_fraction')}\n"
                        f"Expires: {proposal['expires_utc']}\n\nNo order has been submitted. To approve, open\n"
                        f'https://github.com/{repo}/actions/workflows/paper-challenge.yml\n'
                        f'Choose Run workflow on main, mode approve, proposal_id {proposal_id}.\n'
                        'Ignore this email to decline. Email replies do not approve orders.\n'
                        'Approval permits this exact order only if current signal, cash and risk checks still pass.\n'
                        'Manual comparison is for thinkorswim paperMoney only. Crypto has no guaranteed exact match.\n'
                        'Paper only. This strategy is experimental and has not demonstrated profitability.')
        try:
            send_email(f'[Alpaca PAPER] Approval needed: {body["side"]} {body["symbol"]}', instructions)
        except Exception as error:
            proposal['status'] = 'email_failed'
            state['email_alert_status'] = 'not_ready: ' + (str(error) if isinstance(error, RuntimeError) else type(error).__name__)
            save(state)
            return False, proposal_id
        proposal['email_sent_utc'] = as_of.isoformat()  # SMTP accepted; inbox delivery is not guaranteed.
        proposal['status'] = 'awaiting_approval'
        state['email_alert_status'] = 'smtp_accepted'
        save(state)
    return bool(proposal.get('approved_utc')), proposal_id


def approve(state, proposal_id, as_of):
    proposal = state.get('trade_proposals', {}).get(proposal_id)
    if not proposal or not proposal.get('email_sent_utc') or proposal.get('consumed'):
        raise ValueError('Approval requires an emailed, unconsumed proposal')
    if datetime.fromisoformat(proposal['expires_utc']) <= as_of:
        raise ValueError('Proposal expired; request a new current signal')
    proposal['approved_utc'] = as_of.isoformat()
    proposal['approved_by'] = os.getenv('GITHUB_ACTOR', 'local')
    proposal['status'] = 'approved_pending_revalidation'


if __name__ == '__main__':
    send_email('[Alpaca PAPER] Email setup test', 'SMTP setup test. No trade is being placed. Check that this message reaches your inbox.')
    print('SMTP accepted the setup test; verify receipt in your inbox.')
