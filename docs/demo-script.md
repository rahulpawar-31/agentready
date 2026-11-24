# Demo script (about 3 minutes)

The video follows the one flow in the PRD (FR-8.1): paste a URL, prove control, see the score and evidence, review
fixes as a diff, approve. It contains **no numbers of its own**: every figure you say aloud is read from your own
evaluation report (`benchmark/evaluation.md`) or from what is on screen at that moment. If the benchmark has not been
run, do not show a results table; say so.

Use a site the team controls (or has written permission to change), already registered in `benchmark/sites.json`.

## Timeline

| Time | Screen | Do | Say |
|---|---|---|---|
| 0:00 | Title card or the README | nothing | One sentence on the problem: agents are becoming users of websites, and most sites were built for people. Say the A1 wording only if the live-page re-check in the submission checklist passed; otherwise paraphrase without quoting. |
| 0:20 | The page, step 1 | type the team site's address, press "Get verification steps" | "AgentReady only looks at a site you can prove you control." |
| 0:35 | Step 2, verification | show the meta tag and the DNS record; if time allows, press "Verify and audit" before adding the tag to show the refusal | "Before the check, it looks up a DNS record, or fetches the home page once for the tag. Nothing else is requested until this passes." |
| 1:00 | Step 3, the score | press "Verify and audit" with the proof in place; when the report appears, open one finding, then one "not detected" finding | "Every finding shows the URL, what was checked, what was seen and how sure we are. 'Not detected' means this check did not see it, not that it is absent." |
| 1:25 | Step 4, fixes | press "Propose fixes"; open the `llms.txt` diff | "These are proposals. Every one is validated and shown as a diff against what the audit saw." |
| 1:50 | Step 4, approval | press "Approve" on `llms.txt` | "Approving changes nothing on the site. AgentReady never deploys." |
| 2:05 | Step 4, export | press "Download", open the downloaded file | "Now the owner downloads or copies the file, and deploys it themselves." |
| 2:15 | The live site | show the deployed `/llms.txt` in a tab | "The owner put it on the site." |
| 2:25 | Step 3 again | press "Re-audit with fresh pages" | "Same checks, fresh pages." Read the new score and what changed from the screen. |
| 2:40 | `benchmark/evaluation.md` | scroll to the paired comparison and the sample-size line | Read the real numbers from the evaluation report, the number of sites, the number of valid runs, and the line that says whether it is a small sample. "These results describe only the sites we tested." |
| 2:55 | A terminal or an MCP client | an agent calls `audit_site`, then `propose_fixes` | "Agents can call the same checks over MCP. They can propose fixes but cannot approve or export them." Close with the limits sentence: static pages only, tested sites only, discovery and retrieval only. |

## Before recording

- [ ] The API runs with the same `AGENTREADY_SECRET` that was used when the proof was added to the site.
- [ ] `python -m app.runner.preflight --check-control` shows nothing blocked.
- [ ] The benchmark has been run and `benchmark/evaluation.md` exists (or the results segment is cut).
- [ ] A dry run end to end on the real team site, including the download and the re-audit.
- [ ] Browser zoom set so the diff is readable at the recording size; dark and light mode both checked.
- [ ] No secret on screen: not `ANTHROPIC_API_KEY`, not `AGENTREADY_SECRET`, not the shell history.
- [ ] The report id is never shown: the page keeps it out of the address bar, so do not open the REST URLs on camera.
- [ ] The first audit of a new snapshot takes about ten seconds: cut the wait or keep the on-screen status.

## If something fails on camera

- *Verification is refused*: show the message; it is the product working. Add the tag, press the button again.
- *The audit is busy or slow*: wait for the status line; do not click twice.
- *An export is refused (409)*: the fix was not approved, or it changed after approval. Approve it again or say so.
- *Anything else*: stop, fix it, record again. Do not edit the video to hide a failure that matters.
