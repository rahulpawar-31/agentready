# Site labels

One file per site, committed before any run. The task runner refuses to start unless the label and
`benchmark/criteria.json` are committed and unmodified.

Format:

- `site`: the site's origin, for example `https://example.com`.
- `tasks`: an object with all three tasks.
  - `main_info`: `urls`, the hand-labelled page(s) that state what the site offers or what it costs, and an
    required `quote_pattern` (a regular expression, at most 200 characters, matched case-insensitively).
    Choose a pattern that identifies the actual offer or pricing, for example `"plans start at \\$19"`.
    Missing patterns and patterns that match empty text are rejected. The judge also requires the quote to
    appear in the page text shown to the agent. Pattern quality still depends on the owner's independent label.
  - `contact_form`: `urls`, the page(s) with the contact form, and `required_fields`. Each required field is a
    name or a list of accepted names (case and punctuation are ignored when comparing, and a name needs at least
    one letter or digit). The agent sees the form's real `name`, `id` and label text, so list every one of those as
    an alias: for example `["email", "your-email", "contact[email]"]`. An alias only matches a field name that
    reduces to the same letters and digits, so `email` does not match `your-email`.
  - `docs_api`: `urls` of the docs or API reference, or `"none_exists": true` when there is none.
- Every URL must be on the site's own host. List every URL form the site really serves, for example both
  `/docs` and `/docs/index.html`: URLs are compared after normalisation (scheme, fragment, a trailing slash,
  default ports, `//`, `.`/`..` segments, query order and percent-escapes of ordinary characters are ignored),
  but `index.html` is a different page from `/`.
- The file name must be the site's host, for example `example.com.json` (a port becomes an underscore:
  `example.com_8443.json`).
- Every page the agent names must be a page it actually fetched, so a label cannot be passed by guessing a URL.
- `audit_checks` is required before audit-accuracy benchmarking. It maps each snapshot name to all 13 expected check outcomes: `"audit_checks": {"before": {"llms_txt": false, ...}, "after": {"llms_txt": true, ...}}`. Each check value is a boolean: `true` means it should pass; `false` means it should fail. Flat maps are rejected. Use the exact snapshot names you will run; names follow `[A-Za-z0-9_-]{1,64}` and cannot be `audits`. Independently label the baseline and the expected deployed state, and commit **both maps before the first run**. The after expectations can differ where planned fixes change a check. Do not edit labels after collection to match observed results. Do not copy the illustrative booleans from `EXAMPLE.json.example` as real labels. Keep a short rationale or supporting URL in label notes outside these maps.

Copy `EXAMPLE.json.example` to `<site>.json` and fill it in by hand after looking at the site yourself.

The audit runner refuses to fetch pages until the label file, including all 13 `audit_checks` booleans for the requested snapshot, is committed and unchanged. Run an audit snapshot with:

```bash
cd backend
export AGENTREADY_SECRET=<the same value the API server uses>
python -m app.runner.audit_cli --label ../benchmark/labels/example.test.json --snapshot before
```

This uses the same proof-of-control and guarded fetch layer as the API. Audit snapshots are saved immutably under `benchmark/results/audits/<snapshot>/<host>.json`; they are never overwritten. After both audit snapshots and task snapshots exist, the normal report command calculates per-check agreement and false-positive rate. A failed readiness check is the positive class for false-positive rate; unreachable checks are excluded and counted separately.
