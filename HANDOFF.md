# Handoff

## Synthetic order and Slack notice

The admin-only `/admin/orders/test` flow stores a zero-item synthetic order and
its `synthetic` notice in one database transaction. It requires
`SYNTHETIC_ORDER_TEST_ENABLED=true` and the existing admin authentication.
It does not create a payment, real order, customer record, or shipment.

The regular Slack worker deliberately claims only Cafe24 and Coupang notices.
Synthetic notices remain queued until `send_synthetic_orders.py` is run against
an explicit disposable `VERIFY_DATABASE_URL`. `--pending` counts queued test
notices without sending. Delivery requires
`SYNTHETIC_ORDER_NOTIFICATIONS_ENABLED=true` and
`SLACK_TEST_ORDER_WEBHOOK_URL`, which must differ from the regular
`SLACK_ORDER_WEBHOOK_URL`. The sender claims only synthetic rows backed by a
test-order record, sends an ID-only `[테스트·미운영]` message, and retries failures
with a fenced lease. The regular worker cannot claim synthetic notices.

No Slack test webhook or disposable database credentials were available for a
live delivery check. No external Slack message was sent. The verifier exercises
the sender with a fake Slack response and an isolated PostgreSQL schema; it
does not establish real Slack connectivity or channel routing.

## Remaining product work

- Design each menu page beyond the current public landing/catalog pages.
- Implement account signup and login after settling identity, privacy, and
  age-verification requirements.
- Build a board and Q&A workflow with moderation and access rules.
- Design real order and delivery management with payment, fulfillment, and
  channel reconciliation. The synthetic order table is not that ledger.
