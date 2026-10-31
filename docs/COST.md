# Cost

These are Jevlin's current per-call rates, from `../Jevlin-core/deploy/live/searchrouter.toml`. Jevlin has no bulk pricing with any provider. Usman tops up providers inside Jevlin; we never call providers directly.

## One check

One race, one round, 7 engines:

| Engine | Per call |
|---|---|
| exa | $0.007 |
| brave | $0.005 |
| parallel-search | $0.005 |
| perplexity-search | $0.005 |
| telem | $0.0025 |
| serper (Google) | $0.0003 |
| tinyfish | $0 |
| **Total** | **≈ $0.025** |

Adding exa-lite ($0.004) and parallel-fast ($0.001) brings it to about $0.030, but they share indexes with exa and parallel, so leave them out.

Each extra wording (`search_queries`) is one more billed call per engine, so two wordings means about $0.050 per check.

## Scenarios

| Scope | How | Checks | Cost |
|---|---|---|---|
| All 506 races | 5-minute rounds from each race's poll close until all engines show the winner (average about 3 h, so about 36 rounds), then hourly for 7 days for about 60 uncalled races | 506×36 + 60×24×7 ≈ 28,300 | **≈ $700** |
| All 506 races, no stop rule | 5-minute rounds for 10 h, plus the same follow-up | 506×120 + 10,080 ≈ 70,800 | ≈ $1,770 |
| All 506 races, **10-minute rounds** | Same stop rule, about 18 rounds a race on the night, plus the same follow-up | 506×18 + 10,080 ≈ 19,200 | **≈ $480** |
| Senate + Governor + about 80 competitive House races | Stop rule, plus follow-up for about 30 races | 151×36 + 30×168 ≈ 10,500 | **≈ $260** |

Extras:
- **Winner detector model:** runs only on filter hits. Estimate it once the model is chosen. Expect tens of dollars, not hundreds.
- **Smoke tests and the dry run:** under $20.

## How often to check
Each race is checked once per round (every 5 or 10 minutes), not every second. Search indexes pick up new pages in minutes to hours, so 5-minute rounds are the finest resolution worth paying for, and 10-minute rounds probably lose little. A cheaper option is to check every 5 minutes for the first 2 hours after a race's polls close (when most calls happen), then every 15 minutes.

## Rate

At the peak (around 9–10pm ET, when most races are active), full scope means about 400 active races per 5-minute round. That's about 80 checks a minute in total (each race still only once per round), or about 9 provider calls a second across all races and 7 engines. Before Tuesday, confirm with Usman:
- each provider's rate limit,
- our key's rate and concurrency limits in Jevlin, and its daily spend cap (`GET /v1/limits`),
- that the max tier's per-query ceiling covers all 7 engines.

## Rule
Before any run bigger than a smoke test, state the expected spend and get a yes from Hammad.
