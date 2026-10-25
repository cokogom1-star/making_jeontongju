#!/usr/bin/env python3
"""Chromium rehearsal against a local Flask server and disposable PostgreSQL schema.

Run only with VERIFY_DATABASE_URL pointing at a dedicated verify/test database.
No production credentials, provider calls, or synthetic Slack sender are used.
"""
import json
import os
import secrets
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path
from threading import Thread
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
                assert not blocked_urls, 'Browser requested an external URL: ' + repr(blocked_urls)
                assert outbound_request.call_count == 0, 'Application attempted external HTTP'
                context.close()
                browser.close()
            report['passed'] = True
            report['checks'] = ['keyboard rehearsal and three transitions',
                                '320px document width on each page', 'lookup and receipt recovery']
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
