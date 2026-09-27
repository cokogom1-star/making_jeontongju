"""Deterministic dry run for cross-channel order handling; never writes stock."""


def reconcile(initial_stock, events):
    """Deduplicate line IDs, exclude canceled quantities, flag overselling."""
    if not isinstance(initial_stock, dict) or any(type(v) is not int or v < 0
                                                   for v in initial_stock.values()):
        raise ValueError('Invalid opening stock')
    reserved = {sku: 0 for sku in initial_stock}
    seen = {}
    duplicates = 0
    for event in events:
        if not isinstance(event, dict):
            raise ValueError('Invalid order event')
        try:
            key = (event['channel'], event['order_id'], event['line_id'])
            sku = event['sku']
            qty = event['quantity']
        except KeyError as exc:
            raise ValueError('Incomplete order event') from exc
        if any(not isinstance(value, (str, int)) or isinstance(value, bool) or not str(value)
               for value in key) or not isinstance(sku, str) or not sku:
            raise ValueError('Invalid order identity')
        if sku not in reserved:
            raise ValueError('Unknown SKU')
        canceled = event.get('canceled', 0)
        if (type(qty) is not int or type(canceled) is not int
                or qty < 0 or not 0 <= canceled <= qty):
            raise ValueError('Invalid quantity')
        details = (sku, qty, canceled)
        if key in seen:
            if seen[key] != details:
                raise ValueError('Conflicting duplicate order line')
            duplicates += 1
            continue
        seen[key] = details
        reserved[sku] += qty - canceled
    return {'remaining': {sku: initial_stock[sku] - count for sku, count in reserved.items()},
            'shortage': {sku: max(0, count - initial_stock[sku])
                         for sku, count in reserved.items()},
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
    shortage_example = reconcile({'DEMO-SOOL': 1}, events)
    return {'mode': 'demo', 'opening_stock': {'DEMO-SOOL': 2},
            'orders': 3, 'result': reconcile({'DEMO-SOOL': 2}, events),
            'shortage_example': {'opening_stock': {'DEMO-SOOL': 1},
                                 'result': shortage_example},
            'note': '실제 주문·재고를 읽거나 변경하지 않는 시연입니다.'}
