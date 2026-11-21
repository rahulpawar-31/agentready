# Site labels

One file per site, committed before any run. The task runner refuses to start unless the label and
`benchmark/criteria.json` are committed and unmodified.

Format:

- `site`: the site's origin, for example `https://example.com`.
- `tasks`: an object with all three tasks.
  - `main_info`: `urls`, the hand-labelled page(s) that state what the site offers or what it costs.
  - `contact_form`: `urls`, the page(s) with the contact form, and `required_fields`. Each required field is a
    name or a list of accepted names (case and punctuation are ignored when comparing).
  - `docs_api`: `urls` of the docs or API reference, or `"none_exists": true` when there is none.
- Every URL must be on the site's own host.

Copy `EXAMPLE.json.example` to `<site>.json` and fill it in by hand after looking at the site yourself.
