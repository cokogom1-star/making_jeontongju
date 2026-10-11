# Handoff

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


