# The agent

On election night we ran an agent built on Jevlin beside the measurement. It does four jobs: answers questions about the results, finds AP's race calls for the people who confirm them, drafts posts for a person to publish, and competes as "Agent on Jevlin" in the pre-registered measurement. It never decides a result: a race has a winner only after a person has confirmed AP's call.

The code is in `agent/` (Python, LangChain/LangGraph, Claude through the Anthropic API). Every search goes through Jevlin.

## Run it

You need [uv](https://docs.astral.sh/uv/), Python 3.12 or later, a Jevlin key and an Anthropic API key.

```bash
git clone <this repo> && cd midterms
uv sync
```

Environment:

| Variable | What it is |
|---|---|
| `MIDTERMS_MODEL` | The model. We used `claude-sonnet-5-5`. `offline` runs without any model, for free (tests and dry runs). Required: there is no default. |
| `ANTHROPIC_API_KEY` | Your Anthropic key. |
| `MIDTERMS_RUN_CAP_USD`, `MIDTERMS_DAILY_CAP_USD`, `MIDTERMS_TOTAL_CAP_USD` | Spending caps for model calls, in US dollars: per run, per day (UTC) and in total. All three are required for a billed model; a call that would go over a cap is not made. |
| `MIDTERMS_AGENT_JEVLIN_KEY` | Your Jevlin key (`sr-…`). Without it, the key from `~/.jevlin/credentials.json` (the Jevlin CLI's login) is used. |
| `MIDTERMS_STATE` | The results file. Default `state/state.json`; `state/example-state.json` is a synthetic one to try things with. |

Keys are read from the environment only, and never printed.

## Commands

```bash
# Ask about the results. The agent answers only from the results file, through four read-only tools
# (get_race, list_races, leaderboard, compare). It names a winner only for a race AP has called and a
# person has confirmed.
uv run midterms ask "Which engine found governor races first?"

# The agent track: what it would run, and the most it could spend on Jevlin. No calls.
uv run midterms track --dry-run

# Find AP's call for races whose polls have closed and that are not called yet. Writes proposals
# to state/proposals.json for a person to check; it never confirms anything.
uv run midterms call-finder --once --races senate-ga
```

Also: `draft-post <race_id>`, `drafts`, `publish`, `reject` and `thread` (below), and `track` itself, which needs `--budget-usd`.

## Posts: a person publishes every one

The agent can write drafts. It has no tool that posts.

1. `uv run midterms draft-post <race_id>` drafts a post for a called race. A deterministic check (`agent/guard.py`) compares every number, time, engine name, candidate name and link in the draft with the results file, and rejects predictions, odds and party talk. If the draft fails, a fixed template is used instead. The draft goes to the queue in `state/drafts/`.
2. `uv run midterms drafts` lists the queue.
3. A person runs `uv run midterms publish <id> --by <name>`. It shows the text and runs the check again against the current results. It posts to X only if `MIDTERMS_PUBLISH_LIVE=true` is set; otherwise it prints what it would send. A ledger in `state/agent.sqlite` keys each post by race and text, so the same post cannot go out twice.
4. `uv run midterms reject <id> --by <name>` takes a draft out of the queue.

`uv run midterms thread` drafts the results thread from the final leaderboard. Post 1 leads with what holds whatever the ranking: no single engine was first on every race (how many races the best one was first on, and how many engines were first at least once), and asking every engine at once had each result as soon as the fastest one did. If one engine was first everywhere, post 1 says so instead. Post 2 is the ranking against Serper, written in two versions in advance ("AI search was faster" and "Serper was faster"); the leaderboard decides which is queued, and a tie counts as Serper. The thread is queued as a reply chain, and each part is published like any other draft.

## What was pre-registered

Before the first polls closed (Tue 3 Nov 2026, 6:00pm ET), we committed the method and published its SHA-256 (see `PREREGISTRATION.md` and `docs/SCORING.md`). For the agent track that covers:

- `agent/track.json`:
  - the model, `claude-sonnet-5-5`, and its effort setting
  - the step budget: 3 searches and 2 fetches per check
  - the schedule: Senate and Governor races, every 10 minutes from 4 minutes after poll close, for 3 hours
  - the Jevlin request: the same 7 engines, tier, recency and view as the poller
- `agent/prompts/track.txt`: the agent's instructions, word for word.
- The rules around the model:
  - Search 1 is the poller's own result for the race's pre-registered query, not a new call.
  - The agent returns pages, not a verdict. A page counts only if its text came back from Jevlin word for word.
  - The same detector judges the agent's pages and the engines' pages.

A model's output can vary between runs. Every step the agent took (each search and fetch, with its full response, cost and timing, and the model's messages) is in `raw/agent/`.

## Use it from your own coding agent

`skills/midterms/SKILL.md` is a skill for any coding agent with the Jevlin CLI. It answers race questions with citations, and says a race is "not called yet" unless AP has called it.
