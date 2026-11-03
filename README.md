# Midterms: which search finds election results first?

On US election night (Tuesday 3 November 2026), every Senate, Governor and competitive House race (156 in all) runs through [Jevlin](https://jevlin.ai): one call asks six search engines the same question, and we record how many minutes after polls close each engine first returns a page that names the winner.

The engines are Exa, Brave, Parallel, Perplexity, Serper and TinyFish. Every engine is ranked the same way, by minutes after polls closed. "Jevlin, all engines" is the earliest of the six for each race, from the same calls.

- Live page: **[jevlin.ai/midterms](https://jevlin.ai/midterms)**
- Method: [docs/SCORING.md](docs/SCORING.md)
- Pre-registration: [PREREGISTRATION.md](PREREGISTRATION.md)

## How a race is measured

1. **Schedule.** Each race is searched every 5 minutes for the first 3 hours after its polls close, then every 15 minutes up to 12 hours, then hourly (`poller/config.json`). Two sweeps before polls close find sites that name a "winner" early; those sites are excluded all night.
2. **Winner check.** A page counts when a cheap name filter and then a pinned model (TypeSafe `jev-1.14.0`, `detector/`) agree that it names the winner of this race in 2026. Pages naming a different candidate count as wrong answers.
3. **AP first.** No winner shows anywhere until AP has called the race and a person has checked the call against AP's own page (`confirm/`). Search results never decide a race.
4. **Score.** Per engine: median minutes from poll close to the first page naming the confirmed winner, races found first, and wrong answers.

## Verify the pre-registration

The race list, queries, engines, polling schedule, winner check and scoring rule were frozen before the first polls closed, and their SHA-256 was posted from [@jevlinai](https://x.com/jevlinai).

```
git clone https://github.com/jevlinai/midterms && cd midterms
uv run python scripts/preregister.py verify      # rebuilds the archive; prints "match"
```

Or check files one at a time: `PREREGISTRATION.md` lists a SHA-256 for each method file, so `shasum -a 256 data/races.json` (and so on) should match.

## Data

Every query and every response from the night is published after it ends, along with the final results (`state.json`). Until then, the live page reads the current results from `https://midterms-state.hammadtariq.workers.dev/state`.

## Layout

- `data/` race list with candidates, poll close times and queries; `review-log.jsonl` records each person's sign-off
- `poller/` one Jevlin call per race per round
- `detector/` the winner check
- `confirm/` the tool a person uses to confirm AP calls
- `state/` builds the results the page shows
- `agent/` helpers for the night: finds AP calls for the confirmer and drafts posts (it never confirms or posts)
- `cloudflare/state-worker/` serves the live results to the page

Race calls come from AP and are checked by a person before they appear. This is not an official results source.

## License

MIT
