"""Deterministic dry run for cross-channel order handling; never writes stock."""


def reconcile(initial_stock, events):
    """Deduplicate line IDs, exclude canceled quantities, flag overselling."""
    if not isinstance(initial_stock, dict) or any(not isinstance(v, int) or v < 0
                                                   for v in initial_stock.values()):
        raise ValueError('Invalid opening stock')
    reserved = {sku: 0 for sku in initial_stock}
    seen = set()
    duplicates = 0
    for event in events:
        key = (event['channel'], event['order_id'], event['line_id'])
        if key in seen:
            duplicates += 1
            continue
        seen.add(key)
        sku = event['sku']
        if sku not in reserved:
            raise ValueError('Unknown SKU')
        qty = event['quantity']
        canceled = event.get('canceled', 0)
        if (not isinstance(qty, int) or not isinstance(canceled, int)
                or qty < 0 or not 0 <= canceled <= qty):
            raise ValueError('Invalid quantity')
        reserved[sku] += qty - canceled
    return {'remaining': {sku: initial_stock[sku] - count for sku, count in reserved.items()},
            'oversold': any(initial_stock[sku] < count for sku, count in reserved.items()),
            'duplicate_lines_ignored': duplicates}


def dry_run():
    events = [
        {'channel': 'cafe24', 'order_id': 'DEMO-C1', 'line_id': '1',
         'sku': 'DEMO-SOOL', 'quantity': 1},
        {'channel': 'coupang', 'order_id': 'DEMO-P1', 'line_id': '1',
         'sku': 'DEMO-SOOL', 'quantity': 1},
        {'channel': 'coupang', 'order_id': 'DEMO-P1', 'line_id': '1',
         'sku': 'DEMO-SOOL', 'quantity': 1},
        {'channel': 'coupang', 'order_id': 'DEMO-P2', 'line_id': '1',
         'sku': 'DEMO-SOOL', 'quantity': 1, 'canceled': 1},
    ]
    return {'mode': 'demo', 'opening_stock': {'DEMO-SOOL': 2},
            'orders': 3, 'result': reconcile({'DEMO-SOOL': 2}, events),
            'note': '실제 주문·재고를 읽거나 변경하지 않는 시연입니다.'}
