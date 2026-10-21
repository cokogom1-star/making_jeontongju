"""Administrator-created synthetic orders, with a durable optional test notice.

These rows are not purchases, payments, or fulfillment orders. The regular
Slack worker intentionally ignores the synthetic outbox source. A separately
enabled test sender can deliver these notices to the test webhook.
"""

import os
import re
import secrets

from flask import Blueprint, abort, redirect, render_template_string, request, session, url_for
from psycopg.errors import UndefinedTable

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
a{color:inherit}a:focus-visible,button:focus-visible,input:focus-visible{outline:3px solid #7b421f;outline-offset:3px}
small{display:block;color:#625b51}input{width:100%}
dl{margin:8px 0 20px}dt{font-weight:600}dd{margin:0 0 8px;overflow-wrap:anywhere}
</style>'''

TRACKING_FORM = ('''<!doctype html><html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex,nofollow"><title>합성 주문 조회 | 우리술</title>'''
    + TRACKING_STYLE + '''</head><body><main><h1>합성 주문 배송 조회</h1>
<p>관리자 전용 미운영 시연입니다. 실제 주문·배송 정보가 아닙니다.</p>
<section aria-labelledby="pending-heading">
<h2 id="pending-heading">테스트 Slack 미전송 알림</h2>
<p>합성 주문에 연결된 미전송 알림의 현재 집계입니다. 조회만 하며 발송하지 않습니다.</p>
<dl>
<dt>발송 대기 (ready)</dt><dd>{{pending.ready}}건</dd>
<dt>재시도 대기 (deferred)</dt><dd>{{pending.deferred}}건</dd>
<dt>발송 작업 점유 중 (leased)</dt><dd>{{pending.leased}}건</dd>
<dt>미전송 합계 (total)</dt><dd>{{pending.total}}건</dd>
</dl>
</section>
<form method="get" action="{{url_for('test_orders.lookup_test_order')}}">
<label for="order_id">합성 주문 ID</label>
<input id="order_id" name="order_id" required autocomplete="off"
pattern="TEST-[0-9a-f]{32}" placeholder="TEST-…">
<button type="submit">상태 조회</button></form>
<form method="post" action="{{url_for('test_orders.create_test_order', view='html')}}">
<input type="hidden" name="csrf" value="{{csrf}}">
<input type="hidden" name="order_id" value="{{order_id}}">
<button type="submit">새 합성 주문 기록</button></form>
<section aria-labelledby="scenario-heading"><h2 id="scenario-heading">가상 품목 수량 기록</h2>
<p>관리자 전용 가상 장바구니입니다. 실제 판매 상품·재고·가격·결제·배송과 연결되지 않습니다.</p>
<p><a href="{{url_for('test_orders.start_rehearsal')}}">선택·확인·기록 단계로 연습하기</a></p>
<form method="post" action="{{url_for('test_orders.create_test_order', view='html')}}">
<input type="hidden" name="csrf" value="{{csrf}}">
<input type="hidden" name="order_id" value="{{scenario_order_id}}">
<input type="hidden" name="scenario" value="fixed-v1">
<label>가상 청주 <input type="number" name="SYNTHETIC-CHEONGJU" min="0" max="10" step="1" value="1" required></label>
<label>가상 탁주 <input type="number" name="SYNTHETIC-TAKJU" min="0" max="10" step="1" value="0" required></label>
<button type="submit">가상 수량 기록하고 추적하기</button></form></section>
</main></body></html>''')

TRACKING_HISTORY = ('''<!doctype html><html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex,nofollow"><title>합성 배송 상태 | 우리술</title>'''
    + TRACKING_STYLE + '''</head><body><main><h1>합성 주문 배송 상태</h1>
<p>관리자 전용 미운영 시연입니다. 실제 주문·배송 정보가 아닙니다.</p>
<p>주문 ID: <strong>{{history.order_id}}</strong></p>
<p>현재 상태: <strong>{{history.status}}</strong> (버전 {{history.version}})</p>
<h2>가상 품목 기록</h2>{% if scenario_items %}<ul>{% for item in scenario_items %}
<li>{{item.name}} ({{item.id}}) · {{item.quantity}}개</li>{% endfor %}</ul>{% else %}
<p>가상 품목 없음 (기존 0개 합성 주문)</p>{% endif %}
<h2>테스트 Slack 알림</h2>
<dl><dt>상태</dt><dd><strong>{{notice.state}}</strong> ({{notice_label}})</dd>
{% if notice.attempts is defined and notice.attempts is not none %}
<dt>발송 작업 시도 횟수</dt><dd>{{notice.attempts}}회 (작업 점유 포함)</dd>{% endif %}
{% if notice.state == 'deferred' and notice.retry_at is defined and notice.retry_at is not none %}
<dt>재시도 예정 시각</dt><dd><time datetime="{{notice.retry_at}}">{{notice.retry_at}}</time></dd>{% endif %}
{% if notice.state == 'leased' and notice.lease_until is defined and notice.lease_until is not none %}
<dt>발송 점유 만료 시각</dt><dd><time datetime="{{notice.lease_until}}">{{notice.lease_until}}</time></dd>{% endif %}
{% if notice.state == 'delivered' and notice.delivered_at is defined and notice.delivered_at is not none %}
<dt>전송 기록 시각</dt><dd><time datetime="{{notice.delivered_at}}">{{notice.delivered_at}}</time></dd>{% endif %}
</dl>
<small>시각은 각 값에 표시된 ISO 시간대 오프셋을 따릅니다. 배송 이력·가상 품목·알림은 같은 조회 시점의 기록입니다. ready는 발송 전 대기 상태이며,
delivered도 대기열의 전송 기록일 뿐 실제 Slack 수신을 보장하지 않습니다.</small>
<h2>변경 이력</h2><ol>{% for event in history.events %}
<li>{{event.to_status}}<small>버전 {{event.version}} · {{event.created_at}}</small></li>
{% endfor %}</ol>{% if next_status %}
<form method="post" action="{{url_for('test_orders.test_order_history', order_id=history.order_id, view='html')}}">
<input type="hidden" name="csrf" value="{{csrf}}">
<input type="hidden" name="status" value="{{next_status}}">
<input type="hidden" name="expected_version" value="{{history.version}}">
<input type="hidden" name="request_key" value="{{request_key}}">
<button type="submit">{{next_label}}</button></form>{% else %}
<p>합성 배송 시연이 완료되었습니다.</p>{% endif %}
<p><a href="{{url_for('test_orders.create_test_order', view='html')}}">다른 합성 주문 조회</a></p>
</main></body></html>''')

TRACKING_CONFLICT = ('''<!doctype html><html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex,nofollow"><title>합성 상태 변경 확인 | 우리술</title>'''
    + TRACKING_STYLE + '''</head><body><main><h1>합성 상태가 변경되었습니다</h1>
<p>최신 합성 주문 상태를 확인한 뒤 다시 시도해 주세요.</p>
<p><a href="{{url_for('test_orders.test_order_history', order_id=order_id, view='html')}}">최신 상태 보기</a></p>
</main></body></html>''')

TRACKING_CREATION_CONFLICT = ('''<!doctype html><html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex,nofollow"><title>가상 품목 기록 확인 | 우리술</title>'''
    + TRACKING_STYLE + '''</head><body><main><h1>이미 기록된 합성 주문입니다</h1>
<p>이 주문 ID에는 다른 가상 품목 수량이 저장되어 있습니다. 저장된 기록을 확인하거나 새 합성 주문을 시작해 주세요.</p>
<p><a href="{{url_for('test_orders.test_order_history', order_id=order_id, view='html')}}">저장된 주문 보기</a></p>
<p><a href="{{url_for('test_orders.create_test_order', view='html')}}">새 합성 주문 시작하기</a></p>
</main></body></html>''')

TRACKING_NOT_FOUND = ('''<!doctype html><html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex,nofollow"><title>합성 주문 조회 확인 | 우리술</title>'''
    + TRACKING_STYLE + '''</head><body><main><h1>합성 주문을 찾을 수 없습니다</h1>
<p>주문 ID를 확인하고 다시 조회해 주세요. 합성 주문 ID는 TEST-로 시작합니다.</p>
<p><a href="{{url_for('test_orders.create_test_order', view='html')}}">합성 주문 다시 조회하기</a></p>
</main></body></html>''')

REHEARSAL_FORM = ('''<!doctype html><html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex,nofollow"><title>가상 주문 연습 | 우리술</title>'''
    + TRACKING_STYLE + '''</head><body><main><h1>가상 주문 연습</h1>
<p>관리자 전용 시연입니다. 아래 품목은 허구이며 실제 상품·재고·가격·결제·배송과 연결되지 않습니다.</p>
<form method="post" action="{{url_for('test_orders.review_rehearsal')}}">
<input type="hidden" name="csrf" value="{{csrf}}">
<input type="hidden" name="order_id" value="{{order_id}}">
<input type="hidden" name="scenario" value="fixed-v1">
<label>가상 청주 <input type="number" name="SYNTHETIC-CHEONGJU" min="0" max="10" step="1" value="1" required></label>
<label>가상 탁주 <input type="number" name="SYNTHETIC-TAKJU" min="0" max="10" step="1" value="0" required></label>
<button type="submit">선택한 수량 확인</button></form>
<p><a href="{{url_for('test_orders.create_test_order', view='html')}}">관리자 합성 주문 조회</a></p>
</main></body></html>''')

REHEARSAL_REVIEW = ('''<!doctype html><html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex,nofollow"><title>가상 수량 확인 | 우리술</title>'''
    + TRACKING_STYLE + '''</head><body><main><h1>가상 수량 확인</h1>
<p>이 확인 단계에서는 기록하지 않습니다. 허구의 품목이며 실제 주문·결제·배송이 발생하지 않습니다.</p>
<ul>{% for item in items %}<li>{{item.name}} · {{item.quantity}}개</li>{% endfor %}</ul>
<form method="post" action="{{url_for('test_orders.confirm_rehearsal')}}">
<input type="hidden" name="csrf" value="{{csrf}}">
<input type="hidden" name="order_id" value="{{order_id}}">
<button type="submit">이 가상 수량 기록</button></form>
<p><a href="{{url_for('test_orders.start_rehearsal')}}">수량 다시 선택</a></p>
</main></body></html>''')

REHEARSAL_RECEIPT = ('''<!doctype html><html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex,nofollow"><title>가상 기록 확인 | 우리술</title>'''
    + TRACKING_STYLE + '''</head><body><main><h1>가상 기록 확인</h1>
<p>관리자 전용 시연 기록입니다. 실제 주문·결제·발송이 아닙니다.</p>
<p>합성 기록 ID: <strong>{{snapshot.order_id}}</strong></p>
<ul>{% for item in snapshot['items'] %}<li>{{item.name}} · {{item.quantity}}개</li>{% endfor %}</ul>
<p>시연 상태: <strong>{{snapshot.status}}</strong></p>
<p>테스트 알림 기록: <strong>{{snapshot.notice.state}}</strong> ({{notice_label}})</p>
<small>알림 상태는 대기열 기록이며 실제 Slack 수신이나 배송을 뜻하지 않습니다.</small>
<p><a href="{{url_for('test_orders.test_order_history', order_id=snapshot.order_id, view='html')}}">합성 배송 상태 보기</a></p>
<p><a href="{{url_for('test_orders.start_rehearsal')}}">새 가상 주문 연습</a></p>
</main></body></html>''')

REHEARSAL_RECOVERY = ('''<!doctype html><html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex,nofollow"><title>{{title}} | 우리술</title>'''
    + TRACKING_STYLE + '''</head><body><main><h1>{{title}}</h1>
<p>관리자 전용 가상 주문 시연입니다. 실제 주문·결제·배송과 연결되지 않습니다.</p>
<p>{{guidance}}</p>
<p>기록 버튼을 눌렀다면 저장 여부를 이 화면만으로 판단하지 마세요. 합성 주문 ID로 관리자 조회 화면에서 확인할 수 있습니다.</p>
<p><a href="{{url_for('test_orders.start_rehearsal')}}">새 가상 주문 연습 시작하기</a></p>
<p><a href="{{url_for('test_orders.create_test_order', view='html')}}">합성 주문 ID 조회하기</a></p>
</main></body></html>''')

REHEARSAL_ERRORS = {
    400: ('입력을 확인해 주세요', '수량 또는 요청 항목을 확인하고 새 연습 화면에서 다시 선택해 주세요.'),
    403: ('요청이 만료되었거나 확인할 수 없습니다',
          '이 브라우저에서 발급한 요청인지 확인할 수 없습니다. 새 연습 화면을 열어 다시 진행해 주세요.'),
    404: ('가상 기록을 확인할 수 없습니다',
          '이 브라우저의 연습 기록이 만료되었거나 해당 기록을 찾을 수 없습니다.'),
    409: ('가상 기록이 달라졌습니다',
          '앞서 확인한 수량과 다른 요청 또는 저장된 기록이 있습니다. 새 연습을 시작하거나 합성 주문 ID로 저장된 내용을 확인해 주세요.'),
    503: ('가상 기록을 지금 확인할 수 없습니다',
          '기록 조회를 현재 사용할 수 없습니다. 관리자에게 문의하고 합성 주문 ID를 보관해 주세요.'),
}


def rehearsal_error(status):
    title, guidance = REHEARSAL_ERRORS[status]
    return render_template_string(REHEARSAL_RECOVERY, title=title, guidance=guidance), status

NEXT_LABELS = {'TEST_PREPARED': '합성 준비 완료', 'TEST_SHIPPED': '합성 발송 처리',
               'TEST_DELIVERED': '합성 배송 완료'}
NOTICE_LABELS = {'ready': '발송 대기', 'deferred': '재시도 대기',
                 'leased': '발송 작업 점유 중', 'delivered': '전송 기록 완료',
                 'missing': '알림 기록 없음'}
SCENARIO_ITEMS = {'SYNTHETIC-CHEONGJU': '가상 청주', 'SYNTHETIC-TAKJU': '가상 탁주'}
RECENT_FORM_LIMIT = 8
REHEARSAL_LIMIT = 8


def parse_scenario(form):
    """Accept the exact two fixture fields from the protected admin form."""
    keys = set(form.keys())
    if any(len(form.getlist(key)) != 1 for key in keys):
        raise ValueError('Duplicate synthetic scenario field')
    keys.discard('order_id')
    if keys == {'csrf'}:
        return None  # Preserve the older zero-item admin form.
    if keys != {'csrf', 'scenario', *SCENARIO_ITEMS} or form['scenario'] != 'fixed-v1':
        raise ValueError('Invalid synthetic scenario fields')
    quantities = {}
    for item_id in SCENARIO_ITEMS:
        raw = form[item_id]
        if not re.fullmatch(r'(?:0|[1-9]|10)', raw):
            raise ValueError('Invalid synthetic scenario quantity')
        if int(raw):
            quantities[item_id] = int(raw)
    if not quantities:
        raise ValueError('Empty synthetic scenario')
    return quantities


def validated_scenario(scenario):
    if scenario is None:
        return None
    if (not isinstance(scenario, dict) or not scenario
            or any(item not in SCENARIO_ITEMS or type(qty) is not int or not 1 <= qty <= 10
                   for item, qty in scenario.items())):
        raise ValueError('Invalid synthetic scenario')
    return scenario


def remember_form_id(mode, order_id):
    """Keep a small set of rendered form IDs for safe cross-tab submissions."""
    key = 'test_scenario_order_ids' if mode == 'scenario' else 'test_order_ids'
    session[key] = (session.get(key, []) + [order_id])[-RECENT_FORM_LIMIT:]


def rehearsal_drafts():
    """Only server-issued IDs and reviewed baskets from this signed session."""
    return session.get('test_rehearsal_drafts', {})


def valid_rehearsal_csrf():
    token = session.get('test_order_csrf', '')
    submitted = request.form.get('csrf', '')
    if not isinstance(token, str) or not isinstance(submitted, str) or not token:
        return False
    try:
        return secrets.compare_digest(token.encode('ascii'), submitted.encode('ascii'))
    except UnicodeEncodeError:
        return False


def submitted_form_id(scenario):
    """Resolve a submitted ID only from the matching form mode in this session."""
    mode = 'scenario' if scenario is not None else 'empty'
    key = 'test_scenario_order_ids' if mode == 'scenario' else 'test_order_ids'
    order_id = request.form.get('order_id')
    if order_id is None:
        # Preserve only the original zero-item JSON request shape.
        if request.args.get('view') == 'html' or scenario is not None:
            abort(403)
        order_id = session.get('test_order_id', '')
    elif (not re.fullmatch(r'TEST-[0-9a-f]{32}', order_id)
          or order_id not in session.get(key, [])):
        abort(403)
    if not order_id:
        abort(403)
    return order_id


def schema(conn):
    conn.execute('''CREATE TABLE IF NOT EXISTS ourisul_test_orders (
        order_id TEXT PRIMARY KEY,
        status TEXT NOT NULL CHECK (status = 'TEST_CREATED'),
        item_count INTEGER NOT NULL CHECK (item_count = 0),
        created_at TIMESTAMPTZ NOT NULL DEFAULT now()
    )''')
    conn.execute('''CREATE TABLE IF NOT EXISTS ourisul_test_order_item (
        order_id TEXT NOT NULL REFERENCES ourisul_test_orders(order_id),
        item_id TEXT NOT NULL CHECK (item_id IN ('SYNTHETIC-CHEONGJU', 'SYNTHETIC-TAKJU')),
        item_label TEXT NOT NULL,
        quantity INTEGER NOT NULL CHECK (quantity BETWEEN 1 AND 10),
        PRIMARY KEY (order_id, item_id)
    )''')


def record_test_order(order_id, connect=database, scenario=None):
    """Save an empty synthetic order and its notice in one transaction.

    Repeating an ID returns False, restoring a missing notice for an exact replay.
    """
    if not isinstance(order_id, str) or not re.fullmatch(r'TEST-[0-9a-f]{32}', order_id):
        raise ValueError('Invalid synthetic order ID')
    scenario = validated_scenario(scenario)
    with connect() as conn:
        schema(conn)
        notices.schema(conn)
        result = conn.execute('''INSERT INTO ourisul_test_orders (order_id, status, item_count)
            VALUES (%s, 'TEST_CREATED', 0) ON CONFLICT (order_id) DO NOTHING''', (order_id,))
        if result.rowcount:
            for item_id, quantity in (scenario or {}).items():
                conn.execute('''INSERT INTO ourisul_test_order_item (order_id, item_id, item_label, quantity)
                    VALUES (%s, %s, %s, %s)''', (order_id, item_id, SCENARIO_ITEMS[item_id], quantity))
        else:
            stored = dict(conn.execute('''SELECT item_id, quantity FROM ourisul_test_order_item
                WHERE order_id = %s''', (order_id,)).fetchall())
            if stored != (scenario or {}):
                raise ValueError('Synthetic scenario conflicts with existing order')
        # Exact replays may repair a missing outbox row. A pre-existing row is
        # never updated, so delivery, retry and lease state remain intact.
        conn.execute('''INSERT INTO ourisul_order_notice
            (source, order_id, status, ordered_at, item_count)
            SELECT %s, order_id, 'TEST_CREATED',
                to_char(created_at AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS"Z"'), 0
            FROM ourisul_test_orders WHERE order_id = %s
            ON CONFLICT (source, order_id) DO NOTHING''', (SOURCE, order_id))
        fulfillment.initialize(conn, order_id)
        return result.rowcount == 1


def scenario_items(order_id, connect=database):
    """Read stored fictional fixtures without creating schema or changing state."""
    with connect() as conn:
        return scenario_items_in_connection(conn, order_id)


def scenario_items_in_connection(conn, order_id):
    if conn.execute("SELECT to_regclass('ourisul_test_order_item')").fetchone()[0] is None:
        raise ValueError('Synthetic item table not initialized')
    rows = conn.execute('''SELECT item_id, item_label, quantity FROM ourisul_test_order_item
        WHERE order_id = %s ORDER BY item_id''', (order_id,)).fetchall()
    return [{'id': item_id, 'name': item_label, 'quantity': quantity}
            for item_id, item_label, quantity in rows]


def order_snapshot(order_id, connect=database):
    """Read a complete admin rehearsal snapshot without creating or changing rows."""
    fulfillment._valid_order_id(order_id)
    try:
        with connect() as conn:
            conn.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY')
            result = fulfillment.history_in_connection(conn, order_id)
            if result is None:
                return None
            result['items'] = scenario_items_in_connection(conn, order_id)
            result['notice'] = notices.synthetic_notice_status_in_connection(conn, order_id)
            result['is_test'] = True
            result['source'] = SOURCE
            return result
    except UndefinedTable as exc:
        raise ValueError('Synthetic order tables are not initialized') from exc


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


@bp.route('/admin/orders/test/rehearsal')
@protected
def start_rehearsal():
    """Issue a session-bound fictional basket without writing to the database."""
    if not session.get('test_order_csrf'):
        session['test_order_csrf'] = secrets.token_urlsafe(32)
    order_id = 'TEST-' + secrets.token_hex(16)
    drafts = rehearsal_drafts()
    ids = session.get('test_rehearsal_ids', [])
    ids = (ids + [order_id])[-REHEARSAL_LIMIT:]
    drafts[order_id] = None
    session['test_rehearsal_ids'] = ids
    session['test_rehearsal_drafts'] = {issued: drafts[issued] for issued in ids}
    return render_template_string(REHEARSAL_FORM, csrf=session['test_order_csrf'],
                                  order_id=order_id)


@bp.route('/admin/orders/test/rehearsal/review', methods=['POST'])
@protected
def review_rehearsal():
    """Validate the fixture basket and show a read-only confirmation step."""
    if not valid_rehearsal_csrf():
        return rehearsal_error(403)
    order_id = request.form.get('order_id', '')
    drafts = rehearsal_drafts()
    if not re.fullmatch(r'TEST-[0-9a-f]{32}', order_id) or order_id not in drafts:
        return rehearsal_error(403)
    try:
        scenario = parse_scenario(request.form)
    except ValueError:
        return rehearsal_error(400)
    if scenario is None:
        return rehearsal_error(400)
    if drafts[order_id] is not None and drafts[order_id] != scenario:
        return rehearsal_error(409)
    drafts[order_id] = scenario
    session['test_rehearsal_drafts'] = drafts
    items = [{'name': SCENARIO_ITEMS[item_id], 'quantity': quantity}
             for item_id, quantity in scenario.items()]
    return render_template_string(REHEARSAL_REVIEW, csrf=session['test_order_csrf'],
                                  order_id=order_id, items=items)


@bp.route('/admin/orders/test/rehearsal/confirm', methods=['POST'])
@protected
def confirm_rehearsal():
    """Persist exactly the reviewed basket through the existing atomic path."""
    if not valid_rehearsal_csrf():
        return rehearsal_error(403)
    if set(request.form.keys()) != {'csrf', 'order_id'} or any(
            len(request.form.getlist(key)) != 1 for key in request.form.keys()):
        return rehearsal_error(400)
    order_id = request.form['order_id']
    drafts = rehearsal_drafts()
    if not re.fullmatch(r'TEST-[0-9a-f]{32}', order_id) or order_id not in drafts:
        return rehearsal_error(403)
    scenario = drafts[order_id]
    if scenario is None:
        return rehearsal_error(403)
    try:
        record_test_order(order_id, scenario=scenario)
    except ValueError as exc:
        if str(exc) == 'Synthetic scenario conflicts with existing order':
            return rehearsal_error(409)
        return rehearsal_error(400)
    return redirect(url_for('test_orders.rehearsal_receipt', order_id=order_id), code=303)


@bp.route('/admin/orders/test/rehearsal/<order_id>/receipt')
@protected
def rehearsal_receipt(order_id):
    """Show only a session-owned fictional order; no status changes offered."""
    drafts = rehearsal_drafts()
    if not re.fullmatch(r'TEST-[0-9a-f]{32}', order_id) or drafts.get(order_id) is None:
        return rehearsal_error(404)
    try:
        snapshot = order_snapshot(order_id)
    except ValueError as exc:
        if str(exc) in ('Synthetic order tables are not initialized',
                        'Synthetic item table not initialized'):
            return rehearsal_error(503)
        return rehearsal_error(404)
    if snapshot is None:
        return rehearsal_error(404)
    if {item['id']: item['quantity'] for item in snapshot['items']} != drafts[order_id]:
        return rehearsal_error(409)
    return render_template_string(REHEARSAL_RECEIPT, snapshot=snapshot,
                                  notice_label=NOTICE_LABELS.get(snapshot['notice'].get('state'),
                                                                 '알 수 없는 상태'))


@bp.route('/admin/orders/test', methods=['GET', 'POST'])
@protected
def create_test_order():
    if request.method == 'GET':
        if not session.get('test_order_csrf'):
            session['test_order_csrf'] = secrets.token_urlsafe(32)
        session['test_order_id'] = 'TEST-' + secrets.token_hex(16)
        session['test_scenario_order_id'] = 'TEST-' + secrets.token_hex(16)
        remember_form_id('empty', session['test_order_id'])
        remember_form_id('scenario', session['test_scenario_order_id'])
        if request.args.get('view') == 'html':
            try:
                pending = notices.synthetic_pending_counts(database)
            except ValueError as exc:
                if str(exc) != 'Synthetic order tables are not initialized':
                    raise
                abort(503)
            return render_template_string(TRACKING_FORM, csrf=session['test_order_csrf'],
                                          order_id=session['test_order_id'],
                                          scenario_order_id=session['test_scenario_order_id'],
                                          pending=pending)
        return render_template_string('''<!doctype html><html lang="ko"><meta charset="utf-8">
<meta name="robots" content="noindex,nofollow"><title>합성 주문 테스트</title>
<h1>합성 주문 테스트</h1><p>고객·상품·결제·배송이 없는 테스트 주문을 DB에 저장합니다.
Slack 알림은 대기열에 기록되며, 별도 설정된 테스트 발송기만 전송할 수 있습니다.</p>
<form method="post"><input type="hidden" name="csrf" value="{{csrf}}">
<input type="hidden" name="order_id" value="{{order_id}}">
<button type="submit">합성 주문 기록</button></form></html>''',
                                      csrf=session['test_order_csrf'], order_id=session['test_order_id'])
    token = session.get('test_order_csrf', '')
    if not token or not secrets.compare_digest(token, request.form.get('csrf', '')):
        abort(403)
    try:
        scenario = parse_scenario(request.form)
        order_id = submitted_form_id(scenario)
        created = record_test_order(order_id, scenario=scenario)
    except ValueError as exc:
        if str(exc) == 'Synthetic scenario conflicts with existing order':
            if request.args.get('view') == 'html':
                return render_template_string(TRACKING_CREATION_CONFLICT, order_id=order_id), 409
            abort(409)
        abort(400)
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
        return render_template_string(TRACKING_NOT_FOUND), 404
    return redirect(url_for('test_orders.test_order_history', order_id=order_id,
                            view='html'), code=303)


@bp.route('/admin/orders/test/<order_id>/history', methods=['GET', 'POST'])
@protected
def test_order_history(order_id):
    """Read or advance a simulated delivery status; no carrier is contacted."""
    if request.method == 'GET':
        view = request.args.get('view')
        if view in ('snapshot', 'html'):
            try:
                snapshot = order_snapshot(order_id)
            except ValueError as exc:
                if str(exc) in ('Synthetic order tables are not initialized',
                                'Synthetic item table not initialized'):
                    abort(503)
                if view == 'html':
                    return render_template_string(TRACKING_NOT_FOUND), 404
                abort(404)
            if snapshot is None:
                if view == 'html':
                    return render_template_string(TRACKING_NOT_FOUND), 404
                abort(404)
            if view == 'snapshot':
                return snapshot
            if not session.get('test_order_csrf'):
                session['test_order_csrf'] = secrets.token_urlsafe(32)
            next_status = fulfillment.STEPS.get(snapshot['status'])
            notice = snapshot['notice']
            return render_template_string(TRACKING_HISTORY, history=snapshot, notice=notice,
                                          scenario_items=snapshot['items'],
                                          notice_label=NOTICE_LABELS.get(notice.get('state'),
                                                                         '알 수 없는 상태'),
                                          next_status=next_status,
                                          next_label=NEXT_LABELS.get(next_status),
                                          csrf=session['test_order_csrf'],
                                          request_key=secrets.token_urlsafe(24))
        try:
            result = fulfillment.history(order_id)
        except ValueError:
            abort(404)
        if result is None:
            abort(404)
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
            if request.args.get('view') == 'html':
                return render_template_string(TRACKING_CONFLICT, order_id=order_id), 409
            abort(409)
        abort(400)
    if request.args.get('view') == 'html':
        return redirect(url_for('test_orders.test_order_history', order_id=order_id,
                                view='html'), code=303)
    return result

