"""Durable, privacy-limited Slack notifications for observed channel orders.

The channel poller supplies summaries; this module never creates a purchase.
Delivery is at least once: a worker crash after Slack accepts a message can
cause a duplicate after the lease expires.
"""

import hmac
import os
import re
from urllib.parse import urlparse

import requests
from psycopg.errors import UndefinedTable


SOURCES = {'cafe24', 'coupang'}
WEBHOOK_HOST = 'hooks.slack.com'


def schema(conn):
    conn.execute('''CREATE TABLE IF NOT EXISTS ourisul_order_notice (
        source TEXT NOT NULL,
        order_id TEXT NOT NULL,
        status TEXT NOT NULL,
        ordered_at TEXT NOT NULL,
        item_count INTEGER NOT NULL,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        delivered_at TIMESTAMPTZ,
        attempts INTEGER NOT NULL DEFAULT 0,
        next_attempt_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        lease_until TIMESTAMPTZ,
        PRIMARY KEY (source, order_id)
    )''')


def _summary(source, item):
    if source not in SOURCES or not isinstance(item, dict):
        raise ValueError('Invalid order summary')
    order_id = item.get('order_id')
    status = item.get('status')
    ordered_at = item.get('ordered_at')
    item_count = item.get('item_count')
    if (type(order_id) not in (int, str) or str(order_id).strip() != str(order_id)
            or not re.fullmatch(r'[A-Za-z0-9_-]{1,80}', str(order_id))
            or not isinstance(status, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,40}', status)
            or not isinstance(ordered_at, str) or not re.fullmatch(r'[0-9T:Z+ .-]{1,40}', ordered_at)
            or type(item_count) is not int or not 0 <= item_count <= 10000):
        raise ValueError('Invalid order summary')
    return str(order_id), status, ordered_at, item_count


def record_orders(source, summaries, connect):
    """Persist new orders before notifying. connect is a zero-argument DB factory."""
    if source not in SOURCES or not isinstance(summaries, list):
        raise ValueError('Invalid order batch')
    rows = [_summary(source, item) for item in summaries]
    inserted = 0
    with connect() as conn:
        schema(conn)
        for order_id, status, ordered_at, item_count in rows:
            result = conn.execute(
                'INSERT INTO ourisul_order_notice '
                '(source, order_id, status, ordered_at, item_count) VALUES (%s,%s,%s,%s,%s) '
                'ON CONFLICT (source, order_id) DO NOTHING',
                (source, order_id, status, ordered_at, item_count))
            inserted += result.rowcount
    return inserted


def webhook_valid(url):
    parsed = urlparse(url or '')
    return (parsed.scheme == 'https' and parsed.hostname == WEBHOOK_HOST
            and parsed.username is None and parsed.password is None
            and parsed.port is None and not parsed.query and not parsed.fragment
            and re.fullmatch(r'/services/[A-Za-z0-9]+/[A-Za-z0-9]+/[A-Za-z0-9]+', parsed.path) is not None)


def _valid_synthetic_id(order_id):
    if not isinstance(order_id, str) or not re.fullmatch(r'TEST-[0-9a-f]{32}', order_id):
        raise ValueError('Invalid synthetic order ID')


def _claim(connect, synthetic=False, order_id=None):
    # Claim atomically so multiple web workers cannot post the same row at once.
    # Both predicates are fixed here: callers cannot turn the test sender into
    # a general-purpose sender for real order rows.
    if synthetic:
        _valid_synthetic_id(order_id)
    elif order_id is not None:
        raise ValueError('Order ID selection is only available for synthetic notices')
    source_filter = ("source = 'synthetic' AND order_id = %s "
                     "AND EXISTS (SELECT 1 FROM ourisul_test_orders t "
                     "WHERE t.order_id = ourisul_order_notice.order_id)"
                     if synthetic else "source IN ('cafe24', 'coupang')")
    with connect() as conn:
        schema(conn)
        return conn.execute('''UPDATE ourisul_order_notice n SET
            lease_until = now() + interval '60 seconds', attempts = attempts + 1
            FROM (SELECT source, order_id FROM ourisul_order_notice
                  WHERE ''' + source_filter + '''
                    AND delivered_at IS NULL AND next_attempt_at <= now()
                    AND (lease_until IS NULL OR lease_until < now())
                  ORDER BY created_at, source, order_id LIMIT 1 FOR UPDATE SKIP LOCKED) queued
            WHERE n.source = queued.source AND n.order_id = queued.order_id
            RETURNING n.source, n.order_id, n.status, n.ordered_at,
                      n.item_count, n.attempts''', (order_id,) if synthetic else ()).fetchone()


def _deliver_pending(connect, webhook_url, post, limit, synthetic, order_id=None):
    if not webhook_valid(webhook_url):
        raise ValueError('Slack webhook is not configured')
    if type(limit) is not int or not 0 <= limit <= 100:
        raise ValueError('Invalid delivery limit')
    delivered = 0
    failed = 0
    for _ in range(limit):
        row = _claim(connect, synthetic=synthetic, order_id=order_id)
        if row is None:
            break
        source, order_id, status, ordered_at, item_count, attempts = row
        message = (f'[테스트·미운영] {order_id}' if synthetic else
                   f'우리술 주문 접수 | {source} | 주문번호 {order_id} | '
                   f'상태 {status} | 시각 {ordered_at} | 품목 수 {item_count}')
        success = False
        try:
            response = post(webhook_url, json={'text': message}, timeout=(5, 15),
                            allow_redirects=False)
            success = response.status_code == 200 and hmac.compare_digest(response.text.strip(), 'ok')
        except requests.RequestException:
            pass
        with connect() as conn:
            if success:
                result = conn.execute('''UPDATE ourisul_order_notice SET delivered_at = now(), lease_until = NULL
                    WHERE source = %s AND order_id = %s AND attempts = %s AND delivered_at IS NULL''',
                                      (source, order_id, attempts))
                delivered += result.rowcount
            else:
                # Exponential retry, capped at one hour. Keep errors and webhook secrets out of DB.
                delay = min(3600, 30 * 2 ** min(attempts - 1, 7))
                result = conn.execute('''UPDATE ourisul_order_notice SET lease_until = NULL,
                    next_attempt_at = now() + (%s * interval '1 second')
                    WHERE source = %s AND order_id = %s AND attempts = %s AND delivered_at IS NULL''',
                             (delay, source, order_id, attempts))
                failed += result.rowcount
    return {'delivered': delivered, 'failed': failed}


def deliver_pending(connect, webhook_url=None, post=requests.post, limit=20):
    """Attempt real channel notices; failures remain queued with backoff."""
    return _deliver_pending(connect, webhook_url or os.environ.get('SLACK_ORDER_WEBHOOK_URL'),
                            post, limit, synthetic=False)


def deliver_synthetic_pending(connect, order_id, post=requests.post):
    """Attempt exactly one selected synthetic notice on the separate test webhook."""
    _valid_synthetic_id(order_id)
    if os.environ.get('SYNTHETIC_ORDER_NOTIFICATIONS_ENABLED') != 'true':
        raise ValueError('Synthetic notifications are disabled')
    test_webhook = os.environ.get('SLACK_TEST_ORDER_WEBHOOK_URL')
    real_webhook = os.environ.get('SLACK_ORDER_WEBHOOK_URL')
    if test_webhook and real_webhook:
        test_url, real_url = urlparse(test_webhook), urlparse(real_webhook)
        if ((test_url.scheme, test_url.hostname, test_url.path) ==
                (real_url.scheme, real_url.hostname, real_url.path)):
            raise ValueError('Test and real Slack webhooks must differ')
    return _deliver_pending(connect, test_webhook,
                            post, 1, synthetic=True, order_id=order_id)


def synthetic_pending_count(connect):
    """Count eligible synthetic notices without claiming or delivering them."""
    try:
        with connect() as conn:
            return conn.execute('''SELECT count(*) FROM ourisul_order_notice n
                WHERE n.source = 'synthetic' AND n.order_id ~ '^TEST-[0-9a-f]{32}$'
                  AND EXISTS (SELECT 1 FROM ourisul_test_orders t WHERE t.order_id = n.order_id)
                  AND n.delivered_at IS NULL''').fetchone()[0]
    except UndefinedTable as exc:
        raise ValueError('Synthetic order tables are not initialized') from exc


def poll_and_deliver(sources, start, end, connect, webhook_url=None, post=requests.post):
    """Fetch real channel order summaries and flush the Slack outbox.

    sources maps a channel name to its ``order_summaries(start, end)`` callable.
    On a channel failure, earlier recorded orders remain queued for retry; the
    caller should rerun the same time window to avoid missing any orders.
    """
    webhook_url = webhook_url or os.environ.get('SLACK_ORDER_WEBHOOK_URL')
    if not webhook_valid(webhook_url):
        raise ValueError('Slack order webhook is not configured')
    if not isinstance(sources, dict) or not sources or set(sources) - SOURCES:
        raise ValueError('Invalid order sources')
    counts = {}
    for source, fetch in sources.items():
        counts[source] = record_orders(source, fetch(start, end), connect)
    return {'new_orders': counts, **deliver_pending(connect, webhook_url, post=post)}
