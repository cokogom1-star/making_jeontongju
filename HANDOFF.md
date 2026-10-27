# Handoff

## Private channel inbox database recovery

The administrator channel inbox returns a private Korean HTTP 503 recovery page
when PostgreSQL raises `psycopg.OperationalError` while connecting or reading
saved notices and collection history. The page explains that the records could
not be checked, preserves a validated pagination cursor in its retry link, and
also links to the first page. It does not expose database diagnostics or claim
there are no orders. Malformed cursors still return 400, and a missing notice
table still returns 503. The disposable-PostgreSQL verifier injects connection
and page-query failures for both the first and a later page, checks the retry
links, private headers, successful reread, and unchanged notice, state, and
event rows. External provider requests remain blocked. Exact-head CI is
required before promotion; this is a saved-record read recovery only, and its
recovery page has not had a separate browser/mobile rehearsal.

## Browser rehearsal for synthetic Slack outbox retries

The 320 px Chromium verifier now follows a saved fictional basket receipt to
its timeline and snapshot, then exercises the real exact-ID synthetic sender
against an injected mock HTTP transport. A simulated timeout leaves the notice
deferred after one claim. An immediate retry makes no HTTP call or database
change. The verifier advances only that notice's retry timestamp in its
disposable PostgreSQL schema; a second attempt records delivery, and another
send attempt leaves all five synthetic ledgers unchanged. Both mock calls must
contain only the selected test ID, use the separate test webhook and fixed
timeouts, and never reach external HTTP. Chromium checks the ready, deferred,
and delivered labels, attempt counts, exact displayed timestamps, snapshot
states, and receipt/timeline warnings that outbox delivery does not prove
Slack receipt or actual shipping. This does not contact Slack or prove
exactly-once delivery. Exact-head CI must pass before promotion.

## Browser rehearsal for ambiguous database failures

The administrator's synthetic confirmation recovery page now offers a
same-ID, same-CSRF retry form when the reviewed draft remains in that browser
session. Its read-only timeline link comes first, because a connection error
may occur after commit and the recovery page cannot tell whether the record
was saved. Other recovery pages do not gain a retry form.

`verify_admin_browser.py` exercises a pre-write failure and a post-commit
ambiguous failure through Chromium at 320 px. It checks the rendered recovery,
original retry fields and keyboard control, then compares all five synthetic
tables before and after the failure and retry. The post-commit retry must leave
every row unchanged; both cases finish with exactly one fictional order,
basket, initial state, event and outbox record. Failure injection exists only
inside the disposable verifier process; no provider or production data is used.
The existing happy path remains one separate browser scenario. Exact-head CI
is needed to establish the browser result before promotion.

## Isolated admin mobile browser rehearsal

`verify_admin_browser.py` adds a real headless Chromium check to the CI verifier.
It serves the Flask app on loopback against a fresh PostgreSQL schema in the
dedicated verification database, with only synthetic admin settings and blocked
external HTTP requests. A 320 px mobile viewport uses keyboard controls to
select fictional quantities, review them before any write, confirm the record,
open the receipt, and advance all three simulated status steps. It also checks
the browser-rendered document width, Korean page headings, and missing-ID
recovery pages. The schema is removed after the run and the browser result is
uploaded alongside the integration result. This establishes browser behavior
for this narrow synthetic path only; it is not a screen-reader assessment,
provider connectivity check, real purchase, Slack send, or live deployment test.

Run `VERIFY_DATABASE_URL=postgresql://.../ourisul_verify python verify_admin_browser.py`
after installing `requirements.txt`, `playwright==1.56.0`, and its Chromium
browser. Exact-head CI results must be reviewed before promotion.

## Admin rehearsal narrow-screen markup

The synthetic order ID lookup now explains the exact ID format in visible text
and associates that help with its input. Both fictional quantity forms now show
the 0–10 whole-number and at-least-one-item rule and associate it with their
inputs. The shared admin rehearsal style wraps
long IDs in paragraphs and list items, including timeline IDs, recovery IDs,
receipt IDs, and fictional item IDs, so these tokens can break on narrow screens.
The disposable-PostgreSQL verifier parses rendered creation, timeline, and
rehearsal HTML to check both help associations, Korean document language,
viewport, main heading, private headers, and the shared wrapping rule.

This is a markup contract, not a browser layout or screen-reader result. An
isolated browser/mobile pass still needs a reachable disposable app plus
PostgreSQL: use only synthetic IDs and fixtures; verify the 320 px layout has
no horizontal page overflow, keyboard access through selection, review,
confirmation, receipt and three simulated status steps, and readable recovery
pages. Do not use the production database, real orders, provider calls, or
the synthetic Slack sender for that pass.

## Synthetic history read database recovery

The administrator-only `GET /admin/orders/test/<id>/history` now returns a
private HTTP 503 when PostgreSQL raises `psycopg.OperationalError` while reading
the default timeline, HTML timeline, or complete snapshot. The HTML page keeps
the exact ID, links to retry its timeline and the administrator ID lookup, and
does not claim that an order is missing or that any particular status was saved.
The two JSON views return a short error, ID, and same-ID retry guidance without
database diagnostics. Successful payloads, truly missing-order 404 responses,
authentication, the default-off gate, and private response headers remain as
before. The recovery path does not create a CSRF token or submit a transition.

The disposable-PostgreSQL verifier injects a connection failure before and
after a successful read for all three views. It checks private headers, escaped
HTML, successful re-read, missing-order 404s, and exact rows in all five
synthetic order, item, state, event, and notice tables before and after each
request. This is a database-only synthetic rehearsal; it does not contact a
carrier, payment provider, or Slack. Exact-head CI is required before promotion.

## Synthetic status transition database recovery

An administrator's valid synthetic status transition POST now returns HTTP 503
if PostgreSQL raises `psycopg.OperationalError`. The private HTML recovery page
shows the exact synthetic order ID and links to its read-only timeline. It
preserves the original CSRF token, status, expected version, and request key in
an explicit retry form. The administrator should inspect the timeline first:
a connection failure may happen after commit, and this page cannot establish
whether the status changed. Reusing the exact request key is idempotent; a
new timeline form may represent the next simulated step. The default POST
returns a JSON 503 with the order ID and exact-request retry guidance, without
echoing the CSRF token. Its successful response shape is unchanged. Existing validation
and conflict responses remain unchanged. The disposable-PostgreSQL verifier
injects failures before writing and after commit for both views, then checks
exact-key replay and all five synthetic order, item, state, event, and notice
tables. This is only a rehearsal; no provider is contacted. Exact-head CI is
required before promotion, and no browser behavior is claimed here.

## Synthetic rehearsal confirmation database recovery

An administrator's confirmed fictional basket now returns a private HTTP 503
recovery page if the database raises `psycopg.OperationalError` while recording
it. The page displays the same synthetic order ID and links directly to its
read-only timeline. A connection error can occur after commit, so the page
does not assert that the order was unsaved; the administrator should inspect
that ID before submitting the same confirmation again. The reviewed session
draft remains available, and exact-ID replay uses the existing idempotent
record path. Other validation and conflict responses are unchanged. The
disposable-PostgreSQL verifier injects both a failure before writing and an
ambiguous failure after commit, then checks the exact-ID retry and all five
synthetic order, item, state, event, and notice tables. This rehearsal does
not create a real purchase or contact a provider. CI must pass on the exact
PR head before promotion; no browser or live provider behavior is claimed.

## Synthetic status form recovery

The administrator-only synthetic status transition POST now returns a Korean
recovery page for HTML requests with an expired or duplicate CSRF token (403),
malformed or duplicate status/version/request-key fields (400), or an unknown
order (404). The page links to the exact synthetic timeline and the admin ID
lookup, so a fresh form can be loaded before retrying. These responses do not
claim that a change was saved. Existing non-duplicate invalid JSON requests
keep their previous status codes and response shape. Duplicate transition fields are rejected for both
views before any status transition; a duplicate CSRF token is rejected by the
tracking POST with 403. The separate synthetic creation route retains its
existing 400 response for duplicate CSRF fields. The isolated PostgreSQL verifier compares saved status and event
rows before and after each rejected request and confirms a subsequent valid
transition succeeds. This is only a synthetic rehearsal and does not contact
a carrier, payment provider, or Slack. The first CI run for PR #36 passed 55 of
56 checks but exposed the creation-route status regression; this correction
requires a new successful verification run on its exact commit.

## Four-letter taste shorthand

The public seven-question result now starts with a four-letter taste code and
explains each letter from the visitor's selected answer. Positions are aroma
(F: fruit/flower including herbs; G: grain/roast/smoke/spice/wood), sweetness
(D: dry; R: round; S: distinctly sweet), body (L: light; M: medium; F: full),
and finish (C: clean; L: lingering). The middle sweetness and body choices
keep their own letters. These finite answer partitions have no ties. The code
is a playful sensory shorthand, not a personality test or measured fit.

Acidity, bitterness, and texture still contribute to the unchanged weighted
ranking across 28 editorial whisky, beer, wine, and traditional-liquor styles;
the selected-answer recap and detailed comparison cards remain. The separate
evidence-gated product section stays empty until actual product evidence is
curated. The verifier independently checks all 1,296 answer combinations,
the 36 possible codes, selected answer explanations, style-detail continuity,
and no catalog/database contact for unprofiled results. It uses a disposable
PostgreSQL schema and fake provider adapters; browser JavaScript and real
provider connectivity remain separate checks.

## Synthetic order CSRF validation

The legacy synthetic order creation and fulfillment-history POST routes now use
the same ASCII-safe CSRF comparison as the rehearsal routes. A non-ASCII form
token returns HTTP 403 instead of raising an exception; valid tokens and the
existing route behavior are unchanged. The isolated verifier checks both JSON
and HTML variants of the legacy routes and confirms rejected requests leave
the synthetic order, item, state, event, and notice tables unchanged. This is
an administrator-only, default-off synthetic workflow; verification uses a
disposable PostgreSQL schema and no live provider request.

## Synthetic rehearsal request recovery

The administrator-only `/admin/orders/test/rehearsal` review, confirmation,
and receipt routes now show Korean recovery pages for invalid fields (400),
unrecognized or expired session requests (403), unavailable receipts (404),
and conflicting reviewed or saved baskets (409). Receipt storage failures retain
503 with guidance to contact an administrator. Each page links to a fresh
rehearsal and the existing administrator ID lookup. The page does not assert
whether a failed confirmation was saved: an administrator can check the ID
before retrying. Errors do not issue a new draft or write database rows.
Basic authentication, the default-off synthetic gate, CSRF checks, private
headers, successful responses, and route-specific status codes remain intact.

Reproduced review behavior: resubmitting the exact reviewed basket displays
the same review with HTTP 200; changing that basket returns 409. A confirmation
before review, from another session, or after draft eviction returns 403.
The earlier PR #31 closing comment's claim that a repeated review returns 403
was inaccurate. The isolated verifier now asserts the exact replay, changed
review, recovery links and headers, no database writes on error, and conflicting
saved-basket recovery. Full disposable-PostgreSQL verification must pass on the
exact PR head before promotion; these tests do not establish browser behavior.

## Evidence-gated taste-to-product comparison

The seven-question taste result now includes a separate section for actual
Ourisul products. The style ranking is unchanged. `PRODUCT_SENSORY_PROFILES`
starts empty: the current repository has no independently verified sensory
facts for real Cafe24 products. With no profiles, results state clearly that
there is no evidence-backed product to connect; the quiz makes no catalog or
database request. This is the current production behavior, including a
sherry-cask whisky result.

To add a product later, a merchandiser must review its real Cafe24 product
number and current public description, then enter finite quiz traits with an
exact supporting excerpt for every trait. The runtime re-fetches that exact
product, requires it to be currently displayed, rejects sample-labeled
products, and checks each excerpt against its current description. A product
appears only when at least two evidenced traits overlap one of the displayed
style archetypes, including aroma or texture; generic acidity and bitterness
alone cannot create a link. Overlap is checked before contacting Cafe24. At
most the first eligible curated profile triggers an exact-ID catalog lookup
per request; showing multiple products requires a later bounded batch design.
Whitespace-only evidence is rejected. The UI shows the supporting product text and any known
differences and links to the product detail. Shared broad sensory dimensions
are not a claim that traditional liquor tastes like sherry-cask whisky, nor a
compatibility score or purchase recommendation. If the catalog cannot be
checked, the result says so. Inactive, missing, renamed or unsupported entries
silently lose the link. No real profile entry was added in this change.

The isolated verifier uses only fictional fixture products to cover a visible
exact-ID match, evidence and HTML escaping, absent/hidden IDs, stale source
text, sample labels and catalog failure. Local syntax and diff checks passed;
the full disposable-PostgreSQL verifier requires exact-head CI.

## Admin-only synthetic order rehearsal

`/admin/orders/test/rehearsal` offers a fictional two-item selection, a
read-only review step, an explicit record action, and a session-owned receipt.
Every route retains Basic admin authentication, the default-off
`SYNTHETIC_ORDER_TEST_ENABLED` gate, and private response headers. The server
issues bounded, session-bound draft IDs; review validates the exact fixed
fixture fields and saves only to the signed session. Confirmation accepts only
the CSRF token and draft ID, then uses the existing atomic synthetic order,
item, state, and outbox write. Exact replay is idempotent. Receipt checks the
saved item quantities against the session draft and reads the consistent
snapshot without offering a status mutation; it links to the existing
administrator timeline for simulated delivery steps. Repeating the same
review submission displays the review again; changing a reviewed basket
returns 409, with a fresh selection page available at the starting URL.
Only the eight newest issued drafts are kept in the session, so older receipt
links lose session access. It does not access real products,
prices, stock, customer details, payments, carriers, or providers. Its outbox
label explicitly describes a record, not a Slack delivery guarantee.

The isolated PostgreSQL verifier covers the no-write review step, malformed
and duplicate fields, wrong session/CSRF, explicit confirmation, exact replay,
private receipt, bounded drafts, and disabled gate. A local disposable PostgreSQL URL and
installed Flask environment were unavailable, so CI on the exact PR revision
is required before promotion.

## Private channel collection history

The administrator-only `/admin/orders/channels` inbox now displays each
notice's immutable first status alongside the last collected status and its
database observation time. It shows at most the three most recent status
change events per order, ordered by durable event ID. A page still contains at
most 25 Cafe24/Coupang notices; synthetic notices are excluded. The notice,
state, and bounded history are read in one repeatable-read, read-only snapshot.
Legacy notices without a state/event row say that no collection history is
saved. If either observation table does not exist, the notice inbox remains
available but explicitly says its collection ledger cannot be checked. A
missing notice table still returns 503. Reads never create tables, repair
rows, call providers, or expose customer or outbox data. Basic authentication
and no-store/no-referrer headers remain in place.

The last collected status can be stale or move backward when a poll collects
an older provider value. The database timestamps and event order describe
when this application saved observations, not provider fulfillment chronology.
The page explicitly says it cannot prove current payment, dispatch, delivery,
or completion. The isolated verifier covers private access, pagination,
history bounds/order, legacy and missing-ledger states, escaping, and no
mutation. No local disposable PostgreSQL URL was available; CI on the exact
PR revision must complete before promotion.

## Saved channel observations

The authorized Cafe24/Coupang polling write path now keeps the original
`ourisul_order_notice` summary and delivery fields unchanged while saving a
separate current collected status in `ourisul_channel_order_state` and an
append-only status-change sequence in `ourisul_channel_order_event`. A repeated
same-status poll adds no event; a status change adds one. The event's
`observed_at` is the database collection time, not a provider event time or a
claim about fulfillment order. A stale or reordered poll can move the saved
current status backward. These tables are not populated by inbox reads or
synthetic writes. Existing notice rows acquire a first event using their
original `created_at` when next polled, then a change event if the newly
collected status differs. There is no bulk backfill.

Within a batch, the first occurrence of an ID supplies the immutable notice;
the last occurrence supplies the latest collected status. Row locks use sorted
IDs across concurrent batches. The first notice and subsequent status/event
writes commit atomically. The isolated PostgreSQL verifier covers replays,
changed status, preserved delivered/retry/lease fields, pre-existing notices,
duplicate IDs, invalid batches, rollback, and overlapping concurrent polls.
No provider or Slack request is made by this recording function. A local
disposable PostgreSQL URL was unavailable, so exact-head CI must complete
before review can authorize promotion.

## Private saved channel order inbox

`GET /admin/orders/channels` retains its strict
`(created_at, source, order_id)` cursor and excludes synthetic notices. Its
first saved status stays immutable while the newer collection history appears
in separate columns, as described above.


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
private channel inbox is available as described above with the first saved
summary and separately collected status/history.
Customer checkout still requires settled identity, age verification,
inventory, payment, fulfillment, and channel reconciliation requirements.

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


