"""Single-store Cafe24 OAuth. Secrets are supplied only through environment variables."""
import hashlib
import hmac
import json
import os
import re
import secrets
import time
from datetime import date, datetime
from functools import wraps
from urllib.parse import urlencode

import requests
from cryptography.fernet import Fernet
from flask import Blueprint, Response, redirect, render_template_string, request, session

bp = Blueprint('cafe24', __name__)
REQUIRED = ('CAFE24_MALL_ID', 'CAFE24_CLIENT_ID', 'CAFE24_CLIENT_SECRET',
            'FLASK_SECRET_KEY', 'ADMIN_PASSWORD', 'DATABASE_URL', 'TOKEN_ENCRYPTION_KEY')
REDIRECT_URI = 'https://ourisul.onrender.com/oauth/callback'


def config_ok():
    return (all(os.environ.get(key) for key in REQUIRED)
            and re.fullmatch(r'[a-z0-9][a-z0-9-]*', os.environ['CAFE24_MALL_ID']) is not None)


def database():
    import psycopg
    return psycopg.connect(os.environ['DATABASE_URL'], connect_timeout=10)


def schema(conn):
    conn.execute('CREATE TABLE IF NOT EXISTS ourisul_oauth_state '
                 '(digest TEXT PRIMARY KEY, expires_at DOUBLE PRECISION NOT NULL)')
    conn.execute('CREATE TABLE IF NOT EXISTS ourisul_cafe24_token '
                 '(mall_id TEXT PRIMARY KEY, encrypted_token TEXT NOT NULL)')


def protected(fn):
    @wraps(fn)
    def wrapped(*args, **kwargs):
        if not config_ok():
            return '카페24 연결 설정을 준비 중입니다.', 503
        auth = request.authorization
        if not auth or not hmac.compare_digest(auth.username or '', 'admin') or not hmac.compare_digest(
                auth.password or '', os.environ['ADMIN_PASSWORD']):
            return Response('관리자 로그인이 필요합니다.', 401,
                            {'WWW-Authenticate': 'Basic realm="Ourisul admin"'})
        return fn(*args, **kwargs)
    return wrapped


def api_base():
    return 'https://' + os.environ['CAFE24_MALL_ID'] + '.cafe24api.com/api/v2'


def exchange(data):
    response = requests.post(api_base() + '/oauth/token', data=data,
                             auth=(os.environ['CAFE24_CLIENT_ID'], os.environ['CAFE24_CLIENT_SECRET']),
                             timeout=(5, 20))
    response.raise_for_status()
    token = response.json()
    if not isinstance(token, dict) or not token.get('access_token') or not token.get('refresh_token'):
        raise ValueError('Invalid token response')
    if token.get('mall_id') != os.environ['CAFE24_MALL_ID']:
        raise ValueError('Unexpected store')
    return token


def save_token(conn, token):
    encoded = Fernet(os.environ['TOKEN_ENCRYPTION_KEY']).encrypt(json.dumps(token).encode()).decode()
    conn.execute('INSERT INTO ourisul_cafe24_token (mall_id, encrypted_token) VALUES (%s,%s) '
                 'ON CONFLICT (mall_id) DO UPDATE SET encrypted_token = EXCLUDED.encrypted_token',
                 (os.environ['CAFE24_MALL_ID'], encoded))


def public_catalog(product_no=None):
    """Only expose products that Cafe24 marks as visible to shoppers."""
    if product_no is not None and (type(product_no) is not int or product_no <= 0):
        return []
    if not config_ok():
        return None
    with database() as conn:
        schema(conn)
        row = conn.execute('SELECT encrypted_token FROM ourisul_cafe24_token '
                           'WHERE mall_id = %s FOR UPDATE', (os.environ['CAFE24_MALL_ID'],)).fetchone()
        if not row:
            return None
        token = json.loads(Fernet(os.environ['TOKEN_ENCRYPTION_KEY']).decrypt(row[0].encode()))
        path = '/admin/products' + (('/' + str(product_no)) if product_no else '')
        params = None if product_no else {'limit': 24, 'display': 'T'}

        def fetch():
            return requests.get(api_base() + path,
                                headers={'Authorization': 'Bearer ' + token['access_token']},
                                params=params, timeout=(5, 20))

        response = fetch()
        if response.status_code == 401:
            token = exchange({'grant_type': 'refresh_token', 'refresh_token': token['refresh_token']})
            save_token(conn, token)
            conn.commit()
            response = fetch()
        if response.status_code == 404 and product_no:
            return []
        response.raise_for_status()
        data = response.json()
        raw = [data.get('product')] if product_no else data.get('products', [])
        return [p for p in raw if isinstance(p, dict) and p.get('display') == 'T']


def order_summaries(start_date, end_date, limit=100):
    """Read recent order metadata without retaining buyer or receiver details.

    Returns None when the store has not been connected. The caller supplies
    inclusive calendar dates in the Cafe24 store's timezone.
    """
    if isinstance(start_date, datetime):
        start_date = start_date.date()
    if isinstance(end_date, datetime):
        end_date = end_date.date()
    if (not isinstance(start_date, date) or not isinstance(end_date, date)
            or start_date > end_date or (end_date - start_date).days > 90
            or type(limit) is not int or not 1 <= limit <= 100):
        raise ValueError('Invalid order date range or limit')
    if not config_ok():
        return None
    with database() as conn:
        schema(conn)
        row = conn.execute('SELECT encrypted_token FROM ourisul_cafe24_token '
                           'WHERE mall_id = %s FOR UPDATE', (os.environ['CAFE24_MALL_ID'],)).fetchone()
        if not row:
            return None
        token = json.loads(Fernet(os.environ['TOKEN_ENCRYPTION_KEY']).decrypt(row[0].encode()))

        def fetch(offset):
            return requests.get(api_base() + '/admin/orders',
                                headers={'Authorization': 'Bearer ' + token['access_token']},
                                params={'start_date': start_date.isoformat(),
                                        'end_date': end_date.isoformat(),
                                        'limit': limit, 'offset': offset,
                                        'sort': 'order_date', 'order': 'desc',
                                        'embed': 'items'},
                                timeout=(5, 20))

        result = []
        offset = 0
        while True:
            response = fetch(offset)
            if response.status_code == 401:
                token = exchange({'grant_type': 'refresh_token', 'refresh_token': token['refresh_token']})
                save_token(conn, token)
                conn.commit()
                response = fetch(offset)
            response.raise_for_status()
            data = response.json()
            if not isinstance(data, dict) or not isinstance(data.get('orders'), list):
                raise ValueError('Invalid Cafe24 orders response')
            orders = data['orders']
            if len(orders) > limit:
                raise ValueError('Oversized Cafe24 orders response')
            for order in orders:
                if not isinstance(order, dict):
                    raise ValueError('Invalid Cafe24 order')
                order_id = order.get('order_id')
                ordered_at = order.get('order_date')
                if not isinstance(order_id, str) or not order_id.strip():
                    raise ValueError('Invalid Cafe24 order ID')
                if not isinstance(ordered_at, str) or not ordered_at.strip():
                    raise ValueError('Invalid Cafe24 order date')
                # Cafe24 payment_status: F/M awaiting payment; T/A/P paid.
                # C/R are cancellation/return states, not new actionable orders.
                payment = order.get('payment_status')
                status = order.get('order_status')
                if (payment not in ('T', 'A', 'P') or not isinstance(status, str)
                        or not status.startswith('N') or status == 'N00'):
                    continue
                items = order.get('items', [])
                if not isinstance(items, list):
                    raise ValueError('Invalid Cafe24 order items')
                result.append({'order_id': order_id,
                               'ordered_at': ordered_at,
                               'status': status,
                               'item_count': len(items)})
            if len(orders) < limit:
                break
            offset += limit
            if offset > 8000:
                raise ValueError('Cafe24 order window exceeds pagination limit')
        return result


@bp.after_request
def private_headers(response):
    response.headers['Cache-Control'] = 'no-store'
    response.headers['Referrer-Policy'] = 'no-referrer'
    response.headers['X-Content-Type-Options'] = 'nosniff'
    return response


@bp.route('/admin/cafe24', methods=['GET', 'POST'])
@protected
def connect():
    if request.method == 'GET':
        session['connect_csrf'] = secrets.token_urlsafe(32)
        return render_template_string('''<!doctype html><html lang="ko"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>우리술 연결</title>
<h1>카페24 연결</h1><p>상품 및 주문 조회 권한을 연결합니다.</p>
<form method="post"><input type="hidden" name="csrf" value="{{csrf}}">
<button>카페24에서 연결 승인하기</button></form></html>''', csrf=session['connect_csrf'])
    expected = session.pop('connect_csrf', '')
    if not expected or not hmac.compare_digest(expected, request.form.get('csrf', '')):
        return '연결 페이지를 다시 열어주세요.', 400
    state = secrets.token_urlsafe(32)
    session['oauth_state'] = state
    with database() as conn:
        schema(conn)
        conn.execute('DELETE FROM ourisul_oauth_state WHERE expires_at < %s', (time.time(),))
        conn.execute('INSERT INTO ourisul_oauth_state VALUES (%s,%s)',
                     (hashlib.sha256(state.encode()).hexdigest(), time.time() + 600))
    return redirect(api_base() + '/oauth/authorize?' + urlencode({
        'response_type': 'code', 'client_id': os.environ['CAFE24_CLIENT_ID'],
        'state': state, 'redirect_uri': REDIRECT_URI,
        'scope': 'mall.read_product,mall.read_order'}))


@bp.route('/oauth/callback')
def callback():
    if not config_ok():
        return '카페24 연결 설정을 준비 중입니다.', 503
    expected = session.pop('oauth_state', '')
    actual = request.args.get('state', '')
    if not expected or not hmac.compare_digest(expected, actual):
        return '인증 요청을 확인할 수 없습니다. 관리자 연결 페이지에서 다시 시작해주세요.', 400
    with database() as conn:
        schema(conn)
        consumed = conn.execute('DELETE FROM ourisul_oauth_state WHERE digest = %s '
                                'AND expires_at >= %s RETURNING digest',
                                (hashlib.sha256(actual.encode()).hexdigest(), time.time())).fetchone()
    if not consumed:
        return '인증 요청이 만료되었거나 이미 사용되었습니다.', 400
    if request.args.get('error') or not request.args.get('code'):
        return '연결이 승인되지 않았습니다. 관리자 연결 페이지에서 다시 시작해주세요.', 400
    token = exchange({'grant_type': 'authorization_code', 'code': request.args['code'],
                      'redirect_uri': REDIRECT_URI})
    with database() as conn:
        schema(conn)
        save_token(conn, token)
    return redirect('/admin/cafe24/products')


@bp.route('/admin/cafe24/products')
@protected
def products():
    with database() as conn:
        schema(conn)
        # Serialize refreshes across workers. Cafe24 rotates refresh tokens.
        row = conn.execute('SELECT encrypted_token FROM ourisul_cafe24_token '
                           'WHERE mall_id = %s FOR UPDATE', (os.environ['CAFE24_MALL_ID'],)).fetchone()
        if not row:
            return redirect('/admin/cafe24')
        token = json.loads(Fernet(os.environ['TOKEN_ENCRYPTION_KEY']).decrypt(row[0].encode()))
        def fetch(access_token):
            return requests.get(api_base() + '/admin/products',
                                headers={'Authorization': 'Bearer ' + access_token},
                                params={'limit': 10}, timeout=(5, 20))
        response = fetch(token['access_token'])
        if response.status_code == 401:
            token = exchange({'grant_type': 'refresh_token', 'refresh_token': token['refresh_token']})
            save_token(conn, token)
            # Commit the rotated credentials before another external request.
            conn.commit()
            response = fetch(token['access_token'])
        response.raise_for_status()
        items = response.json().get('products', [])
    return render_template_string('''<!doctype html><html lang="ko"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>우리술 상품 확인</title>
<h1>카페24 연결 완료</h1><p>등록 상품을 최대 10개 표시합니다.</p>
<ul>{% for product in items %}<li>{{product.product_name}}</li>{% else %}
<li>등록된 상품이 없습니다.</li>{% endfor %}</ul><a href="/">홈페이지로 이동</a></html>''', items=items)


def init_app(app):
    app.secret_key = os.environ.get('FLASK_SECRET_KEY')
    app.config.update(SESSION_COOKIE_SECURE=True, SESSION_COOKIE_HTTPONLY=True,
                      SESSION_COOKIE_SAMESITE='Lax', MAX_CONTENT_LENGTH=16384)
    app.register_blueprint(bp)

    @app.errorhandler(Exception)
    def safe_error(error):
        from werkzeug.exceptions import HTTPException
        if isinstance(error, HTTPException):
            return error
        # Avoid logging OAuth codes, token responses, database credentials or URLs.
        app.logger.error('Request failed: %s', type(error).__name__)
        return '연결 처리 중 오류가 발생했습니다. 관리자에게 문의해주세요.', 502
