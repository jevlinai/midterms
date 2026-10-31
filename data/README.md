# data/

`races.json`: one object per race. Draft schema:

```json
{
  "id": "senate-oh-special",
  "office": "senate",
  "state": "OH",
  "district": null,
  "special": true,
  "uncontested": false,
  "poll_close_et": "2026-11-03T19:30:00-05:00",
  "candidates": [
    {"name": "…", "surname": "…", "party": "D", "incumbent": false},
    {"name": "…", "surname": "…", "party": "R", "incumbent": true}
  ],
  "query": "Ohio Senate special election results 2026",
  "sources": ["https://ballotpedia.org/…", "https://<state election office>/…"]
}
```

- `office`: one of `senate`, `house` or `governor`.
- `district`: a number for House races. At-large districts use `"AL"`.
- `surname` is what the detector's cheap filter matches. Give the common short form ("Ocasio-Cortez", not "Alexandria Ocasio-Cortez").
- Use one id scheme everywhere: `senate-<st>`, `senate-<st>-special`, `governor-<st>`, `house-<st>-<nn>`.
- Every Senate and Governor entry is checked by a person. Check a sample of House entries, and every competitive one.

`confirmed.json`, written by the confirm tool on the night: `{race_id, winner_surname, ap_call_time, ap_url, confirmed_by[], confirmed_at}`.
