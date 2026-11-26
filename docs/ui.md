# The web page

One page, served by the same app as the API. It follows the flow in the PRD (FR-8.1): paste a site address, prove
control, see the score and the evidence, review the proposed fixes as diffs, approve, then download or copy the file
yourself.

**The flow and visual layout were checked in headless Chrome on 26 Nov 2026**, using the real application and a synthetic
site supplied through the fetcher's mock transport. Firefox, Safari and a real-site browser run remain. The unit tests use a small
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
   `not detected`, `unreachable`) and its confidence. Each finding is a row you open with its **Show evidence** text.
   Findings that need attention come first. Checks that could not be reached are listed separately and are not in the
   score. At most 500 findings are drawn (the score already counts all of them), and the page says how many are not shown. **Re-audit with fresh pages** runs the audit again under a
   new snapshot name (`rerun-<UTC time>`, with `-1`, `-2` added if you re-audit more than once in the same second),
   because an audit under the same snapshot name returns the pages it cached.
   Use it after you have deployed a fix.
4. **Review fixes.** **Propose fixes** calls `POST /report/{id}/fixes`. Each fix is a card with its diff, which is shown
   in full: no line is ever shortened (the box scrolls sideways). **Approve** or **Reject**. Only after approval do
   **Download** and **Copy** appear. If anything about a fix is shown in shortened form (a diff of more than 2,000
   lines, or a description, path, warning or note longer than the page shows), the page says how much is not shown and
   offers only **Reject**, with the sentence "This fix is too large to review here. Reject it or review it through the
   API.", because approval must rest on a review of everything that would be exported. At most 20 fixes are drawn. If a
   server answer to Approve, Reject or Revoke does not carry the status that action should produce, the page does not say
   "Approved" or "Rejected": it shows a fixed error sentence and draws the card from what the server said. Approving does not change your site and does
   not export anything: Download and Copy fetch the file from the export endpoint, which the server refuses (409) for a
   fix that is not approved. You add the file to your site yourself.

## What it protects against

- **Site text becomes markup.** Everything that came from the audited site (titles, evidence, URLs, diffs, descriptions,
  reasons) is placed in the page as text with `textContent`/text nodes through one small element builder
  (`app/ui/dom.js`). That builder only creates elements from a fixed list, only sets attributes from a fixed list, never
  sets `on*`, `style` or `srcdoc`, accepts a child only if it is a real node of the page (an `instanceof` check on the
  document's own `Node` class: look-alike objects are refused), and accepts a link only if it is an anchor inside this
  page (`#something`): no view links anywhere else.
  `innerHTML`, `eval`, `document.write` and the like appear nowhere in the code: `tests/test_ui_static.py` scans for
  them, and the render tests feed hostile data through every view.
- **Links to other sites.** Finding URLs and fix paths are shown as text and are never links. (A blocked redirect target
  can end up in a finding's URL, so clicking would be a way to send a person somewhere the site chose.)
- **Page content security.** `GET /` and `GET /ui/<file>` carry `Content-Security-Policy: default-src 'none'; script-src
  'self'; style-src 'self'; connect-src 'self'; img-src 'self' data:; base-uri 'none'; form-action 'none';
  frame-ancestors 'none'`, plus `X-Content-Type-Options: nosniff`, `Referrer-Policy: no-referrer`,
  `Cache-Control: no-store`, `Cross-Origin-Opener-Policy: same-origin`, `Cross-Origin-Resource-Policy: same-origin` and
  `Permissions-Policy: camera=(), microphone=(), geolocation=(), payment=(), usb=(), interest-cohort=()`. `HEAD`
  requests get the same headers (and the real `Content-Length`) with no body. The page has no inline script, style or
  event handler and asks for no favicon file (`<link rel="icon" href="data:,">`). REST responses do not get these
  headers and are otherwise unchanged (the Plan 4 parity tests still pass; two rows changed: the one that expected
  `GET /` to be a 404, because `/` is now the page, and `//ownership-token`, which the test client turned into a request
  for `/`, so it never tested what its name said).
- **File serving.** Only files named like `app.js` or `app.css` (lower case letters, digits, `_`, `-`, then `.js` or
  `.css`) that were in `backend/app/ui/` when the app started are served under `/ui/`, from memory. There is no path to
  look up on disk, so there is nothing to traverse; symbolic links, upper-case names, `.html` (other than the page
  itself at `/`) and every other extension are never loaded. The directory and each file are opened without following
  links and without blocking, and anything that is not a regular, readable file (a named pipe, a directory) is skipped. Any other name gets the same JSON 404 as an unknown API path.
- **Invisible characters.** Text from the audited site is shown with bidi controls, zero-width characters, byte order
  marks, soft hyphens and line or paragraph separators removed, and with odd spaces turned into a plain space, so a
  displayed address cannot be visually reordered. In a diff nothing is dropped: each such character is shown as a visible
  marker such as `⟦U+202E⟧`, so the review sees exactly what would be exported.
- **A response the page cannot draw.** Every view is drawn before any state changes, every action has a catch, and a
  restore on load cannot reject. A hostile or malformed API body (any field, any type, keys such as `__proto__`)
  leaves a fixed error sentence, enabled buttons and the address form: `tests/ui/hostile.test.mjs` tries about 1,500
  such bodies through the real flow.
- **The report id.** The report id is the only credential for approving and exporting a fix (see `docs/fixes.md`). The
  page keeps it in a variable and in `sessionStorage` (so a reload of the same tab keeps working), and nowhere else: not
  in the URL, not in the page text, not in a message. If the browser refuses storage the page still works for that
  page view. The id leaves the page only in requests to the API on the same origin.
- **Exported files.** A downloaded or copied file is not written into the page. The page validates nothing about the
  file itself: the server validated it, scanned it for secret-like values, and checked the owner's approval before it
  would export it.
- **Wording.** The page never says that something is "absent", "missing" or "secure" about your site, and never calls a
  check "passed". It says what was or was not detected. A check that could not be reached says so and is not in the score.

## Known limits

- The server keeps reports and fix decisions in memory: a restart loses them. The page then says the report is no
  longer available.
- A page reload keeps the report for the same browser tab (the id is in `sessionStorage`). Another tab or browser needs
  the id, which is not shown anywhere in the page. A restore that finishes after you have started something else is
  discarded.
- Typing a new address clears the earlier site's steps, report and saved id **before** the lookup runs, so a failed
  lookup (for example a network error) leaves nothing of the old site on screen. The server still holds that report, but
  approving or exporting needs its id, which the page no longer has: run a new audit to get back to it. (This follows
  the commit "harden asset loading and re-audits", which replaced my earlier version that cleared only after a
  successful lookup.)
- After approving or rejecting a fix, keyboard focus moves to the "4. Review fixes" heading, not to the card you were
  on, because the cards are drawn again. With many fixes you may have to scroll back to your card. (Kept as is.)
- Downloading uses a temporary link to a browser-made file (`blob:` URL), which is released 30 seconds after the click
  (releasing it at once cancels the download in some browsers). A real Chrome download matched the approved export's
  bytes; Firefox and Safari downloads remain to be checked.
- **Copy** writes to the clipboard after the file was fetched from the server. Safari may refuse a clipboard write that
  does not happen directly inside the click; the page then says "Copy is not available in this browser. Use Download
  instead." The same sentence appears if the clipboard has not answered after 10 seconds. (Documented, not worked around.)
- FastAPI's own `/docs` page is served on the same origin as this page and loads its scripts from a CDN. Anything that
  runs there could read this tab's `sessionStorage`. This is how the app already behaved before the page existed, and it
  is not changed here: turn `/docs` off in production (`FastAPI(docs_url=None, redoc_url=None)`).
- Printing: a closed finding row does not print in every browser, so the page opens every finding row for the printout
  and puts them back afterwards. Check a print preview.
- The page needs JavaScript; without it you see a short notice. The REST API and the MCP server do not need it.
- More than 500 findings or 20 fixes are not all drawn (the page says how many are left out); the API returns them all.

## What was tested

- **Unit tests (`node --test tests/ui/*.test.mjs`, 123 tests):** the element builder and its refusals, formatting, score
  bands and invisible-character handling, the API client (every status code, timeouts and their clean-up, malformed
  bodies, id validation, request options), the state store, every view with hostile data, the whole flow against a
  scripted server (double clicks, a browser whose storage throws, a vanished report on reload, a restore that is
  overtaken, a clipboard that never answers, printing, every request path), and about 1,500 hostile-body trials.
- **Python tests (`tests/test_ui_server.py`, `tests/test_ui_static.py`, `tests/test_ui_css.py`, `tests/test_ui_js.py`):**
  routes, headers, HEAD, hostile asset paths, symlinks, the static rules, the contrast ratios computed from the colour
  tokens (text 4.5:1 and boundaries 3:1, light and dark), the forced-colours and print rules, and the Node tests.
- **Mutation checks:** the page code, the routes and the stylesheet were changed one rule at a time in a scratch copy
  (110 changes: a loosened allowlist, a removed header, a dropped clamp, a swapped message and so on) and the tests
  were run each time: 109 were caught. The one that was not removes `encodeURIComponent` from a fix id in a request
  path; it changes nothing, because an id is checked against `^[a-z_]{1,32}$` before the path is built.
- **A real run:** the real application under a real `uvicorn` server, with only the site fetchers, the TXT lookup and
  the resolver replaced by fakes, and the real page modules driven from Node over real HTTP: verification steps, a
  refused first audit, the audit, the proposed fixes, an export refused before approval, approve, download (byte for
  byte equal to the REST export), copy, revoke, re-audit under a `rerun-` snapshot, and a reload. No browser was used.
- **Chrome browser run (26 Nov 2026):** headless Chrome 158 drove the real page and REST API with synthetic site
  responses over the real guard's mock HTTP transport. Checked HTTP ownership on a non-default port, an unverified
  refusal, all three proposals, export refused before approval, approval, an actual download and clipboard copy matching
  the export, revocation, reload, a new tab without a saved report, and a fresh snapshot observing a deployed fixture
  file. Clipboard permission denial showed the documented fallback; granting permission allowed copying. Screenshots
  were inspected at 360 px and 1440 px in light and dark mode; neither layout overflowed the page. Forced colours were
  emulated, and a PDF print check expanded evidence and hid actions. No page errors or failed requests were observed.
  This is UI verification with synthetic data, not a real-site benchmark.

## Remaining manual and cross-browser checks

The Chrome run above covers the main flow, layouts, download, clipboard, reload and print behavior. Repeat the checks
below on a real site and in Firefox and Safari; native Windows high-contrast behavior also remains unverified.

1. The page loads with no console errors and nothing is blocked by the security policy. There should be no request for
   `/favicon.ico`.
2. Layout at 360 px wide, and at a normal desktop width, in light and dark mode. No sideways scrolling of the page. Long
   unbroken text (a long address, a long file name) wraps inside its card.
3. Tab through the page: the focus ring is visible everywhere (including on a finding row and on a diff box), and the
   order matches the reading order. Buttons are large enough to tap.
4. Type a site you control. Check the meta tag text can be selected and copied without extra characters.
5. After a successful audit: the score number, the bar and the band read clearly. Each finding row shows "Show
   evidence"; opening it shows "Hide evidence" and the evidence. The status and confidence chips look different from
   each other, and the bar is visible against its track.
6. Diffs: added lines are tinted and start with `+`; removed lines are struck through and start with `-`; long lines
   scroll sideways inside the diff box, not the page.
7. Approve a fix: **Download** saves a file with the expected name in Chrome, Firefox and Safari; **Copy** puts the text
   on the clipboard (in Safari it may say copy is not available); the file opens and contains what the diff showed.
8. A fix whose diff is very long (more than 2,000 lines) offers only **Reject** and says so.
9. Reload the tab after an audit: the report comes back. Close the tab and open a new one: it does not.
10. High-contrast or forced-colours mode (Windows): the bar, the chips, the `+` lines and the buttons are still
    distinguishable. Print preview: every finding shows its evidence, diffs are not cut off and the buttons are gone.
11. Text with a right-to-left override in an address or a diff shows as plain text or as a `⟦U+202E⟧` marker, never as
    reordered text.
