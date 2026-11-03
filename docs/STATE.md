# state.json

The one file the page, the agent's tools and the Ask panel read. `state/build_state.py` writes it after every round from `data/races.json`, the detector's findings and `state/confirmed.json`, then pushes it to Workers KV. The page reads it at `/midterms/data.json`.

**The hard rule is built into the shape.** A race has a `call` (a winner) only when `status` is `"called"`, which happens only after a person has confirmed AP's call. Per-engine `found_at`/`wrong_at` are measured against the confirmed winner, so they are `null` until then. Nothing in this file is ever an engine's guess at a result.

A synthetic example (fake states and candidates, for building and testing only) is in `state/example-state.json`. Never put real race names or candidates in fixtures.

```jsonc
{
  "version": 1,
  "generated_at": "2026-11-03T21:05:00-05:00",
  "round_minutes": 5,
  "prereg": {"sha256": "…", "committed_at": "2026-11-03T17:00:00-05:00", "url": "https://github.com/…/PREREGISTRATION.md"},

  "engines": [
    {"id": "exa", "label": "Exa"},
    {"id": "brave", "label": "Brave"},
    {"id": "parallel-search", "label": "Parallel"},
    {"id": "perplexity-search", "label": "Perplexity"},
    {"id": "serper", "label": "Serper", "baseline": true},
    {"id": "tinyfish", "label": "TinyFish"},
    {"id": "jevlin", "label": "Jevlin, all engines", "computed": true},   // earliest of the 6, from the same calls
  ],

  "summary": {"races": 156, "polls_closed": 80, "called": 41, "leader": "perplexity-search"},

  "races": [{
    "id": "senate-xx",                        // same ids as data/races.json
    "office": "senate",                       // senate | governor | house
    "state": "XX", "district": null,          // House: "07" or "AL"
    "name": "Example Senate",                 // display name
    "poll_close_et": "2026-11-03T19:00:00-05:00",
    "status": "called",                       // open | closed | called
    "pending_reason": null,                   // when closed and not called: "runoff" | "ranked_choice" | "slow_count" | null
    "candidates": [{"name": "Ada Alpha", "party": "D", "incumbent": false}],
    "call": {                                 // null unless status == "called"
      "winner": "Ada Alpha", "party": "D",
      "ap_call_time": "2026-11-03T20:12:00-05:00",
      "ap_url": "https://apnews.com/…",
      "confirmed_by": ["…", "…"]
    },
    "engines": {                              // one entry per engine id in "engines" that covers this race
      "exa": {
        "found_at": "2026-11-03T20:25:00-05:00",   // round start; null = not found (yet)
        "minutes_after_close": 85,
        "minutes_vs_call": 13,                      // found_at − ap_call_time; negative = before AP's call
        "lead_vs_serper": 4,                        // serper.found_at − exa.found_at; positive = ahead of Serper
        "wrong_at": null,                           // first round naming a different candidate
        "url": "https://…",                         // the first page that named the winner
        "failed_rounds": 0
      }
    },
    "first": ["exa"]                          // engine ids with the earliest found_at (ties share); [] if none
  }],

  "leaderboard": [{
    "engine": "exa",
    "races_scored": 41,                       // called races this engine covers
    "median_minutes_after_close": 22,
    "found_within": {"30": 25, "60": 33, "180": 39},
    "found_first": 9,
    "median_lead_vs_serper": 3,
    "never_found": 2,
    "wrong": 1,
    "failed_rounds": 4,
    "cost_per_check_usd": 0.007
  }]
}
```

Notes
- `minutes_*` are whole minutes. Resolution is one round (5 minutes on the night).
- `jevlin` is computed: `found_at = min(found_at over the 6 engines)`, `wrong_at = min(wrong_at over the 6)`. Cost per check is the whole call's.
- Sorting the leaderboard is the reader's choice. The default is `median_minutes_after_close`, ascending, with `wrong` always shown beside it.
