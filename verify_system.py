#!/usr/bin/env python3
"""Offline application integration verifier using a dedicated real PostgreSQL DB.

Run: VERIFY_DATABASE_URL=postgresql://.../ourisul_verify python verify_system.py
Uses the application modules, Flask routes, encryption, SQL and durable outbox.
Cafe24/Toss/Slack are synthetic adapters: this does NOT certify provider E2E.
No production DB fallback. Every run creates and drops its own random schema.
"""
import io
from itertools import product
import json
import os
import platform
import re
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
from werkzeug.datastructures import MultiDict


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
                        notices.channel_schema(conn)
                        test_orders.schema(conn)
                        fulfillment.schema(conn)
                        conn.execute('TRUNCATE ourisul_cafe24_token, ourisul_test_payments, '
                                     'ourisul_test_order_item, '
                                     'ourisul_test_order_event, ourisul_test_order_state, '
                                     'ourisul_test_orders, ourisul_order_notice, '
                                     'ourisul_channel_order_state, ourisul_channel_order_event')
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

                def test_catalog_paginates_visible_products(self):
                    first = [{'product_no': number, 'display': 'T',
                              'product_name': f'Synthetic {number}', 'price': '1000'}
                             for number in range(1, 25)]
                    first[0].update(display='F', product_name='Hidden first product')
                    last = {'product_no': 25, 'display': 'T',
                            'product_name': 'Last visible product', 'price': '1000'}
                    pages = [response({'products': first + [last]}), response({'products': [last]})]
                    with patch.object(cafe24.requests, 'get', side_effect=pages) as fetch:
                        page = self.get('/products')
                        second = self.get('/products?page=2')
                    self.assertEqual(page.status_code, 200)
                    self.assertIn('다음 상품', page.get_data(as_text=True))
                    self.assertNotIn('Last visible product', page.get_data(as_text=True))
                    self.assertNotIn('Hidden first product', page.get_data(as_text=True))
                    self.assertIn('Last visible product', second.get_data(as_text=True))
                    self.assertIn('이전 상품', second.get_data(as_text=True))
                    self.assertNotIn('다음 상품', second.get_data(as_text=True))
                    self.assertEqual([call.kwargs['params']['offset'] for call in fetch.call_args_list], [0, 24])
                    self.assertTrue(all(call.kwargs['params']['display'] == 'T'
                                        for call in fetch.call_args_list))
                    self.assertEqual(self.get('/products?page=210').status_code, 404)
                    self.assertEqual(self.get('/products?page=garbage').status_code, 404)

                def test_catalog_second_page_refresh_and_malformed_page(self):
                    first = [{'product_no': number, 'display': 'T'} for number in range(1, 26)]
                    refreshed = {'access_token': 'refreshed-access', 'refresh_token': 'refreshed-refresh',
                                 'mall_id': env['CAFE24_MALL_ID']}
                    with patch.object(cafe24.requests, 'get', side_effect=[
                            response(status=401), response({'products': first})]) as fetch, patch.object(
                            cafe24.requests, 'post', return_value=response(refreshed)):
                        catalog, has_next = cafe24.public_catalog(with_next=True)
                        self.assertEqual(len(catalog), 24)
                        self.assertTrue(has_next)
                        self.assertEqual([call.kwargs['params']['offset'] for call in fetch.call_args_list],
                                         [0, 0])
                        self.assertEqual(fetch.call_args.kwargs['headers']['Authorization'],
                                         'Bearer refreshed-access')
                    with patch.object(cafe24.requests, 'get', return_value=response({'products': first})):
                        self.assertIsNone(cafe24.public_catalog(page=209, with_next=True)[1])
                        capped = self.get('/products?page=209').get_data(as_text=True)
                        self.assertIn('표시 한도에 도달했습니다.', capped)
                        self.assertNotIn('다음 상품', capped)
                    with patch.object(cafe24.requests, 'get', return_value=response({'products': None})):
                        self.assertIn('상품 정보를 불러오지 못했습니다.',
                                      self.get('/products').get_data(as_text=True))

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

                def test_channel_observations_preserve_first_notice_and_deduplicate(self):
                    def summary(status, order_id='OBS-1'):
                        return {'order_id': order_id, 'status': status,
                                'ordered_at': '2026-10-01T00:00:00Z', 'item_count': 1}
                    self.assertEqual(notices.record_orders('cafe24', [summary('PAID')], cafe24.database), 1)
                    with cafe24.database() as conn:
                        conn.execute('''UPDATE ourisul_order_notice SET attempts = 3,
                            delivered_at = now(), next_attempt_at = now() + interval '1 hour',
                            lease_until = now() + interval '2 hours'
                            WHERE source = 'cafe24' AND order_id = 'OBS-1' ''')
                        before = conn.execute('''SELECT * FROM ourisul_order_notice
                            WHERE source = 'cafe24' AND order_id = 'OBS-1' ''').fetchone()
                    self.assertEqual(notices.record_orders('cafe24', [summary('PAID')], cafe24.database), 0)
                    self.assertEqual(notices.record_orders('cafe24', [summary('SHIPPED')], cafe24.database), 0)
                    self.assertEqual(notices.record_orders('cafe24', [summary('SHIPPED')], cafe24.database), 0)
                    with cafe24.database() as conn:
                        self.assertEqual(conn.execute('''SELECT * FROM ourisul_order_notice
                            WHERE source = 'cafe24' AND order_id = 'OBS-1' ''').fetchone(), before)
                        self.assertEqual(conn.execute('''SELECT status FROM ourisul_channel_order_state
                            WHERE source = 'cafe24' AND order_id = 'OBS-1' ''').fetchone(), ('SHIPPED',))
                        events = conn.execute('''SELECT status, observed_at FROM ourisul_channel_order_event
                            WHERE source = 'cafe24' AND order_id = 'OBS-1' ORDER BY id''').fetchall()
                        self.assertEqual([event[0] for event in events], ['PAID', 'SHIPPED'])
                        self.assertEqual(events[0][1], before[5])
                    # A duplicate ID retains its first notice and last observed status.
                    self.assertEqual(notices.record_orders('cafe24',
                        [summary('PAID', 'OBS-2'), summary('SHIPPED', 'OBS-2')], cafe24.database), 1)
                    with cafe24.database() as conn:
                        self.assertEqual(conn.execute('''SELECT status FROM ourisul_channel_order_state
                            WHERE source = 'cafe24' AND order_id = 'OBS-2' ''').fetchone(), ('SHIPPED',))
                        self.assertEqual(conn.execute('''SELECT status FROM ourisul_order_notice
                            WHERE source = 'cafe24' AND order_id = 'OBS-2' ''').fetchone(), ('PAID',))
                        self.assertEqual(conn.execute('''SELECT status FROM ourisul_channel_order_event
                            WHERE source = 'cafe24' AND order_id = 'OBS-2' ORDER BY id''').fetchall(),
                            [('PAID',), ('SHIPPED',)])
                    # An older notice gets its original database save time as its first event.
                    with cafe24.database() as conn:
                        conn.execute('''INSERT INTO ourisul_order_notice
                            (source, order_id, status, ordered_at, item_count)
                            VALUES ('coupang', 'LEGACY', 'ACCEPT', '2026-10-01', 1)''')
                    self.assertEqual(notices.record_orders('coupang', [summary('SHIPPED', 'LEGACY')], cafe24.database), 0)
                    with cafe24.database() as conn:
                        self.assertEqual(conn.execute('''SELECT status FROM ourisul_channel_order_event
                            WHERE source = 'coupang' AND order_id = 'LEGACY' ORDER BY id''').fetchall(),
                            [('ACCEPT',), ('SHIPPED',)])
                        saved_at = conn.execute("SELECT created_at FROM ourisul_order_notice WHERE order_id = 'LEGACY'").fetchone()[0]
                        first_at = conn.execute("SELECT observed_at FROM ourisul_channel_order_event WHERE order_id = 'LEGACY' ORDER BY id LIMIT 1").fetchone()[0]
                        self.assertEqual(first_at, saved_at)
                    inbox = self.get('/admin/orders/channels').get_data(as_text=True)
                    self.assertIn('SHIPPED', inbox)
                    self.assertIn('PAID', inbox)
                    self.assertIn('ACCEPT', inbox)

                def test_channel_observation_batch_rollback_and_concurrent_replay(self):
                    row = lambda status, order_id: {'order_id': order_id, 'status': status,
                        'ordered_at': '2026-10-01T00:00:00Z', 'item_count': 1}
                    with self.assertRaises(ValueError):
                        notices.record_orders('cafe24', [row('PAID', 'VALID'),
                            row('INVALID STATUS', 'BAD')], cafe24.database)
                    with cafe24.database() as conn:
                        self.assertEqual(conn.execute("SELECT count(*) FROM ourisul_order_notice WHERE order_id = 'VALID'").fetchone()[0], 0)
                        conn.execute("ALTER TABLE ourisul_channel_order_event ADD CONSTRAINT reject_blocked CHECK (status <> 'BLOCKED')")
                    with self.assertRaises(psycopg.errors.CheckViolation):
                        notices.record_orders('cafe24', [row('PAID', 'A-ROLLBACK'),
                            row('BLOCKED', 'Z-FAIL')], cafe24.database)
                    with cafe24.database() as conn:
                        for table in ('ourisul_order_notice', 'ourisul_channel_order_state',
                                      'ourisul_channel_order_event'):
                            self.assertEqual(conn.execute(f"SELECT count(*) FROM {table} WHERE order_id IN ('A-ROLLBACK', 'Z-FAIL')").fetchone()[0], 0)
                        conn.execute('ALTER TABLE ourisul_channel_order_event DROP CONSTRAINT reject_blocked')
                    barrier = Barrier(2)
                    def record():
                        barrier.wait()
                        return notices.record_orders('cafe24', [row('PAID', 'RACE')], cafe24.database)
                    with ThreadPoolExecutor(max_workers=2) as pool:
                        results = list(pool.map(lambda _: record(), range(2)))
                    self.assertEqual(sorted(results), [0, 1])
                    with cafe24.database() as conn:
                        self.assertEqual(conn.execute("SELECT count(*) FROM ourisul_channel_order_event WHERE order_id = 'RACE'").fetchone()[0], 1)
                    # Opposite batch order still takes row locks in the same order.
                    barrier = Barrier(2)
                    def record_pair(reverse):
                        barrier.wait()
                        ids = ('RACE-A', 'RACE-B')
                        if reverse:
                            ids = tuple(reversed(ids))
                        return notices.record_orders('coupang', [row('PAID', key) for key in ids], cafe24.database)
                    with ThreadPoolExecutor(max_workers=2) as pool:
                        results = list(pool.map(record_pair, (False, True)))
                    self.assertEqual(sorted(results), [0, 2])
                    # Concurrent first initialization cannot put DDL locks in
                    # the same transaction as notice/event writes.
                    with cafe24.database() as conn:
                        conn.execute('DROP TABLE ourisul_channel_order_event, ourisul_channel_order_state')
                    barrier = Barrier(2)
                    def first_poll():
                        barrier.wait()
                        return notices.record_orders('coupang', [row('PAID', 'INIT')], cafe24.database)
                    with ThreadPoolExecutor(max_workers=2) as pool:
                        self.assertEqual(sorted(pool.map(lambda _: first_poll(), range(2))), [0, 1])
                    with cafe24.database() as conn:
                        self.assertEqual(conn.execute("SELECT count(*) FROM ourisul_channel_order_event WHERE order_id = 'INIT'").fetchone()[0], 1)

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
                            test_orders.record_test_order(order_id, scenario={'SYNTHETIC-CHEONGJU': 1})
                        with cafe24.database() as conn:
                            self.assertEqual(conn.execute('SELECT count(*) FROM ourisul_test_orders '
                                                          'WHERE order_id=%s', (order_id,)).fetchone()[0], 0)
                            self.assertEqual(conn.execute('SELECT count(*) FROM ourisul_test_order_item '
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

                def test_synthetic_exact_replay_repairs_only_missing_notice(self):
                    for scenario in (None, {'SYNTHETIC-CHEONGJU': 2}):
                        order_id = 'TEST-' + secrets.token_hex(16)
                        self.assertTrue(test_orders.record_test_order(order_id, scenario=scenario))
                        with cafe24.database() as conn:
                            original = conn.execute('''SELECT ordered_at FROM ourisul_order_notice
                                WHERE source='synthetic' AND order_id=%s''', (order_id,)).fetchone()[0]
                            conn.execute('''DELETE FROM ourisul_order_notice
                                WHERE source='synthetic' AND order_id=%s''', (order_id,))
                        # A different basket cannot turn a conflict into a repair.
                        different = {'SYNTHETIC-TAKJU': 1} if scenario else {'SYNTHETIC-CHEONGJU': 1}
                        with self.assertRaisesRegex(ValueError, 'scenario conflicts'):
                            test_orders.record_test_order(order_id, scenario=different)
                        with cafe24.database() as conn:
                            self.assertEqual(conn.execute('''SELECT count(*) FROM ourisul_order_notice
                                WHERE source='synthetic' AND order_id=%s''', (order_id,)).fetchone()[0], 0)
                        self.assertFalse(test_orders.record_test_order(order_id, scenario=scenario))
                        with cafe24.database() as conn:
                            self.assertEqual(conn.execute('''SELECT status, ordered_at, item_count, attempts,
                                delivered_at FROM ourisul_order_notice WHERE source='synthetic'
                                AND order_id=%s''', (order_id,)).fetchone(),
                                ('TEST_CREATED', original, 0, 0, None))
                            conn.execute('''UPDATE ourisul_order_notice SET attempts=3,
                                next_attempt_at=now()+interval '1 hour',
                                lease_until=now()+interval '2 hours', delivered_at=now()
                                WHERE source='synthetic' AND order_id=%s''', (order_id,))
                            before = conn.execute('''SELECT * FROM ourisul_order_notice
                                WHERE source='synthetic' AND order_id=%s''', (order_id,)).fetchone()
                        self.assertFalse(test_orders.record_test_order(order_id, scenario=scenario))
                        with cafe24.database() as conn:
                            self.assertEqual(conn.execute('''SELECT * FROM ourisul_order_notice
                                WHERE source='synthetic' AND order_id=%s''', (order_id,)).fetchone(), before)
                            self.assertEqual(conn.execute('''SELECT count(*) FROM ourisul_test_order_event
                                WHERE order_id=%s''', (order_id,)).fetchone()[0], 1)

                def test_synthetic_replay_notice_repair_failure_rolls_back(self):
                    order_id = 'TEST-' + secrets.token_hex(16)
                    self.assertTrue(test_orders.record_test_order(order_id))
                    with cafe24.database() as conn:
                        conn.execute('''DELETE FROM ourisul_order_notice
                            WHERE source='synthetic' AND order_id=%s''', (order_id,))
                        conn.execute("ALTER TABLE ourisul_order_notice ADD CONSTRAINT no_repaired_synthetic "
                                     "CHECK (source <> 'synthetic')")
                        tables = ('ourisul_test_orders', 'ourisul_test_order_item',
                                  'ourisul_order_notice', 'ourisul_test_order_state',
                                  'ourisul_test_order_event')
                        before = {table: conn.execute(f'SELECT * FROM {table} ORDER BY 1, 2').fetchall()
                                  for table in tables}
                    try:
                        with self.assertRaises(psycopg.errors.CheckViolation):
                            test_orders.record_test_order(order_id)
                        with cafe24.database() as conn:
                            after = {table: conn.execute(f'SELECT * FROM {table} ORDER BY 1, 2').fetchall()
                                     for table in tables}
                            self.assertEqual(after, before)
                    finally:
                        with cafe24.database() as conn:
                            conn.execute('ALTER TABLE ourisul_order_notice DROP CONSTRAINT no_repaired_synthetic')

                def test_synthetic_route_replay_repairs_only_after_authorized_post(self):
                    path = '/admin/orders/test'
                    for scenario in (False, True):
                        self.assertEqual(self.get(path + '?view=html').status_code, 200)
                        with self.client.session_transaction() as sess:
                            csrf = sess['test_order_csrf']
                            order_id = sess['test_scenario_order_id' if scenario else 'test_order_id']
                        data = {'csrf': csrf, 'order_id': order_id}
                        if scenario:
                            data.update({'scenario': 'fixed-v1',
                                         'SYNTHETIC-CHEONGJU': '1', 'SYNTHETIC-TAKJU': '0'})
                        self.assertEqual(self.post(path, data=data).status_code, 200)
                        with cafe24.database() as conn:
                            conn.execute('''DELETE FROM ourisul_order_notice
                                WHERE source='synthetic' AND order_id=%s''', (order_id,))

                        def missing():
                            with cafe24.database() as conn:
                                return conn.execute('''SELECT count(*) FROM ourisul_order_notice
                                    WHERE source='synthetic' AND order_id=%s''', (order_id,)).fetchone()[0] == 0

                        self.assertTrue(missing())
                        self.assertEqual(self.get(path + '?view=html').status_code, 200)
                        self.assertEqual(self.get(f'/admin/orders/test/{order_id}/history?view=html').status_code, 200)
                        self.assertTrue(missing())
                        self.assertEqual(self.post(path, data=dict(data, csrf='wrong')).status_code, 403)
                        self.assertEqual(self.client.post(path, data=data, base_url='https://localhost').status_code, 401)
                        with patch.dict(os.environ, {'SYNTHETIC_ORDER_TEST_ENABLED': 'false'}):
                            self.assertEqual(self.post(path, data=data).status_code, 404)
                        self.assertTrue(missing())
                        replay = self.post(path, data=data)
                        self.assertEqual((replay.status_code, replay.json['created']), (200, False))
                        self.assertFalse(missing())

                def test_synthetic_forms_keep_identity_across_tabs_and_reject_foreign_ids(self):
                    path = '/admin/orders/test?view=html'

                    def open_form():
                        page = self.get(path)
                        self.assertEqual(page.status_code, 200)
                        with self.client.session_transaction() as sess:
                            result = (sess['test_order_csrf'], sess['test_order_id'],
                                      sess['test_scenario_order_id'])
                        self.assertIn(f'name="order_id" value="{result[1]}"',
                                      page.get_data(as_text=True))
                        self.assertIn(f'name="order_id" value="{result[2]}"',
                                      page.get_data(as_text=True))
                        return result

                    first = open_form()
                    second = open_form()
                    self.assertEqual(first[0], second[0])
                    self.assertNotEqual(first[1:], second[1:])
                    empty = lambda form: {'csrf': form[0], 'order_id': form[1]}
                    basket = lambda form, qty: {'csrf': form[0], 'order_id': form[2],
                        'scenario': 'fixed-v1', 'SYNTHETIC-CHEONGJU': str(qty),
                        'SYNTHETIC-TAKJU': '0'}
                    self.assertEqual(self.post(path, data=empty(first)).status_code, 303)
                    self.assertEqual(self.post(path, data=empty(second)).status_code, 303)
                    self.assertEqual(self.post(path, data=basket(first, 1)).status_code, 303)
                    self.assertEqual(self.post(path, data=basket(second, 2)).status_code, 303)
                    self.assertEqual(self.post(path, data=empty(first)).status_code, 303)
                    self.assertEqual(self.post(path, data=basket(first, 1)).status_code, 303)
                    self.assertEqual(self.post(path, data=basket(first, 3)).status_code, 409)
                    foreign_client = app.test_client()
                    self.assertEqual(foreign_client.get(path, base_url='https://localhost',
                                                        auth=self.auth).status_code, 200)
                    with foreign_client.session_transaction() as sess:
                        foreign_id = sess['test_order_id']
                    tables = ('ourisul_test_orders', 'ourisul_test_order_item',
                              'ourisul_order_notice', 'ourisul_test_order_state',
                              'ourisul_test_order_event')
                    with cafe24.database() as conn:
                        saved = conn.execute('''SELECT order_id FROM ourisul_test_orders
                            ORDER BY order_id''').fetchall()
                        self.assertEqual(saved, [(value,) for value in sorted(
                            (first[1], first[2], second[1], second[2]))])
                        before = {table: conn.execute(f'SELECT * FROM {table} ORDER BY 1, 2').fetchall()
                                  for table in tables}
                    invalid = [empty(first) | {'order_id': foreign_id},
                               empty(first) | {'order_id': 'TEST-' + 'f' * 32},
                               empty(first) | {'order_id': first[2]},
                               basket(first, 1) | {'order_id': first[1]}]
                    for form in invalid:
                        self.assertEqual(self.post(path, data=form).status_code, 403)
                    self.assertEqual(self.post(path, data=MultiDict(list(empty(first).items()) +
                                    [('order_id', first[1])])).status_code, 400)
                    self.assertEqual(self.post(path, data={'csrf': first[0]}).status_code, 403)
                    self.assertEqual(self.post('/admin/orders/test', data={
                        key: value for key, value in basket(first, 1).items()
                        if key != 'order_id'}).status_code, 403)
                    for _ in range(test_orders.RECENT_FORM_LIMIT + 1):
                        open_form()
                    self.assertEqual(self.post(path, data=empty(first)).status_code, 403)
                    self.assertEqual(self.post(path, data=basket(first, 1)).status_code, 403)
                    with cafe24.database() as conn:
                        after = {table: conn.execute(f'SELECT * FROM {table} ORDER BY 1, 2').fetchall()
                                 for table in tables}
                    self.assertEqual(after, before)

                def test_admin_rehearsal_review_confirm_and_session_receipt(self):
                    base = '/admin/orders/test/rehearsal'
                    tables = ('ourisul_test_orders', 'ourisul_test_order_item',
                              'ourisul_order_notice', 'ourisul_test_order_state',
                              'ourisul_test_order_event')

                    def saved():
                        with cafe24.database() as conn:
                            return {table: conn.execute(f'SELECT * FROM {table} ORDER BY 1, 2').fetchall()
                                    for table in tables}

                    self.assertEqual(self.client.get(base).status_code, 401)
                    with patch.dict(os.environ, {'SYNTHETIC_ORDER_TEST_ENABLED': 'false'}):
                        self.assertEqual(self.get(base).status_code, 404)
                    before = saved()
                    page = self.get(base)
                    self.assertEqual(page.status_code, 200)
                    self.assertIn('가상 주문 연습', page.get_data(as_text=True))
                    with self.client.session_transaction() as sess:
                        csrf = sess['test_order_csrf']
                        order_id = sess['test_rehearsal_ids'][-1]
                    self.assertEqual(saved(), before)
                    basket = {'csrf': csrf, 'order_id': order_id, 'scenario': 'fixed-v1',
                              'SYNTHETIC-CHEONGJU': '2', 'SYNTHETIC-TAKJU': '1'}
                    confirm = {'csrf': csrf, 'order_id': order_id}
                    self.assertEqual(self.post(base + '/confirm', data=confirm).status_code, 403)
                    self.assertEqual(self.client.post(base + '/review', data=basket,
                                                     base_url='https://localhost').status_code, 401)
                    self.assertEqual(self.client.post(base + '/confirm', data=confirm,
                                                     base_url='https://localhost').status_code, 401)
                    with patch.dict(os.environ, {'SYNTHETIC_ORDER_TEST_ENABLED': 'false'}):
                        self.assertEqual(self.post(base + '/review', data=basket).status_code, 404)
                        self.assertEqual(self.post(base + '/confirm', data=confirm).status_code, 404)
                    for invalid, status in (
                            ({**basket, 'csrf': 'wrong'}, 403),
                            ({**basket, 'order_id': 'TEST-' + 'f' * 32}, 403),
                            ({**basket, 'SYNTHETIC-CHEONGJU': '11'}, 400),
                            ({**basket, 'extra': 'x'}, 400),
                            (MultiDict(list(basket.items()) + [('SYNTHETIC-TAKJU', '1')]), 400)):
                        self.assertEqual(self.post(base + '/review', data=invalid).status_code, status)
                    self.assertEqual(saved(), before)
                    review = self.post(base + '/review', data=basket)
                    self.assertEqual(review.status_code, 200)
                    self.assertIn('가상 청주 · 2개', review.get_data(as_text=True))
                    self.assertEqual(self.post(base + '/review', data=basket).status_code, 200)
                    self.assertEqual(self.post(base + '/review', data={**basket,
                                     'SYNTHETIC-CHEONGJU': '3'}).status_code, 409)
                    self.assertEqual(saved(), before)
                    receipt = f'{base}/{order_id}/receipt'
                    self.assertEqual(self.get(receipt).status_code, 404)
                    self.assertEqual(self.post(base + '/confirm', data={**confirm,
                                      'SYNTHETIC-CHEONGJU': '9'}).status_code, 400)
                    self.assertEqual(self.post(base + '/confirm', data={**confirm,
                                      'csrf': 'wrong'}).status_code, 403)
                    self.assertEqual(saved(), before)
                    result = self.post(base + '/confirm', data=confirm)
                    self.assertEqual((result.status_code, result.location), (303, receipt))
                    persisted = saved()
                    self.assertNotEqual(persisted, before)
                    self.assertEqual(self.post(base + '/confirm', data=confirm).status_code, 303)
                    self.assertEqual(saved(), persisted)
                    display = self.get(receipt)
                    self.assertEqual(display.status_code, 200)
                    self.assertIn('가상 탁주 · 1개', display.get_data(as_text=True))
                    self.assertIn('실제 주문·결제·발송이 아닙니다', display.get_data(as_text=True))
                    self.assertIn(f'/admin/orders/test/{order_id}/history?view=html',
                                  display.get_data(as_text=True))
                    self.assertEqual(display.headers['Cache-Control'], 'no-store')
                    self.assertEqual(saved(), persisted)
                    self.assertEqual(self.client.get(receipt).status_code, 401)
                    foreign = app.test_client()
                    self.assertEqual(foreign.get(receipt, base_url='https://localhost',
                                                 auth=self.auth).status_code, 404)
                    with patch.dict(os.environ, {'SYNTHETIC_ORDER_TEST_ENABLED': 'false'}):
                        self.assertEqual(self.get(receipt).status_code, 404)

                    ids = [order_id]
                    for _ in range(test_orders.REHEARSAL_LIMIT):
                        self.assertEqual(self.get(base).status_code, 200)
                        with self.client.session_transaction() as sess:
                            ids.append(sess['test_rehearsal_ids'][-1])
                    with self.client.session_transaction() as sess:
                        self.assertEqual(sess['test_rehearsal_ids'], ids[1:])
                        self.assertEqual(set(sess['test_rehearsal_drafts']), set(ids[1:]))
                    self.assertEqual(self.get(receipt).status_code, 404)
                    self.assertEqual(self.post(base + '/confirm', data=confirm).status_code, 403)
                    self.assertEqual(saved(), persisted)

                def test_synthetic_scenario_persists_atomically_and_replays_exact_payload(self):
                    path = '/admin/orders/test?view=html'
                    self.assertEqual(self.client.get(path).status_code, 401)
                    with patch.dict(os.environ, {'SYNTHETIC_ORDER_TEST_ENABLED': 'false'}):
                        self.assertEqual(self.get(path).status_code, 404)
                    form = self.get(path)
                    self.assertIn('SYNTHETIC-CHEONGJU', form.get_data(as_text=True))
                    with self.client.session_transaction() as sess:
                        csrf = sess['test_order_csrf']
                        scenario_id = sess['test_scenario_order_id']
                        legacy_id = sess['test_order_id']
                    data = {'csrf': csrf, 'order_id': scenario_id, 'scenario': 'fixed-v1',
                            'SYNTHETIC-CHEONGJU': '2', 'SYNTHETIC-TAKJU': '1'}
                    self.assertEqual(self.post(path, data=dict(data, csrf='bad')).status_code, 403)
                    for bad in (dict(data, **{'SYNTHETIC-TAKJU': '11'}),
                                dict(data, **{'SYNTHETIC-TAKJU': '-1'}),
                                dict(data, **{'SYNTHETIC-TAKJU': '0', 'SYNTHETIC-CHEONGJU': '0'}),
                                dict(data, extra='bad'),
                                {key: value for key, value in data.items() if key != 'SYNTHETIC-TAKJU'}):
                        self.assertEqual(self.post(path, data=bad).status_code, 400)
                    duplicate = MultiDict(list(data.items()) + [('SYNTHETIC-CHEONGJU', '2')])
                    self.assertEqual(self.post(path, data=duplicate).status_code, 400)
                    self.assertEqual(self.post(path, data=MultiDict(list(data.items()) +
                                    [('csrf', csrf)])).status_code, 400)
                    with cafe24.database() as conn:
                        self.assertEqual(conn.execute('SELECT count(*) FROM ourisul_test_orders').fetchone()[0], 0)
                    created = self.post(path, data=data)
                    self.assertEqual((created.status_code, created.headers['Location']),
                                     (303, f'/admin/orders/test/{scenario_id}/history?view=html'))
                    self.assertEqual(self.post(path, data=data).status_code, 303)
                    tables = ('ourisul_test_orders', 'ourisul_test_order_item',
                              'ourisul_order_notice', 'ourisul_test_order_state',
                              'ourisul_test_order_event')
                    with cafe24.database() as conn:
                        before_conflict = {table: conn.execute(
                            f'SELECT * FROM {table} ORDER BY 1, 2').fetchall()
                            for table in tables}
                    changed = dict(data, **{'SYNTHETIC-CHEONGJU': '3'})
                    conflict = self.post(path, data=changed)
                    self.assertEqual((conflict.status_code, conflict.mimetype), (409, 'text/html'))
                    self.assertIn(f'/admin/orders/test/{scenario_id}/history?view=html',
                                  conflict.get_data(as_text=True))
                    self.assertIn('href="/admin/orders/test?view=html"',
                                  conflict.get_data(as_text=True))
                    self.assertIn('새 합성 주문 시작하기', conflict.get_data(as_text=True))
                    default_conflict = self.post('/admin/orders/test', data=changed)
                    self.assertEqual((default_conflict.status_code, default_conflict.mimetype),
                                     (409, 'text/html'))
                    self.assertNotIn('새 합성 주문 시작하기',
                                     default_conflict.get_data(as_text=True))
                    with cafe24.database() as conn:
                        after_conflict = {table: conn.execute(
                            f'SELECT * FROM {table} ORDER BY 1, 2').fetchall()
                            for table in tables}
                    self.assertEqual(after_conflict, before_conflict)
                    with cafe24.database() as conn:
                        self.assertEqual(conn.execute('SELECT order_id, status, item_count FROM ourisul_test_orders').fetchall(),
                                         [(scenario_id, 'TEST_CREATED', 0)])
                        self.assertEqual(conn.execute('SELECT item_id, item_label, quantity FROM ourisul_test_order_item '
                                                      'ORDER BY item_id').fetchall(),
                                         [('SYNTHETIC-CHEONGJU', '가상 청주', 2),
                                          ('SYNTHETIC-TAKJU', '가상 탁주', 1)])
                        self.assertEqual(conn.execute('SELECT order_id, item_count FROM ourisul_order_notice').fetchall(),
                                         [(scenario_id, 0)])
                        self.assertEqual(conn.execute('SELECT count(*) FROM ourisul_test_order_state').fetchone()[0], 1)
                        self.assertEqual(conn.execute('SELECT count(*) FROM ourisul_test_order_event').fetchone()[0], 1)
                    history = self.get(created.headers['Location'])
                    self.assertIn('가상 청주', history.get_data(as_text=True))
                    self.assertIn('가상 탁주', history.get_data(as_text=True))
                    self.assertNotIn('가상 청주', self.get('/').get_data(as_text=True))
                    with cafe24.database() as conn:
                        before = {table: conn.execute(f'SELECT * FROM {table} ORDER BY 1, 2').fetchall()
                                  for table in tables}
                    self.get(created.headers['Location'])
                    with cafe24.database() as conn:
                        after = {table: conn.execute(f'SELECT * FROM {table} ORDER BY 1, 2').fetchall()
                                 for table in tables}
                        self.assertEqual(after, before)
                        self.assertEqual(conn.execute('SELECT count(*) FROM ourisul_test_orders '
                                                      'WHERE order_id=%s', (legacy_id,)).fetchone()[0], 0)
                    history_path = f'/admin/orders/test/{scenario_id}/history'
                    for expected, version in (('TEST_PREPARED', 0), ('TEST_SHIPPED', 1),
                                              ('TEST_DELIVERED', 2)):
                        advanced = self.post(history_path, data={'csrf': csrf, 'status': expected,
                            'expected_version': str(version), 'request_key': f'scenario-{version}'})
                        self.assertEqual(advanced.status_code, 200)
                    self.assertIn('TEST_DELIVERED', self.get(created.headers['Location']).get_data(as_text=True))
                    with cafe24.database() as conn:
                        self.assertEqual(conn.execute('SELECT item_id, quantity FROM ourisul_test_order_item '
                                                      'ORDER BY item_id').fetchall(),
                                         [('SYNTHETIC-CHEONGJU', 2), ('SYNTHETIC-TAKJU', 1)])
                        self.assertEqual(conn.execute('SELECT count(*) FROM ourisul_test_order_event '
                                                      'WHERE order_id=%s', (scenario_id,)).fetchone()[0], 4)
                        self.assertEqual(conn.execute('SELECT item_count FROM ourisul_order_notice '
                                                      'WHERE order_id=%s', (scenario_id,)).fetchone()[0], 0)
                        conn.execute('DROP TABLE ourisul_test_order_item')
                    self.assertEqual(self.get(created.headers['Location']).status_code, 503)

                def test_synthetic_basket_rehearsal_from_form_through_retry_and_snapshot(self):
                    form_path = '/admin/orders/test?view=html'
                    self.assertEqual(self.get(form_path).status_code, 200)
                    with self.client.session_transaction() as sess:
                        csrf = sess['test_order_csrf']
                        order_id = sess['test_scenario_order_id']
                    basket = {'csrf': csrf, 'order_id': order_id, 'scenario': 'fixed-v1',
                              'SYNTHETIC-CHEONGJU': '2', 'SYNTHETIC-TAKJU': '1'}
                    created = self.post(form_path, data=basket)
                    self.assertEqual(created.status_code, 303)
                    snapshot_path = f'/admin/orders/test/{order_id}/history?view=snapshot'
                    initial = self.get(snapshot_path).json
                    self.assertEqual((initial['is_test'], initial['source'], initial['status'],
                                      initial['version']), (True, 'synthetic', 'TEST_CREATED', 0))
                    self.assertEqual(initial['items'], [
                        {'id': 'SYNTHETIC-CHEONGJU', 'name': '가상 청주', 'quantity': 2},
                        {'id': 'SYNTHETIC-TAKJU', 'name': '가상 탁주', 'quantity': 1}])
                    self.assertEqual((initial['notice']['state'], initial['notice']['attempts']),
                                     ('ready', 0))
                    real_id = 'VERIFY-OTHER-ORDER'
                    self.assertEqual(notices.record_orders('cafe24', [
                        {'order_id': real_id, 'status': 'N10',
                         'ordered_at': '2026-10-15T00:00:00Z', 'item_count': 1}],
                        cafe24.database), 1)
                    with cafe24.database() as conn:
                        real_before = conn.execute('''SELECT * FROM ourisul_order_notice
                            WHERE source='cafe24' AND order_id=%s''', (real_id,)).fetchone()
                    webhook = 'https://hooks.slack.com/services/TEST/TEST/SYNTHETIC'
                    fake_post = Mock(side_effect=[requests.Timeout(), response()])
                    actual_sender = notices.deliver_synthetic_pending

                    def fake_network_sender(connect, selected_id):
                        return actual_sender(connect, selected_id, post=fake_post)

                    def run_sender():
                        # CLI checks a distinct production identity; the Flask app
                        # keeps its isolated DATABASE_URL outside this narrow call.
                        with patch.dict(os.environ, {
                                'DATABASE_URL': 'postgresql://dummy@localhost/production'}):
                            return send_synthetic_orders.run(['--send', order_id])

                    with patch.dict(os.environ, {
                            'VERIFY_DATABASE_URL': isolated_dsn,
                            'SYNTHETIC_ORDER_NOTIFICATIONS_ENABLED': 'true',
                            'SLACK_TEST_ORDER_WEBHOOK_URL': webhook,
                            'SLACK_ORDER_WEBHOOK_URL':
                                'https://hooks.slack.com/services/TEST/TEST/REAL'}), \
                            patch.object(notices, 'deliver_synthetic_pending',
                                         side_effect=fake_network_sender):
                        self.assertEqual(run_sender(),
                                         {'delivered': 0, 'failed': 1})
                        deferred = self.get(snapshot_path).json
                        self.assertEqual((deferred['notice']['state'],
                                          deferred['notice']['attempts']), ('deferred', 1))
                        self.assertIn('retry_at', deferred['notice'])
                        self.assertEqual(run_sender(),
                                         {'delivered': 0, 'failed': 0})
                        self.assertEqual(fake_post.call_count, 1)
                        with cafe24.database() as conn:
                            conn.execute('''UPDATE ourisul_order_notice
                                SET next_attempt_at=now()-interval '1 second'
                                WHERE source='synthetic' AND order_id=%s''', (order_id,))
                        self.assertEqual(run_sender(),
                                         {'delivered': 1, 'failed': 0})
                        self.assertEqual(run_sender(),
                                         {'delivered': 0, 'failed': 0})
                    self.assertEqual(fake_post.call_count, 2)
                    for call in fake_post.call_args_list:
                        self.assertEqual(call.args, (webhook,))
                        self.assertEqual(call.kwargs['json'],
                                         {'text': f'[테스트·미운영] {order_id}'})
                    delivered = self.get(snapshot_path).json
                    self.assertEqual((delivered['notice']['state'],
                                      delivered['notice']['attempts']), ('delivered', 2))
                    self.assertIn('delivered_at', delivered['notice'])
                    tables = ('ourisul_test_orders', 'ourisul_test_order_item',
                              'ourisul_order_notice', 'ourisul_test_order_state',
                              'ourisul_test_order_event')
                    with cafe24.database() as conn:
                        before_replay = {table: conn.execute(
                            f'SELECT * FROM {table} ORDER BY 1, 2').fetchall()
                            for table in tables}
                    self.assertEqual(self.post(form_path, data=basket).status_code, 303)
                    self.assertEqual(self.get(snapshot_path).json['notice'], delivered['notice'])
                    with cafe24.database() as conn:
                        after_replay = {table: conn.execute(
                            f'SELECT * FROM {table} ORDER BY 1, 2').fetchall()
                            for table in tables}
                    self.assertEqual(after_replay, before_replay)
                    history_path = f'/admin/orders/test/{order_id}/history'
                    for version, status in enumerate(('TEST_PREPARED', 'TEST_SHIPPED',
                                                      'TEST_DELIVERED')):
                        payload = {'csrf': csrf, 'status': status,
                                   'expected_version': str(version),
                                   'request_key': f'e2e-{version}'}
                        advanced = self.post(history_path, data=payload)
                        self.assertEqual(advanced.json,
                                         {'status': status, 'version': version + 1,
                                          'created': True})
                        self.assertEqual(self.post(history_path, data=payload).json,
                                         {'status': status, 'version': version + 1,
                                          'created': False})
                    with cafe24.database() as conn:
                        real_after = conn.execute('''SELECT * FROM ourisul_order_notice
                            WHERE source='cafe24' AND order_id=%s''', (real_id,)).fetchone()
                        before_read = {table: conn.execute(
                            f'SELECT * FROM {table} ORDER BY 1, 2').fetchall()
                            for table in tables}
                    self.assertEqual(real_after, real_before)
                    final = self.get(snapshot_path).json
                    self.assertEqual((final['status'], final['version']), ('TEST_DELIVERED', 3))
                    self.assertEqual([event['to_status'] for event in final['events']],
                                     ['TEST_CREATED', 'TEST_PREPARED', 'TEST_SHIPPED',
                                      'TEST_DELIVERED'])
                    self.assertEqual(final['items'], initial['items'])
                    self.assertEqual(final['notice'], delivered['notice'])
                    with cafe24.database() as conn:
                        after_read = {table: conn.execute(
                            f'SELECT * FROM {table} ORDER BY 1, 2').fetchall()
                            for table in tables}
                    self.assertEqual(after_read, before_read)

                def test_synthetic_scenario_item_failure_rolls_back_everything(self):
                    self.assertEqual(self.get('/admin/orders/test?view=html').status_code, 200)
                    with self.client.session_transaction() as sess:
                        csrf, order_id = sess['test_order_csrf'], sess['test_scenario_order_id']
                    with cafe24.database() as conn:
                        conn.execute("ALTER TABLE ourisul_test_order_item ADD CONSTRAINT reject_scenario "
                                     "CHECK (item_id <> 'SYNTHETIC-CHEONGJU')")
                    try:
                        direct_id = 'TEST-' + secrets.token_hex(16)
                        with self.assertRaises(psycopg.errors.CheckViolation):
                            test_orders.record_test_order(
                                direct_id, scenario={'SYNTHETIC-CHEONGJU': 1})
                        failed = self.post('/admin/orders/test?view=html', data={
                            'csrf': csrf, 'order_id': order_id, 'scenario': 'fixed-v1',
                            'SYNTHETIC-CHEONGJU': '1', 'SYNTHETIC-TAKJU': '0'})
                        self.assertEqual(failed.status_code, 502)
                        with cafe24.database() as conn:
                            for table in ('ourisul_test_orders', 'ourisul_test_order_item',
                                          'ourisul_order_notice', 'ourisul_test_order_state',
                                          'ourisul_test_order_event'):
                                for checked_id in (direct_id, order_id):
                                    self.assertEqual(conn.execute(f'SELECT count(*) FROM {table} '
                                                                  'WHERE order_id=%s', (checked_id,)).fetchone()[0], 0)
                    finally:
                        with cafe24.database() as conn:
                            conn.execute('ALTER TABLE ourisul_test_order_item DROP CONSTRAINT reject_scenario')

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

                def test_synthetic_tracking_html_admin_flow_is_read_only(self):
                    form_path = '/admin/orders/test?view=html'
                    self.assertEqual(self.client.get(form_path).status_code, 401)
                    self.assertEqual(self.client.get('/admin/orders/test/lookup').status_code, 401)
                    with patch.dict(os.environ, {'SYNTHETIC_ORDER_TEST_ENABLED': 'false'}):
                        self.assertEqual(self.get(form_path).status_code, 404)
                        self.assertEqual(self.get('/admin/orders/test/lookup').status_code, 404)
                    form = self.get(form_path)
                    self.assertEqual(form.status_code, 200)
                    self.assertIn('합성 주문 배송 조회', form.get_data(as_text=True))
                    self.assertIn('name="viewport"', form.get_data(as_text=True))
                    self.assertIn('max-width:720px', form.get_data(as_text=True))
                    with self.client.session_transaction() as sess:
                        csrf, order_id = sess['test_order_csrf'], sess['test_order_id']
                    self.assertEqual(self.post(form_path, data={'csrf': 'bad'}).status_code, 403)
                    created = self.post(form_path, data={'csrf': csrf, 'order_id': order_id})
                    self.assertEqual(created.status_code, 303)
                    self.assertEqual(created.headers['Location'],
                                     f'/admin/orders/test/{order_id}/history?view=html')
                    path = created.headers['Location']
                    self.assertEqual(self.client.get(path).status_code, 401)
                    with patch.dict(os.environ, {'SYNTHETIC_ORDER_TEST_ENABLED': 'false'}):
                        self.assertEqual(self.get(path).status_code, 404)
                    with cafe24.database() as conn:
                        before = tuple(conn.execute('SELECT count(*) FROM ' + table).fetchone()[0]
                                       for table in ('ourisul_test_orders', 'ourisul_test_order_state',
                                                     'ourisul_test_order_event', 'ourisul_order_notice'))
                    page = self.get(path)
                    self.assertEqual(page.status_code, 200)
                    self.assertIn(order_id, page.get_data(as_text=True))
                    self.assertIn('TEST_CREATED', page.get_data(as_text=True))
                    self.assertIn('<dt>상태</dt><dd><strong>ready</strong> (발송 대기)</dd>',
                                  page.get_data(as_text=True))
                    self.assertIn('name="viewport"', page.get_data(as_text=True))
                    self.assertIn('noindex,nofollow', page.get_data(as_text=True))
                    self.assertEqual(page.headers['Cache-Control'], 'no-store')
                    self.assertEqual(page.headers['Referrer-Policy'], 'no-referrer')
                    lookup = self.get('/admin/orders/test/lookup', query_string={'order_id': order_id})
                    self.assertEqual(lookup.status_code, 303)
                    self.assertEqual(lookup.headers['Location'], path)
                    self.assertEqual(self.get(f'/admin/orders/test/{order_id}/history').json['status'],
                                     'TEST_CREATED')
                    with cafe24.database() as conn:
                        after = tuple(conn.execute('SELECT count(*) FROM ' + table).fetchone()[0]
                                      for table in ('ourisul_test_orders', 'ourisul_test_order_state',
                                                    'ourisul_test_order_event', 'ourisul_order_notice'))
                    self.assertEqual(after, before)
                    self.assertEqual(self.post(f'/admin/orders/test/{order_id}/history', data={
                        'csrf': csrf, 'status': 'TEST_PREPARED', 'expected_version': '0',
                        'request_key': 'tracking-timeline'}).status_code, 200)
                    timeline = self.get(path).get_data(as_text=True)
                    self.assertLess(timeline.index('<li>TEST_CREATED<small>'),
                                    timeline.index('<li>TEST_PREPARED<small>'))
                    self.assertIn('현재 상태: <strong>TEST_PREPARED</strong>', timeline)
                    with cafe24.database() as conn:
                        before_recovery = tuple(conn.execute('SELECT count(*) FROM ' + table).fetchone()[0]
                                                for table in ('ourisul_test_orders', 'ourisul_test_order_item',
                                                              'ourisul_test_order_state', 'ourisul_test_order_event',
                                                              'ourisul_order_notice'))
                    unknown_id = 'TEST-' + '0' * 32
                    for query in ({'order_id': 'invalid'}, {}):
                        recovery = self.get('/admin/orders/test/lookup', query_string=query)
                        self.assertEqual(recovery.status_code, 404)
                        self.assertIn('합성 주문을 찾을 수 없습니다', recovery.get_data(as_text=True))
                        self.assertIn('href="/admin/orders/test?view=html"', recovery.get_data(as_text=True))
                        self.assertIn('noindex,nofollow', recovery.get_data(as_text=True))
                        self.assertEqual(recovery.headers['Cache-Control'], 'no-store')
                        self.assertEqual(recovery.headers['Referrer-Policy'], 'no-referrer')
                    self.assertEqual(self.get('/admin/orders/test/lookup',
                                              query_string={'order_id': unknown_id}).status_code, 303)
                    recovery = self.get(f'/admin/orders/test/{unknown_id}/history?view=html')
                    self.assertEqual(recovery.status_code, 404)
                    self.assertIn('합성 주문을 찾을 수 없습니다', recovery.get_data(as_text=True))
                    self.assertIn('href="/admin/orders/test?view=html"', recovery.get_data(as_text=True))
                    self.assertEqual(self.get(f'/admin/orders/test/{unknown_id}/history').status_code, 404)
                    with cafe24.database() as conn:
                        after_recovery = tuple(conn.execute('SELECT count(*) FROM ' + table).fetchone()[0]
                                               for table in ('ourisul_test_orders', 'ourisul_test_order_item',
                                                             'ourisul_test_order_state', 'ourisul_test_order_event',
                                                             'ourisul_order_notice'))
                    self.assertEqual(after_recovery, before_recovery)
                    legacy_id = 'TEST-' + secrets.token_hex(16)
                    with cafe24.database() as conn:
                        conn.execute("INSERT INTO ourisul_test_orders (order_id, status, item_count) "
                                     "VALUES (%s, 'TEST_CREATED', 0)", (legacy_id,))
                    self.assertEqual(self.get(f'/admin/orders/test/{legacy_id}/history?view=html').status_code,
                                     404)
                    with cafe24.database() as conn:
                        self.assertEqual(conn.execute('SELECT count(*) FROM ourisul_test_order_state '
                                                      'WHERE order_id=%s', (legacy_id,)).fetchone()[0], 0)
                    with patch.object(fulfillment, 'history_in_connection', return_value={
                            'order_id': order_id, 'status': '<script>alert(1)</script>',
                            'version': 0, 'events': [{'version': 0, 'from_status': None,
                                                       'to_status': '<b>unsafe</b>',
                                                       'created_at': '<img src=x>'}]}):
                        escaped = self.get(path).get_data(as_text=True)
                    self.assertNotIn('<script>', escaped)
                    self.assertNotIn('<b>unsafe</b>', escaped)
                    self.assertNotIn('<img src=x>', escaped)
                    self.assertIn('&lt;b&gt;unsafe&lt;/b&gt;', escaped)
                    with patch.object(notices, 'synthetic_notice_status_in_connection', return_value={
                            'state': '<img src=x onerror=alert(1)>'}):
                        escaped_notice = self.get(path).get_data(as_text=True)
                    self.assertNotIn('<img src=x onerror=alert(1)>', escaped_notice)
                    self.assertIn('&lt;img src=x onerror=alert(1)&gt;', escaped_notice)
                    self.assertIn('알 수 없는 상태', escaped_notice)
                    self.assertNotIn('발송 작업 시도 횟수', escaped_notice)
                    with patch.object(notices, 'synthetic_notice_status_in_connection', return_value={
                            'state': 'deferred', 'attempts': '<b>2</b>',
                            'retry_at': '2026-10-12T01:00:00+00:00" onmouseover="alert(1)'}):
                        escaped_metadata = self.get(path).get_data(as_text=True)
                    self.assertNotIn('<b>2</b>', escaped_metadata)
                    self.assertIn('&lt;b&gt;2&lt;/b&gt;', escaped_metadata)
                    self.assertNotIn('onmouseover="alert(1)"', escaped_metadata)
                    self.assertIn('onmouseover=&#34;alert(1)', escaped_metadata)
                    with patch.object(notices, 'synthetic_notice_status_in_connection', return_value={
                            'state': 'deferred'}):
                        incomplete = self.get(path).get_data(as_text=True)
                    self.assertNotIn('재시도 예정 시각', incomplete)

                def test_synthetic_tracking_html_notice_states_are_read_only(self):
                    ids = {state: 'TEST-' + secrets.token_hex(16) for state in
                           ('ready', 'deferred', 'leased', 'delivered', 'missing')}
                    for order_id in ids.values():
                        self.assertTrue(test_orders.record_test_order(order_id))
                    with cafe24.database() as conn:
                        conn.execute('''UPDATE ourisul_order_notice SET
                            next_attempt_at = now() + interval '5 minutes', attempts = 2
                            WHERE source = 'synthetic' AND order_id = %s''', (ids['deferred'],))
                        conn.execute('''UPDATE ourisul_order_notice SET
                            lease_until = now() + interval '5 minutes', attempts = 3
                            WHERE source = 'synthetic' AND order_id = %s''', (ids['leased'],))
                        conn.execute('''UPDATE ourisul_order_notice SET delivered_at = now(), attempts = 4
                            WHERE source = 'synthetic' AND order_id = %s''', (ids['delivered'],))
                        conn.execute('''DELETE FROM ourisul_order_notice
                            WHERE source = 'synthetic' AND order_id = %s''', (ids['missing'],))
                        before = conn.execute('''SELECT source, order_id, attempts, delivered_at,
                            next_attempt_at, lease_until FROM ourisul_order_notice
                            ORDER BY source, order_id''').fetchall()
                    labels = {'ready': '발송 대기', 'deferred': '재시도 대기',
                              'leased': '발송 작업 점유 중', 'delivered': '전송 기록 완료',
                              'missing': '알림 기록 없음'}
                    times = {'deferred': ('재시도 예정 시각', 'retry_at'),
                             'leased': ('발송 점유 만료 시각', 'lease_until'),
                             'delivered': ('전송 기록 시각', 'delivered_at')}
                    with patch.object(requests.sessions.Session, 'request',
                                      side_effect=AssertionError('Timeline must not use HTTP')) as http_request:
                        for state, order_id in ids.items():
                            path = f'/admin/orders/test/{order_id}/history'
                            page = self.get(path + '?view=html')
                            self.assertEqual(page.status_code, 200)
                            html = page.get_data(as_text=True)
                            self.assertIn(f'<dt>상태</dt><dd><strong>{state}</strong> '
                                          f'({labels[state]})</dd>', html)
                            self.assertIn('name="viewport"', html)
                            self.assertIn('focus-visible', html)
                            if state == 'missing':
                                self.assertNotIn('발송 작업 시도 횟수', html)
                            else:
                                expected = {'ready': 0, 'deferred': 2, 'leased': 3,
                                            'delivered': 4}[state]
                                self.assertIn(f'<dt>발송 작업 시도 횟수</dt><dd>{expected}회 (작업 점유 포함)</dd>', html)
                            inspected = notices.synthetic_notice_status(cafe24.database, order_id)
                            for candidate, (label, key) in times.items():
                                if state == candidate:
                                    self.assertIn(f'<dt>{label}</dt><dd><time datetime="'
                                                  f'{inspected[key]}">{inspected[key]}</time></dd>', html)
                                else:
                                    self.assertNotIn(label, html)
                            self.assertNotIn('notice', self.get(path).json)
                        http_request.assert_not_called()
                    with cafe24.database() as conn:
                        after = conn.execute('''SELECT source, order_id, attempts, delivered_at,
                            next_attempt_at, lease_until FROM ourisul_order_notice
                            ORDER BY source, order_id''').fetchall()
                    self.assertEqual(after, before)

                def test_synthetic_tracking_html_uninitialized_notice_table_is_unavailable(self):
                    order_id = 'TEST-' + secrets.token_hex(16)
                    self.assertTrue(test_orders.record_test_order(order_id))
                    path = f'/admin/orders/test/{order_id}/history?view=html'
                    with patch.object(notices, 'synthetic_notice_status_in_connection', side_effect=ValueError(
                            'Synthetic order tables are not initialized')):
                        self.assertEqual(self.get(path).status_code, 503)
                    self.assertEqual(self.get(path).status_code, 200)

                def test_synthetic_tracking_html_transition_controls(self):
                    self.assertEqual(self.get('/admin/orders/test?view=html').status_code, 200)
                    with self.client.session_transaction() as sess:
                        order_id, csrf = sess['test_order_id'], sess['test_order_csrf']
                    self.assertTrue(test_orders.record_test_order(order_id))
                    path = f'/admin/orders/test/{order_id}/history?view=html'
                    page = self.get(path)
                    self.assertEqual(page.status_code, 200)

                    def form_data(response):
                        html = response.get_data(as_text=True)
                        values = dict(re.findall(r'<input type="hidden" name="([^"]+)" value="([^"]*)">',
                                                 html))
                        self.assertEqual(set(values), {'csrf', 'status', 'expected_version', 'request_key'})
                        return values

                    initial = form_data(page)
                    self.assertNotEqual(initial['request_key'], form_data(self.get(path))['request_key'])
                    self.assertEqual(initial['csrf'], csrf)
                    self.assertEqual((initial['status'], initial['expected_version']),
                                     ('TEST_PREPARED', '0'))
                    self.assertIn('합성 준비 완료', page.get_data(as_text=True))
                    self.assertEqual(self.post(path, data=dict(initial, csrf='bad')).status_code, 403)
                    invalid = self.post(path, data=dict(initial, status='TEST_DELIVERED'))
                    self.assertEqual(invalid.status_code, 409)
                    self.assertIn('최신 상태 보기', invalid.get_data(as_text=True))
                    self.assertEqual(fulfillment.history(order_id)['version'], 0)
                    prepared = self.post(path, data=initial)
                    self.assertEqual((prepared.status_code, prepared.headers['Location']), (303, path))
                    self.assertEqual(self.post(path, data=initial).status_code, 303)
                    stale_form = form_data(self.get(path))
                    self.assertEqual((stale_form['status'], stale_form['expected_version']),
                                     ('TEST_SHIPPED', '1'))
                    self.assertIn('합성 발송 처리', self.get(path).get_data(as_text=True))
                    stale = self.post(path, data=dict(initial, request_key='stale-form'))
                    self.assertEqual(stale.status_code, 409)
                    self.assertIn(path, stale.get_data(as_text=True))
                    self.assertEqual(self.post(path, data=stale_form).status_code, 303)
                    shipped_form = form_data(self.get(path))
                    self.assertEqual((shipped_form['status'], shipped_form['expected_version']),
                                     ('TEST_DELIVERED', '2'))
                    self.assertIn('합성 배송 완료', self.get(path).get_data(as_text=True))
                    self.assertEqual(self.post(path, data=shipped_form).status_code, 303)
                    final = self.get(path).get_data(as_text=True)
                    self.assertIn('현재 상태: <strong>TEST_DELIVERED</strong>', final)
                    self.assertNotIn('name="request_key"', final)
                    self.assertIn('합성 배송 시연이 완료되었습니다.', final)
                    self.assertEqual(self.get(path.split('?')[0]).json['version'], 3)
                    with cafe24.database() as conn:
                        self.assertEqual(conn.execute('SELECT count(*) FROM ourisul_test_order_event '
                                                      'WHERE order_id=%s', (order_id,)).fetchone()[0], 4)
                    with self.client.session_transaction() as sess:
                        sess.pop('test_order_csrf')
                    direct_id = 'TEST-' + secrets.token_hex(16)
                    self.assertTrue(test_orders.record_test_order(direct_id))
                    direct_path = f'/admin/orders/test/{direct_id}/history?view=html'
                    direct = self.get(direct_path)
                    self.assertEqual(direct.status_code, 200)
                    with self.client.session_transaction() as sess:
                        self.assertEqual(form_data(direct)['csrf'], sess['test_order_csrf'])
                    self.assertEqual(self.post(direct_path, data=form_data(direct)).status_code, 303)

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

                def test_synthetic_order_snapshot_is_backed_private_and_read_only(self):
                    order_id = 'TEST-' + secrets.token_hex(16)
                    path = f'/admin/orders/test/{order_id}/history'
                    self.assertEqual(self.client.get(path + '?view=snapshot').status_code, 401)
                    with patch.dict(os.environ, {'SYNTHETIC_ORDER_TEST_ENABLED': 'false'}):
                        self.assertEqual(self.get(path + '?view=snapshot').status_code, 404)
                    self.assertEqual(self.get(path + '?view=snapshot').status_code, 404)
                    self.assertEqual(self.get('/admin/orders/test/bad/history?view=snapshot').status_code, 404)
                    self.assertTrue(test_orders.record_test_order(order_id, scenario={
                        'SYNTHETIC-CHEONGJU': 2, 'SYNTHETIC-TAKJU': 1}))
                    tables = ('ourisul_test_orders', 'ourisul_test_order_item',
                              'ourisul_order_notice', 'ourisul_test_order_state',
                              'ourisul_test_order_event')
                    with cafe24.database() as conn:
                        before = {table: conn.execute(f'SELECT * FROM {table} ORDER BY 1, 2').fetchall()
                                  for table in tables}
                    snapshot = self.get(path + '?view=snapshot')
                    self.assertEqual(snapshot.status_code, 200)
                    self.assertEqual(snapshot.headers['Cache-Control'], 'no-store')
                    self.assertEqual(snapshot.headers['Referrer-Policy'], 'no-referrer')
                    data = snapshot.json
                    self.assertEqual((data['order_id'], data['status'], data['version']),
                                     (order_id, 'TEST_CREATED', 0))
                    self.assertEqual((data['is_test'], data['source']), (True, 'synthetic'))
                    self.assertEqual([event['to_status'] for event in data['events']], ['TEST_CREATED'])
                    self.assertEqual(data['items'], [
                        {'id': 'SYNTHETIC-CHEONGJU', 'name': '가상 청주', 'quantity': 2},
                        {'id': 'SYNTHETIC-TAKJU', 'name': '가상 탁주', 'quantity': 1}])
                    self.assertEqual(data['notice']['state'], 'ready')
                    self.assertEqual(data['notice']['attempts'], 0)
                    self.assertEqual(set(self.get(path).json), {'order_id', 'status', 'version', 'events'})
                    with cafe24.database() as conn:
                        after = {table: conn.execute(f'SELECT * FROM {table} ORDER BY 1, 2').fetchall()
                                 for table in tables}
                    self.assertEqual(after, before)
                    with cafe24.database() as conn:
                        conn.execute('DROP TABLE ourisul_test_order_item')
                    self.assertEqual(self.get(path + '?view=snapshot').status_code, 503)
                    with cafe24.database() as conn:
                        test_orders.schema(conn)
                        conn.execute('DROP TABLE ourisul_order_notice')
                    self.assertEqual(self.get(path + '?view=snapshot').status_code, 503)
                    with cafe24.database() as conn:
                        notices.schema(conn)
                        conn.execute('DROP TABLE ourisul_test_order_state')
                    self.assertEqual(self.get(path + '?view=snapshot').status_code, 503)

                def test_synthetic_order_snapshot_uses_one_database_snapshot(self):
                    order_id = 'TEST-' + secrets.token_hex(16)
                    self.assertTrue(test_orders.record_test_order(order_id, scenario={
                        'SYNTHETIC-CHEONGJU': 1}))
                    reads = []

                    @contextmanager
                    def interleaved_read():
                        with cafe24.database() as conn:
                            class ReadConnection:
                                def execute(self, query, params=None):
                                    cursor = conn.execute(query, params)
                                    reads.append(query)
                                    if len(reads) == 2:
                                        # First SELECT has taken the reader snapshot.
                                        fulfillment.transition(order_id, 'TEST_PREPARED', 0,
                                                               'during-snapshot')
                                        with cafe24.database() as writer:
                                            writer.execute('''UPDATE ourisul_order_notice
                                                SET delivered_at=now() WHERE source='synthetic'
                                                AND order_id=%s''', (order_id,))
                                            writer.execute('''UPDATE ourisul_test_order_item SET quantity=2
                                                WHERE order_id=%s AND item_id='SYNTHETIC-CHEONGJU' ''',
                                                (order_id,))
                                    return cursor

                            yield ReadConnection()

                    snapshot = test_orders.order_snapshot(order_id, connect=interleaved_read)
                    self.assertEqual((snapshot['status'], snapshot['version']), ('TEST_CREATED', 0))
                    self.assertEqual(snapshot['items'][0]['quantity'], 1)
                    self.assertEqual(snapshot['notice']['state'], 'ready')
                    self.assertEqual(test_orders.order_snapshot(order_id)['status'], 'TEST_PREPARED')
                    self.assertEqual(test_orders.order_snapshot(order_id)['notice']['state'], 'delivered')
                    self.assertEqual(test_orders.order_snapshot(order_id)['items'][0]['quantity'], 2)

                def test_synthetic_html_timeline_uses_one_database_snapshot(self):
                    order_id = 'TEST-' + secrets.token_hex(16)
                    self.assertTrue(test_orders.record_test_order(order_id, scenario={
                        'SYNTHETIC-CHEONGJU': 1}))
                    path = f'/admin/orders/test/{order_id}/history?view=html'
                    original_snapshot = test_orders.order_snapshot
                    reads = []

                    @contextmanager
                    def interleaved_read():
                        with cafe24.database() as conn:
                            class ReadConnection:
                                def execute(self, query, params=None):
                                    cursor = conn.execute(query, params)
                                    reads.append(query)
                                    if len(reads) == 2:
                                        # The history SELECT captured the read-only snapshot.
                                        fulfillment.transition(order_id, 'TEST_PREPARED', 0,
                                                               'during-html')
                                        with cafe24.database() as writer:
                                            writer.execute('''UPDATE ourisul_order_notice
                                                SET delivered_at=now() WHERE source='synthetic'
                                                AND order_id=%s''', (order_id,))
                                            writer.execute('''UPDATE ourisul_test_order_item
                                                SET quantity=2 WHERE order_id=%s
                                                AND item_id='SYNTHETIC-CHEONGJU' ''', (order_id,))
                                    return cursor

                            yield ReadConnection()

                    with patch.object(test_orders, 'order_snapshot',
                                      side_effect=lambda oid: original_snapshot(
                                          oid, connect=interleaved_read)):
                        page = self.get(path)
                    self.assertEqual(page.status_code, 200)
                    self.assertGreaterEqual(len(reads), 4)
                    html = page.get_data(as_text=True)
                    self.assertIn('현재 상태: <strong>TEST_CREATED</strong> (버전 0)', html)
                    self.assertIn('가상 청주 (SYNTHETIC-CHEONGJU) · 1개', html)
                    self.assertIn('<strong>ready</strong>', html)
                    self.assertIn('name="status" value="TEST_PREPARED"', html)
                    self.assertIn('name="expected_version" value="0"', html)
                    self.assertNotIn('<strong>delivered</strong>', html)
                    self.assertIn('같은 조회 시점의 기록', html)
                    latest = self.get(path).get_data(as_text=True)
                    self.assertIn('현재 상태: <strong>TEST_PREPARED</strong> (버전 1)', latest)
                    self.assertIn('가상 청주 (SYNTHETIC-CHEONGJU) · 2개', latest)
                    self.assertIn('<strong>delivered</strong>', latest)

                def test_synthetic_html_snapshot_errors_keep_recovery_and_gate(self):
                    order_id = 'TEST-' + secrets.token_hex(16)
                    path = f'/admin/orders/test/{order_id}/history?view=html'
                    self.assertEqual(self.client.get(path).status_code, 401)
                    with patch.dict(os.environ, {'SYNTHETIC_ORDER_TEST_ENABLED': 'false'}):
                        self.assertEqual(self.get(path).status_code, 404)
                    self.assertEqual(self.get(path).status_code, 404)
                    self.assertIn('합성 주문 다시 조회하기',
                                  self.get(path).get_data(as_text=True))
                    self.assertEqual(self.get('/admin/orders/test/bad/history?view=html').status_code, 404)
                    self.assertTrue(test_orders.record_test_order(order_id))
                    with cafe24.database() as conn:
                        conn.execute('DELETE FROM ourisul_test_order_event WHERE order_id=%s',
                                     (order_id,))
                        conn.execute('DELETE FROM ourisul_test_order_state WHERE order_id=%s',
                                     (order_id,))
                    self.assertEqual(self.get(path).status_code, 404)
                    with cafe24.database() as conn:
                        fulfillment.initialize(conn, order_id)
                        conn.execute('DROP TABLE ourisul_test_order_item')
                    self.assertEqual(self.get(path).status_code, 503)

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
                            notices.deliver_synthetic_pending(cafe24.database, order_id, post=sender)
                        sender.assert_not_called()
                        with patch.dict(os.environ, {'SYNTHETIC_ORDER_NOTIFICATIONS_ENABLED': 'true',
                                                     'SLACK_TEST_ORDER_WEBHOOK_URL': ''}):
                            with self.assertRaisesRegex(ValueError, 'not configured'):
                                notices.deliver_synthetic_pending(cafe24.database, order_id, post=sender)
                        sender.assert_not_called()
                        with patch.dict(os.environ, {'SYNTHETIC_ORDER_NOTIFICATIONS_ENABLED': 'true',
                                                     'STORE_LIVE': 'false'}):
                            with patch.dict(os.environ, {'SLACK_TEST_ORDER_WEBHOOK_URL': real_webhook}):
                                with self.assertRaisesRegex(ValueError, 'must differ'):
                                    notices.deliver_synthetic_pending(cafe24.database, order_id, post=sender)
                            with patch.dict(os.environ, {'SLACK_TEST_ORDER_WEBHOOK_URL':
                                                         real_webhook.replace('hooks.slack.com', 'HOOKS.SLACK.COM')}):
                                with self.assertRaisesRegex(ValueError, 'must differ'):
                                    notices.deliver_synthetic_pending(cafe24.database, order_id, post=sender)
                            sender.assert_not_called()
                            with patch.dict(os.environ, {'VERIFY_DATABASE_URL': isolated_dsn,
                                                         'DATABASE_URL': 'postgresql://dummy@localhost/production'}):
                                self.assertEqual(send_synthetic_orders.run(['--pending']),
                                                 {'ready': 1, 'deferred': 0, 'leased': 0, 'total': 1})
                            failed = Mock(side_effect=requests.Timeout)
                            self.assertEqual(notices.deliver_synthetic_pending(cafe24.database, order_id, post=failed),
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
                            self.assertEqual(notices.deliver_synthetic_pending(cafe24.database, order_id, post=sender),
                                             {'delivered': 1, 'failed': 0})
                            self.assertEqual(sender.call_args.args[0], test_webhook)
                            self.assertEqual(sender.call_args.kwargs['json'],
                                             {'text': f'[테스트·미운영] {order_id}'})
                            self.assertEqual(notices.deliver_synthetic_pending(cafe24.database, order_id, post=sender),
                                             {'delivered': 0, 'failed': 0})
                            self.assertEqual(sender.call_count, 1)
                            with patch.dict(os.environ, {'VERIFY_DATABASE_URL': isolated_dsn,
                                                         'DATABASE_URL': 'postgresql://dummy@localhost/production'}):
                                self.assertEqual(send_synthetic_orders.run(['--pending']),
                                                 {'ready': 0, 'deferred': 0, 'leased': 0, 'total': 0})
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
                                    conn.execute('TRUNCATE ourisul_test_order_item, ourisul_test_order_event, '
                                                 'ourisul_test_order_state, ourisul_test_orders, '
                                                 'ourisul_order_notice')
                                order_id = 'TEST-' + secrets.token_hex(16)
                                self.assertTrue(test_orders.record_test_order(order_id))

                                def reclaim(_url, **_kwargs):
                                    with cafe24.database() as conn:
                                        conn.execute('''UPDATE ourisul_order_notice
                                            SET lease_until = now() - interval '1 second'
                                            WHERE source = 'synthetic' AND order_id = %s''', (order_id,))
                                    newer = notices._claim(cafe24.database, synthetic=True, order_id=order_id)
                                    self.assertEqual(newer[1], order_id)
                                    self.assertEqual(newer[5], 2)
                                    if old_post_succeeds:
                                        return response()
                                    raise requests.Timeout('synthetic stale attempt')

                                result = notices.deliver_synthetic_pending(cafe24.database, order_id,
                                                                          post=reclaim)
                                self.assertEqual(result, {'delivered': 0, 'failed': 0})
                                with cafe24.database() as conn:
                                    row = conn.execute('''SELECT attempts, delivered_at,
                                        lease_until > now(), next_attempt_at <= now()
                                        FROM ourisul_order_notice WHERE source = 'synthetic'
                                        AND order_id = %s''', (order_id,)).fetchone()
                                self.assertEqual(row, (2, None, True, True))

                def test_synthetic_sender_requires_exact_ready_id(self):
                    older = 'TEST-' + secrets.token_hex(16)
                    selected = 'TEST-' + secrets.token_hex(16)
                    orphan = 'TEST-' + secrets.token_hex(16)
                    self.assertTrue(test_orders.record_test_order(older))
                    self.assertTrue(test_orders.record_test_order(selected))
                    for source in ('cafe24', 'coupang'):
                        self.assertEqual(notices.record_orders(source, [{
                            'order_id': 'VERIFY-' + source, 'status': 'N10',
                            'ordered_at': '2026-09-29T00:00:00Z', 'item_count': 1,
                        }], cafe24.database), 1)
                    with cafe24.database() as conn:
                        conn.execute('''INSERT INTO ourisul_order_notice
                            (source, order_id, status, ordered_at, item_count)
                            VALUES ('synthetic', %s, 'TEST_CREATED', '2026-09-29T00:00:00Z', 0)''',
                            (orphan,))
                    sender = Mock(return_value=response())
                    with patch.dict(os.environ, {
                        'SYNTHETIC_ORDER_NOTIFICATIONS_ENABLED': 'true',
                        'SLACK_TEST_ORDER_WEBHOOK_URL':
                            'https://hooks.slack.com/services/TEST/TEST/SYNTHETIC',
                        'SLACK_ORDER_WEBHOOK_URL':
                            'https://hooks.slack.com/services/TEST/TEST/REAL',
                    }):
                        with patch.dict(os.environ, {'VERIFY_DATABASE_URL': isolated_dsn,
                                                     'DATABASE_URL': 'postgresql://dummy@localhost/production'}):
                            with patch('sys.stderr', new_callable=io.StringIO):
                                with self.assertRaises(SystemExit):
                                    send_synthetic_orders.run([])
                                with self.assertRaises(SystemExit):
                                    send_synthetic_orders.run(['--limit', '20'])
                            self.assertEqual(send_synthetic_orders.run(['--pending']),
                                             {'ready': 2, 'deferred': 0, 'leased': 0, 'total': 2})
                            with self.assertRaisesRegex(ValueError, 'Invalid synthetic order ID'):
                                send_synthetic_orders.run(['--send', older.upper()])
                        sender.assert_not_called()
                        missing = 'TEST-' + secrets.token_hex(16)
                        original_send = notices.deliver_synthetic_pending
                        with patch.object(notices, 'deliver_synthetic_pending',
                                          side_effect=lambda connect, order_id: original_send(
                                              connect, order_id, post=sender)) as command_send:
                            with patch.dict(os.environ, {'VERIFY_DATABASE_URL': isolated_dsn,
                                                         'DATABASE_URL': 'postgresql://dummy@localhost/production'}):
                                for order_id in (missing, orphan):
                                    self.assertEqual(send_synthetic_orders.run(['--send', order_id]),
                                                     {'delivered': 0, 'failed': 0})
                                self.assertEqual(send_synthetic_orders.run(['--send', selected]),
                                                 {'delivered': 1, 'failed': 0})
                            self.assertEqual([call.args[1] for call in command_send.call_args_list],
                                             [missing, orphan, selected])
                        self.assertEqual(sender.call_count, 1)
                        self.assertEqual(sender.call_args.kwargs['json'],
                                         {'text': f'[테스트·미운영] {selected}'})
                        self.assertEqual(notices.deliver_synthetic_pending(
                            cafe24.database, selected, post=sender),
                            {'delivered': 0, 'failed': 0})
                        with cafe24.database() as conn:
                            conn.execute('''UPDATE ourisul_order_notice
                                SET lease_until = now() + interval '60 seconds'
                                WHERE source = 'synthetic' AND order_id = %s''', (older,))
                        self.assertEqual(notices.deliver_synthetic_pending(
                            cafe24.database, older, post=sender),
                            {'delivered': 0, 'failed': 0})
                        with cafe24.database() as conn:
                            conn.execute('''UPDATE ourisul_order_notice
                                SET lease_until = now() - interval '1 second',
                                    next_attempt_at = now() + interval '60 seconds'
                                WHERE source = 'synthetic' AND order_id = %s''', (older,))
                        self.assertEqual(notices.deliver_synthetic_pending(
                            cafe24.database, older, post=sender),
                            {'delivered': 0, 'failed': 0})
                        self.assertEqual(sender.call_count, 1)
                        with cafe24.database() as conn:
                            rows = conn.execute('''SELECT source, order_id, attempts,
                                delivered_at IS NOT NULL FROM ourisul_order_notice
                                ORDER BY source, order_id''').fetchall()
                        self.assertEqual(rows, sorted([
                            ('cafe24', 'VERIFY-cafe24', 0, False),
                            ('coupang', 'VERIFY-coupang', 0, False),
                            ('synthetic', older, 0, False),
                            ('synthetic', selected, 1, True),
                            ('synthetic', orphan, 0, False),
                        ]))

                def test_synthetic_pending_classifies_without_claiming(self):
                    ids = {name: 'TEST-' + secrets.token_hex(16) for name in
                           ('ready', 'expired', 'deferred', 'leased', 'leased_deferred', 'delivered')}
                    for order_id in ids.values():
                        self.assertTrue(test_orders.record_test_order(order_id))
                    self.assertEqual(notices.record_orders('cafe24', [{
                        'order_id': 'VERIFY-REAL', 'status': 'N10',
                        'ordered_at': '2026-09-29T00:00:00Z', 'item_count': 1,
                    }], cafe24.database), 1)
                    orphan = 'TEST-' + secrets.token_hex(16)
                    with cafe24.database() as conn:
                        for name in ('expired', 'deferred', 'leased', 'leased_deferred'):
                            conn.execute('''UPDATE ourisul_order_notice SET
                                next_attempt_at = now() + (%s * interval '1 minute'),
                                lease_until = now() + (%s * interval '1 minute')
                                WHERE source = 'synthetic' AND order_id = %s''',
                                (1 if name in ('deferred', 'leased_deferred') else -1,
                                 1 if name in ('leased', 'leased_deferred') else -1, ids[name]))
                        conn.execute('''UPDATE ourisul_order_notice SET delivered_at = now()
                            WHERE source = 'synthetic' AND order_id = %s''', (ids['delivered'],))
                        conn.execute('''INSERT INTO ourisul_order_notice
                            (source, order_id, status, ordered_at, item_count)
                            VALUES ('synthetic', %s, 'TEST_CREATED', '2026-09-29T00:00:00Z', 0),
                                   ('synthetic', 'TEST-NOT-LOWERCASE-HEX', 'TEST_CREATED',
                                    '2026-09-29T00:00:00Z', 0)''', (orphan,))
                        before = conn.execute('''SELECT source, order_id, attempts, delivered_at,
                            next_attempt_at, lease_until FROM ourisul_order_notice ORDER BY source, order_id''').fetchall()
                    with patch.dict(os.environ, {'VERIFY_DATABASE_URL': isolated_dsn,
                                                 'DATABASE_URL': 'postgresql://dummy@localhost/production'}):
                        self.assertEqual(send_synthetic_orders.run(['--pending']),
                                         {'ready': 2, 'deferred': 1, 'leased': 2, 'total': 5})
                    with cafe24.database() as conn:
                        after = conn.execute('''SELECT source, order_id, attempts, delivered_at,
                            next_attempt_at, lease_until FROM ourisul_order_notice ORDER BY source, order_id''').fetchall()
                    self.assertEqual(after, before)

                    # now() is stable within this transaction: equality is leased,
                    # while a retry exactly at now() is ready when its lease expired.
                    with cafe24.database() as conn:
                        conn.execute('''UPDATE ourisul_order_notice SET lease_until = now(),
                            next_attempt_at = now() + interval '1 minute'
                            WHERE source = 'synthetic' AND order_id = %s''', (ids['leased'],))
                        conn.execute('''UPDATE ourisul_order_notice SET lease_until = now() - interval '1 second',
                            next_attempt_at = now()
                            WHERE source = 'synthetic' AND order_id = %s''', (ids['ready'],))

                        @contextmanager
                        def same_connection():
                            yield conn

                        self.assertEqual(notices.synthetic_pending_counts(same_connection),
                                         {'ready': 2, 'deferred': 1, 'leased': 2, 'total': 5})

                def test_synthetic_pending_requires_initialized_tables(self):
                    connection = Mock()
                    connection.__enter__ = Mock(return_value=connection)
                    connection.__exit__ = Mock(return_value=False)
                    connection.execute.side_effect = notices.UndefinedTable()
                    with self.assertRaisesRegex(ValueError, 'not initialized'):
                        notices.synthetic_pending_counts(lambda: connection)

                def test_synthetic_pending_html_is_private_and_read_only(self):
                    path = '/admin/orders/test?view=html'
                    self.assertEqual(self.client.get(path).status_code, 401)
                    with patch.dict(os.environ, {'SYNTHETIC_ORDER_TEST_ENABLED': 'false'}):
                        self.assertEqual(self.get(path).status_code, 404)
                    ids = {state: 'TEST-' + secrets.token_hex(16) for state in
                           ('ready', 'expired', 'deferred', 'leased', 'leased_deferred',
                            'delivered')}
                    for order_id in ids.values():
                        self.assertTrue(test_orders.record_test_order(order_id))
                    orphan = 'TEST-' + secrets.token_hex(16)
                    with cafe24.database() as conn:
                        for state in ('expired', 'deferred', 'leased', 'leased_deferred'):
                            conn.execute('''UPDATE ourisul_order_notice SET
                                next_attempt_at = now() + (%s * interval '1 minute'),
                                lease_until = now() + (%s * interval '1 minute')
                                WHERE source = 'synthetic' AND order_id = %s''',
                                (1 if state in ('deferred', 'leased_deferred') else -1,
                                 1 if state in ('leased', 'leased_deferred') else -1,
                                 ids[state]))
                        conn.execute('''UPDATE ourisul_order_notice SET delivered_at = now()
                            WHERE source = 'synthetic' AND order_id = %s''',
                            (ids['delivered'],))
                        conn.execute('''INSERT INTO ourisul_order_notice
                            (source, order_id, status, ordered_at, item_count)
                            VALUES ('synthetic', %s, 'TEST_CREATED', '2026-10-13T00:00:00Z', 0),
                                   ('cafe24', 'VERIFY-REAL', 'N10', '2026-10-13T00:00:00Z', 1)''',
                            (orphan,))

                    tables = ('ourisul_test_orders', 'ourisul_test_order_state',
                              'ourisul_test_order_event', 'ourisul_order_notice')

                    def snapshot():
                        with cafe24.database() as conn:
                            return tuple(tuple(conn.execute('SELECT * FROM ' + table +
                                                            ' ORDER BY 1').fetchall())
                                         for table in tables)

                    before = snapshot()
                    with patch.object(requests.sessions.Session, 'request',
                                      side_effect=AssertionError('Pending page must not use HTTP')) as http:
                        page = self.get(path)
                        self.assertEqual(page.status_code, 200)
                        html = page.get_data(as_text=True)
                        self.assertIn('<section aria-labelledby="pending-heading">', html)
                        self.assertIn('name="viewport"', html)
                        self.assertIn('<dt>발송 대기 (ready)</dt><dd>2건</dd>', html)
                        self.assertIn('<dt>재시도 대기 (deferred)</dt><dd>1건</dd>', html)
                        self.assertIn('<dt>발송 작업 점유 중 (leased)</dt><dd>2건</dd>', html)
                        self.assertIn('<dt>미전송 합계 (total)</dt><dd>5건</dd>', html)
                        self.assertEqual(page.headers['Cache-Control'], 'no-store')
                        self.assertEqual(page.headers['Referrer-Policy'], 'no-referrer')
                        default = self.get('/admin/orders/test')
                        self.assertEqual(default.status_code, 200)
                        self.assertNotIn('pending-heading', default.get_data(as_text=True))
                        self.assertNotIn('미전송 합계', default.get_data(as_text=True))
                        history = self.get(f'/admin/orders/test/{ids["ready"]}/history')
                        self.assertEqual(history.status_code, 200)
                        self.assertEqual(set(history.json),
                                         {'order_id', 'status', 'version', 'events'})
                        http.assert_not_called()
                    self.assertEqual(snapshot(), before)

                    with cafe24.database() as conn:
                        conn.execute('DROP TABLE ourisul_order_notice')
                    unavailable = self.get(path)
                    self.assertEqual(unavailable.status_code, 503)
                    self.assertEqual(unavailable.headers['Cache-Control'], 'no-store')

                def test_synthetic_exact_inspection_is_read_only(self):
                    ids = {name: 'TEST-' + secrets.token_hex(16) for name in
                           ('ready', 'deferred', 'leased', 'both', 'delivered', 'delivered_both')}
                    for order_id in ids.values():
                        self.assertTrue(test_orders.record_test_order(order_id))
                    orphan = 'TEST-' + secrets.token_hex(16)
                    missing = 'TEST-' + secrets.token_hex(16)
                    with cafe24.database() as conn:
                        conn.execute('''UPDATE ourisul_order_notice SET
                            next_attempt_at = now() + interval '5 minutes'
                            WHERE source = 'synthetic' AND order_id = %s''', (ids['deferred'],))
                        conn.execute('''UPDATE ourisul_order_notice SET
                            lease_until = now() + interval '5 minutes'
                            WHERE source = 'synthetic' AND order_id = %s''', (ids['leased'],))
                        conn.execute('''UPDATE ourisul_order_notice SET
                            next_attempt_at = now() + interval '5 minutes',
                            lease_until = now() + interval '5 minutes'
                            WHERE source = 'synthetic' AND order_id = %s''', (ids['both'],))
                        conn.execute('''UPDATE ourisul_order_notice SET delivered_at = now()
                            WHERE source = 'synthetic' AND order_id = %s''', (ids['delivered'],))
                        conn.execute('''UPDATE ourisul_order_notice SET delivered_at = now(),
                            next_attempt_at = now() + interval '5 minutes',
                            lease_until = now() + interval '5 minutes'
                            WHERE source = 'synthetic' AND order_id = %s''', (ids['delivered_both'],))
                        conn.execute('''INSERT INTO ourisul_order_notice
                            (source, order_id, status, ordered_at, item_count)
                            VALUES ('synthetic', %s, 'TEST_CREATED', '2026-09-29T00:00:00Z', 0),
                                   ('cafe24', %s, 'N10', '2026-09-29T00:00:00Z', 1)''',
                            (orphan, missing))
                        before = conn.execute('''SELECT source, order_id, attempts, delivered_at,
                            next_attempt_at, lease_until FROM ourisul_order_notice
                            ORDER BY source, order_id''').fetchall()
                        before_states = conn.execute('''SELECT *
                            FROM ourisul_test_order_state ORDER BY order_id''').fetchall()
                        before_events = conn.execute('''SELECT *
                            FROM ourisul_test_order_event ORDER BY order_id, version''').fetchall()
                    with patch.dict(os.environ, {'VERIFY_DATABASE_URL': isolated_dsn,
                                                 'DATABASE_URL': 'postgresql://dummy@localhost/production'}), \
                            patch.object(requests.sessions.Session, 'request',
                                         side_effect=AssertionError('Inspection must not use HTTP')) as http_request:
                        for name in ('ready', 'deferred', 'leased', 'both', 'delivered', 'delivered_both'):
                            result = send_synthetic_orders.run(['--inspect', ids[name]])
                            self.assertEqual(result['order_id'], ids[name])
                            self.assertEqual(result['attempts'], 0)
                            expected_state = {'both': 'leased', 'delivered_both': 'delivered'}.get(name, name)
                            self.assertEqual(result['state'], expected_state)
                            if name == 'deferred':
                                self.assertIn('retry_at', result)
                            if name in ('leased', 'both'):
                                self.assertIn('lease_until', result)
                            if name in ('delivered', 'delivered_both'):
                                self.assertIn('delivered_at', result)
                                self.assertNotIn('lease_until', result)
                                self.assertNotIn('retry_at', result)
                        for order_id in (missing, orphan):
                            self.assertEqual(send_synthetic_orders.run(['--inspect', order_id]),
                                             {'order_id': order_id, 'state': 'missing'})
                        with self.assertRaisesRegex(ValueError, 'Invalid synthetic order ID'):
                            send_synthetic_orders.run(['--inspect', missing.upper()])
                        with self.assertRaisesRegex(ValueError, 'Invalid synthetic order ID'):
                            send_synthetic_orders.run(['--inspect', ''])
                        for actions in (['--pending', '--inspect', ids['ready']],
                                        ['--pending', '--send', ids['ready']],
                                        ['--inspect', ids['ready'], '--send', ids['ready']]):
                            with patch('sys.stderr', new_callable=io.StringIO):
                                with self.assertRaises(SystemExit) as rejected:
                                    send_synthetic_orders.run(actions)
                            self.assertEqual(rejected.exception.code, 2)
                        http_request.assert_not_called()
                    with cafe24.database() as conn:
                        after = conn.execute('''SELECT source, order_id, attempts, delivered_at,
                            next_attempt_at, lease_until FROM ourisul_order_notice
                            ORDER BY source, order_id''').fetchall()
                        after_states = conn.execute('''SELECT *
                            FROM ourisul_test_order_state ORDER BY order_id''').fetchall()
                        after_events = conn.execute('''SELECT *
                            FROM ourisul_test_order_event ORDER BY order_id, version''').fetchall()
                    self.assertEqual(after, before)
                    self.assertEqual(after_states, before_states)
                    self.assertEqual(after_events, before_events)

                def test_synthetic_inspection_lease_at_now_is_active(self):
                    order_id = 'TEST-' + secrets.token_hex(16)
                    self.assertTrue(test_orders.record_test_order(order_id))
                    # PostgreSQL now() is transaction-stable, so this checks equality
                    # instead of racing two wall-clock reads across transactions.
                    conn = cafe24.database()
                    try:
                        conn.execute('''UPDATE ourisul_order_notice SET lease_until = now(),
                            next_attempt_at = now() + interval '5 minutes'
                            WHERE source = 'synthetic' AND order_id = %s''', (order_id,))
                        with patch.object(requests.sessions.Session, 'request',
                                          side_effect=AssertionError('Inspection must not use HTTP')) as http_request:
                            result = notices.synthetic_notice_status(lambda: conn, order_id)
                            http_request.assert_not_called()
                        self.assertEqual(result['state'], 'leased')
                        self.assertIn('lease_until', result)
                        self.assertNotIn('retry_at', result)
                    finally:
                        conn.close()

                def test_synthetic_inspection_requires_initialized_tables(self):
                    connection = Mock()
                    connection.__enter__ = Mock(return_value=connection)
                    connection.__exit__ = Mock(return_value=False)
                    connection.execute.side_effect = notices.UndefinedTable()
                    with self.assertRaisesRegex(ValueError, 'not initialized'):
                        notices.synthetic_notice_status(
                            lambda: connection, 'TEST-' + secrets.token_hex(16))

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

                def test_channel_inbox_is_private_bounded_historical_and_read_only(self):
                    path = '/admin/orders/channels'
                    self.assertEqual(self.client.get(path).status_code, 401)
                    self.assertIn('저장된 채널 주문 기록이 없습니다.',
                                  self.get(path).get_data(as_text=True))
                    stamp = datetime(2026, 10, 1, tzinfo=timezone.utc)
                    with cafe24.database() as conn:
                        for i in range(27):
                            conn.execute('''INSERT INTO ourisul_order_notice
                                (source, order_id, status, ordered_at, item_count, created_at)
                                VALUES (%s, %s, %s, %s, 0, %s)''',
                                ('cafe24' if i % 2 else 'coupang', f'INBOX-{i:02}',
                                 '<script>alert(1)</script>' if i == 26 else 'FIRST',
                                 '2026-10-01T00:00:00Z', stamp))
                        conn.execute('''INSERT INTO ourisul_order_notice
                            (source, order_id, status, ordered_at, item_count, created_at)
                            VALUES ('synthetic', 'TEST-inbox', 'TEST', '2026-10-01', 0, %s)''', (stamp,))
                    self.assertEqual(notices.record_orders('cafe24', [{
                        'order_id': 'INBOX-25', 'status': 'CHANGED',
                        'ordered_at': '2026-10-02T00:00:00Z', 'item_count': 9,
                    }], cafe24.database), 0)
                    for status in ('PACKED', 'REVIEW', 'CHANGED_AGAIN'):
                        self.assertEqual(notices.record_orders('cafe24', [{
                            'order_id': 'INBOX-25', 'status': status,
                            'ordered_at': '2026-10-02T00:00:00Z', 'item_count': 9,
                        }], cafe24.database), 0)
                    with cafe24.database() as conn:
                        conn.execute('''INSERT INTO ourisul_channel_order_state
                            (source, order_id, status, observed_at) VALUES
                            ('coupang', 'INBOX-26', %s, %s)''',
                            ('<img src=x onerror=alert(2)>', stamp))
                        conn.execute('''INSERT INTO ourisul_channel_order_event
                            (source, order_id, status, observed_at) VALUES
                            ('coupang', 'INBOX-26', %s, %s)''',
                            ('<img src=x onerror=alert(2)>', stamp))
                        before = conn.execute('''SELECT source, order_id, status, ordered_at, item_count,
                            created_at, delivered_at, attempts, next_attempt_at, lease_until
                            FROM ourisul_order_notice ORDER BY source, order_id''').fetchall()
                        observations_before = [conn.execute('SELECT * FROM ' + table +
                            ' ORDER BY source, order_id' + (', id' if table.endswith('event') else '')).fetchall()
                            for table in ('ourisul_channel_order_state', 'ourisul_channel_order_event')]
                    first = self.get(path)
                    self.assertEqual(first.status_code, 200)
                    self.assertEqual(first.headers['Cache-Control'], 'no-store')
                    self.assertEqual(first.headers['Referrer-Policy'], 'no-referrer')
                    html = first.get_data(as_text=True)
                    self.assertIn('최초 수집 당시', html)
                    self.assertIn('지금 채널의 최신 상태를 보장하지 않습니다', html)
                    self.assertIn('결제·출고·배송의 실제 진행이나 완료를 증명하지 않습니다', html)
                    self.assertIn('CHANGED_AGAIN', html)
                    self.assertIn('REVIEW', html)
                    self.assertIn('PACKED', html)
                    self.assertNotIn('<li>CHANGED ', html)
                    self.assertEqual(html.count('<li>'), 4)
                    self.assertLess(html.index('CHANGED_AGAIN'), html.index('REVIEW'))
                    self.assertLess(html.index('REVIEW'), html.index('PACKED'))
                    self.assertIn('수집 이력 없음', html)
                    self.assertEqual(html.count('<tr><td>'), 25)
                    self.assertNotIn('TEST-inbox', html)
                    self.assertIn('&lt;script&gt;alert(1)&lt;/script&gt;', html)
                    self.assertNotIn('<script>alert(1)</script>', html)
                    self.assertIn('&lt;img src=x onerror=alert(2)&gt;', html)
                    self.assertNotIn('<img src=x onerror=alert(2)>', html)
                    from html import unescape
                    link = re.search(r'<a href="([^"]+)">이전 기록 보기 →</a>', html)
                    self.assertIsNotNone(link)
                    second = self.get(unescape(link.group(1)))
                    self.assertEqual(second.status_code, 200)
                    self.assertEqual(second.get_data(as_text=True).count('<tr><td>'), 2)
                    self.assertNotIn('이전 기록 보기 →', second.get_data(as_text=True))
                    first_ids = re.findall(r'<td>(INBOX-\d\d)</td>', html)
                    second_ids = re.findall(r'<td>(INBOX-\d\d)</td>', second.get_data(as_text=True))
                    self.assertEqual(len(first_ids), 25)
                    self.assertEqual(len(second_ids), 2)
                    self.assertEqual(set(first_ids + second_ids), {f'INBOX-{i:02}' for i in range(27)})
                    self.assertEqual(len(set(first_ids + second_ids)), 27)
                    self.assertEqual(self.get(path, query_string={'cursor': 'bad'}).status_code, 400)
                    self.assertEqual(self.get(path + '?cursor=a&cursor=b').status_code, 400)
                    self.assertEqual(self.get(path + '?cursor=').status_code, 400)
                    self.assertEqual(self.get(path + '?limit=1000').status_code, 400)
                    with cafe24.database() as conn:
                        after = conn.execute('''SELECT source, order_id, status, ordered_at, item_count,
                            created_at, delivered_at, attempts, next_attempt_at, lease_until
                            FROM ourisul_order_notice ORDER BY source, order_id''').fetchall()
                        observations_after = [conn.execute('SELECT * FROM ' + table +
                            ' ORDER BY source, order_id' + (', id' if table.endswith('event') else '')).fetchall()
                            for table in ('ourisul_channel_order_state', 'ourisul_channel_order_event')]
                    self.assertEqual(before, after)
                    self.assertEqual(observations_before, observations_after)
                    with cafe24.database() as conn:
                        conn.execute('DROP TABLE ourisul_channel_order_event, ourisul_channel_order_state')
                    missing_ledger = self.get(path)
                    self.assertEqual(missing_ledger.status_code, 200)
                    self.assertIn('수집 기록 저장소를 확인할 수 없음', missing_ledger.get_data(as_text=True))
                    self.assertEqual(missing_ledger.headers['Cache-Control'], 'no-store')
                    with cafe24.database() as conn:
                        conn.execute('TRUNCATE ourisul_order_notice')
                    self.assertIn('저장된 채널 주문 기록이 없습니다.',
                                  self.get(path).get_data(as_text=True))
                    with cafe24.database() as conn:
                        conn.execute('DROP TABLE ourisul_order_notice')
                    self.assertEqual(self.get(path).status_code, 503)
                    with cafe24.database() as conn:
                        self.assertIsNone(conn.execute("SELECT to_regclass('ourisul_order_notice')").fetchone()[0])

                def test_channel_inbox_history_is_bounded_and_snapshot_consistent(self):
                    import channel_inbox
                    from contextlib import contextmanager
                    item = {'order_id': 'SAME-ID', 'status': 'FIRST',
                            'ordered_at': '2026-10-01T00:00:00Z', 'item_count': 0}
                    notices.record_orders('cafe24', [item], cafe24.database)
                    notices.record_orders('coupang', [item], cafe24.database)
                    with cafe24.database() as conn:
                        conn.execute('''INSERT INTO ourisul_channel_order_event
                            (source, order_id, status, observed_at)
                            SELECT 'cafe24', 'SAME-ID', 'EVENT_' || n, now()
                            FROM generate_series(1, 30) AS n ORDER BY n''')
                        conn.execute('''UPDATE ourisul_channel_order_state SET status = 'EVENT_30'
                            WHERE source = 'cafe24' AND order_id = 'SAME-ID' ''')
                    rows, cursor = channel_inbox.saved_orders(connect=cafe24.database)
                    self.assertIsNone(cursor)
                    self.assertEqual(len(rows), 2)
                    by_source = {row['source']: row for row in rows}
                    self.assertEqual(by_source['coupang']['observed_status'], 'FIRST')
                    self.assertEqual([event['status'] for event in by_source['cafe24']['events']],
                                     ['EVENT_30', 'EVENT_29', 'EVENT_28'])

                    # Commit a concurrent collection after the first snapshot
                    # query, but before the page query on that same connection.
                    @contextmanager
                    def interleaved():
                        with cafe24.database() as conn:
                            class ConnectionProxy:
                                def execute(self, query, params=None):
                                    if 'WITH page AS' in query:
                                        notices.record_orders('cafe24', [{**item, 'status': 'NEW'}],
                                                              cafe24.database)
                                    return conn.execute(query, params)
                            yield ConnectionProxy()

                    old_rows, _ = channel_inbox.saved_orders(connect=interleaved)
                    old_cafe = next(row for row in old_rows if row['source'] == 'cafe24')
                    self.assertEqual(old_cafe['observed_status'], 'EVENT_30')
                    self.assertEqual(old_cafe['events'][0]['status'], 'EVENT_30')
                    new_rows, _ = channel_inbox.saved_orders(connect=cafe24.database)
                    new_cafe = next(row for row in new_rows if row['source'] == 'cafe24')
                    self.assertEqual(new_cafe['observed_status'], 'NEW')
                    self.assertEqual(new_cafe['events'][0]['status'], 'NEW')

                def test_network_default_deny(self):
                    with self.assertRaisesRegex(AssertionError, 'Unexpected external HTTP'):
                        requests.get('https://example.invalid/')

                def test_taste_explorer_unified_editorial_ranking_without_provider_access(self):
                    from app import TASTE_QUESTIONS, TASTE_STYLES, TASTE_WEIGHTS, rank_taste_styles
                    keys = tuple(question[0] for question in TASTE_QUESTIONS)
                    with patch('app.products_for_page', side_effect=AssertionError('catalog accessed')), patch.object(
                            cafe24, 'database', side_effect=AssertionError('database accessed')):
                        landing = self.get('/taste-explorer')
                        self.assertEqual(landing.status_code, 200)
                        text = landing.get_data(as_text=True)
                        self.assertIn('위스키·맥주·와인·전통주', text)
                        self.assertNotIn('name="category"', text)
                        self.assertNotIn('taste-categories', text.split('<main id="main">', 1)[1])
                        self.assertIn('href="/taste-explorer"', self.get('/about').get_data(as_text=True))
                        self.assertEqual(text.count('<fieldset>'), 7)
                        self.assertEqual(text.count('type="radio"'), sum(len(q[2]) for q in TASTE_QUESTIONS))
                        self.assertEqual(text.count(' required '), sum(len(q[2]) for q in TASTE_QUESTIONS))
                        self.assertIn('action="/taste-explorer#taste-result"', text)
                        self.assertNotIn('name="style"', text)
                        self.assertEqual(len(TASTE_STYLES), 28)
                        self.assertEqual(len({style['key'] for style in TASTE_STYLES}), 28)
                        self.assertEqual({style['category'] for style in TASTE_STYLES},
                                         {'위스키', '맥주', '와인', '전통주'})
                        for style in TASTE_STYLES:
                            self.assertEqual(len(style['profile']), len(keys))
                            self.assertTrue(all(style[field] for field in ('character', 'explore', 'compare')))
                            self.assertTrue(all(feature in {option[0] for option in question[2]}
                                                for feature, question in zip(style['profile'], TASTE_QUESTIONS)))
                        first_place = set()
                        tied_top = 0
                        cutoff_ties = 0
                        for answers in product(*(tuple(option[0] for option in question[2])
                                                 for question in TASTE_QUESTIONS)):
                            # Independent reference computation, avoiding the ranking helper under test.
                            expected = sorted(TASTE_STYLES, key=lambda style: (
                                -sum(weight for selected, feature, weight in zip(
                                    answers, style['profile'], TASTE_WEIGHTS) if selected == feature), style['key']))
                            actual = [row[1] for row in rank_taste_styles(answers)]
                            self.assertEqual(actual, expected)
                            first_place.add(actual[0]['key'])
                            top_score = rank_taste_styles(answers)[0][0]
                            tied_top += sum(score == top_score for score, _ in rank_taste_styles(answers)) > 1
                            cutoff_ties += rank_taste_styles(answers)[2][0] == rank_taste_styles(answers)[3][0]
                        self.assertEqual(first_place, {style['key'] for style in TASTE_STYLES})
                        self.assertGreater(tied_top, 0)
                        self.assertGreater(cutoff_ties, 0)
                        # Golden examples selected by editorial expectation, rather than a computed result.
                        golden = (
                            (('smoke', 'dry', 'full', 'soft', 'pronounced', 'clear', 'long'), '피트 위스키'),
                            (('roast', 'dry', 'medium', 'soft', 'pronounced', 'creamy', 'long'), '드라이 스타우트'),
                            (('fruit', 'dry', 'light', 'bright', 'low', 'bubbly', 'clean'), '브뤼 스파클링 와인'),
                            (('grain', 'round', 'medium', 'bright', 'low', 'creamy', 'clean'), '탁주'),
                            (('spice', 'round', 'full', 'soft', 'low', 'clear', 'long'), '버번'),
                            (('fruit', 'dry', 'light', 'soft', 'low', 'clear', 'clean'), '과일 향 몰트 위스키'),
                        )
                        for answers, expected_name in golden:
                            page = self.get('/taste-explorer', query_string=dict(zip(keys, answers)))
                            self.assertEqual(page.status_code, 200)
                            self.assertEqual(page.headers['Cache-Control'], 'no-store')
                            result = page.get_data(as_text=True).split('id="taste-result"', 1)[1].split('</section>', 1)[0]
                            self.assertIn('role="region"', result)
                            self.assertIn('tabindex="-1"', result)
                            self.assertIn('먼저 살펴볼 스타일: ' + expected_name, result)
                            self.assertEqual(result.count('class="taste-style"'), 3)
                            self.assertIn('답과 겹친 감각:', result)
                            self.assertIn('비교해 볼 점', result)
                            self.assertIn('특정 상품, 구매 가능 여부나 측정된 적합도를 뜻하지 않습니다.', result)
                            self.assertNotIn('href="/products', result)
                        # Top and cutoff ties are disclosed, including the omitted fourth profile.
                        tied = self.get('/taste-explorer', query_string=dict(zip(keys,
                            ('fruit', 'dry', 'light', 'soft', 'low', 'clear', 'clean')))).get_data(as_text=True)
                        self.assertIn('같은 점수의 다른 스타일도 있습니다.', tied)
                        top_tied = self.get('/taste-explorer', query_string=dict(zip(keys,
                            ('fruit', 'dry', 'medium', 'soft', 'low', 'clear', 'long')))).get_data(as_text=True)
                        self.assertIn('함께 살펴볼 스타일', top_tied)
                        self.assertIn('가장 앞선 4가지 스타일은 동점', top_tied)
                        self.assertEqual(top_tied.count('class="taste-style"'), 4)
                        for name in ('복분자주', '셰리 캐스크 위스키', '카베르네 소비뇽', '피노 누아'):
                            self.assertIn('<h3>' + name + '</h3>', top_tied)

                def test_taste_explorer_rejects_ambiguous_or_invalid_answers(self):
                    from app import TASTE_QUESTIONS
                    valid = dict((q[0], q[2][0][0]) for q in TASTE_QUESTIONS)
                    from urllib.parse import urlencode
                    invalid = (
                        {'category': 'beer', **valid},
                        {**valid, 'style': 'ipa'},
                        {**valid, 'aroma': 'invalid'},
                        {key: value for key, value in valid.items() if key != 'finish'},
                    )
                    for params in invalid:
                        path = '/taste-explorer?' + urlencode(params)
                        page = self.get(path)
                        self.assertEqual(page.status_code, 400, path)
                        self.assertIn('취향 탐색 다시 시작하기', page.get_data(as_text=True))
                        self.assertEqual(page.headers['Cache-Control'], 'no-store')
                    for extra in ('aroma=fruit', 'finish=clean', 'category=beer', 'unexpected=1'):
                        path = '/taste-explorer?' + urlencode(valid) + '&' + extra
                        self.assertEqual(self.get(path).status_code, 400, path)
                    self.assertEqual(self.get('/taste-explorer?aroma=fruit').status_code, 400)
                    self.assertEqual(self.get('/taste-explorer?category=beer').status_code, 400)

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


