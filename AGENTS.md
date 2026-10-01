# Development and production promotion

- Develop every change on a feature branch and open a pull request targeting main. Do not edit production directly or push application changes directly to main.
- Use verify_system.py with a disposable PostgreSQL verification database. Never use production credentials or production data in tests.
- Implement and independently review each change as a two-agent pair. Record the review findings and verification run in the pull request.
- Add meaningful coverage for changed behavior. All verifier checks must finish successfully on the current pull request revision before merging. Failed, cancelled, skipped, missing or outdated runs do not authorize promotion.
- After merging, the main revision must pass the Ourisul system verifier again before Render deploys it. Configure ourisul Auto-Deploy as After CI Checks Pass. Do not bypass a failed check with a manual deploy.
- After deployment, confirm the deployed revision, live status, read-only page health and recent errors. Report verification limitations explicitly; passing tests does not establish that every possible error is absent.
- Cafe24, Coupang, Toss and Slack requests in automated verification use synthetic adapters. Real product registration, purchase activation and real orders are outside this workflow.
- The verifier is an ephemeral integration environment, not a permanently hosted staging website. Browser JavaScript and actual provider connectivity require separate authorized checks before claiming end-to-end readiness.
