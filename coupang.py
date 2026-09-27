"""Read-only Coupang order integration. No API request is made without Wing keys."""
import hashlib
import hmac
import json
import os
import re
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode, urlparse

import requests

API_BASE = 'https://api-gateway.coupang.com'
KST = timezone(timedelta(hours=9))
ORDER_STATUSES = {'ACCEPT', 'INSTRUCT', 'DEPARTURE', 'DELIVERING',
                  'FINAL_DELIVERY', 'NONE_TRACKING'}


def configured():
    return all(os.environ.get(key) for key in
               ('COUPANG_VENDOR_ID', 'COUPANG_ACCESS_KEY', 'COUPANG_SECRET_KEY'))


def product_url(product_no):
    """Only allow a genuine product page on the configured purchase channel."""
    try:
        links = json.loads(os.environ.get('COUPANG_PRODUCT_URLS', '{}'))
        url = links.get(str(product_no)) if isinstance(links, dict) else None
        parsed = urlparse(url or '')
        if (parsed.scheme == 'https' and parsed.hostname in ('coupang.com', 'www.coupang.com')
                and parsed.username is None and parsed.password is None
                and re.fullmatch(r'/vp/products/\d+', parsed.path)):
            return url
    except (ValueError, TypeError):
        pass
    return None


def authorization(method, path, query, access_key, secret_key, now=None):
    """Sign the exact query string sent to Coupang (HMAC SHA256)."""
    timestamp = (now or datetime.now(timezone.utc)).astimezone(timezone.utc).strftime('%y%m%dT%H%M%SZ')
    message = timestamp + method.upper() + path + query
    signature = hmac.new(secret_key.encode(), message.encode(), hashlib.sha256).hexdigest()
    return (f'CEA algorithm=HmacSHA256, access-key={access_key}, '
            f'signed-date={timestamp}, signature={signature}')


def order_summaries(start, end, status='ACCEPT', get=requests.get):
    """Query a sub-24-hour interval and return only non-personal order metadata."""
    if not configured():
        raise ValueError('Coupang Wing API is not connected')
    if status not in ORDER_STATUSES or start.tzinfo is None or end.tzinfo is None:
        raise ValueError('Invalid order query')
    if not timedelta(0) < end - start < timedelta(hours=24):
        raise ValueError('Order query must be under 24 hours')
    vendor = os.environ['COUPANG_VENDOR_ID']
    if not re.fullmatch(r'[A-Za-z0-9]+', vendor):
        raise ValueError('Invalid vendor ID')
    path = f'/v2/providers/openapi/apis/api/v5/vendors/{vendor}/ordersheets'
    query = urlencode({'createdAtFrom': start.astimezone(KST).isoformat(timespec='minutes'),
                       'createdAtTo': end.astimezone(KST).isoformat(timespec='minutes'),
                       'searchType': 'timeFrame', 'status': status})
    auth = authorization('GET', path, query, os.environ['COUPANG_ACCESS_KEY'],
                         os.environ['COUPANG_SECRET_KEY'])
    response = get(API_BASE + path + '?' + query,
                   headers={'Authorization': auth, 'X-Requested-By': vendor}, timeout=(5, 20))
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict) or not isinstance(payload.get('data'), list):
        raise ValueError('Unexpected Coupang response')
    return [{'order_id': item.get('orderId'), 'status': item.get('status'),
             'ordered_at': item.get('orderedAt'), 'item_count': len(item.get('orderItems') or [])}
            for item in payload['data'] if isinstance(item, dict)]


def demo_orders():
    return [{'order_id': 'DEMO-1001', 'status': 'ACCEPT',
             'ordered_at': '시연 데이터', 'item_count': 1}]
