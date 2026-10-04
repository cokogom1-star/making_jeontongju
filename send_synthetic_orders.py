"""Inspect or deliver synthetic order notices from the configured database.

Set VERIFY_DATABASE_URL to an explicit disposable database URL. Sending also
requires SYNTHETIC_ORDER_NOTIFICATIONS_ENABLED=true and a separate
SLACK_TEST_ORDER_WEBHOOK_URL. This command never polls real order channels.
"""

import argparse
import os

import psycopg
from psycopg.conninfo import conninfo_to_dict

import order_notifications as notices


def _verify_database():
    dsn = os.environ.get('VERIFY_DATABASE_URL')
    if not dsn:
        raise ValueError('VERIFY_DATABASE_URL is required; no DATABASE_URL fallback')
    info = conninfo_to_dict(dsn)
    if (not any(part in info.get('dbname', '').lower() for part in ('verify', 'test'))
            or not info.get('host') or not info.get('user') or info.get('service')):
        raise ValueError('Explicit disposable test/verify database host and user required')
    production_dsn = os.environ.get('DATABASE_URL')
    if production_dsn:
        production = conninfo_to_dict(production_dsn)
        identity = lambda item: (item.get('host'), item.get('port', '5432'), item.get('dbname'))
        if identity(info) == identity(production):
            raise ValueError('Verification database must differ from DATABASE_URL')
    return lambda: psycopg.connect(dsn, connect_timeout=10)


def run(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument('--pending', action='store_true', help='show ready, deferred and leased synthetic notices')
    action.add_argument('--send', metavar='TEST-ID', help='attempt one exact synthetic notice')
    args = parser.parse_args(argv)
    if args.send is not None:
        notices._valid_synthetic_id(args.send)
    connect = _verify_database()
    if args.pending:
        return notices.synthetic_pending_counts(connect)
    return notices.deliver_synthetic_pending(connect, args.send)


if __name__ == '__main__':
    print(run())
