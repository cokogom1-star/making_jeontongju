"""Administrator-only, synthetic Toss Payments test transaction.

This module cannot charge a live key, sell a product or create a fulfillment order.
Register ``bp`` explicitly in the Flask application to enable its routes.
"""

import os
import re
import secrets

import requests
from flask import Blueprint, abort, render_template_string, request, session, url_for

from cafe24 import database, protected

bp = Blueprint('direct_checkout', __name__)
AMOUNT = 1000
ORDER_NAME = '결제 연동 테스트 (상품 없음)'
CONFIRM_URL = 'https://api.tosspayments.com/v1/payments/confirm'


def enabled():
    client = os.getenv('TOSS_TEST_CLIENT_KEY', '')
    secret = os.getenv('TOSS_TEST_SECRET_KEY', '')
    return (os.getenv('DIRECT_CHECKOUT_TEST_ENABLED') == 'true'
            and client.startswith('test_gck_') and secret.startswith('test_gsk_')
            and bool(os.getenv('DATABASE_URL')))


def connect():
    return database()


def schema(conn):
    conn.execute('''CREATE TABLE IF NOT EXISTS ourisul_test_payments (
        order_id TEXT PRIMARY KEY, amount INTEGER NOT NULL CHECK(amount = 1000),
        status TEXT NOT NULL, payment_key TEXT UNIQUE,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        approved_at TIMESTAMPTZ)''')


def require_enabled():
    if not enabled():
        abort(404)


@bp.before_request
def hide_disabled_test_checkout():
    require_enabled()


@bp.after_request
def private_headers(response):
    response.headers['Cache-Control'] = 'no-store'
    response.headers['Referrer-Policy'] = 'no-referrer'
    response.headers['X-Content-Type-Options'] = 'nosniff'
    return response


@bp.route('/admin/payments/test', methods=['GET', 'POST'])
@protected
def test_checkout():
    require_enabled()
    if request.method == 'GET':
        session['payment_csrf'] = secrets.token_urlsafe(32)
        return render_template_string('''<!doctype html><html lang="ko"><meta charset="utf-8">
<meta name="robots" content="noindex,nofollow"><title>관리자 결제 테스트</title>
<h1>토스페이먼츠 테스트 결제</h1><p>실물 상품이 없는 1,000원 테스트 거래입니다.
실제 금액은 청구되지 않으며 배송·주문 처리도 하지 않습니다.</p>
<form method="post"><input type="hidden" name="csrf" value="{{csrf}}">
<button type="submit">테스트 거래 만들기</button></form></html>''', csrf=session['payment_csrf'])
    token = session.pop('payment_csrf', '')
    if not token or not secrets.compare_digest(token, request.form.get('csrf', '')):
        abort(403)
    order_id = 'TEST-' + secrets.token_hex(16)
    with connect() as conn:
        schema(conn)
        conn.execute('INSERT INTO ourisul_test_payments(order_id, amount, status) VALUES (%s,%s,%s)',
                     (order_id, AMOUNT, 'CREATED'))
    session['payment_order_id'] = order_id
    return render_template_string('''<!doctype html><html lang="ko"><meta charset="utf-8">
<meta name="robots" content="noindex,nofollow"><title>관리자 결제 테스트</title>
<h1>테스트 결제 1,000원</h1><p>실물 상품 없음 · 실제 청구 없음</p>
<button id="pay" disabled>테스트 결제창 열기</button><p id="message"></p>
<script src="https://js.tosspayments.com/v2/standard"></script>
<script>
(async () => {
 const message = document.getElementById('message');
 try {
  const toss = TossPayments({{client_key|tojson}});
  const widgets = toss.widgets({customerKey: 'ANONYMOUS'});
  await widgets.setAmount({currency: 'KRW', value: {{amount|tojson}}});
  const button = document.getElementById('pay');
  button.disabled = false;
  button.addEventListener('click', async () => {
   button.disabled = true;
   try {
    const paymentWindow = await widgets.renderPaymentWindow();
    paymentWindow.on('paymentRequest', async () => {
     try {
      await widgets.requestPayment({
       orderId: {{order_id|tojson}}, orderName: {{order_name|tojson}},
       successUrl: {{success_url|tojson}}, failUrl: {{fail_url|tojson}}
      });
     } catch (_) { message.textContent = '결제 요청이 실패했습니다.'; button.disabled = false; }
    });
    paymentWindow.on('cancel', () => { button.disabled = false; });
   } catch (_) { message.textContent = '결제창을 열 수 없습니다.'; button.disabled = false; }
  });
 } catch (_) { message.textContent = '결제 테스트를 시작할 수 없습니다.'; }
})();
</script></html>''', client_key=os.environ['TOSS_TEST_CLIENT_KEY'], amount=AMOUNT,
                                  order_id=order_id, order_name=ORDER_NAME,
                                  success_url=url_for('.success', _external=True),
                                  fail_url=url_for('.failure', _external=True))


@bp.get('/admin/payments/test/success')
@protected
def success():
    require_enabled()
    order_id = request.args.get('orderId', '')
    payment_key = request.args.get('paymentKey', '')
    amount = request.args.get('amount', '')
    if (not re.fullmatch(r'TEST-[0-9a-f]{32}', order_id)
            or not payment_key or len(payment_key) > 200
            or amount != str(AMOUNT)
            or session.get('payment_order_id') != order_id):
        abort(400)
    with connect() as conn:
        schema(conn)
        row = conn.execute('SELECT amount,status,payment_key FROM ourisul_test_payments '
                           'WHERE order_id=%s FOR UPDATE', (order_id,)).fetchone()
        if not row or row[0] != AMOUNT:
            abort(400)
        if row[1] == 'DONE':
            if row[2] != payment_key:
                abort(409)
            return '이미 검증된 테스트 거래입니다.'
        if row[1] != 'CREATED':
            abort(409)
        try:
            response = requests.post(CONFIRM_URL,
                                     auth=(os.environ['TOSS_TEST_SECRET_KEY'], ''),
                                     headers={'Content-Type': 'application/json',
                                              'Idempotency-Key': order_id},
                                     json={'paymentKey': payment_key, 'orderId': order_id,
                                           'amount': AMOUNT}, timeout=12)
            response.raise_for_status()
            payment = response.json()
        except (requests.RequestException, ValueError):
            # A timeout can follow a successful approval. Reconcile with Toss first.
            try:
                lookup = requests.get('https://api.tosspayments.com/v1/payments/orders/'
                                      + order_id, auth=(os.environ['TOSS_TEST_SECRET_KEY'], ''),
                                      timeout=8)
                lookup.raise_for_status()
                payment = lookup.json()
            except (requests.RequestException, ValueError):
                return '결제 승인 상태를 확인할 수 없습니다. 관리자 확인이 필요합니다.', 502
        if (not isinstance(payment, dict) or payment.get('paymentKey') != payment_key
                or payment.get('orderId') != order_id
                or type(payment.get('totalAmount')) is not int
                or payment['totalAmount'] != AMOUNT
                or payment.get('status') != 'DONE'):
            return '결제 승인 응답을 검증할 수 없습니다.', 502
        conn.execute('UPDATE ourisul_test_payments SET status=%s, payment_key=%s, '
                     'approved_at=now() WHERE order_id=%s', ('DONE', payment_key, order_id))
    return '테스트 결제 승인 검증 완료. 실물 상품 주문은 생성되지 않았습니다.'


@bp.get('/admin/payments/test/fail')
@protected
def failure():
    require_enabled()
    return '테스트 결제가 완료되지 않았습니다.'
