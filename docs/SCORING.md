# Scoring (draft: frozen at the pre-registration commit)

## Timestamps per race
- `t_close`: the race's poll close (the latest close in its state or district), in ET.
- `t_call`: AP's call time, confirmed by a person, with exact times backfilled from AP's published calls.
- `t_found[e]`: for engine `e`, the start time of the first round in which the detector found that `e`'s results name the confirmed winner of this 2026 general election.
- `t_wrong[e]`: the first round in which `e`'s results named a different candidate as the winner.

Resolution is one polling round (5 minutes on election night, 1 hour after). Every engine is asked in the same call, so they all share each round's timestamp.

## Metrics per engine
- **Time to found:** `t_found − t_close`, in minutes. Report the median across races, and the share of races found within 30 minutes, 1 hour and 3 hours.
- **Versus the call:** `t_found − t_call`. A negative value means the engine had a page naming the winner before AP's call, usually from early returns or other outlets' projections. Report it, but don't rank on it.
- **Found first:** the number of races where `e` had the earliest `t_found`. Ties share the credit.
- **Lead over Google:** `t_found[serper] − t_found[e]`, median across races that both engines found.
- **Never found:** the number of races with no `t_found` before the race went inactive.
- **Wrong:** the number of races with any `t_wrong`. Show it beside every other number.

## Rules
- Only confirmed races count.
- Uncontested races are excluded.
- If a round fails for an engine (error, timeout, or trimmed by the router), that engine has no result for that round. Report failures per engine.
- No reruns, no dropped races and no changed queries after the pre-registration commit. Any forced change is logged in `CHANGES.md` with a timestamp and published.
- Headline: whichever engine has the best median time to found, whether or not it's AI search.

## Pre-registration
Before 5:00pm ET on Tue 3 Nov:
1. Commit `data/races.json`, the query templates, the engine list, the polling schedule, the detector prompt, and this file.
2. Compute the SHA-256 of a tarball of those files, and publish it: commit it to `PREREGISTRATION.md` and post it from @jevlinai.
3. Polls close at 6:00pm ET.
