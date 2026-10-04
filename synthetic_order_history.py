"""Database-only fulfillment rehearsal for administrator-created synthetic orders.

No payment, customer, carrier, or external shipment is created here. The
current state and its append-only history change in one PostgreSQL transaction.
"""

import re

from psycopg.errors import UndefinedTable

from cafe24 import database


STEPS = {
    'TEST_CREATED': 'TEST_PREPARED',
    'TEST_PREPARED': 'TEST_SHIPPED',
    'TEST_SHIPPED': 'TEST_DELIVERED',
}


def schema(conn):
    conn.execute('''CREATE TABLE IF NOT EXISTS ourisul_test_order_state (
        order_id TEXT PRIMARY KEY REFERENCES ourisul_test_orders(order_id),
        status TEXT NOT NULL CHECK (status IN
            ('TEST_CREATED', 'TEST_PREPARED', 'TEST_SHIPPED', 'TEST_DELIVERED')),
        version INTEGER NOT NULL CHECK (version >= 0),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
    )''')
    conn.execute('''CREATE TABLE IF NOT EXISTS ourisul_test_order_event (
        order_id TEXT NOT NULL REFERENCES ourisul_test_orders(order_id),
        version INTEGER NOT NULL CHECK (version >= 0),
        request_key TEXT NOT NULL,
        from_status TEXT,
        to_status TEXT NOT NULL,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        PRIMARY KEY (order_id, version),
        UNIQUE (order_id, request_key)
    )''')


def _valid_order_id(order_id):
    if not isinstance(order_id, str) or not re.fullmatch(r'TEST-[0-9a-f]{32}', order_id):
        raise ValueError('Invalid synthetic order ID')


def initialize(conn, order_id):
    """Initialize a new or pre-existing synthetic order within its transaction."""
    _valid_order_id(order_id)
    schema(conn)
    result = conn.execute('''INSERT INTO ourisul_test_order_state (order_id, status, version)
        SELECT order_id, 'TEST_CREATED', 0 FROM ourisul_test_orders WHERE order_id = %s
        ON CONFLICT (order_id) DO NOTHING''', (order_id,))
    if result.rowcount:
        conn.execute('''INSERT INTO ourisul_test_order_event
            (order_id, version, request_key, from_status, to_status)
            VALUES (%s, 0, 'initial', NULL, 'TEST_CREATED')''', (order_id,))
    return result.rowcount == 1


def transition(order_id, next_status, expected_version, request_key, connect=database):
    """Advance one test-only status; retries with the same key do not add events.

    The state row lock serializes concurrent writers. A repeated key with a
    different target is an error, even when the order has advanced further.
    """
    _valid_order_id(order_id)
    if next_status not in STEPS.values():
        raise ValueError('Invalid synthetic status')
    if type(expected_version) is not int or expected_version < 0:
        raise ValueError('Invalid expected version')
    if not isinstance(request_key, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,80}', request_key):
        raise ValueError('Invalid request key')
    with connect() as conn:
        initialize(conn, order_id)
        current = conn.execute('''SELECT status, version FROM ourisul_test_order_state
            WHERE order_id = %s FOR UPDATE''', (order_id,)).fetchone()
        if current is None:
            raise ValueError('Synthetic order not found')
        prior = conn.execute('''SELECT to_status, version FROM ourisul_test_order_event
            WHERE order_id = %s AND request_key = %s''', (order_id, request_key)).fetchone()
        if prior:
            if prior[0] != next_status:
                raise ValueError('Request key reused for another status')
            return {'status': prior[0], 'version': prior[1], 'created': False}
        status, version = current
        if version != expected_version:
            raise ValueError('Stale synthetic order version')
        if STEPS.get(status) != next_status:
            raise ValueError('Invalid synthetic status transition')
        next_version = version + 1
        conn.execute('''UPDATE ourisul_test_order_state
            SET status = %s, version = %s, updated_at = now() WHERE order_id = %s''',
            (next_status, next_version, order_id))
        conn.execute('''INSERT INTO ourisul_test_order_event
            (order_id, version, request_key, from_status, to_status)
            VALUES (%s, %s, %s, %s, %s)''',
            (order_id, next_version, request_key, status, next_status))
        return {'status': next_status, 'version': next_version, 'created': True}


def history(order_id, connect=database):
    """Read the current simulated delivery state and ordered event history."""
    _valid_order_id(order_id)
    try:
        with connect() as conn:
            current = conn.execute('''SELECT status, version FROM ourisul_test_order_state
                WHERE order_id = %s''', (order_id,)).fetchone()
            if current is None:
                return None
            events = conn.execute('''SELECT version, from_status, to_status, created_at
                FROM ourisul_test_order_event WHERE order_id = %s ORDER BY version''',
                (order_id,)).fetchall()
    except UndefinedTable:
        # A new deployment may receive a read before any synthetic order has
        # initialized the tables. Reads must not run DDL or create records.
        return None
    return {'order_id': order_id, 'status': current[0], 'version': current[1],
            'events': [{'version': version, 'from_status': before,
                        'to_status': after, 'created_at': created_at.isoformat()}
                       for version, before, after, created_at in events]}
