"""Administrator-created synthetic orders, with a durable optional test notice.

These rows are not purchases, payments, or fulfillment orders. The regular
Slack worker intentionally ignores the synthetic outbox source. A separately
enabled test sender can deliver these notices to the test webhook.
"""

import os
import re
import secrets

from flask import Blueprint, abort, redirect, render_template_string, request, session, url_for

from cafe24 import database, protected
import order_notifications as notices
import synthetic_order_history as fulfillment


bp = Blueprint('test_orders', __name__)
SOURCE = 'synthetic'

TRACKING_STYLE = '''<style>
*{box-sizing:border-box}body{max-width:720px;margin:0 auto;padding:24px 18px;
font:16px/1.6 system-ui,sans-serif;color:#24221e;background:#f6f2e9}
h1{font-size:clamp(1.5rem,5vw,2.2rem)}form{display:grid;gap:12px;margin:24px 0}
input,button{font:inherit;padding:10px;max-width:100%}button{cursor:pointer}
ol{padding-left:24px}li{padding:8px 0;border-bottom:1px solid #d8d1c4}
a{color:inherit}small{display:block;color:#625b51}input{width:100%}
</style>'''

TRACKING_FORM = ('''<!doctype html><html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex,nofollow"><title>합성 주문 조회 | 우리술</title>'''
    + TRACKING_STYLE + '''</head><body><main><h1>합성 주문 배송 조회</h1>
<p>관리자 전용 미운영 시연입니다. 실제 주문·배송 정보가 아닙니다.</p>
<form method="get" action="{{url_for('test_orders.lookup_test_order')}}">
<label for="order_id">합성 주문 ID</label>
<input id="order_id" name="order_id" required autocomplete="off"
pattern="TEST-[0-9a-f]{32}" placeholder="TEST-…">
<button type="submit">상태 조회</button></form>
<form method="post" action="{{url_for('test_orders.create_test_order', view='html')}}">
<input type="hidden" name="csrf" value="{{csrf}}">
<button type="submit">새 합성 주문 기록</button></form>
</main></body></html>''')

TRACKING_HISTORY = ('''<!doctype html><html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex,nofollow"><title>합성 배송 상태 | 우리술</title>'''
    + TRACKING_STYLE + '''</head><body><main><h1>합성 주문 배송 상태</h1>
<p>관리자 전용 미운영 시연입니다. 실제 주문·배송 정보가 아닙니다.</p>
<p>주문 ID: <strong>{{history.order_id}}</strong></p>
<p>현재 상태: <strong>{{history.status}}</strong> (버전 {{history.version}})</p>
<h2>변경 이력</h2><ol>{% for event in history.events %}
<li>{{event.to_status}}<small>버전 {{event.version}} · {{event.created_at}}</small></li>
{% endfor %}</ol><p><a href="{{url_for('test_orders.create_test_order')}}">다른 합성 주문 조회</a></p>
</main></body></html>''')


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
        if result.rowcount:
            conn.execute('''INSERT INTO ourisul_order_notice
                (source, order_id, status, ordered_at, item_count)
                VALUES (%s, %s, 'TEST_CREATED', to_char(now() AT TIME ZONE 'UTC',
                        'YYYY-MM-DD"T"HH24:MI:SS"Z"'), 0)
                ON CONFLICT (source, order_id) DO NOTHING''', (SOURCE, order_id))
        fulfillment.initialize(conn, order_id)
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
        if request.args.get('view') == 'html':
            return render_template_string(TRACKING_FORM, csrf=session['test_order_csrf'])
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
    if request.args.get('view') == 'html':
        return redirect(url_for('test_orders.test_order_history', order_id=order_id,
                                view='html'), code=303)
    return {'order_id': order_id, 'created': created, 'notice': 'queued_for_test_sender'}


@bp.route('/admin/orders/test/lookup')
@protected
def lookup_test_order():
    """Navigate to a read-only synthetic status page without changing the DB."""
    order_id = request.args.get('order_id', '')
    if not isinstance(order_id, str) or not re.fullmatch(r'TEST-[0-9a-f]{32}', order_id):
        abort(404)
    return redirect(url_for('test_orders.test_order_history', order_id=order_id,
                            view='html'), code=303)


@bp.route('/admin/orders/test/<order_id>/history', methods=['GET', 'POST'])
@protected
def test_order_history(order_id):
    """Read or advance a simulated delivery status; no carrier is contacted."""
    if request.method == 'GET':
        try:
            result = fulfillment.history(order_id)
        except ValueError:
            abort(404)
        if result is None:
            abort(404)
        if request.args.get('view') == 'html':
            return render_template_string(TRACKING_HISTORY, history=result)
        return result
    token = session.get('test_order_csrf', '')
    if not token or not secrets.compare_digest(token, request.form.get('csrf', '')):
        abort(403)
    try:
        expected_version = int(request.form.get('expected_version', ''))
        result = fulfillment.transition(order_id, request.form.get('status'),
                                        expected_version, request.form.get('request_key'))
    except (ValueError, TypeError) as exc:
        if str(exc) == 'Synthetic order not found':
            abort(404)
        if str(exc) in ('Stale synthetic order version', 'Invalid synthetic status transition',
                        'Request key reused for another status'):
            abort(409)
        abort(400)
    return result
