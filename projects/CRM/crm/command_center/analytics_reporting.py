"""Read-only GA4 totals for one configured property and its public website hosts.

Credentials stay on the CRM server. A failed or incomplete report is never zero.
"""
import json
import os
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, HTTPRedirectHandler, build_opener
from zoneinfo import ZoneInfo

from . import access

SCOPE = 'https://www.googleapis.com/auth/analytics.readonly'
METRICS = ('sessions', 'activeUsers', 'screenPageViews')


class ReportingError(Exception):
    pass


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise ReportingError('Google returned an unexpected redirect.')


def credentials(config):
    values = {}
    if config.get('env_file'):
        for line in Path(config['env_file']).read_text(encoding='utf-8-sig').splitlines():
            key, sep, value = line.partition('=')
            if sep:
                values[key.strip()] = value.strip().strip('"').strip("'")
    result = {key: os.environ.get('CTA_ANALYTICS_GOOGLE_' + key.upper()) or
              values.get('CTA_ANALYTICS_GOOGLE_' + key.upper())
              for key in ('client_id', 'client_secret', 'refresh_token')}
    if not all(result.values()):
        raise ReportingError('Google Analytics needs its read-only reporting sign-in.')
    return result


class Client:
    def __init__(self, config):
        self.credentials = credentials(config)
        self.deadline = time.monotonic() + 12
        self.opener = build_opener(NoRedirect)

    def _send(self, request):
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise ReportingError('Google Analytics refresh took too long. Try again.')
        try:
            with self.opener.open(request, timeout=min(6, remaining)) as response:
                return json.load(response)
        except HTTPError as exc:
            if exc.code in (400, 401, 403):
                # Only inspect structured error codes; never expose Google's body or credentials.
                try:
                    body = json.load(exc)
                    details = body.get('error', {}).get('details', []) if isinstance(body.get('error'), dict) else []
                except (ValueError, OSError):
                    details = []
                if any(d.get('reason') == 'SERVICE_DISABLED' for d in details):
                    raise ReportingError('Enable the Google Analytics Data API for the connected Google application.') from None
                raise ReportingError('Google Analytics access needs attention. Check the reporting sign-in and property access.') from None
            raise ReportingError('Google Analytics could not refresh. Try again shortly.') from None
        except (OSError, URLError, ValueError):
            raise ReportingError('Google Analytics could not be reached. Try again shortly.') from None

    def run_report(self, property_id, hosts):
        if not re.fullmatch(r'[1-9][0-9]*', property_id):
            raise ReportingError('Confirm the Google Analytics property ID.')
        if not isinstance(hosts, list) or not hosts or any(not isinstance(h, str) or
                not re.fullmatch(r'[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?', h) for h in hosts):
            raise ReportingError('Confirm the website hostnames before reading traffic.')
        token = self._send(Request('https://oauth2.googleapis.com/token',
            data=urlencode({**self.credentials, 'grant_type': 'refresh_token'}).encode(),
            headers={'Content-Type': 'application/x-www-form-urlencoded'}, method='POST'))
        if not token.get('access_token') or (token.get('scope') and set(token['scope'].split()) != {SCOPE}):
            raise ReportingError('Google did not return the expected read-only reporting access.')
        body = {'dateRanges': [{'startDate': '30daysAgo', 'endDate': 'yesterday'}],
                'metrics': [{'name': name} for name in METRICS],
                'dimensionFilter': {'filter': {'fieldName': 'hostName',
                    'inListFilter': {'values': hosts, 'caseSensitive': False}}}, 'limit': '1'}
        return self._send(Request(f'https://analyticsdata.googleapis.com/v1beta/properties/{property_id}:runReport',
            data=json.dumps(body).encode(), headers={'Content-Type': 'application/json',
            'Authorization': 'Bearer ' + token['access_token']}, method='POST'))


def report(client, config, now):
    result = client.run_report(str(config['property_id']), config['hostnames'])
    if [h.get('name') for h in result['metricHeaders']] != list(METRICS) or result.get('dimensionHeaders'):
        raise ReportingError('Google returned an unexpected report layout.')
    metadata = result['metadata']
    if metadata.get('subjectToThresholding') or metadata.get('dataLossFromOtherRow') or metadata.get('samplingMetadatas') or metadata.get('schemaRestrictionResponse', {}).get('activeMetricRestrictions'):
        raise ReportingError('Google returned a limited report. Review the full report in Analytics.')
    tz = ZoneInfo(metadata['timeZone'])
    rows = result.get('rows', [])
    if type(result.get('rowCount', 0)) is not int or result.get('rowCount', 0) != len(rows) or len(rows) > 1:
        raise ReportingError('Google returned an incomplete report.')
    values = [m['value'] for m in rows[0]['metricValues']] if rows else ['0'] * len(METRICS)
    if len(values) != len(METRICS) or any(not isinstance(v, str) or not re.fullmatch(r'[0-9]+', v) for v in values):
        raise ReportingError('Google returned invalid traffic counts.')
    end = now.astimezone(tz).date() - timedelta(days=1)
    return {'state': 'connected', 'property_id': str(config['property_id']),
            'property_name': config.get('property_name', 'Website'),
            'dashboard_url': config.get('dashboard_url'), 'hostnames': config['hostnames'],
            'from': (end - timedelta(days=29)).isoformat(), 'to': end.isoformat(),
            'timezone': metadata['timeZone'], 'read_at': now.isoformat(), 'read_only': True,
            'sessions': int(values[0]), 'active_users': int(values[1]), 'page_views': int(values[2])}


def snapshot(prof, person, client=None, now=None):
    if client is not None or now is not None or access.sees(prof, person) != 'all' or not access.can_do(prof, person, 'crm_read'):
        return _snapshot(prof,person,client,now)
    from . import reporting_cache
    return reporting_cache.read('analytics',prof,lambda:_snapshot(prof,person),300)


def _snapshot(prof, person, client=None, now=None):
    config = (prof.get('crm') or {}).get('analytics') or {}
    public = {k: config.get(k) for k in ('property_id', 'property_name', 'dashboard_url')}
    if not access.can_do(prof, person, 'crm_read') or access.sees(prof, person) != 'all':
        return {'state': 'restricted'}
    if config.get('enabled') is not True:
        return {'state': 'not_connected', **public}
    try:
        return report(client or Client(config), config, now or datetime.now(timezone.utc))
    except ReportingError as exc:
        return {'state': 'error', 'message': str(exc), **public}
    except (OSError, ValueError, KeyError, TypeError, IndexError, AttributeError):
        return {'state': 'error', 'message': 'Google Analytics configuration or report needs review.', **public}
