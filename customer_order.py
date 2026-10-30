"""Session-owned, fictional customer order rehearsal; never a purchase."""

import os
import re
import secrets

import psycopg
from psycopg.conninfo import conninfo_to_dict
from flask import Blueprint, abort, redirect, render_template_string, request, session, url_for

from test_orders import SCENARIO_ITEMS, TRACKING_STYLE, order_snapshot, record_test_order

bp = Blueprint('customer_order', __name__)
ID = re.compile(r'TEST-[0-9a-f]{32}\Z')
MAX_OWNED = 8


def enabled():
    if (os.getenv('CUSTOMER_ORDER_REHEARSAL_ENABLED') != 'true'
            or os.getenv('SYNTHETIC_ORDER_TEST_ENABLED') != 'true'
            or os.getenv('STORE_LIVE', '').lower() == 'true'
            or not os.getenv('FLASK_SECRET_KEY')):
        return False
    sandbox, production = os.getenv('CUSTOMER_REHEARSAL_DATABASE_URL'), os.getenv('DATABASE_URL')
    if not sandbox or not production:
        return False
    try:
        test = conninfo_to_dict(sandbox)
        live = conninfo_to_dict(production)
        db = (test.get('dbname') or '').lower()
        # A separate database is required. A different schema on the same DB
        # would still grant the public flow access to production credentials.
        return (bool(re.search(r'(?:sandbox|verify|test)', db))
                and bool(test.get('host') and test.get('user'))
                and not test.get('service') and not live.get('service')
                and db != (live.get('dbname') or '').lower())
    except (ValueError, TypeError, AttributeError, psycopg.Error):
        return False


def sandbox_database():
    if not enabled():
        abort(404)
    return psycopg.connect(os.environ['CUSTOMER_REHEARSAL_DATABASE_URL'], connect_timeout=10)


@bp.before_request
def require_rehearsal():
    if not enabled():
        abort(404)


@bp.after_request
def private(response):
    response.headers['Cache-Control'] = 'no-store'
    response.headers['Referrer-Policy'] = 'no-referrer'
    response.headers['X-Content-Type-Options'] = 'nosniff'
    return response


PAGE = ('''<!doctype html><html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex,nofollow"><title>{{title}} | 우리술</title>'''
        + TRACKING_STYLE + '''</head><body><main><h1>{{title}}</h1>
<p>가상 주문 연습입니다. 실제 상품·가격·재고·결제·배송과 연결되지 않으며 개인 정보를 입력하지 않습니다.</p>
{% if stage == 'choose' %}
<form method="post" action="{{url_for('customer_order.review')}}">
<input type="hidden" name="csrf" value="{{draft.csrf}}">
<input type="hidden" name="order_id" value="{{draft.id}}">
<p id="quantity-help">각 가상 품목은 0~10개, 적어도 한 품목은 1개 이상 선택하세요.</p>
<label>가상 청주 <input type="number" name="SYNTHETIC-CHEONGJU" min="0" max="10" step="1" value="1" required aria-describedby="quantity-help"></label>
<label>가상 탁주 <input type="number" name="SYNTHETIC-TAKJU" min="0" max="10" step="1" value="0" required aria-describedby="quantity-help"></label>
<button>선택한 가상 수량 확인</button></form>
<form method="get" action="{{url_for('customer_order.lookup')}}">
<label for="order_id">이 브라우저에서 기록한 가상 주문 ID 조회</label>
<input id="order_id" name="order_id" pattern="TEST-[0-9a-f]{32}" required autocomplete="off" placeholder="TEST-…">
<button>기록 조회</button></form>
{% elif stage == 'review' %}
<p>아직 저장되지 않았습니다. 수량을 확인한 뒤 기록하세요.</p>
<ul>{% for item in items %}<li>{{item.name}} · {{item.quantity}}개</li>{% endfor %}</ul>
<form method="post" action="{{url_for('customer_order.confirm')}}">
<input type="hidden" name="csrf" value="{{draft.csrf}}"><input type="hidden" name="order_id" value="{{draft.id}}">
<button>이 가상 주문 기록</button></form>
<p><a href="{{url_for('customer_order.start')}}">수량 다시 선택</a></p>
{% elif stage == 'receipt' %}
<p>가상 기록 ID: <strong>{{snapshot.order_id}}</strong></p>
<ul>{% for item in snapshot['items'] %}<li>{{item.name}} · {{item.quantity}}개</li>{% endfor %}</ul>
<p>시연 상태: <strong>{{snapshot.status}}</strong></p>
<h2>가상 상태 이력</h2><ol>{% for event in snapshot.events %}
<li>{{event.to_status}}<small>버전 {{event.version}} · {{event.created_at}}</small></li>
{% endfor %}</ol>
<p>이 상태는 관리자 시뮬레이션 기록이며 실제 발송 또는 수령을 의미하지 않습니다.</p>
<p><a href="{{url_for('customer_order.start')}}">다른 가상 주문 연습</a></p>
{% else %}
<p>{{guidance}}</p>{% if order_id %}<p>가상 기록 ID: <strong>{{order_id}}</strong></p>
<p><a href="{{url_for('customer_order.receipt', order_id=order_id)}}">이 ID의 기록 다시 확인</a></p>{% endif %}
<p><a href="{{url_for('customer_order.start')}}">새 연습 시작</a></p>
{% endif %}</main></body></html>''')


def show(title, stage, status=200, **values):
    return render_template_string(PAGE, title=title, stage=stage, **values), status


def error(status, guidance, order_id=None):
    owned = session.get('customer_order_ids', [])
    draft = session.get('customer_draft') or {}
    return show('가상 주문을 확인해 주세요', 'error', status, guidance=guidance,
                order_id=order_id if order_id in owned or order_id == draft.get('id') else None)


def valid_csrf(draft):
    provided = request.form.get('csrf', '')
    try:
        return secrets.compare_digest(draft['csrf'].encode('ascii'), provided.encode('ascii'))
    except (UnicodeEncodeError, KeyError, AttributeError):
        return False


def parse_quantities():
    if set(request.form) != {'csrf', 'order_id', *SCENARIO_ITEMS}:
        raise ValueError('Unexpected fields')
    if any(len(request.form.getlist(key)) != 1 for key in request.form):
        raise ValueError('Duplicate fields')
    values = {}
    for key in SCENARIO_ITEMS:
        raw = request.form[key]
        if not re.fullmatch(r'(?:0|[1-9]|10)', raw):
            raise ValueError('Invalid quantity')
        if int(raw):
            values[key] = int(raw)
    if not values:
        raise ValueError('Empty basket')
    return values


def items_for(scenario):
    return [{'name': SCENARIO_ITEMS[key], 'quantity': quantity} for key, quantity in scenario.items()]


@bp.get('/order-rehearsal')
def start():
    draft = {'id': 'TEST-' + secrets.token_hex(16), 'csrf': secrets.token_urlsafe(32)}
    session['customer_draft'] = draft
    return show('가상 주문 연습', 'choose', draft=draft)


@bp.post('/order-rehearsal/review')
def review():
    draft = session.get('customer_draft') or {}
    if not valid_csrf(draft) or request.form.get('order_id') != draft.get('id'):
        return error(403, '이 브라우저의 선택 화면을 다시 열어 주세요.')
    try:
        scenario = parse_quantities()
    except ValueError:
        return error(400, '가상 품목 수량을 확인하고 다시 선택해 주세요.')
    if draft.get('scenario') is not None and draft['scenario'] != scenario:
        return error(409, '이미 확인한 수량과 다릅니다. 새 연습 화면에서 다시 선택해 주세요.')
    draft['scenario'] = scenario
    session['customer_draft'] = draft
    return show('가상 수량 확인', 'review', draft=draft, items=items_for(scenario))


@bp.post('/order-rehearsal/confirm')
def confirm():
    draft = session.get('customer_draft') or {}
    if (set(request.form) != {'csrf', 'order_id'}
            or any(len(request.form.getlist(key)) != 1 for key in request.form)
            or not valid_csrf(draft) or request.form.get('order_id') != draft.get('id')
            or not draft.get('scenario')):
        return error(403, '확인한 가상 수량이 없습니다. 선택 화면을 다시 열어 주세요.')
    order_id = draft['id']
    try:
        record_test_order(order_id, connect=sandbox_database, scenario=draft['scenario'])
    except psycopg.OperationalError:
        # Commit may have succeeded. Retain exactly the same ID and basket.
        return error(503, '저장 여부를 확인할 수 없습니다. 이 ID의 기록을 먼저 확인한 뒤 같은 요청을 다시 제출하세요.', order_id)
    except ValueError:
        return error(409, '저장된 가상 수량이 다릅니다. 새 연습을 시작해 주세요.')
    owned = session.get('customer_order_ids', [])
    session['customer_order_ids'] = ([old for old in owned if old != order_id] + [order_id])[-MAX_OWNED:]
    return redirect(url_for('.receipt', order_id=order_id), code=303)


@bp.get('/order-rehearsal/lookup')
def lookup():
    order_id = request.args.get('order_id', '')
    if len(request.args.getlist('order_id')) != 1 or not ID.fullmatch(order_id):
        return error(400, '가상 기록 ID 형식을 확인해 주세요.')
    if order_id not in session.get('customer_order_ids', []) and order_id != (session.get('customer_draft') or {}).get('id'):
        return error(404, '이 브라우저에서 만든 가상 기록만 조회할 수 있습니다.')
    return redirect(url_for('.receipt', order_id=order_id), code=303)


@bp.get('/order-rehearsal/<order_id>/receipt')
def receipt(order_id):
    if (not ID.fullmatch(order_id)
            or (order_id not in session.get('customer_order_ids', [])
                and order_id != (session.get('customer_draft') or {}).get('id'))):
        return error(404, '이 브라우저에서 만든 가상 기록만 조회할 수 있습니다.')
    try:
        snapshot = order_snapshot(order_id, connect=sandbox_database)
    except (psycopg.OperationalError, ValueError):
        return error(503, '지금 기록을 조회할 수 없습니다. 잠시 후 같은 ID로 다시 확인해 주세요.', order_id)
    if snapshot is None:
        return error(404, '기록이 확인되지 않습니다. 저장을 시도했다면 같은 요청을 다시 제출해 주세요.', order_id)
    return show('가상 주문 기록', 'receipt', snapshot=snapshot)
