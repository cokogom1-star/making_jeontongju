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


def channel_schema(conn):
    """Create the channel observation ledger on the authorized poller write path."""
    schema(conn)
    conn.execute('''CREATE TABLE IF NOT EXISTS ourisul_channel_order_state (
        source TEXT NOT NULL CHECK (source IN ('cafe24', 'coupang')),
        order_id TEXT NOT NULL,
        status TEXT NOT NULL,
        observed_at TIMESTAMPTZ NOT NULL,
        PRIMARY KEY (source, order_id)
    )''')
    conn.execute('''CREATE TABLE IF NOT EXISTS ourisul_channel_order_event (
        id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        source TEXT NOT NULL,
        order_id TEXT NOT NULL,
        status TEXT NOT NULL,
        observed_at TIMESTAMPTZ NOT NULL
    )''')
    conn.execute('''CREATE INDEX IF NOT EXISTS ourisul_channel_order_event_order_idx
        ON ourisul_channel_order_event (source, order_id, id)''')


def _channel_schema_ready(conn):
    return all(conn.execute('SELECT to_regclass(%s)', (name,)).fetchone()[0] is not None
               for name in ('ourisul_order_notice', 'ourisul_channel_order_state',
                            'ourisul_channel_order_event', 'ourisul_channel_order_event_order_idx'))


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
    """Persist first notices and channel observations; never alter a queued notice."""
    if source not in SOURCES or not isinstance(summaries, list):
        raise ValueError('Invalid order batch')
    # Preserve the first summary for the immutable notice and the final status
    # as the batch's latest collected observation. Validate before any writes.
    rows = [_summary(source, item) for item in summaries]
    first = {}
    last = {}
    for row in rows:
        first.setdefault(row[0], row)
        last[row[0]] = row
    inserted = 0
    with connect() as conn:
        # Initialize only when needed. DDL and order writes must not share a
        # transaction: a concurrent poll waiting on a notice row can deadlock
        # with CREATE INDEX's table lock. The advisory lock serializes first
        # initialization, with a second check for the waiting worker.
        if not _channel_schema_ready(conn):
            conn.execute('SELECT pg_advisory_xact_lock(20261018, 1)')
            if not _channel_schema_ready(conn):
                channel_schema(conn)
    with connect() as conn:
        # A stable lock order avoids deadlock between overlapping poll batches.
        for order_id in sorted(first):
            _, first_status_in_batch, ordered_at, item_count = first[order_id]
            status = last[order_id][1]
            result = conn.execute(
                'INSERT INTO ourisul_order_notice '
                '(source, order_id, status, ordered_at, item_count) VALUES (%s,%s,%s,%s,%s) '
                'ON CONFLICT (source, order_id) DO NOTHING',
                (source, order_id, first_status_in_batch, ordered_at, item_count))
            inserted += result.rowcount
            # The notice row is the per-order serialization lock. It also keeps
            # the first summary and all delivery fields immutable on repolls.
            first_status, first_seen = conn.execute('''SELECT status, created_at
                FROM ourisul_order_notice WHERE source = %s AND order_id = %s FOR UPDATE''',
                (source, order_id)).fetchone()
            state = conn.execute('''SELECT status FROM ourisul_channel_order_state
                WHERE source = %s AND order_id = %s''', (source, order_id)).fetchone()
            if state is None:
                conn.execute('''INSERT INTO ourisul_channel_order_state
                    (source, order_id, status, observed_at) VALUES (%s,%s,%s,%s)''',
                    (source, order_id, first_status, first_seen))
                conn.execute('''INSERT INTO ourisul_channel_order_event
                    (source, order_id, status, observed_at) VALUES (%s,%s,%s,%s)''',
                    (source, order_id, first_status, first_seen))
                state = (first_status,)
            if state[0] != status:
                observed_at = conn.execute('SELECT clock_timestamp()').fetchone()[0]
                conn.execute('''UPDATE ourisul_channel_order_state
                    SET status = %s, observed_at = %s WHERE source = %s AND order_id = %s''',
                    (status, observed_at, source, order_id))
                conn.execute('''INSERT INTO ourisul_channel_order_event
                    (source, order_id, status, observed_at) VALUES (%s,%s,%s,%s)''',
                    (source, order_id, status, observed_at))
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
        source, claimed_order_id, status, ordered_at, item_count, attempts = row
        message = (f'[테스트·미운영] {claimed_order_id}' if synthetic else
                   f'우리술 주문 접수 | {source} | 주문번호 {claimed_order_id} | '
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
                                      (source, claimed_order_id, attempts))
                delivered += result.rowcount
            else:
                # Exponential retry, capped at one hour. Keep errors and webhook secrets out of DB.
                delay = min(3600, 30 * 2 ** min(attempts - 1, 7))
                result = conn.execute('''UPDATE ourisul_order_notice SET lease_until = NULL,
                    next_attempt_at = now() + (%s * interval '1 second')
                    WHERE source = %s AND order_id = %s AND attempts = %s AND delivered_at IS NULL''',
                             (delay, source, claimed_order_id, attempts))
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


def synthetic_pending_counts(connect):
    """Classify undelivered synthetic notices without claiming or delivering them."""
    try:
        with connect() as conn:
            ready, deferred, leased, total = conn.execute('''SELECT
                count(*) FILTER (WHERE (n.lease_until IS NULL OR n.lease_until < now())
                    AND n.next_attempt_at <= now()),
                count(*) FILTER (WHERE (n.lease_until IS NULL OR n.lease_until < now())
                    AND n.next_attempt_at > now()),
                count(*) FILTER (WHERE n.lease_until >= now()),
                count(*)
                FROM ourisul_order_notice n
                WHERE n.source = 'synthetic' AND n.order_id ~ '^TEST-[0-9a-f]{32}$'
                  AND EXISTS (SELECT 1 FROM ourisul_test_orders t WHERE t.order_id = n.order_id)
                  AND n.delivered_at IS NULL''').fetchone()
            return {'ready': ready, 'deferred': deferred, 'leased': leased, 'total': total}
    except UndefinedTable as exc:
        raise ValueError('Synthetic order tables are not initialized') from exc


def synthetic_notice_status_in_connection(conn, order_id):
    """Inspect a backed notice in the caller's read transaction."""
    _valid_synthetic_id(order_id)
    row = conn.execute('''SELECT n.attempts, n.delivered_at,
                    n.next_attempt_at, n.lease_until,
                    n.lease_until >= now(), n.next_attempt_at > now()
                FROM ourisul_order_notice n
                WHERE n.source = 'synthetic' AND n.order_id = %s
                  AND EXISTS (SELECT 1 FROM ourisul_test_orders t WHERE t.order_id = n.order_id)''',
                (order_id,)).fetchone()
    return _synthetic_notice_result(order_id, row)


def synthetic_notice_status(connect, order_id):
    """Inspect one backed test notice without claiming it or contacting Slack."""
    _valid_synthetic_id(order_id)
    try:
        with connect() as conn:
            return synthetic_notice_status_in_connection(conn, order_id)
    except UndefinedTable as exc:
        raise ValueError('Synthetic order tables are not initialized') from exc


def _synthetic_notice_result(order_id, row):
    if row is None:
        return {'order_id': order_id, 'state': 'missing'}
    attempts, delivered_at, next_attempt_at, lease_until, leased, deferred = row
    result = {'order_id': order_id, 'attempts': attempts}
    if delivered_at is not None:
        result.update(state='delivered', delivered_at=delivered_at.isoformat())
    elif leased:
        result.update(state='leased', lease_until=lease_until.isoformat())
    elif deferred:
        result.update(state='deferred', retry_at=next_attempt_at.isoformat())
    else:
        result['state'] = 'ready'
    return result


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

