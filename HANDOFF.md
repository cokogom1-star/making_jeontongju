# Handoff

## Synthetic order and Slack notice

The admin-only `/admin/orders/test` flow stores a zero-item synthetic order and
its `synthetic` notice in one database transaction. It requires
`SYNTHETIC_ORDER_TEST_ENABLED=true` and the existing admin authentication.
It does not create a payment, real order, customer record, or shipment.

The regular Slack worker deliberately claims only Cafe24 and Coupang notices.
Synthetic notices remain queued but cannot be sent by that worker. A separate
test webhook and an explicitly enabled synthetic-only sender are still needed;
do not use the regular order webhook for test delivery. The actual Slack
`#ourisul-orders` channel (`C0C4WV04AF3`) was confirmed from the September 28
connection-test record, but no real Slack send was made in this change.

## Remaining product work

- Design each menu page beyond the current public landing/catalog pages.
- Implement account signup and login after settling identity, privacy, and
  age-verification requirements.
- Build a board and Q&A workflow with moderation and access rules.
- Design real order and delivery management with payment, fulfillment, and
  channel reconciliation. The synthetic order table is not that ledger.
