# Scoring (draft: frozen at the pre-registration commit)

## Timestamps per race
- `t_close`: the race's poll close (the latest close in its state or district), in ET.
- `t_call`: AP's call time, confirmed by a person, with exact times backfilled from AP's published calls.
- `t_found[e]`: for engine `e`, the start time of the first round in which `e` returned a page that the detector says names the confirmed winner of this 2026 general election.
- `t_wrong[e]`: the first round in which `e` returned a page that the detector says names a different candidate as the winner.

### What counts as "a page that names the winner" (the detector)
1. **Cheap filter:** the page's text contains one of the race's candidates (first name + surname when two candidates share a surname) and a win word (`detector/textmatch.py`).
2. **Model check:** TypeSafe Jev (`detector/questions.json`, exact model version pinned at the freeze) is given the race, its candidates, the page text and the page's reported publish date, and answers four typed questions: which candidate the text says won (or none); whether it is about this race in the 3 Nov 2026 general election; whether it reports a call, projection, concession or declared winner rather than a lead; and whether the winner is said to have beaten someone who isn't a candidate in this race (a sign of an earlier election). The page names candidate X only if all four pass the thresholds in `questions.json`. Validation on 783 real pages from past elections and primaries: `detector/eval/RESULTS.md` (no false winners, 84% recall).
3. The detector never sees AP's call. It reports which candidate a page names; the confirmed winner is applied afterwards (`state/build_state.py`).

**Primary rule: page level.** A page is judged on all the text that any engine returned for that URL in that round, and every engine that returned the URL gets the credit. Engines return snippets of very different lengths (Serper about 120 characters, Exa about 1,000), so judging each engine only on its own snippet would measure snippet length, not whether the engine found the page. **Secondary rule (reported, not ranked): engine level**, each engine judged on its own title and snippet only.

### Pre-written "results" pages are excluded
Some sites publish pages that already name winners before any vote is counted (found in testing on 31 Oct: a site whose pages said the Georgia Senate and Arizona governor races had been won, three days before the election). The detector can't tell such a page from a real report. So:
- The poller runs two **pre-close sweeps** for every race, 60 and 15 minutes before its polls close (`preclose_sweep_minutes` in `poller/config.json`).
- Any **site** (the last two labels of the host, e.g. `aheadofprint.com`) with a page that the detector says named a winner of any race before that race's polls closed is **excluded for every race, all night**, for every engine and the agent alike.
- Pre-close rounds never count as found or wrong. The excluded sites are listed in `state.json` (`excluded_sites`) and on the page.

### Computed row
- **Jevlin, all engines:** `t_found[jevlin] = min over the 6 engines of t_found[e]`, and the same for `t_wrong`. It is what one Jevlin call returned; nothing extra is run.
- **No agent row.** An "Agent on Jevlin" track (an AI agent searching through Jevlin on Senate and Governor races) was built and rehearsed, then dropped from the method on 3 Nov, before the freeze. Its code stays in `agent/track.py` but does not run and is not scored.

Resolution is one polling round: every 5 minutes for the first 3 hours after a race's polls close, every 15 minutes (:00, :15, :30, :45) from then to 12 hours after close, and every hour after that (`night` and `followup` in `poller/config.json`). The same schedule applies to every engine. Every engine is asked in the same call, so they all share each round's timestamp.

## Metrics per engine
- **Time to found:** `t_found − t_close`, in minutes. Report the median across races, and the share of races found within 30 minutes, 1 hour and 3 hours.
- **Versus the call:** `t_found − t_call`. A negative value means the engine had a page naming the winner before AP's call, usually from early returns or other outlets' projections. Report it, but don't rank on it.
- **Found first:** the number of races where `e` had the earliest `t_found`. Ties share the credit.
- **Lead over Serper:** `t_found[serper] − t_found[e]`, median across races that both engines found. Kept in the data for reference only. There is no baseline in anything public: every engine is ranked the same way, and "Jevlin, all engines" (the earliest of the 6 for each race) is the row the results lead with.
- **First coverage (the thread's lead):** for each single engine, the number of called races on which it was first (ties count for each); the best engine's count, out of the races any engine found; and how many engines were first at least once.
- **Never found:** the number of races with no `t_found` before the race went inactive.
- **Wrong:** the number of races with any `t_wrong`. Show it beside every other number.

## Rules
- Only confirmed races count: a person checked AP's call against AP's own page (one person; decided 3 Nov).
- Rounds served from the router's cache are dropped for every engine.
- Uncontested races are excluded.
- If a round fails for an engine (error, timeout, or trimmed by the router), that engine has no result for that round. Report failures per engine.
- No reruns, no dropped races and no changed queries after the pre-registration commit. Any forced change is logged in `CHANGES.md` with a timestamp and published.
- Headline: whichever engine has the best median time to found, whether or not it's AI search.

## Pre-registration
Before 5:00pm ET on Tue 3 Nov:
1. Commit `data/races.json` (with its queries), `poller/config.json`, `detector/questions.json` with the pinned Jev version, `detector/textmatch.py` and this file.
2. Compute the SHA-256 of a tarball of those files, and publish it: commit it to `PREREGISTRATION.md` and post it from @jevlinai.
3. Polls close at 6:00pm ET.

**Engines (6):** Exa, Brave, Parallel, Perplexity, Serper, TinyFish. A first freeze at 8:29am ET on 3 Nov (SHA-256 `ee5f5a94…`) also included Telem. Telem combines other providers rather than being one, so it was removed and the method frozen again before the hash was posted; the first hash showed on jevlin.ai/midterms for about two hours and was never used.
