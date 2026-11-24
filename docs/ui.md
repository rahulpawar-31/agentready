# The web page

One page, served by the same app as the API. It follows the flow in the PRD (FR-8.1): paste a site address, prove
control, see the score and the evidence, review the proposed fixes as diffs, approve, then download or copy the file
yourself.

**The visual layout was not checked in a real browser.** It was built and tested without one: the unit tests use a small
fake DOM, and the real page modules were driven from Node against a real running server (see "What was tested"). The
first time you open it in a browser, please look at the list at the end of this page.

## Run it

From `backend/`:

    AGENTREADY_SECRET=choose-a-long-random-value uvicorn app.main:app --port 8000

Open `http://127.0.0.1:8000/`. The server listens on 127.0.0.1 only unless you pass `--host`. The page is the same
origin as the REST API, so there is no CORS setup. The files under `backend/app/ui/` are read once when the app starts:
after editing one, restart the server.

There is no build step and no `node_modules`. Node 20 or newer is needed only to run the page's unit tests
(`cd backend && node --test tests/ui/*.test.mjs`, also run by `pytest` through `tests/test_ui_js.py`, which is skipped
with a message when Node is not installed).

## The flow

1. **Your site.** Type an address such as `example.com`. Surrounding spaces and a leading `http://` or `https://` are
   accepted; anything that is not a plain host (a path with a space, a user name, another scheme, an IPv6 literal) is
   refused before any request. The page then asks `GET /ownership-token` for the verification steps.
2. **Prove control.** The page shows the meta tag and, for sites on a default port, the DNS TXT record. **Verify and
   audit** sends `POST /audit`. If the site has neither yet, you see "We could not find the verification tag or DNS
   record yet" and the steps stay on screen.
3. **Score and evidence.** A 0 to 100 score with a text band (Good 90 to 100, Needs work 50 to 89, Poor 0 to 49), the
   category scores, and every finding with what was checked, what was observed, where, its status (`detected`,
   `not detected`, `unreachable`) and its confidence. Findings that need attention come first. Checks that could not be
   reached are listed separately and are not in the score. **Re-audit with fresh pages** runs the audit again under a
   new snapshot name (`rerun-<UTC time>`), because an audit under the same snapshot name returns the pages it cached.
   Use it after you have deployed a fix.
4. **Review fixes.** **Propose fixes** calls `POST /report/{id}/fixes`. Each fix is a card with its diff. **Approve**
   or **Reject**. Only after approval do **Download** and **Copy** appear. Approving does not change your site and does
   not export anything: Download and Copy fetch the file from the export endpoint, which the server refuses (409) for a
   fix that is not approved. You add the file to your site yourself.

## What it protects against

- **Site text becomes markup.** Everything that came from the audited site (titles, evidence, URLs, diffs, descriptions,
  reasons) is placed in the page as text with `textContent`/text nodes through one small element builder
  (`app/ui/dom.js`). That builder only creates elements from a fixed list, only sets attributes from a fixed list, never
  sets `on*`, `style` or `srcdoc`, and accepts a link only if it points at a fixed place in this page or this app.
  `innerHTML`, `eval`, `document.write` and the like appear nowhere in the code: `tests/test_ui_static.py` scans for
  them, and the render tests feed hostile data through every view.
- **Links to other sites.** Finding URLs and fix paths are shown as text and are never links. (A blocked redirect target
  can end up in a finding's URL, so clicking would be a way to send a person somewhere the site chose.)
- **Page content security.** `GET /` and `GET /ui/<file>` carry `Content-Security-Policy: default-src 'none'; script-src
  'self'; style-src 'self'; connect-src 'self'; img-src 'self' data:; base-uri 'none'; form-action 'none';
  frame-ancestors 'none'`, plus `X-Content-Type-Options: nosniff`, `Referrer-Policy: no-referrer` and
  `Cache-Control: no-store`. The page has no inline script, style or event handler. REST responses do not get these
  headers and are otherwise unchanged (the Plan 4 parity tests still pass; two rows changed: the one that expected
  `GET /` to be a 404, because `/` is now the page, and `//ownership-token`, which the test client turned into a request
  for `/`, so it never tested what its name said).
- **File serving.** Only files named like `app.js` or `app.css` (lower case letters, digits, `_`, `-`, then `.js` or
  `.css`) that were in `backend/app/ui/` when the app started are served under `/ui/`, from memory. There is no path to
  look up on disk, so there is nothing to traverse. Any other name gets the same JSON 404 as an unknown API path.
- **The report id.** The report id is the only credential for approving and exporting a fix (see `docs/fixes.md`). The
  page keeps it in a variable and in `sessionStorage` (so a reload of the same tab keeps working), and nowhere else: not
  in the URL, not in the page text, not in a message. If the browser refuses storage the page still works for that
  page view. The id leaves the page only in requests to the API on the same origin.
- **Exported files.** A downloaded or copied file is not written into the page. The page validates nothing about the
  file itself: the server validated it, scanned it for secret-like values, and checked the owner's approval before it
  would export it.
- **Wording.** The page never says that something is "absent", "missing" or "secure" about your site, and never calls a
  check "passed". It says what was or was not detected. A check that could not be reached says so and is not in the score.

## Limits

- The server keeps reports and fix decisions in memory: a restart loses them. The page then says the report is no
  longer available.
- A page reload keeps the report for the same browser tab (the id is in `sessionStorage`). Another tab or browser needs
  the id, which is not shown anywhere in the page.
- After approving or rejecting a fix, keyboard focus moves to the "4. Review fixes" heading, not to the card you were
  on, because the cards are drawn again. With many fixes you may have to scroll back to your card.
- Downloading uses a temporary link to a browser-made file (`blob:` URL). That path is the one thing in the page the
  tests only exercise with a fake document.
- The page needs JavaScript; without it you see a short notice. The REST API and the MCP server do not need it.

## What was tested

- **Unit tests (`node --test tests/ui/*.test.mjs`, 76 tests):** the element builder and its refusals, formatting and
  score bands, the API client (every status code, timeouts, malformed bodies, id validation), the state store, every
  view with hostile data, and the whole flow against a scripted server (including double clicks, a browser whose storage
  throws, a vanished report on reload, and every request path).
- **Python tests (`tests/test_ui_server.py`, `tests/test_ui_static.py`, `tests/test_ui_js.py`):** routes, headers,
  hostile asset paths, the static rules, and the Node tests.
- **A real run:** the real application under a real `uvicorn` server, with only the site fetchers, the TXT lookup and
  the resolver replaced by fakes, and the real page modules driven from Node over real HTTP: verification steps, a
  refused first audit, the audit, the proposed fixes, an export refused before approval, approve, download (byte for
  byte equal to the REST export), copy, revoke, re-audit under a `rerun-` snapshot, and a reload. No browser was used.

## First manual run in a browser: what to look at

1. The page loads with no console errors, and nothing is blocked by the security policy.
2. Layout at 360 px wide, and at a normal desktop width, in light and dark mode. No sideways scrolling of the page.
3. Tab through the page: the focus ring is visible everywhere, and the order matches the reading order.
4. Type a site you control. Check the meta tag text can be selected and copied without extra characters.
5. After a successful audit: the score number, the bar and the band read clearly; expand a finding.
6. Diffs: added lines are tinted and start with `+`; removed lines are struck through and start with `-`; long lines
   scroll sideways inside the diff box, not the page.
7. Approve a fix: **Download** saves a file with the expected name; **Copy** puts the text on the clipboard; the file
   opens and contains what the diff showed.
8. Reload the tab after an audit: the report comes back. Close the tab and open a new one: it does not.
