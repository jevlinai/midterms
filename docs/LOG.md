# Log: what happened outside the plan

Times are ET. The method itself (race list, queries, engines, schedule, scoring) did not change after the pre-registration was published.

| When | What |
|---|---|
| 3 Nov, before 6:00pm | Pre-registration published (SHA-256 `0d5581c776215f968e559f89995e686627d9d926d2851d70e8cd06ed1037ec03`). Earlier freezes that were replaced are listed in `PREREGISTRATION.md`. |
| 3–4 Nov, overnight | TinyFish's upstream returned errors on part of its calls; those rounds are kept as failed, and the router used TinyFish's direct API for some of them. |
| 4 Nov, 7:15am – 10:00am | **No polling.** The machine running the poller lost its network and restarted. The 7:30am to 9:15am rounds did not run; the 9:30am and 9:45am rounds ran but every request failed (network still down). Polling resumed with the 10:00am round. Races affected are only those whose first find would have fallen in that window. |
| 4 Nov, morning | AP calls checked during the outage were entered on a stand-in confirm page and merged afterwards; each was checked by a person against AP's page before it counted. |
| 4 Nov, 1:00pm | Snapshot for the results thread and the Hugging Face dataset: 112 of 156 races called. Polling of uncalled races continues on the published schedule. |
| 4 Nov | "Wrong answer" flags are under review: most are detector misreads (live results pages listing both candidates, "polls open" articles, earlier-election pages). They are not part of the headline ranking. |
