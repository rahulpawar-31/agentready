# Site labels

One file per site, committed before any run. The task runner refuses to start unless the label and
`benchmark/criteria.json` are committed and unmodified.

Format:

- `site`: the site's origin, for example `https://example.com`.
- `tasks`: an object with all three tasks.
  - `main_info`: `urls`, the hand-labelled page(s) that state what the site offers or what it costs, and an
    optional `quote_pattern` (a regular expression, at most 200 characters, matched case-insensitively). Without
    it the task only checks that the labelled page was fetched and that the quote is on it, so boilerplate such
    as a copyright line would pass: set a pattern such as `"\\$\\d+"` when the page states prices.
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

Copy `EXAMPLE.json.example` to `<site>.json` and fill it in by hand after looking at the site yourself.
