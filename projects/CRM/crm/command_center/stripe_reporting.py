"""Read-only Stripe reporting. Stripe remains the source; no CRM ledger writes.

Only GET requests are supported. Keys stay on the server. Failed, incomplete,
wrong-account and test-mode reads never become a successful zero-dollar report.
"""
import json
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, HTTPRedirectHandler, build_opener
from zoneinfo import ZoneInfo

from . import access

COLLECTIONS = {'charge', 'partial_capture_reversal', 'charge_failure', 'refund',
               'refund_failure', 'dispute', 'dispute_reversal'}


class ReportingError(Exception):
    pass


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise ReportingError('Stripe returned an unexpected redirect.')


class Client:
    def __init__(self, key):
        if not re.fullmatch(r'rk_live_[A-Za-z0-9]+', key or ''):
            raise ReportingError('The live read-only Stripe key is not configured.')
        self.key = key
        self.deadline = time.monotonic() + 18
        self.opener = build_opener(NoRedirect)

    def get(self, resource, params=None):
        if resource not in {'account', 'balance', 'balance_transactions', 'charges'}:
            raise ReportingError('Unsupported Stripe reporting resource.')
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise ReportingError('Stripe refresh took too long. Try again.')
        url = 'https://api.stripe.com/v1/' + resource
        if params:
            url += '?' + urlencode(params)
        request = Request(url, headers={'Authorization': 'Bearer ' + self.key,
                          'Stripe-Version': '2024-06-20'}, method='GET')
        try:
            with self.opener.open(request, timeout=min(8, remaining)) as response:
                return json.load(response)
        except HTTPError as exc:
            if exc.code in (401, 403):
                raise ReportingError('Stripe access needs attention. Check the reporting key and read permissions.') from None
            raise ReportingError('Stripe could not be refreshed. Try again shortly.') from None
        except (OSError, URLError, ValueError):
            raise ReportingError('Stripe could not be reached. Try again shortly.') from None

    def listed(self, resource, start, end):
        params = {'limit': 100, 'created[gte]': start, 'created[lt]': end}
        rows, seen = [], set()
        for _ in range(100):
            page = self.get(resource, params)
            if not isinstance(page.get('data'), list) or type(page.get('has_more')) is not bool:
                raise ReportingError('Stripe returned an incomplete report.')
            for row in page['data']:
                if row['id'] in seen:
                    raise ReportingError('Stripe pagination changed. Refresh to read a complete report.')
                seen.add(row['id'])
                rows.append(row)
            if not page['has_more']:
                return rows
            if not page['data']:
                break
            params['starting_after'] = page['data'][-1]['id']
        raise ReportingError('Stripe report is incomplete; totals are unavailable.')


def secret(config):
    name = config.get('key_env', 'CTA_STRIPE_REPORTING_KEY')
    if not re.fullmatch(r'[A-Z][A-Z0-9_]+', name):
        raise ReportingError('Stripe environment setting needs review.')
    if os.environ.get(name):
        return os.environ[name]
    env_file = config.get('env_file')
    if env_file:
        for line in Path(env_file).read_text(encoding='utf-8-sig').splitlines():
            key, sep, value = line.partition('=')
            if sep and key.strip() == name:
                return value.strip().strip('\"').strip("'")
    return ''


def report(client, config, currency, tz, now):
    account_id = config['account_id']
    if not re.fullmatch(r'acct_[A-Za-z0-9]+', account_id):
        raise ReportingError('Confirm the Stripe account ID before connecting.')
    account = client.get('account')
    if account.get('id') != account_id:
        raise ReportingError('The Stripe key belongs to a different account. No totals were imported.')
    balance = client.get('balance')
    if balance.get('livemode') is not True:
        raise ReportingError('Stripe is in test mode. Live totals are unavailable.')
    local = now.astimezone(tz)
    end = int(now.timestamp()) + 1
    # Read complete history once so an earlier payment cannot vanish behind a
    # current-month total. Pagination and the request deadline still fail closed.
    transactions = client.listed('balance_transactions', 0, end)
    charges = client.listed('charges', 0, end)
    if any(c.get('livemode') is not True for c in charges):
        raise ReportingError('Test payments appeared in a live report. Totals are unavailable.')
    if any(t['currency'].upper() != currency for t in transactions):
        raise ReportingError('Stripe has multiple settlement currencies. Review them before combining totals.')
    def cents(row, field):
        value = row[field]
        if type(value) is not int:
            raise ReportingError('Stripe returned an invalid amount.')
        return value
    charge_by_id = {c['id']: c for c in charges}
    rows = [{'id': t['id'], 'source': t.get('source'), 'category': t['reporting_category'],
             'amount_cents': cents(t, 'amount'), 'fee_cents': cents(t, 'fee'),
             'net_cents': cents(t, 'net'), 'currency': currency,
             'date': datetime.fromtimestamp(t['created'], tz).date().isoformat(),
             'description': str(t.get('description') or ''), 'funds_status': t['status'],
             'payer': str((charge_by_id.get(t.get('source'), {}).get('billing_details') or {}).get('name') or ''),
             'payment_id': t.get('source') if t.get('source') in charge_by_id else None}
            for t in transactions]
    failed = [{'id': c['id'], 'amount_cents': cents(c, 'amount'),
               'currency': c['currency'].upper(), 'date': datetime.fromtimestamp(c['created'], tz).date().isoformat(),
               'description': str(c.get('description') or ''),
               'reason': str(c.get('failure_message') or 'Payment attempt failed.')}
              for c in charges if c.get('status') == 'failed']
    rows.sort(key=lambda r: (r['date'], r['id']), reverse=True)
    month = local.strftime('%Y-%m')
    relevant = [r for r in rows if r['category'] in COLLECTIONS]
    monthly = [r for r in relevant if r['date'][:7] == month]
    return {'state': 'connected', 'account_id': account_id,
            'name': (account.get('business_profile') or {}).get('name') or 'Stripe',
            'currency': currency, 'month': month, 'history_complete': True,
            'read_at': now.isoformat(), 'read_only': True,
            'collected_cents': sum(r['amount_cents'] for r in monthly),
            'fees_cents': sum(r['fee_cents'] for r in monthly),
            'total_collected_cents': sum(r['amount_cents'] for r in relevant),
            'total_fees_cents': sum(r['fee_cents'] for r in relevant),
            'available_cents': sum(cents(r, 'amount') for r in balance['available'] if r['currency'].upper() == currency),
            'pending_cents': sum(cents(r, 'amount') for r in balance['pending'] if r['currency'].upper() == currency),
            'rows': [r for r in rows if r['date'][:7] == month], 'all_rows': rows,
            'failed': [r for r in failed if r['date'][:7] == month], 'all_failed': failed,
            'charges_enabled': account.get('charges_enabled') is True}


def snapshot(prof, person, client=None, now=None):
    if (((prof.get('crm') or {}).get('payments') or {}).get('stripe') or {}).get('enabled') is not True:
        return _snapshot(prof,person,client,now)
    if client is not None or now is not None or access.sees(prof, person) != 'all' or not access.can_do(prof, person, 'crm_read'):
        return _snapshot(prof,person,client,now)
    from . import reporting_cache
    return reporting_cache.read('stripe',prof,lambda:_snapshot(prof,person),60)


def _snapshot(prof, person, client=None, now=None):
    config = ((prof.get('crm') or {}).get('payments') or {}).get('stripe') or {}
    if config.get('enabled') is not True:
        return {'state': 'not_connected'}
    # Account-wide financial data must never leak into an owner-scoped CRM view.
    if not access.can_do(prof, person, 'crm_read') or access.sees(prof, person) != 'all':
        return {'state': 'restricted'}
    try:
        tz = ZoneInfo(prof['crm']['payments'].get('timezone', 'UTC'))
        return report(client or Client(secret(config)), config, prof['crm'].get('currency', 'USD'),
                      tz, now or datetime.now(timezone.utc))
    except ReportingError as exc:
        return {'state': 'error', 'message': str(exc)}
    except (OSError, ValueError, KeyError, TypeError):
        return {'state': 'error', 'message': 'Stripe reporting configuration or response needs review.'}
