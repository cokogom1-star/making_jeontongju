"""Administrator-created synthetic orders, with a durable optional test notice.

These rows are not purchases, payments, or fulfillment orders. The regular
Slack worker intentionally ignores the synthetic outbox source. A separately
enabled test sender can deliver these notices to the test webhook.
"""

import os
import re
import secrets

from flask import Blueprint, abort, render_template_string, request, session

from cafe24 import database, protected
import order_notifications as notices


bp = Blueprint('test_orders', __name__)
SOURCE = 'synthetic'


def schema(conn):
    conn.execute('''CREATE TABLE IF NOT EXISTS ourisul_test_orders (
        order_id TEXT PRIMARY KEY,
        status TEXT NOT NULL CHECK (status = 'TEST_CREATED'),
        item_count INTEGER NOT NULL CHECK (item_count = 0),
        created_at TIMESTAMPTZ NOT NULL DEFAULT now()
    )''')


def record_test_order(order_id, connect=database):
    """Save an empty synthetic order and its notice in one transaction.

    Repeating an ID returns False without creating another order or notice.
    """
    if not isinstance(order_id, str) or not re.fullmatch(r'TEST-[0-9a-f]{32}', order_id):
        raise ValueError('Invalid synthetic order ID')
    with connect() as conn:
        schema(conn)
        notices.schema(conn)
        result = conn.execute('''INSERT INTO ourisul_test_orders (order_id, status, item_count)
            VALUES (%s, 'TEST_CREATED', 0) ON CONFLICT (order_id) DO NOTHING''', (order_id,))
        conn.execute('''INSERT INTO ourisul_order_notice
            (source, order_id, status, ordered_at, item_count)
            VALUES (%s, %s, 'TEST_CREATED', to_char(now() AT TIME ZONE 'UTC',
                    'YYYY-MM-DD"T"HH24:MI:SS"Z"'), 0)
            ON CONFLICT (source, order_id) DO NOTHING''', (SOURCE, order_id))
        return result.rowcount == 1


@bp.after_request
def private_headers(response):
    response.headers['Cache-Control'] = 'no-store'
    response.headers['Referrer-Policy'] = 'no-referrer'
    response.headers['X-Content-Type-Options'] = 'nosniff'
    return response


@bp.before_request
def hide_disabled_test_orders():
    if os.environ.get('SYNTHETIC_ORDER_TEST_ENABLED') != 'true':
        abort(404)


@bp.route('/admin/orders/test', methods=['GET', 'POST'])
@protected
def create_test_order():
    if request.method == 'GET':
        session['test_order_csrf'] = secrets.token_urlsafe(32)
        session['test_order_id'] = 'TEST-' + secrets.token_hex(16)
        return render_template_string('''<!doctype html><html lang="ko"><meta charset="utf-8">
<meta name="robots" content="noindex,nofollow"><title>합성 주문 테스트</title>
<h1>합성 주문 테스트</h1><p>고객·상품·결제·배송이 없는 테스트 주문을 DB에 저장합니다.
Slack 알림은 대기열에 기록되며, 별도 설정된 테스트 발송기만 전송할 수 있습니다.</p>
<form method="post"><input type="hidden" name="csrf" value="{{csrf}}">
<button type="submit">합성 주문 기록</button></form></html>''', csrf=session['test_order_csrf'])
    token = session.get('test_order_csrf', '')
    order_id = session.get('test_order_id', '')
    if not token or not secrets.compare_digest(token, request.form.get('csrf', '')) or not order_id:
        abort(403)
    created = record_test_order(order_id)
    return {'order_id': order_id, 'created': created, 'notice': 'queued_for_test_sender'}
