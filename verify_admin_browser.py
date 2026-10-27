#!/usr/bin/env python3
"""Chromium rehearsal against a local Flask server and disposable PostgreSQL schema.

Run only with VERIFY_DATABASE_URL pointing at a dedicated verify/test database.
No production credentials, provider calls, or real Slack requests are used.
The synthetic sender runs only with an injected mock HTTP transport.
"""
import json
import os
import secrets
import sys
import traceback
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Thread
from types import SimpleNamespace
from unittest.mock import patch
from urllib.parse import urlparse


def check(page, heading):
    assert page.locator('h1').inner_text() == heading, page.url
    assert page.locator('html').get_attribute('lang') == 'ko', page.url
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'), page.url
    assert page.locator('main').count() == 1, page.url


def private_ok(response):
    assert response.status == 200, response.url
    assert response.headers.get('cache-control') == 'no-store', response.url
    assert response.headers.get('referrer-policy') == 'no-referrer', response.url


def saved_rows(database):
    """Capture all five synthetic ledgers, including rows from earlier browser steps."""
    tables = ('ourisul_test_orders', 'ourisul_test_order_item',
              'ourisul_test_order_state', 'ourisul_test_order_event',
              'ourisul_order_notice')
    with database() as conn:
        return {table: conn.execute(f'SELECT * FROM {table} ORDER BY 1, 2').fetchall()
                for table in tables}


def main():
    report = {'started_at': datetime.now(timezone.utc).isoformat(),
              'commit_sha': os.environ.get('GITHUB_SHA', 'unknown'),
              'verified_head_sha': os.environ.get('VERIFIED_HEAD_SHA', 'unknown'),
              'mode': 'chromium-320px-local-app-disposable-postgres',
              'isolated_schema_cleaned': False, 'passed': False}
    dsn = os.environ.get('VERIFY_DATABASE_URL')
    production_dsn = os.environ.get('DATABASE_URL')
    schema_name = 'verify_browser_' + secrets.token_hex(12)
    created = False
    server = None
    thread = None
    try:
        if not dsn:
            raise ValueError('VERIFY_DATABASE_URL is required; no DATABASE_URL fallback')
        import psycopg
        from psycopg import sql
        from psycopg.conninfo import conninfo_to_dict, make_conninfo
        from cryptography.fernet import Fernet
        from playwright.sync_api import sync_playwright
        from werkzeug.serving import make_server
        import requests
        from psycopg import OperationalError

        info = conninfo_to_dict(dsn)
        if not any(part in info.get('dbname', '').lower() for part in ('verify', 'test')):
            raise ValueError('Dedicated database name must contain verify or test')
        if info.get('service') or not info.get('host') or not info.get('user'):
            raise ValueError('Explicit database host and user are required')
        if production_dsn:
            prod = conninfo_to_dict(production_dsn)
            identity = lambda value: (value.get('host'), value.get('port', '5432'), value.get('dbname'))
            if identity(info) == identity(prod):
                raise ValueError('Verification database must differ from DATABASE_URL')
        isolated_dsn = make_conninfo(dsn, options='-c search_path=' + schema_name)
        with psycopg.connect(dsn, connect_timeout=10) as conn:
            conn.execute(sql.SQL('CREATE SCHEMA {}').format(sql.Identifier(schema_name)))
        created = True
        env = {'DATABASE_URL': isolated_dsn, 'CAFE24_MALL_ID': 'ourisul-verify',
               'CAFE24_CLIENT_ID': 'synthetic-client', 'CAFE24_CLIENT_SECRET': 'synthetic-secret',
               'FLASK_SECRET_KEY': secrets.token_hex(32), 'ADMIN_PASSWORD': secrets.token_hex(20),
               'TOKEN_ENCRYPTION_KEY': Fernet.generate_key().decode(),
               'SYNTHETIC_ORDER_TEST_ENABLED': 'true', 'STORE_LIVE': 'false'}
        with patch.dict(os.environ, env, clear=True), patch.object(
                requests.sessions.Session, 'request',
                side_effect=AssertionError('External HTTP forbidden')) as outbound_request:
            import cafe24
            import order_notifications as notices
            import synthetic_order_history as fulfillment
            import test_orders
            import channel_inbox
            from app import app

            # The loopback HTTP server has no TLS. Only this process relaxes the cookie flag.
            app.config.update(TESTING=True, SESSION_COOKIE_SECURE=False)
            with cafe24.database() as conn:
                test_orders.schema(conn)
                fulfillment.schema(conn)
                notices.schema(conn)
            server = make_server('127.0.0.1', 0, app, threaded=True)
            thread = Thread(target=server.serve_forever, daemon=True)
            thread.start()
            base = f'http://127.0.0.1:{server.server_port}'

            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(headless=True)
                report['browser_version'] = browser.version
                context = browser.new_context(viewport={'width': 320, 'height': 640},
                                              is_mobile=True, has_touch=True,
                                              http_credentials={'username': 'admin',
                                                                'password': env['ADMIN_PASSWORD']})
                page = context.new_page()
                blocked_urls = []

                def local_only(route):
                    target = urlparse(route.request.url)
                    if target.scheme == 'http' and target.netloc == urlparse(base).netloc:
                        route.continue_()
                    else:
                        blocked_urls.append(route.request.url)
                        route.abort()

                page.route('**/*', local_only)
                result = page.goto(base + '/admin/orders/test/rehearsal')
                private_ok(result)
                check(page, '가상 주문 연습')
                order_id = page.locator('input[name="order_id"]').input_value()
                page.locator('input[name="SYNTHETIC-CHEONGJU"]').focus()
                page.keyboard.press('ControlOrMeta+A')
                page.keyboard.type('2')
                page.keyboard.press('Tab')
                assert page.locator('input[name="SYNTHETIC-TAKJU"]').evaluate(
                    '(el) => document.activeElement === el')
                page.keyboard.press('ControlOrMeta+A')
                page.keyboard.type('1')
                page.keyboard.press('Tab')
                assert page.get_by_role('button', name='선택한 수량 확인').evaluate(
                    '(el) => document.activeElement === el')
                with page.expect_navigation() as navigation:
                    page.keyboard.press('Enter')
                private_ok(navigation.value)
                check(page, '가상 수량 확인')
                assert '가상 청주 · 2개' in page.locator('main').inner_text()
                assert '가상 탁주 · 1개' in page.locator('main').inner_text()
                with cafe24.database() as conn:
                    assert conn.execute('SELECT count(*) FROM ourisul_test_orders').fetchone()[0] == 0
                page.keyboard.press('Tab')
                assert page.get_by_role('button', name='이 가상 수량 기록').evaluate(
                    '(el) => document.activeElement === el')
                with page.expect_navigation() as navigation:
                    page.keyboard.press('Enter')
                private_ok(navigation.value)
                check(page, '가상 기록 확인')
                assert order_id in page.locator('main').inner_text()
                assert page.url.endswith('/receipt')
                receipt_url = page.url
                assert '실제 Slack 수신이나 배송을 뜻하지 않습니다' in page.locator('main').inner_text()
                with cafe24.database() as conn:
                    assert conn.execute('SELECT order_id FROM ourisul_test_orders').fetchall() == [(order_id,)]
                    assert conn.execute('SELECT item_id, quantity FROM ourisul_test_order_item '
                                        'ORDER BY item_id').fetchall() == [
                                            ('SYNTHETIC-CHEONGJU', 2), ('SYNTHETIC-TAKJU', 1)]
                    assert conn.execute('SELECT status, version FROM ourisul_test_order_state').fetchall() == [
                        ('TEST_CREATED', 0)]
                    assert conn.execute('SELECT to_status FROM ourisul_test_order_event').fetchall() == [
                        ('TEST_CREATED',)]
                    assert conn.execute('SELECT source, status, delivered_at, attempts '
                                        'FROM ourisul_order_notice WHERE order_id = %s',
                                        (order_id,)).fetchall() == [('synthetic', 'TEST_CREATED', None, 0)]
                # The browser reads the same exact outbox row that the real synthetic
                # sender claims. Only its HTTP transport is replaced with a fake.
                timeline_url = base + f'/admin/orders/test/{order_id}/history?view=html'
                snapshot_url = base + f'/admin/orders/test/{order_id}/history?view=snapshot'
                webhook = 'https://hooks.slack.com/services/VERIFY/VERIFY/SYNTHETIC'
                posts = []

                def fake_post(url, **kwargs):
                    posts.append((url, kwargs))
                    if len(posts) == 1:
                        raise requests.Timeout('synthetic verifier timeout')
                    return SimpleNamespace(status_code=200, text='ok')

                notice_labels = {'ready': '발송 대기', 'deferred': '재시도 대기',
                                 'delivered': '전송 기록 완료'}

                def notice_view(state, attempts, time_key=None, time_label=None):
                    private_ok(page.goto(timeline_url))
                    check(page, '합성 주문 배송 상태')
                    assert f'{state} ({notice_labels[state]})' in page.locator('dl').inner_text()
                    assert f'{attempts}회' in page.locator('dl').inner_text()
                    assert '실제 Slack 수신을 보장하지 않습니다' in page.locator('main').inner_text()
                    snapshot_response = page.goto(snapshot_url)
                    private_ok(snapshot_response)
                    notice = snapshot_response.json()['notice']
                    assert (notice['state'], notice['attempts']) == (state, attempts)
                    private_ok(page.goto(timeline_url))
                    if time_key:
                        rendered_time = page.locator('dt', has_text=time_label).locator(
                            'xpath=following-sibling::dd[1]/time')
                        assert rendered_time.count() == 1
                        assert rendered_time.get_attribute('datetime') == notice[time_key]
                    return notice

                ready_rows = saved_rows(cafe24.database)
                notice_view('ready', 0)
                assert saved_rows(cafe24.database) == ready_rows
                with patch.dict(os.environ, {
                        'SYNTHETIC_ORDER_NOTIFICATIONS_ENABLED': 'true',
                        'SLACK_TEST_ORDER_WEBHOOK_URL': webhook,
                        'SLACK_ORDER_WEBHOOK_URL':
                            'https://hooks.slack.com/services/VERIFY/VERIFY/REAL'}):
                    assert notices.deliver_synthetic_pending(cafe24.database, order_id,
                                                             post=fake_post) == {
                                                                 'delivered': 0, 'failed': 1}
                    deferred = notice_view('deferred', 1, 'retry_at', '재시도 예정 시각')
                    assert deferred['retry_at']
                    deferred_rows = saved_rows(cafe24.database)
                    assert notices.deliver_synthetic_pending(cafe24.database, order_id,
                                                             post=fake_post) == {
                                                                 'delivered': 0, 'failed': 0}
                    assert len(posts) == 1
                    assert saved_rows(cafe24.database) == deferred_rows
                    with cafe24.database() as conn:
                        conn.execute('''UPDATE ourisul_order_notice
                            SET next_attempt_at = now() - interval '1 second'
                            WHERE source = 'synthetic' AND order_id = %s''', (order_id,))
                    assert notices.deliver_synthetic_pending(cafe24.database, order_id,
                                                             post=fake_post) == {
                                                                 'delivered': 1, 'failed': 0}
                    delivered = notice_view('delivered', 2, 'delivered_at', '전송 기록 시각')
                    assert delivered['delivered_at']
                    delivered_rows = saved_rows(cafe24.database)
                    for table in ready_rows:
                        if table != 'ourisul_order_notice':
                            assert delivered_rows[table] == ready_rows[table], table
                    assert notices.deliver_synthetic_pending(cafe24.database, order_id,
                                                             post=fake_post) == {
                                                                 'delivered': 0, 'failed': 0}
                    assert len(posts) == 2
                    assert saved_rows(cafe24.database) == delivered_rows
                for url, kwargs in posts:
                    assert url == webhook
                    assert kwargs == {'json': {'text': f'[테스트·미운영] {order_id}'},
                                      'timeout': (5, 15), 'allow_redirects': False}
                private_ok(page.goto(receipt_url))
                check(page, '가상 기록 확인')
                assert 'delivered (전송 기록 완료)' in page.locator('main').inner_text()
                assert '실제 Slack 수신이나 배송을 뜻하지 않습니다' in page.locator('main').inner_text()
                assert saved_rows(cafe24.database) == delivered_rows
                page.keyboard.press('Tab')
                assert page.get_by_role('link', name='합성 배송 상태 보기').evaluate(
                    '(el) => document.activeElement === el')
                with page.expect_navigation() as navigation:
                    page.keyboard.press('Enter')
                private_ok(navigation.value)
                for version, status, button in (
                        (0, 'TEST_CREATED', '합성 준비 완료'),
                        (1, 'TEST_PREPARED', '합성 발송 처리'),
                        (2, 'TEST_SHIPPED', '합성 배송 완료')):
                    check(page, '합성 주문 배송 상태')
                    assert page.locator('main p').filter(has_text='현재 상태:').locator('strong').inner_text() == status
                    page.keyboard.press('Tab')
                    assert page.get_by_role('button', name=button).evaluate(
                        '(el) => document.activeElement === el')
                    with page.expect_navigation() as navigation:
                        page.keyboard.press('Enter')
                    private_ok(navigation.value)
                    with cafe24.database() as conn:
                        assert conn.execute('SELECT status, version FROM ourisul_test_order_state '
                                            'WHERE order_id = %s', (order_id,)).fetchone() == (
                                                ('TEST_PREPARED', 'TEST_SHIPPED', 'TEST_DELIVERED')[version], version + 1)
                check(page, '합성 주문 배송 상태')
                assert page.locator('main p').filter(has_text='현재 상태:').locator('strong').inner_text() == 'TEST_DELIVERED'
                assert page.get_by_role('button').count() == 0
                with cafe24.database() as conn:
                    assert conn.execute('SELECT version, to_status FROM ourisul_test_order_event '
                                        'WHERE order_id = %s ORDER BY version', (order_id,)).fetchall() == [
                                            (0, 'TEST_CREATED'), (1, 'TEST_PREPARED'),
                                            (2, 'TEST_SHIPPED'), (3, 'TEST_DELIVERED')]

                # Exercise both connection-loss timings through the real browser POST.
                # After-commit mode writes only to this disposable schema.
                for failure in ('before_write', 'after_commit'):
                    response = page.goto(base + '/admin/orders/test/rehearsal')
                    private_ok(response)
                    check(page, '가상 주문 연습')
                    retry_id = page.locator('input[name="order_id"]').input_value()
                    page.locator('input[name="SYNTHETIC-CHEONGJU"]').fill('2')
                    page.locator('input[name="SYNTHETIC-TAKJU"]').fill('1')
                    with page.expect_navigation() as navigation:
                        page.get_by_role('button', name='선택한 수량 확인').click()
                    private_ok(navigation.value)
                    check(page, '가상 수량 확인')
                    assert retry_id == page.locator('input[name="order_id"]').input_value()
                    review_csrf = page.locator('input[name="csrf"]').input_value()
                    assert '가상 청주 · 2개' in page.locator('main').inner_text()
                    assert '가상 탁주 · 1개' in page.locator('main').inner_text()
                    before = saved_rows(cafe24.database)
                    original_record = test_orders.record_test_order

                    def disconnected(*args, **kwargs):
                        if failure == 'after_commit':
                            original_record(*args, **kwargs)
                        raise OperationalError('injected browser rehearsal failure')

                    with patch.object(test_orders, 'record_test_order', side_effect=disconnected):
                        with page.expect_navigation() as navigation:
                            page.get_by_role('button', name='이 가상 수량 기록').click()
                        failed = navigation.value
                    assert failed.status == 503, failed.url
                    assert failed.headers.get('cache-control') == 'no-store'
                    assert failed.headers.get('referrer-policy') == 'no-referrer'
                    check(page, '가상 기록을 지금 확인할 수 없습니다')
                    assert retry_id in page.locator('main').inner_text()
                    assert page.get_by_role('link', name='이 ID의 저장 기록 확인하기').get_attribute(
                        'href') == f'/admin/orders/test/{retry_id}/history?view=html'
                    after_failure = saved_rows(cafe24.database)
                    if failure == 'before_write':
                        assert after_failure == before, 'Failed pre-write request changed a ledger'
                    else:
                        assert after_failure != before, 'Ambiguous post-commit request did not persist'

                    # The recovery form replays the original reviewed request exactly.
                    assert retry_id == page.locator('form input[name="order_id"]').input_value()
                    assert review_csrf == page.locator('form input[name="csrf"]').input_value()
                    page.get_by_role('link', name='이 ID의 저장 기록 확인하기').focus()
                    page.keyboard.press('Tab')
                    assert page.get_by_role('button', name='같은 가상 기록 요청 다시 시도').evaluate(
                        '(el) => document.activeElement === el')
                    with page.expect_navigation() as navigation:
                        page.keyboard.press('Enter')
                    private_ok(navigation.value)
                    check(page, '가상 기록 확인')
                    assert retry_id in page.locator('main').inner_text()
                    assert '가상 청주 · 2개' in page.locator('main').inner_text()
                    assert '가상 탁주 · 1개' in page.locator('main').inner_text()
                    after_retry = saved_rows(cafe24.database)
                    if failure == 'after_commit':
                        assert after_retry == after_failure, 'Exact-ID retry duplicated a ledger row'
                    with cafe24.database() as conn:
                        assert conn.execute('SELECT count(*) FROM ourisul_test_orders WHERE order_id = %s',
                                            (retry_id,)).fetchone()[0] == 1
                        assert conn.execute('SELECT item_id, quantity FROM ourisul_test_order_item '
                                            'WHERE order_id = %s ORDER BY item_id', (retry_id,)).fetchall() == [
                                                ('SYNTHETIC-CHEONGJU', 2), ('SYNTHETIC-TAKJU', 1)]
                        assert conn.execute('SELECT status, version FROM ourisul_test_order_state '
                                            'WHERE order_id = %s', (retry_id,)).fetchall() == [
                                                ('TEST_CREATED', 0)]
                        assert conn.execute('SELECT to_status FROM ourisul_test_order_event '
                                            'WHERE order_id = %s', (retry_id,)).fetchall() == [
                                                ('TEST_CREATED',)]
                        assert conn.execute('SELECT source, status, delivered_at, attempts '
                                            'FROM ourisul_order_notice WHERE order_id = %s',
                                            (retry_id,)).fetchall() == [
                                                ('synthetic', 'TEST_CREATED', None, 0)]

                # Browser-rendered recovery pages, including a long ID and lookup retry.
                response = page.goto(base + '/admin/orders/test/lookup?order_id=invalid')
                assert response.status == 404
                check(page, '합성 주문을 찾을 수 없습니다')
                page.keyboard.press('Tab')
                assert page.get_by_role('link', name='합성 주문 다시 조회하기').evaluate(
                    '(el) => document.activeElement === el')
                with page.expect_navigation() as navigation:
                    page.keyboard.press('Enter')
                private_ok(navigation.value)
                check(page, '합성 주문 배송 조회')
                response = page.goto(base + '/admin/orders/test/rehearsal/' + 'TEST-' + 'f' * 32 + '/receipt')
                assert response.status == 404
                check(page, '가상 기록을 확인할 수 없습니다')

                # Saved channel records are fictional and live only in this
                # disposable schema. Browser reads must never change them.
                inbox_path = '/admin/orders/channels'
                with cafe24.database() as conn:
                    notices.channel_schema(conn)
                    for i in range(27):
                        conn.execute('''INSERT INTO ourisul_order_notice
                            (source, order_id, status, ordered_at, item_count, created_at)
                            VALUES (%s, %s, 'FIRST', '2026-10-01T00:00:00Z', 0, %s)''',
                            ('cafe24' if i % 2 else 'coupang', f'BROWSER-{i:02}',
                             datetime(2026, 10, 1, tzinfo=timezone.utc) + timedelta(seconds=i)))
                    conn.execute('''INSERT INTO ourisul_order_notice
                        (source, order_id, status, ordered_at, item_count)
                        VALUES ('synthetic', 'TEST-browser-inbox', 'TEST', '2026-10-01', 0)''')
                notices.record_orders('cafe24', [{'order_id': 'BROWSER-25', 'status': 'PACKED',
                    'ordered_at': '2026-10-01T00:00:00Z', 'item_count': 0}], cafe24.database)

                def channel_rows():
                    with cafe24.database() as conn:
                        return tuple(conn.execute('SELECT * FROM ' + table + ' ORDER BY ' + order).fetchall()
                                     for table, order in (
                                         ('ourisul_order_notice', 'source, order_id'),
                                         ('ourisul_channel_order_state', 'source, order_id'),
                                         ('ourisul_channel_order_event', 'id')))

                inbox_before = channel_rows()
                private_ok(page.goto(base + inbox_path))
                check(page, '채널 주문 기록')
                assert page.locator('tbody tr').count() == 25
                assert 'PACKED' in page.locator('main').inner_text()
                assert 'TEST-browser-inbox' not in page.locator('main').inner_text()
                first_ids = page.locator('tbody tr td:nth-child(2)').all_inner_texts()
                next_link = page.get_by_role('link', name='이전 기록 보기 →')
                next_href = next_link.get_attribute('href')
                assert next_href.startswith(inbox_path + '?cursor=')
                scroll_region = page.get_by_role('region', name='저장된 주문 요약')
                assert scroll_region.evaluate('(el) => el.scrollWidth > el.clientWidth')
                page.keyboard.press('Tab')
                assert scroll_region.evaluate('(el) => document.activeElement === el')
                page.keyboard.press('Tab')
                assert next_link.evaluate('(el) => document.activeElement === el')
                with page.expect_navigation() as navigation:
                    page.keyboard.press('Enter')
                private_ok(navigation.value)
                check(page, '채널 주문 기록')
                assert page.locator('tbody tr').count() == 2
                second_ids = page.locator('tbody tr td:nth-child(2)').all_inner_texts()
                assert len(set(first_ids + second_ids)) == 27
                assert set(first_ids + second_ids) == {f'BROWSER-{i:02}' for i in range(27)}
                assert page.get_by_role('link', name='이전 기록 보기 →').count() == 0
                first_link = page.get_by_role('link', name='처음 기록으로 돌아가기')
                assert first_link.get_attribute('href') == inbox_path
                scroll_region = page.get_by_role('region', name='저장된 주문 요약')
                assert scroll_region.evaluate('(el) => el.scrollWidth > el.clientWidth')
                page.keyboard.press('Tab')
                assert scroll_region.evaluate('(el) => document.activeElement === el')
                page.keyboard.press('Tab')
                assert first_link.evaluate('(el) => document.activeElement === el')
                with page.expect_navigation() as navigation:
                    page.keyboard.press('Enter')
                private_ok(navigation.value)
                check(page, '채널 주문 기록')
                assert page.locator('tbody tr td:nth-child(2)').all_inner_texts() == first_ids
                assert channel_rows() == inbox_before

                @contextmanager
                def failing_connect():
                    raise OperationalError('private browser database diagnostic')
                    yield

                @contextmanager
                def failing_read():
                    with cafe24.database() as conn:
                        class ConnectionProxy:
                            def execute(self, query, params=None):
                                if 'WITH page AS' in query:
                                    raise OperationalError('private browser database diagnostic')
                                return conn.execute(query, params)
                        yield ConnectionProxy()

                original_saved_orders = channel_inbox.saved_orders
                for path in (inbox_path, next_href):
                    for failed_connect in (failing_connect, failing_read):
                        with patch.object(channel_inbox, 'saved_orders',
                                          side_effect=lambda selected: original_saved_orders(
                                              selected, connect=failed_connect)):
                            failed = page.goto(base + path)
                        assert failed.status == 503, failed.url
                        assert failed.headers.get('cache-control') == 'no-store'
                        assert failed.headers.get('referrer-policy') == 'no-referrer'
                        check(page, '채널 주문 기록을 불러올 수 없습니다')
                        content = page.locator('main').inner_text()
                        assert '주문 기록이 없다는 뜻은 아닙니다' in content
                        assert 'private browser database diagnostic' not in page.content()
                        assert '저장된 채널 주문 기록이 없습니다' not in content
                        retry = page.get_by_role('link', name='이 페이지 다시 시도')
                        assert retry.get_attribute('href') == path
                        page.keyboard.press('Tab')
                        assert retry.evaluate('(el) => document.activeElement === el')
                        with page.expect_navigation() as navigation:
                            page.keyboard.press('Enter')
                        private_ok(navigation.value)
                        check(page, '채널 주문 기록')
                        assert page.locator('tbody tr').count() == (25 if path == inbox_path else 2)
                        assert channel_rows() == inbox_before

                assert not blocked_urls, 'Browser requested an external URL: ' + repr(blocked_urls)
                assert outbound_request.call_count == 0, 'Application attempted external HTTP'
                context.close()
                browser.close()
            report['passed'] = True
            report['checks'] = ['keyboard rehearsal and three transitions',
                                '320px document width on each page', 'lookup and receipt recovery',
                                'receipt-to-mock-sender failure, defer, retry and recorded delivery',
                                'pre-write and post-commit failure retry with five-ledger idempotency',
                                'channel inbox keyboard pagination and database recovery']
    except Exception as exc:
        report['error'] = type(exc).__name__ + ': ' + str(exc)
        traceback.print_exc()
    finally:
        if server is not None:
            server.shutdown()
        if thread is not None:
            thread.join(timeout=5)
        if created:
            try:
                import psycopg
                from psycopg import sql
                with psycopg.connect(dsn, connect_timeout=10) as conn:
                    conn.execute(sql.SQL('DROP SCHEMA {} CASCADE').format(sql.Identifier(schema_name)))
                report['isolated_schema_cleaned'] = True
            except Exception as exc:
                report['passed'] = False
                report['cleanup_error'] = type(exc).__name__
        Path('browser-verification-result.json').write_text(json.dumps(report, ensure_ascii=False, indent=2))
        print(json.dumps(report, ensure_ascii=False))
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    sys.exit(main())
