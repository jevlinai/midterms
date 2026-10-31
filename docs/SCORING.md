# Scoring (draft: frozen at the pre-registration commit)

## Timestamps per race
- `t_close`: the race's poll close (the latest close in its state or district), in ET.
- `t_call`: AP's call time, confirmed by a person, with exact times backfilled from AP's published calls.
- `t_found[e]`: for engine `e`, the start time of the first round in which `e` returned a page that the detector says names the confirmed winner of this 2026 general election.
- `t_wrong[e]`: the first round in which `e` returned a page that the detector says names a different candidate as the winner.

### What counts as "a page that names the winner" (the detector)
1. **Cheap filter:** the page's text contains one of the race's candidates (first name + surname when two candidates share a surname) and a win word (`detector/textmatch.py`).
2. **Model check:** TypeSafe Jev (`detector/questions.json`, exact model version pinned at the freeze) answers three typed questions about the page: which candidate it says won (or none), whether it is about this race in the 3 Nov 2026 general election, and whether it reports a call, projection, concession or declared winner rather than a lead. The page names candidate X only if all three pass the thresholds in `questions.json`.
3. The detector never sees AP's call. It reports which candidate a page names; the confirmed winner is applied afterwards (`state/build_state.py`).

**Primary rule: page level.** A page is judged on all the text that any engine returned for that URL in that round, and every engine that returned the URL gets the credit. Engines return snippets of very different lengths (Google via Serper about 120 characters, Exa about 1,000), so judging each engine only on its own snippet would measure snippet length, not whether the engine found the page. **Secondary rule (reported, not ranked): engine level**, each engine judged on its own title and snippet only.

### Computed and agent rows
- **Jevlin, all engines:** `t_found[jevlin] = min over the 7 engines of t_found[e]`, and the same for `t_wrong`. It is what one Jevlin call returned; nothing extra is run.
- **Agent on Jevlin:** an agent (model, prompt, step budget and tools frozen in `agent/prompts/track.txt` and `agent/track.json`) searches through Jevlin for Senate and Governor races every 10 minutes for 3 hours after polls close. The pages it would cite are judged by the same detector. Model output can vary between runs; every step is logged in `raw/agent/`.

Resolution is one polling round (5 minutes on election night, 1 hour after). Every engine is asked in the same call, so they all share each round's timestamp.

## Metrics per engine
- **Time to found:** `t_found − t_close`, in minutes. Report the median across races, and the share of races found within 30 minutes, 1 hour and 3 hours.
- **Versus the call:** `t_found − t_call`. A negative value means the engine had a page naming the winner before AP's call, usually from early returns or other outlets' projections. Report it, but don't rank on it.
- **Found first:** the number of races where `e` had the earliest `t_found`. Ties share the credit.
- **Lead over Google:** `t_found[serper] − t_found[e]`, median across races that both engines found.
- **Never found:** the number of races with no `t_found` before the race went inactive.
- **Wrong:** the number of races with any `t_wrong`. Show it beside every other number.

## Rules
- Only confirmed races count (Senate and Governor calls confirmed by two people).
- Rounds served from the router's cache are dropped for every engine.
- Uncontested races are excluded.
- If a round fails for an engine (error, timeout, or trimmed by the router), that engine has no result for that round. Report failures per engine.
- No reruns, no dropped races and no changed queries after the pre-registration commit. Any forced change is logged in `CHANGES.md` with a timestamp and published.
- Headline: whichever engine has the best median time to found, whether or not it's AI search.

## Pre-registration
Before 5:00pm ET on Tue 3 Nov:
1. Commit `data/races.json` (with its queries), `poller/config.json`, `detector/questions.json` with the pinned Jev version, `detector/textmatch.py`, the agent track's prompt and config, and this file.
2. Compute the SHA-256 of a tarball of those files, and publish it: commit it to `PREREGISTRATION.md` and post it from @jevlinai.
3. Polls close at 6:00pm ET.
