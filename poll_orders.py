"""Poll connected sales channels and deliver queued Slack order notices.

Run on a scheduler every ten minutes. Each run overlaps the previous hour;
the database primary key suppresses repeated order notifications.
"""

import os
from datetime import datetime, timedelta

from cafe24 import database, order_summaries as cafe24_orders
from coupang import KST, configured as coupang_configured, order_summaries as coupang_orders
from order_notifications import poll_and_deliver


def run(now=None):
    now = now or datetime.now(KST)
    if now.tzinfo is None:
        raise ValueError('Timezone-aware clock required')
    sources = {}
    if os.environ.get('CAFE24_ORDER_READ_ENABLED', '').lower() == 'true':
        sources['cafe24'] = cafe24_orders
    if (os.environ.get('COUPANG_ORDER_READ_ENABLED', '').lower() == 'true'
            and coupang_configured()):
        sources['coupang'] = coupang_orders
    if not sources:
        raise ValueError('No order-reading channel enabled')
    return poll_and_deliver(sources, now - timedelta(hours=1), now, database)


if __name__ == '__main__':
    print(run())
