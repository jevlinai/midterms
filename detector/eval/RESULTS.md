# Detector validation (1 Nov 2026, before the freeze)

**Question.** Can the detector (cheap filter, then TypeSafe Jev's typed questions) tell a page that reports a race's general-election winner from a page that doesn't: a preview, a poll, odds, a primary, or an earlier year's result?

## Test set
Real winners from 2026 can't exist before polls close, so positives come from **proxy races that have already happened**: the 4 November 2025 elections for Virginia governor, New Jersey governor and New York City mayor. The detector is asked about each with that election's own date. Negatives come from:
- the primaries of the same races (June 2025);
- **2026 races asked about with other years' pages**: Ossoff's January 2021 runoff win (Georgia Senate 2026), Hobbs' 2022 win (Arizona governor 2026) and Shapiro's 2022 win (Pennsylvania governor 2026);
- 2026 primary results for those races.

Pages: 16 queries through Jevlin (the poller's 7 engines and tier, no recency filter), $0.36. That gave **783 results that passed the cheap filter** (`candidates.jsonl`). Jev judged every one (`verdicts-nodate.jsonl`, about $0.03 per full pass).

**Labels are silver, not gold.** Positives and negatives were assigned by regular expressions over the text (`general win` vs `primary only`), and pages matching neither were left unlabelled. Every page the detector named where the label said "negative" was then **read by hand**. All but two turned out to be real positives, which the regex had missed. Those two are the undated 2022 Pennsylvania pages below.

## Results (final rule: winner ≥ 0.8, this election ≥ 0.4, is a call ≥ 0.5, other opponent ≤ 0.5)
- **Precision:** no page from another election, primary or year was named as a winner, and **no page named the wrong candidate**. The one page counted as a false positive ("How Mamdani Built a Winning Campaign… Now Mr. Mamdani has been elected") is a real positive the regex missed.
- **Recall:** 202 of 241 labelled positive pages named the right winner (**84%**). Most misses are pages where Jev was unsure the text was about that election (this-election score below 0.4).

## What changed because of this test
1. **A wording bug:** the "is this the election?" question hard-coded the 2026 date, so it rejected every 2025 proxy page. It now refers only to `race.election`.
2. **Old-year pages without a year in the text** ("Democrat Josh Shapiro wins Pennsylvania governor's race", AP, 2022) scored as 2026 results.
   - **Fix:** the page's reported publish date is given to the model (`page.published`).
   - **New typed question:** does the text say the winner beat someone who isn't a candidate in this race? The 2022 pages name Mastriano, who isn't on the 2026 ballot, and both scored 0.98 on this question, so both are rejected.
3. **A hard date rule was tried and dropped.** The rule was "a page whose every reported publish date is before election day can't name the winner". It added no precision on top of 1 and 2, and engines' dates are often wrong: an AP race-call article dated February 2025, Wikipedia's 2025 page dated 2021. So it would have penalised engines (mostly Parallel and Perplexity) for bad metadata rather than for being slow.
4. **Pre-written "results" pages** (found 31 Oct: pages already stating 2026 winners) pass the detector, because nothing in their text gives them away. They are handled by the **pre-close sweep and site exclusion** in `docs/SCORING.md`, not by the detector. That rule depends on the precision shown here: an old AP page wrongly named before polls close would exclude apnews.com.

## Reproduce
```
python3 -I detector/eval/collect.py --budget-usd 0.60 > detector/eval/candidates.jsonl   # Jevlin, about $0.36
DATE_RULE=0 VERDICTS_OUT=verdicts-nodate.jsonl uv run python detector/eval/run_jev.py   # Jev, about $0.03
```
Jev version: `jev-1.14.0`.
