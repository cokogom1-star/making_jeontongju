"""Private, read-only view of saved channel orders and collected observations."""

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
HISTORY_SIZE = 3
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
<p>관리자 전용 저장 기록입니다. 최근 수집 상태는 마지막으로 저장된 관측값이며, 지금 채널의 최신 상태를 보장하지 않습니다.
수집 기록은 상태 변화가 저장된 순서입니다. 결제·출고·배송의 실제 진행이나 완료를 증명하지 않습니다.</p>
{% if rows %}<div class="scroll" role="region" aria-label="저장된 주문 요약" tabindex="0"><table><caption>저장된 주문 요약 (최대 25건)</caption>
<thead><tr><th scope="col">채널</th><th scope="col">주문 ID</th><th scope="col">최초 수집 당시 상태</th>
<th scope="col">최근 수집 상태</th><th scope="col">최근 상태 변화 기록</th>
<th scope="col">채널 주문 시각</th><th scope="col">저장 시각</th></tr></thead><tbody>
{% for row in rows %}<tr><td>{{row.source}}</td><td>{{row.order_id}}</td><td>{{row.status}}</td>
<td>{% if row.observed_status is not none %}{{row.observed_status}}<br><small>저장 시각: <time datetime="{{row.observed_at}}">{{row.observed_at}}</time></small>{% elif row.ledger_unavailable %}수집 기록 저장소를 확인할 수 없음{% else %}수집 이력 없음{% endif %}</td>
<td>{% if row.events %}<ol>{% for event in row.events %}<li>{{event.status}} <time datetime="{{event.observed_at}}">{{event.observed_at}}</time></li>{% endfor %}</ol>
<small>최근 최대 {{history_size}}건 · 새 기록부터</small>{% elif row.ledger_unavailable %}수집 기록 저장소를 확인할 수 없음{% else %}수집 이력 없음{% endif %}</td>
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
            conn.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
            ledger_ready = all(conn.execute('SELECT to_regclass(%s)', (name,)).fetchone()[0]
                               is not None for name in ('ourisul_channel_order_state',
                                                     'ourisul_channel_order_event'))
            # The notice page is selected before joining history. Every visible
            # order gets at most HISTORY_SIZE events, ordered by durable event ID.
            page = '''WITH page AS (SELECT source, order_id, status, ordered_at, created_at
                FROM ourisul_order_notice WHERE source IN ('cafe24', 'coupang') ''' + where + '''
                ORDER BY created_at DESC, source DESC, order_id DESC LIMIT %s)
                SELECT page.source, page.order_id, page.status, page.ordered_at,
                    page.created_at'''
            if ledger_ready:
                query = page + ''', state.status, state.observed_at,
                    event.status, event.observed_at, event.id
                    FROM page LEFT JOIN ourisul_channel_order_state state
                      ON state.source = page.source AND state.order_id = page.order_id
                    LEFT JOIN LATERAL (SELECT id, status, observed_at
                        FROM ourisul_channel_order_event
                        WHERE source = page.source AND order_id = page.order_id
                        ORDER BY id DESC LIMIT %s) event ON true
                    ORDER BY page.created_at DESC, page.source DESC, page.order_id DESC,
                        event.id DESC'''
                records = conn.execute(query, (*params, PAGE_SIZE + 1, HISTORY_SIZE)).fetchall()
            else:
                records = conn.execute(page + ''' FROM page ORDER BY created_at DESC,
                    source DESC, order_id DESC''', (*params, PAGE_SIZE + 1)).fetchall()
    except UndefinedTable as exc:
        raise ChannelInboxUnavailable from exc
    rows = []
    keys = set()
    for record in records:
        key = (record[0], record[1])
        if key not in keys:
            keys.add(key)
            if len(rows) == PAGE_SIZE:
                break
            rows.append(dict(source=record[0], order_id=record[1], status=record[2],
                             ordered_at=record[3], created_at=record[4].isoformat(),
                             observed_status=record[5] if ledger_ready else None,
                             observed_at=record[6].isoformat() if ledger_ready and record[6] else None,
                             ledger_unavailable=not ledger_ready, events=[]))
        if ledger_ready and record[7] is not None and len(rows) <= PAGE_SIZE:
            rows[-1]['events'].append(dict(status=record[7], observed_at=record[8].isoformat()))
    if not rows:
        return [], None
    last = rows[-1]
    next_cursor = encode_cursor((last['source'], last['order_id'], None, None,
                                 datetime.fromisoformat(last['created_at']))) if len(keys) > PAGE_SIZE else None
    return rows, next_cursor


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
    response = render_template_string(PAGE, rows=rows, next_cursor=next_cursor,
                                      cursor_used=bool(values), history_size=HISTORY_SIZE)
    return response, 200, {'Cache-Control': 'no-store', 'Referrer-Policy': 'no-referrer'}
