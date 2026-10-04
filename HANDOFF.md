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

## Synthetic order status and delivery rehearsal

New synthetic orders also create `ourisul_test_order_state` and an initial
`ourisul_test_order_event` in the same transaction as the test order and notice.
An administrator can read `GET /admin/orders/test/<TEST-ID>/history` without
changing the database. `POST` to the same route requires the existing admin
authentication, enabled test-order gate, CSRF token from `/admin/orders/test`,
`status`, `expected_version`, and a unique `request_key` form field. The only
allowed sequence is `TEST_CREATED` → `TEST_PREPARED` → `TEST_SHIPPED` →
`TEST_DELIVERED`. A row lock, expected version, and request key prevent two
concurrent requests from both advancing the same version; a retry of the same
key returns the prior event without adding another. This records simulated
delivery progress only. It does not create a real shipment or contact a carrier.

Orders written before this status model have no state row, so a read-only GET
returns 404 until the first authorized transition initializes their initial
state/event in its transaction. No automatic production backfill is performed.
The verifier covers the transition path and concurrent version conflict with
isolated PostgreSQL; actual carrier integration is untested and disabled.

The administrator-only `/admin/orders/test?view=html` page now provides a
synthetic order creation button and an ID lookup form. Creation redirects to a
read-only HTML status/timeline at
`/admin/orders/test/<TEST-ID>/history?view=html`; lookup only redirects there.
The original JSON GET/POST behavior remains the default. HTML lookup is behind
the existing admin authentication and `SYNTHETIC_ORDER_TEST_ENABLED` gate, and
uses no-store/no-referrer headers. Invalid, missing, or pre-status-model orders
return 404 without initializing state on read. This is not a public customer
tracking service or a real shipping integration. The verifier checks the HTML
flow, escaping, gate, JSON compatibility, and read-only database behavior with
isolated PostgreSQL; it does not test a live browser or carrier.

## CI artifact upload runtime

The verifier uploads `verification-result.json` with
`actions/upload-artifact@v6`, keeping the artifact name and
`if-no-files-found: error` behavior unchanged. GitHub's
[Node 20 deprecation notice](https://github.blog/changelog/2025-09-19-deprecation-of-node-20-on-github-actions-runners/)
and the [v6 release](https://github.com/actions/upload-artifact/releases/tag/v6.0.0)
identify v6 as a Node 24 action requiring Actions Runner 2.327.1 or newer.
The previous main run used GitHub-hosted runner 2.337.0, but the change is
not verified by a new PR run yet. Before merging, confirm the current PR
revision passes `verify` and `promotion-ready`, the artifact contains
`verification-result.json`, and the upload step has no Node 20 or
`punycode` deprecation warning. Recheck main after merge before deploy.

## Remaining product work

- Design each menu page beyond the current public landing/catalog pages.
- Implement account signup and login after settling identity, privacy, and
  age-verification requirements.
- Build a board and Q&A workflow with moderation and access rules.
- Design real order and delivery management with payment, fulfillment, and
  channel reconciliation. The synthetic order table is not that ledger.
