"""Private, read-only view of the first saved channel order summaries."""

import base64
import binascii
from datetime import datetime
import json
import re

from flask import Blueprint, render_template_string, request, url_for
from psycopg.errors import UndefinedTable

from cafe24 import database, protected


bp = Blueprint('channel_inbox', __name__)
PAGE_SIZE = 25
ORDER_ID = re.compile(r'[A-Za-z0-9_-]{1,80}\Z')


class ChannelInboxUnavailable(Exception):
    """The persisted notice table does not yet exist."""

PAGE = '''<!doctype html><html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex,nofollow"><title>채널 주문 기록 | 우리술</title>
<style>body{max-width:960px;margin:0 auto;padding:24px 18px;background:#f6f2e9;
color:#24221e;font:16px/1.6 system-ui,sans-serif}h1{line-height:1.3}
table{width:100%;border-collapse:collapse}th,td{text-align:left;padding:9px;
border-bottom:1px solid #c9c0b1;overflow-wrap:anywhere}a{color:inherit}
.scroll{overflow-x:auto}a:focus-visible{outline:3px solid #7b421f;outline-offset:3px}
</style></head><body><main><h1>채널 주문 기록</h1>
<p>관리자 전용 저장 기록입니다. 상태는 <strong>최초 수집 당시</strong> 값이며 이후 변경은 반영되지 않을 수 있습니다.
현재 주문 상태나 결제·배송 진행 상황을 확인하는 화면이 아닙니다.</p>
{% if rows %}<div class="scroll" role="region" aria-label="저장된 주문 요약" tabindex="0"><table><caption>저장된 주문 요약 (최대 25건)</caption>
<thead><tr><th scope="col">채널</th><th scope="col">주문 ID</th><th scope="col">최초 수집 당시 상태</th>
<th scope="col">채널 주문 시각</th><th scope="col">저장 시각</th></tr></thead><tbody>
{% for row in rows %}<tr><td>{{row.source}}</td><td>{{row.order_id}}</td><td>{{row.status}}</td>
<td>{{row.ordered_at}}</td><td><time datetime="{{row.created_at}}">{{row.created_at}}</time></td></tr>{% endfor %}
</tbody></table></div>{% else %}<p>저장된 채널 주문 기록이 없습니다.</p>{% endif %}
{% if next_cursor %}<p><a href="{{url_for('channel_inbox.inbox', cursor=next_cursor)}}">이전 기록 보기 →</a></p>{% endif %}
{% if cursor_used %}<p><a href="{{url_for('channel_inbox.inbox')}}">처음 기록으로 돌아가기</a></p>{% endif %}
</main></body></html>'''


def decode_cursor(value):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,512}', value):
        raise ValueError('Invalid cursor')
    try:
        raw = base64.urlsafe_b64decode(value + '=' * (-len(value) % 4))
        stamp, source, order_id = json.loads(raw)
        parsed = datetime.fromisoformat(stamp)
    except (ValueError, TypeError, UnicodeDecodeError, binascii.Error) as exc:
        raise ValueError('Invalid cursor') from exc
    if (not isinstance(stamp, str) or parsed.tzinfo is None or
            source not in ('cafe24', 'coupang') or not isinstance(order_id, str) or
            not ORDER_ID.fullmatch(order_id)):
        raise ValueError('Invalid cursor')
    return parsed, source, order_id


def encode_cursor(row):
    raw = json.dumps([row[4].isoformat(), row[0], row[1]], separators=(',', ':')).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip('=')


def saved_orders(cursor=None, connect=database):
    """One bounded page; no schema initialization or provider access on reads."""
    params = () if cursor is None else decode_cursor(cursor)
    where = '' if cursor is None else 'AND (created_at, source, order_id) < (%s, %s, %s)'
    try:
        with connect() as conn:
            conn.execute('SET TRANSACTION READ ONLY')
            records = conn.execute('''SELECT source, order_id, status, ordered_at, created_at
                FROM ourisul_order_notice WHERE source IN ('cafe24', 'coupang') ''' + where + '''
                ORDER BY created_at DESC, source DESC, order_id DESC LIMIT %s''',
                (*params, PAGE_SIZE + 1)).fetchall()
    except UndefinedTable as exc:
        raise ChannelInboxUnavailable from exc
    visible = records[:PAGE_SIZE]
    rows = [dict(source=r[0], order_id=r[1], status=r[2], ordered_at=r[3],
                 created_at=r[4].isoformat()) for r in visible]
    return rows, encode_cursor(visible[-1]) if len(records) > PAGE_SIZE else None


@bp.get('/admin/orders/channels')
@protected
def inbox():
    values = request.args.getlist('cursor')
    if set(request.args) - {'cursor'} or len(values) > 1 or (values and not values[0]):
        return '잘못된 페이지 주소입니다. /admin/orders/channels에서 다시 시작하세요.', 400, {
            'Cache-Control': 'no-store', 'Referrer-Policy': 'no-referrer'}
    try:
        rows, next_cursor = saved_orders(values[0] if values else None)
    except ValueError:
        return '잘못된 페이지 주소입니다. /admin/orders/channels에서 다시 시작하세요.', 400, {
            'Cache-Control': 'no-store', 'Referrer-Policy': 'no-referrer'}
    except ChannelInboxUnavailable:
        return '주문 기록을 불러올 수 없습니다.', 503, {
            'Cache-Control': 'no-store', 'Referrer-Policy': 'no-referrer'}
    response = render_template_string(PAGE, rows=rows, next_cursor=next_cursor, cursor_used=bool(values))
    return response, 200, {'Cache-Control': 'no-store', 'Referrer-Policy': 'no-referrer'}
