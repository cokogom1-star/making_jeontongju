#!/usr/bin/env python3
"""Offline application integration verifier using a dedicated real PostgreSQL DB.

Run: VERIFY_DATABASE_URL=postgresql://.../ourisul_verify python verify_system.py
Uses the application modules, Flask routes, encryption, SQL and durable outbox.
Cafe24/Toss/Slack are synthetic adapters: this does NOT certify provider E2E.
No production DB fallback. Every run creates and drops its own random schema.
"""
import io
import json
import os
import platform
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from threading import Barrier
from pathlib import Path
from importlib.metadata import version, PackageNotFoundError
import secrets
import sys
import traceback
import unittest
from datetime import date, datetime, timedelta, timezone
from unittest.mock import Mock, patch


def main():
    report = {'started_at': datetime.now(timezone.utc).isoformat(),
              'python_version': platform.python_version(),
              'mode': 'real-app-real-postgres-fake-providers',
              'external_requests_permitted': False, 'tests': [], 'passed': False}
    report['commit_sha'] = os.environ.get('GITHUB_SHA', 'unknown')
    report['package_versions'] = {}
    for package in ('Flask', 'gunicorn', 'requests', 'cryptography', 'psycopg'):
        try:
            report['package_versions'][package] = version(package)
        except PackageNotFoundError:
            report['package_versions'][package] = 'not-installed'
    dsn = os.environ.get('VERIFY_DATABASE_URL')
    production_dsn = os.environ.get('DATABASE_URL')
    created = False
    schema_name = 'verify_' + secrets.token_hex(12)
    try:
        if not dsn:
            raise ValueError('VERIFY_DATABASE_URL is required; no DATABASE_URL fallback')
        import psycopg
        from psycopg import sql
        from psycopg.conninfo import conninfo_to_dict, make_conninfo
        from cryptography.fernet import Fernet
        import requests
        info = conninfo_to_dict(dsn)
        dbname = info.get('dbname', '').lower()
        if not any(part in dbname for part in ('verify', 'test')):
            raise ValueError('Dedicated database name must contain verify or test')
        if info.get('service') or not info.get('host') or not info.get('user'):
            raise ValueError('Explicit database host and user are required; service DSNs are prohibited')
        if production_dsn:
            prod = conninfo_to_dict(production_dsn)
            identity = lambda d: (d.get('host'), d.get('port', '5432'), d.get('dbname'))
            if identity(info) == identity(prod):
                raise ValueError('Verification database must differ from DATABASE_URL')
        # Override options so all unqualified application SQL stays in this schema.
        # public is intentionally absent; a missing table must never fall through.
        isolated_dsn = make_conninfo(dsn, options='-c search_path=' + schema_name)
        with psycopg.connect(dsn, connect_timeout=10) as conn:
            report['postgres_version'] = conn.execute('SHOW server_version').fetchone()[0]
            conn.execute(sql.SQL('CREATE SCHEMA {}').format(sql.Identifier(schema_name)))
        created = True
        env = {'DATABASE_URL': isolated_dsn, 'CAFE24_MALL_ID': 'ourisul-verify',
               'CAFE24_CLIENT_ID': 'synthetic-client', 'CAFE24_CLIENT_SECRET': 'synthetic-secret',
               'FLASK_SECRET_KEY': secrets.token_hex(32), 'ADMIN_PASSWORD': secrets.token_hex(20),
               'TOKEN_ENCRYPTION_KEY': Fernet.generate_key().decode(),
               'DIRECT_CHECKOUT_TEST_ENABLED': 'true',
               'SYNTHETIC_ORDER_TEST_ENABLED': 'true',
               'TOSS_TEST_CLIENT_KEY': 'test_gck_synthetic',
               'TOSS_TEST_SECRET_KEY': 'test_gsk_synthetic', 'STORE_LIVE': 'false'}
        with patch.dict(os.environ, env, clear=True), patch.object(
                requests.sessions.Session, 'request', side_effect=AssertionError('Unexpected external HTTP request')):
            # Import after clearing environment; no credentials inherited from the host.
            import cafe24
            import coupang
            import direct_checkout
            import order_notifications as notices
            import send_synthetic_orders
            import synthetic_order_history as fulfillment
            import test_orders
            from app import app
            app.config.update(TESTING=True)

            def response(payload=None, status=200, text='ok'):
                value = Mock(status_code=status, text=text)
                value.json.return_value = payload
                if status >= 400:
                    value.raise_for_status.side_effect = requests.HTTPError('Synthetic provider failure')
                else:
                    value.raise_for_status.return_value = None
                return value

            class IntegrationChecks(unittest.TestCase):
                def setUp(self):
                    self.client = app.test_client()
                    self.auth = ('admin', env['ADMIN_PASSWORD'])
                    with cafe24.database() as conn:
                        cafe24.schema(conn)
                        direct_checkout.schema(conn)
                        notices.schema(conn)
                        test_orders.schema(conn)
                        fulfillment.schema(conn)
                        conn.execute('TRUNCATE ourisul_cafe24_token, ourisul_test_payments, '
                                     'ourisul_test_order_event, ourisul_test_order_state, '
                                     'ourisul_test_orders, ourisul_order_notice')
                        cafe24.save_token(conn, {'access_token': 'synthetic-access',
                                               'refresh_token': 'synthetic-refresh'})

                def get(self, path, **kw):
                    return self.client.get(path, base_url='https://localhost', auth=self.auth, **kw)

                def post(self, path, **kw):
                    return self.client.post(path, base_url='https://localhost', auth=self.auth, **kw)

                def create_payment(self):
                    self.assertEqual(self.get('/admin/payments/test').status_code, 200)
                    with self.client.session_transaction() as sess:
                        csrf = sess['payment_csrf']
                    self.assertEqual(self.post('/admin/payments/test', data={'csrf': csrf}).status_code, 200)
                    with self.client.session_transaction() as sess:
                        return sess['payment_order_id']

                def test_catalog_real_encryption_and_routes(self):
                    items = [{'product_no': 1, 'display': 'T', 'product_name': 'Synthetic product', 'price': '1000'},
                             {'product_no': 2, 'display': 'F', 'product_name': 'Hidden'}]
                    with patch.object(cafe24.requests, 'get', return_value=response({'products': items})) as request:
                        self.assertEqual(cafe24.public_catalog(), [items[0]])
                        self.assertEqual(self.get('/products').status_code, 200)
                        self.assertIn(b'Synthetic product', self.get('/products').data)
                        self.assertEqual(request.call_args.kwargs['headers']['Authorization'], 'Bearer synthetic-access')
                    with patch.object(cafe24.requests, 'get', return_value=response({'product': items[0]})):
                        self.assertEqual(self.get('/products/1').status_code, 200)
                    with cafe24.database() as conn:
                        encrypted = conn.execute('SELECT encrypted_token FROM ourisul_cafe24_token').fetchone()[0]
                    self.assertNotIn('synthetic-access', encrypted)
                    self.assertEqual(cafe24.public_catalog(-1), [])

                def test_catalog_missing_hidden_and_disconnected(self):
                    with patch.object(cafe24.requests, 'get', return_value=response(status=404)):
                        self.assertEqual(self.get('/products/999').status_code, 404)
                    hidden = {'product_no': 999, 'display': 'F', 'product_name': 'Private catalog item'}
                    with patch.object(cafe24.requests, 'get', return_value=response({'product': hidden})):
                        page = self.get('/products/999')
                        self.assertEqual(page.status_code, 404)
                        self.assertNotIn(b'Private catalog item', page.data)
                    with patch.object(cafe24.requests, 'get', return_value=response({'products': []})):
                        page = self.get('/products')
                        self.assertEqual(page.status_code, 200)
                        self.assertIn('등록된 상품이 없습니다.', page.get_data(as_text=True))
                    with cafe24.database() as conn:
                        conn.execute('DELETE FROM ourisul_cafe24_token')
                    with patch.object(cafe24.requests, 'get') as fetch:
                        self.assertEqual(self.get('/products/1').status_code, 503)
                        self.assertIn('상품 준비 중입니다.', self.get('/products').get_data(as_text=True))
                        fetch.assert_not_called()

                def test_catalog_provider_failure_has_safe_recovery_page(self):
                    # Public list preserves navigation; detail distinguishes an outage from 404.
                    failure = requests.Timeout('synthetic-private-diagnostic')
                    with patch.object(cafe24.requests, 'get', side_effect=failure):
                        listing = self.get('/products')
                        detail = self.get('/products/1')
                    self.assertEqual(listing.status_code, 200)
                    self.assertEqual(detail.status_code, 503)
                    for page in (listing, detail):
                        text = page.get_data(as_text=True)
                        self.assertIn('상품 정보를 불러오지 못했습니다.', text)
                        self.assertNotIn('synthetic-private-diagnostic', text)
                        self.assertNotIn('synthetic-access', text)
                    with patch.object(cafe24.requests, 'get', return_value=response(status=500)):
                        self.assertEqual(self.get('/products/1').status_code, 503)

                def test_catalog_invalid_rows_duplicate_and_unsafe_image(self):
                    valid = {'product_no': 7, 'display': 'T', 'product_name': 'Visible unique item',
                             'price': 'NaN', 'list_image': 'javascript:alert(1)', 'selling': 'T'}
                    items = [valid, dict(valid, product_name='Duplicate item'),
                             {'display': 'T', 'product_name': 'Missing number'},
                             {'product_no': 'invalid', 'display': 'T', 'product_name': 'Invalid number'},
                             {'product_no': 0, 'display': 'T', 'product_name': 'Zero number'}]
                    with patch.object(cafe24.requests, 'get', return_value=response({'products': items})):
                        page = self.get('/products')
                    self.assertEqual(page.status_code, 200)
                    text = page.get_data(as_text=True)
                    self.assertIn('Visible unique item', text)
                    self.assertIn('가격 문의', text)
                    for excluded in ('Duplicate item', 'Missing number', 'Invalid number', 'Zero number', 'javascript:alert(1)'):
                        self.assertNotIn(excluded, text)
                    with patch.object(cafe24.requests, 'get', return_value=response({'product': valid})):
                        detail = self.get('/products/7')
                        self.assertEqual(detail.status_code, 200)
                        self.assertNotIn('이동하여 주문하기', detail.get_data(as_text=True))
                        self.assertEqual(self.get('/products/8').status_code, 404)

                def test_catalog_token_refresh_and_failure(self):
                    refreshed = {'access_token': 'refreshed-access', 'refresh_token': 'refreshed-refresh',
                                 'mall_id': env['CAFE24_MALL_ID']}
                    with patch.object(cafe24.requests, 'get', side_effect=[response(status=401), response({'products': []})]) as fetch, patch.object(
                            cafe24.requests, 'post', return_value=response(refreshed)) as refresh:
                        self.assertEqual(cafe24.public_catalog(), [])
                        self.assertEqual(fetch.call_args.kwargs['headers']['Authorization'], 'Bearer refreshed-access')
                        self.assertEqual(refresh.call_args.kwargs['data']['refresh_token'], 'synthetic-refresh')
                    with cafe24.database() as conn:
                        encrypted = conn.execute('SELECT encrypted_token FROM ourisul_cafe24_token').fetchone()[0]
                        saved = json.loads(Fernet(env['TOKEN_ENCRYPTION_KEY']).decrypt(encrypted.encode()))
                        self.assertEqual(saved, refreshed)
                    with patch.object(cafe24.requests, 'get', return_value=response(status=401)), patch.object(
                            cafe24.requests, 'post', return_value=response(status=400)):
                        with self.assertRaises(requests.HTTPError):
                            cafe24.public_catalog()
                    with cafe24.database() as conn:
                        self.assertEqual(conn.execute('SELECT encrypted_token FROM ourisul_cafe24_token').fetchone()[0], encrypted)

                def test_cafe24_orders_pagination_filter_and_outbox(self):
                    paid = {'order_id': 'VERIFY-PAID', 'order_date': '2026-09-29T01:00:00Z',
                            'payment_status': 'T', 'order_status': 'N10', 'items': [{}],
                            'buyer_email': 'private@example.invalid'}
                    unpaid = dict(paid, order_id='VERIFY-UNPAID', payment_status='F')
                    cancelled = dict(paid, order_id='VERIFY-CANCELLED', order_status='C10')
                    with patch.object(cafe24.requests, 'get', side_effect=[response({'orders': [paid, unpaid]}), response({'orders': [cancelled]})]) as fetch:
                        rows = cafe24.order_summaries(date(2026, 9, 29), date(2026, 9, 29), limit=2)
                        self.assertEqual([c.kwargs['params']['offset'] for c in fetch.call_args_list], [0, 2])
                    self.assertEqual(rows, [{'order_id': 'VERIFY-PAID', 'ordered_at': paid['order_date'], 'status': 'N10', 'item_count': 1}])
                    self.assertEqual(notices.record_orders('cafe24', rows, cafe24.database), 1)
                    self.assertEqual(notices.record_orders('cafe24', rows, cafe24.database), 0)
                    with patch.object(cafe24.requests, 'get', return_value=response({'orders': 'malformed'})):
                        with self.assertRaises(ValueError):
                            cafe24.order_summaries(date(2026, 9, 29), date(2026, 9, 29))

                def test_coupang_signed_order_adapter_and_outbox(self):
                    start = datetime(2026, 9, 29, tzinfo=timezone.utc)
                    payload = {'data': [{'orderId': 123, 'status': 'ACCEPT',
                                        'orderedAt': '2026-09-29T09:00:00+09:00',
                                        'orderItems': [{}], 'receiver': {'name': 'PRIVATE'}}]}
                    fake_get = Mock(return_value=response(payload))
                    with patch.dict(os.environ, {'COUPANG_VENDOR_ID': 'VERIFY123', 'COUPANG_ACCESS_KEY': 'synthetic-access',
                                                 'COUPANG_SECRET_KEY': 'synthetic-secret'}):
                        rows = coupang.order_summaries(start, start + timedelta(hours=1), get=fake_get)
                        self.assertIn('signature=', fake_get.call_args.kwargs['headers']['Authorization'])
                        self.assertEqual(fake_get.call_args.kwargs['headers']['X-Requested-By'], 'VERIFY123')
                        self.assertEqual(rows, [{'order_id': 123, 'status': 'ACCEPT', 'ordered_at': '2026-09-29T09:00:00+09:00', 'item_count': 1}])
                        self.assertEqual(notices.record_orders('coupang', rows, cafe24.database), 1)
                        fake_get.return_value = response({'data': [{}]})
                        with self.assertRaises(ValueError):
                            coupang.order_summaries(start, start + timedelta(hours=1), get=fake_get)
                        before = fake_get.call_count
                        with self.assertRaises(ValueError):
                            coupang.order_summaries(start, start + timedelta(hours=24), get=fake_get)
                        self.assertEqual(fake_get.call_count, before)

                def test_payment_auth_csrf_and_disabled_gate(self):
                    self.assertEqual(self.client.get('/admin/payments/test').status_code, 401)
                    self.assertEqual(self.post('/admin/payments/test', data={'csrf': 'bad'}).status_code, 403)
                    with patch.dict(os.environ, {'DIRECT_CHECKOUT_TEST_ENABLED': 'false'}):
                        self.assertEqual(self.get('/admin/payments/test').status_code, 404)
                    with cafe24.database() as conn:
                        self.assertEqual(conn.execute('SELECT count(*) FROM ourisul_test_payments').fetchone()[0], 0)

                def test_synthetic_order_auth_csrf_gate_atomic_and_idempotent(self):
                    path = '/admin/orders/test'
                    self.assertEqual(self.client.get(path).status_code, 401)
                    self.assertEqual(self.post(path, data={'csrf': 'bad'}).status_code, 403)
                    with patch.dict(os.environ, {'SYNTHETIC_ORDER_TEST_ENABLED': 'false'}):
                        self.assertEqual(self.get(path).status_code, 404)
                    self.assertEqual(self.get(path).status_code, 200)
                    with self.client.session_transaction() as sess:
                        csrf = sess['test_order_csrf']
                        expected_id = sess['test_order_id']
                    self.assertEqual(self.post(path, data={'csrf': 'bad'}).status_code, 403)
                    first = self.post(path, data={'csrf': csrf})
                    self.assertEqual(first.status_code, 200)
                    self.assertEqual(first.json, {'order_id': expected_id, 'created': True,
                                                  'notice': 'queued_for_test_sender'})
                    self.assertEqual(self.post(path, data={'csrf': csrf}).json['created'], False)
                    with cafe24.database() as conn:
                        self.assertEqual(conn.execute('SELECT order_id, status, item_count '
                                                      'FROM ourisul_test_orders').fetchall(),
                                         [(expected_id, 'TEST_CREATED', 0)])
                        self.assertEqual(conn.execute('SELECT source, order_id, status, item_count, '
                                                      'attempts, delivered_at FROM ourisul_order_notice').fetchall(),
                                         [('synthetic', expected_id, 'TEST_CREATED', 0, 0, None)])
                        self.assertEqual(conn.execute('SELECT count(*) FROM ourisul_test_payments').fetchone()[0], 0)
                        self.assertEqual(conn.execute('''SELECT status, version FROM ourisul_test_order_state
                            WHERE order_id=%s''', (expected_id,)).fetchone(), ('TEST_CREATED', 0))
                        self.assertEqual(conn.execute('''SELECT version, from_status, to_status FROM
                            ourisul_test_order_event WHERE order_id=%s''', (expected_id,)).fetchall(),
                            [(0, None, 'TEST_CREATED')])
                    sender = Mock(return_value=response())
                    webhook = 'https://hooks.slack.com/services/TEST/TEST/TEST'
                    self.assertEqual(notices.deliver_pending(cafe24.database, webhook, post=sender),
                                     {'delivered': 0, 'failed': 0})
                    sender.assert_not_called()

                def test_synthetic_order_notice_failure_rolls_back_order(self):
                    with cafe24.database() as conn:
                        conn.execute("ALTER TABLE ourisul_order_notice ADD CONSTRAINT no_synthetic "
                                     "CHECK (source <> 'synthetic')")
                    order_id = 'TEST-' + secrets.token_hex(16)
                    try:
                        with self.assertRaises(psycopg.errors.CheckViolation):
                            test_orders.record_test_order(order_id)
                        with cafe24.database() as conn:
                            self.assertEqual(conn.execute('SELECT count(*) FROM ourisul_test_orders '
                                                          'WHERE order_id=%s', (order_id,)).fetchone()[0], 0)
                            self.assertEqual(conn.execute('SELECT count(*) FROM ourisul_order_notice '
                                                          'WHERE order_id=%s', (order_id,)).fetchone()[0], 0)
                            self.assertEqual(conn.execute('SELECT count(*) FROM ourisul_test_order_state '
                                                          'WHERE order_id=%s', (order_id,)).fetchone()[0], 0)
                            self.assertEqual(conn.execute('SELECT count(*) FROM ourisul_test_order_event '
                                                          'WHERE order_id=%s', (order_id,)).fetchone()[0], 0)
                    finally:
                        with cafe24.database() as conn:
                            conn.execute('ALTER TABLE ourisul_order_notice DROP CONSTRAINT no_synthetic')

                def test_synthetic_fulfillment_auth_gate_history_and_idempotency(self):
                    self.assertEqual(self.get('/admin/orders/test').status_code, 200)
                    with self.client.session_transaction() as sess:
                        order_id, csrf = sess['test_order_id'], sess['test_order_csrf']
                    self.assertTrue(test_orders.record_test_order(order_id))
                    path = f'/admin/orders/test/{order_id}/history'
                    self.assertEqual(self.client.get(path).status_code, 401)
                    self.assertEqual(self.client.post(path, data={}).status_code, 401)
                    with patch.dict(os.environ, {'SYNTHETIC_ORDER_TEST_ENABLED': 'false'}):
                        self.assertEqual(self.get(path).status_code, 404)
                        self.assertEqual(self.post(path, data={'csrf': csrf}).status_code, 404)
                    self.assertEqual(self.post(path, data={'csrf': 'bad'}).status_code, 403)
                    original = self.get(path)
                    self.assertEqual(original.status_code, 200)
                    self.assertEqual(original.json['status'], 'TEST_CREATED')
                    self.assertEqual(len(original.json['events']), 1)
                    with cafe24.database() as conn:
                        self.assertEqual(conn.execute('SELECT count(*) FROM ourisul_test_order_event').fetchone()[0], 1)
                    data = {'csrf': csrf, 'status': 'TEST_PREPARED',
                            'expected_version': '0', 'request_key': 'prepare-once'}
                    self.assertEqual(self.post(path, data=data).json,
                                     {'status': 'TEST_PREPARED', 'version': 1, 'created': True})
                    self.assertEqual(self.post(path, data=data).json,
                                     {'status': 'TEST_PREPARED', 'version': 1, 'created': False})
                    self.assertEqual(self.post(path, data=dict(data, status='TEST_SHIPPED')).status_code, 409)
                    self.assertEqual(self.post(path, data=dict(data, request_key='stale')).status_code, 409)
                    self.assertEqual(self.post(path, data=dict(data, status='TEST_DELIVERED',
                                                               expected_version='1', request_key='skip')).status_code, 409)
                    self.assertEqual(self.post(path, data=dict(data, status='TEST_SHIPPED',
                                                               expected_version='1', request_key='ship-once')).status_code, 200)
                    self.assertEqual(self.post(path, data=dict(data, status='TEST_DELIVERED',
                                                               expected_version='2', request_key='deliver-once')).status_code, 200)
                    self.assertEqual(self.post(path, data=data).json,
                                     {'status': 'TEST_PREPARED', 'version': 1, 'created': False})
                    final = self.get(path).json
                    self.assertEqual((final['status'], final['version']), ('TEST_DELIVERED', 3))
                    self.assertEqual([event['to_status'] for event in final['events']],
                                     ['TEST_CREATED', 'TEST_PREPARED', 'TEST_SHIPPED', 'TEST_DELIVERED'])
                    with cafe24.database() as conn:
                        self.assertEqual(conn.execute('SELECT count(*) FROM ourisul_test_order_event').fetchone()[0], 4)
                        self.assertEqual(conn.execute('''SELECT count(*) FROM ourisul_order_notice
                            WHERE source='synthetic' AND order_id=%s''', (order_id,)).fetchone()[0], 1)

                def test_synthetic_fulfillment_rejects_unknown_and_bad_input_without_writes(self):
                    self.assertEqual(self.get('/admin/orders/test').status_code, 200)
                    with self.client.session_transaction() as sess:
                        csrf = sess['test_order_csrf']
                    order_id = 'TEST-' + secrets.token_hex(16)
                    path = f'/admin/orders/test/{order_id}/history'
                    self.assertEqual(self.get(path).status_code, 404)
                    self.assertEqual(self.post(path, data={'csrf': csrf, 'status': 'TEST_PREPARED',
                                                           'expected_version': '0', 'request_key': 'missing'}).status_code, 404)
                    self.assertTrue(test_orders.record_test_order(order_id))
                    for bad in ({'status': 'TEST_DELIVERED', 'expected_version': '0', 'request_key': 'skip'},
                                {'status': 'TEST_PREPARED', 'expected_version': '-1', 'request_key': 'bad-version'},
                                {'status': 'TEST_PREPARED', 'expected_version': '0', 'request_key': 'bad key'},
                                {'status': 'TEST_PREPARED', 'expected_version': 'garbage', 'request_key': 'bad-int'}):
                        self.assertIn(self.post(path, data={'csrf': csrf, **bad}).status_code, (400, 409))
                    self.assertEqual(fulfillment.history(order_id)['version'], 0)
                    with cafe24.database() as conn:
                        self.assertEqual(conn.execute('SELECT count(*) FROM ourisul_test_order_event').fetchone()[0], 1)
                        conn.execute('DROP TABLE ourisul_test_order_event, ourisul_test_order_state')
                    self.assertEqual(self.get(path).status_code, 404)

                def test_synthetic_fulfillment_history_uses_one_snapshot(self):
                    order_id = 'TEST-' + secrets.token_hex(16)
                    self.assertTrue(test_orders.record_test_order(order_id))
                    reads = []

                    @contextmanager
                    def interleaved_read():
                        with cafe24.database() as conn:
                            class ReadConnection:
                                def execute(self, query, params=None):
                                    cursor = conn.execute(query, params)
                                    reads.append(query)
                                    if len(reads) == 1:
                                        # Commit after the reader's first SELECT has captured
                                        # its snapshot, before it consumes the cursor.
                                        fulfillment.transition(order_id, 'TEST_PREPARED', 0,
                                                               'during-history')
                                    return cursor

                            yield ReadConnection()

                    snapshot = fulfillment.history(order_id, connect=interleaved_read)
                    self.assertEqual(len(reads), 1)
                    self.assertEqual((snapshot['status'], snapshot['version']),
                                     ('TEST_CREATED', 0))
                    self.assertEqual([(event['version'], event['to_status'])
                                      for event in snapshot['events']],
                                     [(0, 'TEST_CREATED')])
                    latest = fulfillment.history(order_id)
                    self.assertEqual((latest['status'], latest['version']),
                                     ('TEST_PREPARED', 1))
                    self.assertEqual([event['version'] for event in latest['events']], [0, 1])

                def test_synthetic_fulfillment_concurrent_version_conflict(self):
                    order_id = 'TEST-' + secrets.token_hex(16)
                    self.assertTrue(test_orders.record_test_order(order_id))
                    start = Barrier(2)

                    def advance(key):
                        start.wait(timeout=10)
                        try:
                            return fulfillment.transition(order_id, 'TEST_PREPARED', 0, key)
                        except ValueError as exc:
                            return str(exc)

                    with ThreadPoolExecutor(max_workers=2) as pool:
                        results = list(pool.map(advance, ('prepare-a', 'prepare-b')))
                    self.assertEqual(sum(isinstance(item, dict) for item in results), 1)
                    self.assertIn('Stale synthetic order version', results)
                    self.assertEqual(fulfillment.history(order_id)['version'], 1)
                    with cafe24.database() as conn:
                        self.assertEqual(conn.execute('SELECT count(*) FROM ourisul_test_order_event').fetchone()[0], 2)

                def test_synthetic_sender_gate_source_isolation_retry_and_idempotency(self):
                    order_id = 'TEST-' + secrets.token_hex(16)
                    self.assertTrue(test_orders.record_test_order(order_id))
                    self.assertFalse(test_orders.record_test_order(order_id))
                    real = [{'order_id': 'VERIFY-REAL', 'status': 'N10',
                             'ordered_at': '2026-09-29T00:00:00Z', 'item_count': 1}]
                    self.assertEqual(notices.record_orders('cafe24', real, cafe24.database), 1)
                    test_webhook = 'https://hooks.slack.com/services/TEST/TEST/SYNTHETIC'
                    real_webhook = 'https://hooks.slack.com/services/TEST/TEST/REAL'
                    sender = Mock(return_value=response())
                    with patch.dict(os.environ, {'SLACK_TEST_ORDER_WEBHOOK_URL': test_webhook,
                                                 'SLACK_ORDER_WEBHOOK_URL': real_webhook}):
                        with self.assertRaisesRegex(ValueError, 'disabled'):
                            notices.deliver_synthetic_pending(cafe24.database, post=sender)
                        sender.assert_not_called()
                        with patch.dict(os.environ, {'SYNTHETIC_ORDER_NOTIFICATIONS_ENABLED': 'true',
                                                     'SLACK_TEST_ORDER_WEBHOOK_URL': ''}):
                            with self.assertRaisesRegex(ValueError, 'not configured'):
                                notices.deliver_synthetic_pending(cafe24.database, post=sender)
                        sender.assert_not_called()
                        with patch.dict(os.environ, {'SYNTHETIC_ORDER_NOTIFICATIONS_ENABLED': 'true',
                                                     'STORE_LIVE': 'false'}):
                            with patch.dict(os.environ, {'SLACK_TEST_ORDER_WEBHOOK_URL': real_webhook}):
                                with self.assertRaisesRegex(ValueError, 'must differ'):
                                    notices.deliver_synthetic_pending(cafe24.database, post=sender)
                            with patch.dict(os.environ, {'SLACK_TEST_ORDER_WEBHOOK_URL':
                                                         real_webhook.replace('hooks.slack.com', 'HOOKS.SLACK.COM')}):
                                with self.assertRaisesRegex(ValueError, 'must differ'):
                                    notices.deliver_synthetic_pending(cafe24.database, post=sender)
                            sender.assert_not_called()
                            with patch.dict(os.environ, {'VERIFY_DATABASE_URL': isolated_dsn,
                                                         'DATABASE_URL': 'postgresql://dummy@localhost/production'}):
                                self.assertEqual(send_synthetic_orders.run(['--pending']), {'pending': 1})
                            failed = Mock(side_effect=requests.Timeout)
                            self.assertEqual(notices.deliver_synthetic_pending(cafe24.database, post=failed),
                                             {'delivered': 0, 'failed': 1})
                            self.assertEqual(failed.call_args.args[0], test_webhook)
                            self.assertEqual(failed.call_args.kwargs['json'],
                                             {'text': f'[테스트·미운영] {order_id}'})
                            with cafe24.database() as conn:
                                synthetic = conn.execute('''SELECT attempts, delivered_at, next_attempt_at > now(),
                                    lease_until FROM ourisul_order_notice WHERE source='synthetic' ''').fetchone()
                                real_attempts = conn.execute('''SELECT attempts FROM ourisul_order_notice
                                    WHERE source='cafe24' ''').fetchone()[0]
                                self.assertEqual(synthetic, (1, None, True, None))
                                self.assertEqual(real_attempts, 0)
                                conn.execute('''UPDATE ourisul_order_notice SET next_attempt_at=now()-interval '1 second'
                                    WHERE source='synthetic' ''')
                            self.assertEqual(notices.deliver_synthetic_pending(cafe24.database, post=sender),
                                             {'delivered': 1, 'failed': 0})
                            self.assertEqual(sender.call_args.args[0], test_webhook)
                            self.assertEqual(sender.call_args.kwargs['json'],
                                             {'text': f'[테스트·미운영] {order_id}'})
                            self.assertEqual(notices.deliver_synthetic_pending(cafe24.database, post=sender),
                                             {'delivered': 0, 'failed': 0})
                            self.assertEqual(sender.call_count, 1)
                            with patch.dict(os.environ, {'VERIFY_DATABASE_URL': isolated_dsn,
                                                         'DATABASE_URL': 'postgresql://dummy@localhost/production'}):
                                self.assertEqual(send_synthetic_orders.run(['--pending']), {'pending': 0})
                            with cafe24.database() as conn:
                                self.assertEqual(conn.execute('''SELECT attempts, delivered_at IS NOT NULL
                                    FROM ourisul_order_notice WHERE source='synthetic' ''').fetchone(), (2, True))
                                self.assertEqual(conn.execute('''SELECT attempts FROM ourisul_order_notice
                                    WHERE source='cafe24' ''').fetchone()[0], 0)
                            # Real channel sender remains independently scoped and configured.
                            self.assertEqual(notices.deliver_pending(cafe24.database, post=sender),
                                             {'delivered': 1, 'failed': 0})
                            self.assertEqual(sender.call_args.args[0], real_webhook)
                            self.assertEqual(sender.call_count, 2)

                def test_synthetic_sender_stale_attempt_cannot_change_new_lease(self):
                    test_webhook = 'https://hooks.slack.com/services/TEST/TEST/SYNTHETIC'
                    with patch.dict(os.environ, {'SYNTHETIC_ORDER_NOTIFICATIONS_ENABLED': 'true',
                                                 'SLACK_TEST_ORDER_WEBHOOK_URL': test_webhook}):
                        for old_post_succeeds in (True, False):
                            with self.subTest(old_post_succeeds=old_post_succeeds):
                                with cafe24.database() as conn:
                                    conn.execute('TRUNCATE ourisul_test_order_event, ourisul_test_order_state, '
                                                 'ourisul_test_orders, ourisul_order_notice')
                                order_id = 'TEST-' + secrets.token_hex(16)
                                self.assertTrue(test_orders.record_test_order(order_id))

                                def reclaim(_url, **_kwargs):
                                    with cafe24.database() as conn:
                                        conn.execute('''UPDATE ourisul_order_notice
                                            SET lease_until = now() - interval '1 second'
                                            WHERE source = 'synthetic' AND order_id = %s''', (order_id,))
                                    newer = notices._claim(cafe24.database, synthetic=True)
                                    self.assertEqual(newer[1], order_id)
                                    self.assertEqual(newer[5], 2)
                                    if old_post_succeeds:
                                        return response()
                                    raise requests.Timeout('synthetic stale attempt')

                                result = notices.deliver_synthetic_pending(cafe24.database,
                                                                          post=reclaim, limit=1)
                                self.assertEqual(result, {'delivered': 0, 'failed': 0})
                                with cafe24.database() as conn:
                                    row = conn.execute('''SELECT attempts, delivered_at,
                                        lease_until > now(), next_attempt_at <= now()
                                        FROM ourisul_order_notice WHERE source = 'synthetic'
                                        AND order_id = %s''', (order_id,)).fetchone()
                                self.assertEqual(row, (2, None, True, True))

                def test_synthetic_sender_cli_rejects_missing_or_unsafe_database(self):
                    with patch.dict(os.environ, {'VERIFY_DATABASE_URL': ''}):
                        with self.assertRaisesRegex(ValueError, 'VERIFY_DATABASE_URL is required'):
                            send_synthetic_orders.run(['--pending'])
                    with patch.dict(os.environ, {'VERIFY_DATABASE_URL':
                                                 'postgresql://verify@localhost/ourisul'}):
                        with self.assertRaisesRegex(ValueError, 'disposable test/verify'):
                            send_synthetic_orders.run(['--pending'])
                    with patch.dict(os.environ, {'VERIFY_DATABASE_URL': isolated_dsn}):
                        with self.assertRaisesRegex(ValueError, 'must differ'):
                            send_synthetic_orders.run(['--pending'])

                def test_payment_amount_approval_and_duplicate(self):
                    order = self.create_payment()
                    args = {'orderId': order, 'paymentKey': 'synthetic-key', 'amount': '999'}
                    self.assertEqual(self.get('/admin/payments/test/success', query_string=args).status_code, 400)
                    args['amount'] = '1000'
                    payload = {'orderId': order, 'paymentKey': 'synthetic-key', 'totalAmount': 1000, 'status': 'DONE'}
                    with patch.object(direct_checkout.requests, 'post', return_value=response(payload)) as confirm:
                        self.assertEqual(self.get('/admin/payments/test/success', query_string=args).status_code, 200)
                        self.assertEqual(self.get('/admin/payments/test/success', query_string=args).status_code, 200)
                        self.assertEqual(confirm.call_count, 1)
                        self.assertEqual(confirm.call_args.kwargs['headers']['Idempotency-Key'], order)
                    with cafe24.database() as conn:
                        self.assertEqual(conn.execute('SELECT status, amount FROM ourisul_test_payments').fetchall(), [('DONE', 1000)])
                    args['paymentKey'] = 'different-key'
                    self.assertEqual(self.get('/admin/payments/test/success', query_string=args).status_code, 409)

                def test_payment_timeout_reconciliation(self):
                    order = self.create_payment()
                    args = {'orderId': order, 'paymentKey': 'timeout-key', 'amount': '1000'}
                    payload = {'orderId': order, 'paymentKey': 'timeout-key', 'totalAmount': 1000, 'status': 'DONE'}
                    with patch.object(direct_checkout.requests, 'post', side_effect=requests.Timeout), patch.object(
                            direct_checkout.requests, 'get', return_value=response(payload)) as lookup:
                        self.assertEqual(self.get('/admin/payments/test/success', query_string=args).status_code, 200)
                        self.assertEqual(lookup.call_count, 1)

                def test_outbox_real_sql_dedup_retry_and_delivery(self):
                    batch = [{'order_id': 'VERIFY-1', 'status': 'N10', 'ordered_at': '2026-09-29T00:00:00Z',
                              'item_count': 1, 'buyer_email': 'never-forward@example.invalid'}]
                    self.assertEqual(notices.record_orders('cafe24', batch, cafe24.database), 1)
                    self.assertEqual(notices.record_orders('cafe24', batch, cafe24.database), 0)
                    webhook = 'https://hooks.slack.com/services/TEST/TEST/TEST'
                    failed = Mock(side_effect=requests.Timeout)
                    self.assertEqual(notices.deliver_pending(cafe24.database, webhook, post=failed),
                                     {'delivered': 0, 'failed': 1})
                    with cafe24.database() as conn:
                        row = conn.execute('SELECT attempts, delivered_at, next_attempt_at > now(), lease_until FROM ourisul_order_notice').fetchone()
                        self.assertEqual(row, (1, None, True, None))
                        conn.execute("UPDATE ourisul_order_notice SET next_attempt_at=now()-interval '1 second'")
                    delivered = Mock(return_value=response())
                    self.assertEqual(notices.deliver_pending(cafe24.database, webhook, post=delivered),
                                     {'delivered': 1, 'failed': 0})
                    self.assertNotIn('buyer_email', str(delivered.call_args))
                    self.assertNotIn('never-forward', str(delivered.call_args))
                    self.assertEqual(notices.deliver_pending(cafe24.database, webhook, post=delivered),
                                     {'delivered': 0, 'failed': 0})
                    self.assertEqual(delivered.call_count, 1)
                    with cafe24.database() as conn:
                        self.assertEqual(conn.execute('SELECT attempts, delivered_at IS NOT NULL FROM ourisul_order_notice').fetchone(), (2, True))

                def test_network_default_deny(self):
                    with self.assertRaisesRegex(AssertionError, 'Unexpected external HTTP'):
                        requests.get('https://example.invalid/')

            class Results(unittest.TextTestResult):
                def addSuccess(self, test):
                    super().addSuccess(test)
                    report['tests'].append({'name': test._testMethodName, 'status': 'passed'})
                def addFailure(self, test, err):
                    super().addFailure(test, err)
                    report['tests'].append({'name': test._testMethodName, 'status': 'failed', 'error_type': err[0].__name__,
                                              'locations': [{'file': Path(frame.filename).name, 'line': frame.lineno,
                                                             'function': frame.name}
                                                            for frame in traceback.extract_tb(err[2])][-5:]})
                def addError(self, test, err):
                    super().addError(test, err)
                    report['tests'].append({'name': test._testMethodName, 'status': 'error', 'error_type': err[0].__name__,
                                              'locations': [{'file': Path(frame.filename).name, 'line': frame.lineno,
                                                             'function': frame.name}
                                                            for frame in traceback.extract_tb(err[2])][-5:]})

            result = unittest.TextTestRunner(stream=io.StringIO(), resultclass=Results).run(
                unittest.defaultTestLoader.loadTestsFromTestCase(IntegrationChecks))
            report['passed'] = result.wasSuccessful()
            report['tests_run'] = result.testsRun
            report['limitations'] = ['Provider APIs are fake; no real purchase or Slack delivery',
                                     'Flask test client does not execute browser JavaScript or Toss SDK']
    except Exception as exc:
        report['error_type'] = type(exc).__name__
        # Do not print connection errors, which can contain private DSN information.
        report['error'] = str(exc) if isinstance(exc, ValueError) and not created else 'Verifier setup or execution failed'
    finally:
        if created:
            try:
                with psycopg.connect(dsn, connect_timeout=10) as conn:
                    conn.execute(sql.SQL('DROP SCHEMA {} CASCADE').format(sql.Identifier(schema_name)))
                report['isolated_schema_cleaned'] = True
            except Exception:
                report['isolated_schema_cleaned'] = False
                report['cleanup_required_schema'] = schema_name
                report['passed'] = False
        report['finished_at'] = datetime.now(timezone.utc).isoformat()
        rendered = json.dumps(report, ensure_ascii=False, indent=2)
        Path('verification-result.json').write_text(rendered + '\n', encoding='utf-8')
        print(rendered)
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    sys.exit(main())
