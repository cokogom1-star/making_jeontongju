# Handoff

## Consistent synthetic delivery timeline

The administrator HTML timeline now reads simulated status, fictional basket
items, and the synthetic Slack outbox from the same PostgreSQL repeatable-read,
read-only transaction as `?view=snapshot`. A concurrent status transition or
sender update can no longer make the page show a mixture of old and new values;
the page's next-step form also uses the version from that same snapshot. The
default JSON history response remains unchanged. Invalid, unknown, and
pre-status-model orders still return a 404 recovery page in HTML, while a
missing required item or notice table returns 503. Authentication, the
synthetic-order gate, and private response headers remain in place. The
isolated verifier forces another connection to commit a status, item, and
notice update between the timeline's reads, then checks the whole rendered
page reflects the original snapshot and a subsequent request reflects the
new one. It also checks the gate and missing-table behavior.

This remains a rehearsal, not customer checkout or shipment tracking. The
next product milestone is an authenticated, read-only channel order inbox
using already persisted Cafe24/Coupang order metadata, explicitly labeled as
the last observed status rather than live fulfillment. Customer checkout
still requires settled identity, age verification, inventory, payment,
fulfillment, and channel reconciliation requirements.

## Admin synthetic lookup recovery

The administrator-only synthetic order lookup now returns a Korean 404 recovery
page for a missing or malformed ID. A correctly shaped ID absent from the
status history also returns that page from the HTML timeline route. Both pages
link back to `/admin/orders/test?view=html` so an administrator can retry the
lookup. The successful lookup still redirects to the timeline. JSON history
continues to return its existing 404 response. The existing authentication,
test-order gate, no-store/no-referrer headers and read-only lookup behavior
remain in force. The isolated PostgreSQL verifier checks malformed, missing,
unknown and valid IDs, the recovery link, JSON compatibility and unchanged
synthetic table counts. This change does not activate purchases or delivery.

## Combined synthetic order rehearsal verification

The isolated PostgreSQL verifier now follows one fictional basket through the
admin HTML creation POST, persisted snapshot, exact-ID synthetic sender CLI,
mocked timeout and deferred retry, mocked successful retry, exact creation
replay, three HTTP status transitions, and final snapshot lookup. It checks
that an early retry and a post-delivery retry issue no HTTP request, a Cafe24
notice remains unchanged, a creation replay preserves the delivered notice,
transition request keys do not duplicate events, and the final snapshot GET
does not mutate any of the five synthetic-order tables. The fake webhook only
receives the selected ID-only test message. This covers the composed rehearsal
path with synthetic adapters; it does not establish real Slack receipt,
carrier delivery, or production order readiness.

## Admin synthetic order snapshot

`GET /admin/orders/test/<TEST-ID>/history?view=snapshot` returns the saved
synthetic status and event history, fictional basket item IDs/labels/quantities,
and backed synthetic Slack outbox state in one JSON response. Explicit
`is_test: true` and `source: synthetic` fields identify this as rehearsal data
for downstream consumers. It uses the
existing admin authentication and `SYNTHETIC_ORDER_TEST_ENABLED` gate. The
read runs in a single PostgreSQL repeatable-read, read-only transaction, so
status, basket and notice reflect one database snapshot even if a status
transition or sender update commits during the request. It does not create
schema, repair rows, send messages, or contact a carrier. Unknown orders and
invalid IDs return 404; a missing required item, event/state, or notice table
returns 503. The previous default JSON history shape and HTML page are
unchanged. The notice state is an outbox record and does not prove Slack
receipt. The isolated PostgreSQL verifier covers access controls, data shape,
read-only behavior, missing tables, and concurrent interleaving. A local
PostgreSQL verifier URL was not available; CI must run the full verifier on
the exact PR revision before merge.

## Fictional basket rehearsal

An exact, authorized creation POST replay now restores a missing synthetic
Slack outbox row in the same transaction. It uses the saved order creation time
for `ordered_at` and does not change an existing notice, including its delivered,
retry, attempt, or lease fields. A changed basket still conflicts before repair.
Read-only GETs never repair notices; there is no production backfill. If an
outbox row was deleted after Slack accepted it, restoring the row can cause a
duplicate test message on a later explicit send. This path does not provide
exactly-once delivery. The isolated PostgreSQL verifier covers zero-item and
basket repair, conflict rejection, full-row preservation, and failed-repair
rollback.

The admin creation forms now submit their own generated `TEST-*` IDs. A GET
keeps the session CSRF token stable and remembers the last eight IDs for each
of the zero-item and fictional-basket modes. This lets an older tab submit or
retry against its original ID after another tab loads the page; exact replay
remains idempotent and a changed basket still conflicts. An ID from the wrong
mode, an unissued or evicted ID, a duplicate ID field, or a missing ID on an
HTML submission is rejected before any database write. The original zero-item
CSRF-only JSON POST remains supported for compatibility; basket submissions
require their explicit ID. The bounded list means forms older than eight newer
page loads must be refreshed.
This covers tabs loaded in sequence. Simultaneous GET responses can overwrite
each other's cookie-backed allowlist; an unrecognized form then fails closed
with 403. The legacy zero-item JSON request without an ID still targets the
latest session ID, so callers that need stable replay should submit the
explicit ID.

The administrator-only `/admin/orders/test?view=html` page has a separate
fictional basket form with two fixed `SYNTHETIC-*` fixtures. Each accepts a
whole-number quantity from 0 to 10, with at least one positive quantity.
This form uses its own session order ID and the existing CSRF token. The exact
fixture IDs, labels and positive quantities are saved in
`ourisul_test_order_item` in the same transaction as the zero-item synthetic
transport order, ID-only synthetic notice, and initial status event. The
original zero-item admin form and JSON response remain available. A retry
with the same ID and quantities is idempotent; a changed basket conflicts.
For an HTML submission, the conflict returns a 409 recovery page linking to
the saved order timeline and a fresh creation form. The default endpoint keeps
its previous 409 response. The verifier compares all five synthetic-order
tables before and after both conflict responses to confirm no saved state changes.
Missing, duplicate, unknown and malformed form fields are rejected. The
HTML history displays the saved labels and quantities without updating them;
a missing item table returns 503 rather than claiming the order had no items.
The table is initialized on the first synthetic write. Existing orders do not
receive an automatic production backfill.

These fixtures have no stock, price, customer, payment, or carrier connection.
The legacy order and notice `item_count` remain zero, and the regular Slack
worker still ignores synthetic notices. The isolated PostgreSQL verifier has
new checks for form validation, exact replay, changed-basket conflicts,
read-only history, status transitions, and transaction rollback. Full verifier
and provider connectivity results must be recorded separately for the exact
revision being reviewed.

## Synthetic order and Slack notice

The admin-only `/admin/orders/test` flow stores a zero-item synthetic order and
its `synthetic` notice in one database transaction. It requires
`SYNTHETIC_ORDER_TEST_ENABLED=true` and the existing admin authentication.
It does not create a payment, real order, customer record, or shipment.

The regular Slack worker deliberately claims only Cafe24 and Coupang notices.
Synthetic notices remain queued until `send_synthetic_orders.py` is run against
an explicit disposable `VERIFY_DATABASE_URL`. `--pending` reports read-only
`ready`, `deferred` (retry time is in the future), `leased` (active lease),
and `total` counts for undelivered, backed test notices without sending.
An active lease takes precedence over a future retry time. Running without an
action does not send. `--inspect TEST-<32 lowercase hex digits>` reads only
that backed notice and reports `ready`, `deferred` (with retry time), `leased`
(with lease expiry), `delivered` (with delivery time), or `missing`. A missing
record and an unbacked notice are both reported as `missing`; no claim,
schema creation, or Slack request occurs. This exact-ID inspection is separate
from the aggregate `--pending` counts. Delivery requires `--send TEST-<32 lowercase hex digits>`
and attempts only that one
exact synthetic ID, never another queued notice. Missing, already delivered,
leased, or not-yet-retryable IDs result in zero sends. Delivery also requires
`SYNTHETIC_ORDER_NOTIFICATIONS_ENABLED=true` and
`SLACK_TEST_ORDER_WEBHOOK_URL`, which must differ from the regular
`SLACK_ORDER_WEBHOOK_URL`. The sender claims only synthetic rows backed by a
test-order record, sends an ID-only `[테스트·미운영]` message, and retries failures
with a fenced lease. The regular worker cannot claim synthetic notices.

The isolated PostgreSQL verifier covers inspection priority when a delivered
notice also has an active lease and future retry, and when a lease is exactly
`now()` (active). It asserts zero HTTP requests from inspection. These are
test-only checks; no webhook is contacted.

No Slack test webhook or disposable database credentials were available for a
live delivery check. No external Slack message was sent. The verifier exercises
the sender with a fake Slack response and an isolated PostgreSQL schema; it
does not establish real Slack connectivity or channel routing.

## Synthetic order status and delivery rehearsal

The administrator-only `/admin/orders/test?view=html` now also shows a
read-only snapshot of backed, undelivered synthetic Slack notices: `ready`,
`deferred`, `leased`, and `total`. It uses the same aggregate query as
`send_synthetic_orders.py --pending`; an active lease takes precedence over a
future retry, and delivered, unbacked, malformed-ID, and real-channel notices
are excluded. The section uses a heading and description list for narrow
screens and assistive technology. It has no send action and makes no HTTP
request or database mutation. Existing admin protection, the
`SYNTHETIC_ORDER_TEST_ENABLED` gate, and no-store/no-referrer headers remain.
An uninitialized notice or test-order table yields HTTP 503 on this HTML GET.
The default `/admin/orders/test` response and JSON history remain unchanged.
The isolated PostgreSQL verifier checks exact counts, full rows in all four
synthetic-order/notice tables before and after GET, zero HTTP requests,
default response compatibility, the gate, and a missing-table 503. It does
not establish real Slack delivery.

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
HTML status/timeline at
`/admin/orders/test/<TEST-ID>/history?view=html`; lookup only redirects there.
The timeline GET reads state without writing to the database. For each
nonterminal status it shows one button for the permitted next simulated step,
with a session CSRF token, expected version and fresh request key. Successful
HTML timelines also show the exact synthetic notice's read-only outbox state:
`ready`, `deferred`, `leased`, `delivered`, or `missing`. This uses the same
backed-ID inspection as `--inspect` without claiming or sending a notice;
`ready` has not been sent and `delivered` means the outbox recorded delivery,
not proven Slack receipt. History and notice are read in separate database
snapshots, so a concurrent sender may change the notice between the two reads.
The page pairs each raw state with a Korean explanation and shows the already
retrieved worker attempt count when present. Attempts include queue claims
before an HTTP request, and `leased` means task ownership until expiry rather
than proven active processing. It shows only the timestamp relevant to
the current state: `retry_at` for deferred, `lease_until` for leased, and
`delivered_at` for delivered. Missing or absent fields produce no fabricated
count or time. Timestamps keep their ISO timezone offsets rather than inventing
a local timezone. A labeled description list and `<time>` elements keep these
details readable on narrow screens and understandable to assistive technology;
template autoescaping applies to status and timestamp text and attributes.
An uninitialized notice table returns HTTP 503 rather than claiming a missing
notice or exposing an internal error.
The JSON history response is unchanged. Successful
HTML POSTs reuse the existing atomic transition route and redirect (303) to
the updated timeline; stale or invalid transitions return a 409 recovery page
linking to the latest state. The terminal page has no transition button.
The original JSON GET/POST behavior remains the default. HTML lookup and controls are behind
the existing admin authentication and `SYNTHETIC_ORDER_TEST_ENABLED` gate, and
uses no-store/no-referrer headers. Invalid, missing, or pre-status-model orders
return 404 without initializing state on read. This is not a public customer
tracking service or a real shipping integration. The verifier checks the HTML
flow, escaping, gate, JSON compatibility, GET database behavior, transitions,
retries and stale forms with
isolated PostgreSQL; it does not test a live browser or carrier.
No Slack recipient was found in the available 우리술/Ourisul search, and no actual
message was sent. Synthetic notices remain scoped to the separately enabled test sender.

## CI artifact upload runtime

The verifier uploads `verification-result.json` with
`actions/upload-artifact@v6`, keeping the artifact name and
`if-no-files-found: error` behavior unchanged. GitHub's
[Node 20 deprecation notice](https://github.blog/changelog/2025-09-19-deprecation-of-node-20-on-github-actions-runners/)
and the [v6 release](https://github.com/actions/upload-artifact/releases/tag/v6.0.0)
identify v6 as a Node 24 action requiring Actions Runner 2.327.1 or newer.
The [2026-10-07 main run](https://github.com/cokogom1-star/making_jeontongju/actions/runs/37569275319)
on `32d8575` used GitHub-hosted runner 2.337.0. Its isolated PostgreSQL
verifier reported 30 tests run with all passing; both `verify` and
`promotion-ready` succeeded. The `actions/upload-artifact@v6` step succeeded
and uploaded `ourisul-verification` (artifact ID `11460087214`) from
`verification-result.json`. The verify job log showed no Node 20 or
`punycode` deprecation warning. This confirms that run's CI evidence, not
live provider connectivity or a production deployment. Each future PR still
needs successful checks and its own verification artifact on its exact head,
followed by a successful main check before deploy.

## Taste explorer

The public `/taste-explorer` now opens directly on one seven-question native radio form.
A visitor chooses an aroma, sweetness, body, acidity, bitterness, texture and
finish without first selecting a drink category. The GET response considers
28 editorial style archetypes across whisky, beer, wine and Korean traditional
liquor (seven per group). These are named styles and typical sensory directions,
not actual stocked products or guaranteed tasting notes. The examples explain
what to compare and distinguish neighboring styles, including bourbon, rye,
sherry-cask and peated whisky; pilsner, wheat beer, IPA, stout and porter;
Chardonnay, Sauvignon Blanc, Pinot Noir, Cabernet, Riesling and sparkling wine;
and cheongju/yakju, takju, makgeolli and distilled soju.

`rank_taste_styles` counts exact overlaps with each seven-feature editorial
profile: aroma weighs three, sweetness/body/acidity/bitterness/texture two
apiece, finish one. Every candidate is ranked globally; equal scores use a
stable style identifier order only for display. The result shows the first
three with matched answer labels, character, an observation prompt and a
comparison. If the leading score ties for more than three styles, the entire
leading tie group is shown. A tie at the three-card cutoff is disclosed, as
are ties on each displayed card. This is a transparent browsing aid, not a
percentage, measured compatibility claim or purchase recommendation. It has
no database or provider dependency and provides no result purchase action.

The route accepts either an empty GET or exactly one answer for every question
from the finite option allowlist. Partial, duplicate (including identical),
unknown, old category or style parameters return a 400 recovery page. It uses
native fieldset/legend/radio controls, retained choices after GET, a landmarked
result and a mobile single-column layout. Answer values appear in the URL and
browser history; there is no profile persistence. The verifier adds independent
golden examples and exhaustive coverage of all 1,296 answer tuples, proving
all 28 styles can lead at least once, plus no-provider and invalid-input
checks. Local standalone smoke checks passed for all tuples, results, ties,
invalid inputs and mocked provider/database access. The full PostgreSQL
verifier requires an explicit disposable `VERIFY_DATABASE_URL`; none was
available in this workspace. A browser accessibility pass remains separate.

## Remaining product work

- Design each menu page beyond the current public landing/catalog pages.
- Implement account signup and login after settling identity, privacy, and
  age-verification requirements.
- Build a board and Q&A workflow with moderation and access rules.
- Design real order and delivery management with payment, fulfillment, and
  channel reconciliation. The synthetic order table is not that ledger.


