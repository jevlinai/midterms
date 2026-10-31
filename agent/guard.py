"""The post guard: a deterministic check that a draft post says only what state.json says.

`check_post(text, race_id, state) -> (ok, reasons)`. Every rule that fails adds a reason; ok means
no reasons. The guard is strict on purpose: a draft that fails falls back to `template_post`, which
always passes, so strictness costs wording, never a post.

Rules
1. Length: at most 280, counted as X counts (a link is 23; wide characters count 2).
2. Links: only the race's AP link (once called) and jevlin.ai.
3. No commentary, predictions, odds or party talk: a word and phrase list.
4. A winner, or any word about a call ("called", "won", ...), only if the race is called, and
   then the post must carry that race's AP link. Only the confirmed winner may be named; naming
   another candidate, or a candidate from another race, fails.
5. Names: every run of capitalised words must be known (race name, states, engines, offices,
   the confirmed winner). This catches an invented name the lists above can't know about.
6. Engines: only engines in the measurement, only engines that cover this race, and a "first"
   claim must name an engine in the race's `first`. Engine results exist only for called races.
7. Numbers: "N min ahead of/behind Google", "after polls closed", "after/before AP" must equal
   the named engine's number; every other number must be one of this race's numbers (the
   ported `check_text`); the only free number is the year 2026. Clock times must be the AP call
   time or poll close, in ET.
"""

import re
from datetime import datetime, timedelta, timezone

from agent.state import engines as engine_map
from agent.state import is_called, race_by_id
from agent.verify import check_text

ET = timezone(timedelta(hours=-5))  # EST: DST ended 1 Nov 2026, before election night
MAX_WEIGHTED = 280
URL_WEIGHT = 23
LIVE_URL = "jevlin.ai/midterms"
ALLOWED_HOSTS = {"jevlin.ai", "www.jevlin.ai"}

# Words and phrases that are commentary, prediction, odds or party talk. Matched case-insensitively
# on word boundaries. "ahead"/"behind" are allowed only about engines vs Google (rule 7).
BANNED = [
    "projected", "projects", "projection", "likely", "unlikely", "probably", "should", "will win", "would win",
    "expected to", "poised", "on track", "favored", "favoured", "favorite", "favourite", "frontrunner",
    "front-runner", "odds", "chance", "chances", "predict", "predicted", "prediction", "forecast", "polls show",
    "poll shows", "polling", "landslide", "victory", "victorious", "victory for", "defeat", "defeats", "defeated",
    "beat", "beats", "upset", "stunning", "shock", "historic", "crushing", "blowout", "narrowly", "comfortably",
    "flip", "flips", "flipped", "hold", "holds", "keeps", "gain", "gains", "pickup", "pick-up", "wave",
    "blue wave", "red wave", "mandate", "concede", "concedes", "conceded", "leads", "leading", "trailing", "trails",
    "close race", "tight race", "toss-up", "tossup", "battleground", "swing",
    "democrat", "democrats", "democratic", "dem", "dems", "republican", "republicans", "gop", "libertarian",
    "independent", "liberal", "liberals", "conservative", "conservatives", "progressive", "progressives",
    "maga", "left-wing", "right-wing", "far-left", "far-right", "radical", "extremist", "party", "parties",
    "good news", "bad news", "great", "terrible", "disaster", "congratulations", "congrats", "deserved",
    "finally", "sadly", "unfortunately", "thankfully", "hopefully",
]
_BANNED_RE = re.compile(r"(?<![\w-])(" + "|".join(re.escape(b) for b in sorted(BANNED, key=len, reverse=True))
                        + r")(?![\w-])", re.IGNORECASE)
_BANNED_SYMBOLS = re.compile(r"\((?:D|R|I|L|G)(?:-[A-Z]{2})?\)|[\U0001F300-\U0001FAFF☀-➿]")
CALL_WORDS = re.compile(r"\b(called|calls?|call for|wins?|won|winner|winning|elected|re-elected)\b", re.IGNORECASE)
OTHER_SEARCH = re.compile(r"\b(Bing|DuckDuckGo|Kagi|You\.com|Tavily|ChatGPT|OpenAI|Gemini|Yahoo|Baidu|Yandex|"
                          r"Ecosia|Startpage|Mojeek|SearXNG|Grok|Copilot)\b", re.IGNORECASE)

URL_RE = re.compile(r"https?://[^\s<>\"']+|\b(?:[a-z0-9-]+\.)+(?:ai|com|org|net|io|news|gov|us|co|dev|app|xyz)"
                    r"(?:/[^\s<>\"']*)?", re.IGNORECASE)
CLOCK_RE = re.compile(r"\b(\d{1,2})(?::(\d{2}))?\s*([ap])\.?m\.?(?![a-z])", re.IGNORECASE)
CAP_RUN = re.compile(r"\b[A-Z][\w'.-]*(?:[ \t]+[A-Z][\w'.-]*)+")
MINUTE_CLAIM = re.compile(
    r"(?<![\w.])(\d+(?:\.\d+)?)\s*(?:min|mins|minutes?)\b"
    r"(?:\s+(ahead of|behind|before|after)\s+(Google|AP\b|AP's call|the AP call|polls?\s+clos\w*|poll\s+close))?",
    re.IGNORECASE)

STATE_NAMES = {
    "AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas", "CA": "California", "CO": "Colorado",
    "CT": "Connecticut", "DE": "Delaware", "FL": "Florida", "GA": "Georgia", "HI": "Hawaii", "ID": "Idaho",
    "IL": "Illinois", "IN": "Indiana", "IA": "Iowa", "KS": "Kansas", "KY": "Kentucky", "LA": "Louisiana",
    "ME": "Maine", "MD": "Maryland", "MA": "Massachusetts", "MI": "Michigan", "MN": "Minnesota",
    "MS": "Mississippi", "MO": "Missouri", "MT": "Montana", "NE": "Nebraska", "NV": "Nevada",
    "NH": "New Hampshire", "NJ": "New Jersey", "NM": "New Mexico", "NY": "New York", "NC": "North Carolina",
    "ND": "North Dakota", "OH": "Ohio", "OK": "Oklahoma", "OR": "Oregon", "PA": "Pennsylvania",
    "RI": "Rhode Island", "SC": "South Carolina", "SD": "South Dakota", "TN": "Tennessee", "TX": "Texas",
    "UT": "Utah", "VT": "Vermont", "VA": "Virginia", "WA": "Washington", "WV": "West Virginia",
    "WI": "Wisconsin", "WY": "Wyoming", "DC": "District of Columbia",
}
VOCAB = {
    "AP", "ET", "EST", "Live", "Google", "Serper", "Jevlin", "Associated", "Press", "First", "Election", "Night",
    "Midterms", "Midterm", "US", "U.S.", "Senate", "House", "Governor", "Governor's", "Gubernatorial", "District",
    "Special", "Race", "Results", "Result", "Search", "Engine", "The", "A", "No", "It", "At", "In", "On", "For",
    "Both", "All", "Of", "And", "Congressional", "Seat", "Tuesday", "Wednesday", "Nov", "November", "Via",
} | {w for name in STATE_NAMES.values() for w in name.split()}


# ---------- helpers ----------

def weighted_length(text: str) -> int:
    """X's count: each link is 23; code points outside X's light ranges count 2."""
    n, last = 0, 0
    for m in URL_RE.finditer(text):
        n += _char_weight(text[last:m.start()]) + URL_WEIGHT
        last = m.end()
    return n + _char_weight(text[last:])


def _char_weight(s: str) -> int:
    light = ((0, 4351), (8192, 8205), (8208, 8223), (8242, 8247))
    return sum(1 if any(a <= ord(c) <= b for a, b in light) else 2 for c in s)


def _urls(text: str) -> list[str]:
    return [m.group(0).rstrip(".,;:!?)]'\"") for m in URL_RE.finditer(text)]


def _norm_url(u: str) -> str:
    u = re.sub(r"^https?://", "", u.strip(), flags=re.IGNORECASE)
    return u.rstrip("/").lower()


def _host(u: str) -> str:
    return _norm_url(u).split("/", 1)[0]


def _word_re(phrase: str) -> re.Pattern:
    return re.compile(r"(?<![\w-])" + re.escape(phrase) + r"(?![\w-])")


def to_et(iso: str) -> datetime:
    return datetime.fromisoformat(iso).astimezone(ET)


def clock(iso: str) -> str:
    t = to_et(iso)
    return f"{t.hour % 12 or 12}:{t.minute:02d}{'am' if t.hour < 12 else 'pm'}"


def _labels(state: dict) -> list[tuple[str, str]]:
    """(name as written, engine id), longest first, including short forms ("Perplexity", "Google")."""
    out = []
    for eid, e in engine_map(state).items():
        label = e.get("label", eid)
        out.append((label, eid))
        short = label.split(" (")[0].split(",")[0]
        if short != label and eid not in ("jevlin",):  # bare "Jevlin" is the product, not the computed row
            out.append((short, eid))
        out.append((eid, eid))
    return sorted(set(out), key=lambda x: -len(x[0]))


def engine_mentions(text: str, state: dict) -> list[tuple[int, str]]:
    """(position, engine id) for each engine named, leftmost first, without overlaps."""
    taken: list[tuple[int, int]] = []
    found = []
    for name, eid in _labels(state):
        for m in _word_re(name).finditer(text):
            if any(a < m.end() and m.start() < b for a, b in taken):
                continue
            taken.append((m.start(), m.end()))
            found.append((m.start(), eid))
    return sorted(found)


def _blank(text: str, spans: list[tuple[int, int]]) -> str:
    chars = list(text)
    for a, b in spans:
        chars[a:b] = " " * (b - a)
    return "".join(chars)


def _race_numbers(race: dict) -> list[float]:
    vals = []
    for e in (race.get("engines") or {}).values():
        for k in ("minutes_after_close", "minutes_vs_call", "lead_vs_google"):
            if isinstance(e.get(k), (int, float)) and not isinstance(e.get(k), bool):
                vals.append(float(e[k]))
    return vals


# ---------- the guard ----------

def check_post(text: str, race_id: str, state: dict) -> tuple[bool, list[str]]:
    reasons: list[str] = []
    race = race_by_id(state, race_id)
    if race is None:
        return False, [f"no race {race_id!r} in state"]
    text = text.strip()
    if not text:
        return False, ["empty post"]
    called = is_called(race)
    call = race.get("call") or {}
    ap_url = call.get("ap_url") if called else None

    # 1. length
    if (n := weighted_length(text)) > MAX_WEIGHTED:
        reasons.append(f"too long: {n} of {MAX_WEIGHTED} (links count 23)")

    # 2. links
    urls = _urls(text)
    for u in urls:
        if ap_url and _norm_url(u) == _norm_url(ap_url):
            continue
        if _host(u) in ALLOWED_HOSTS:
            continue
        reasons.append(f"link not allowed: {u} (only the race's AP link and jevlin.ai)")
    has_ap_link = bool(ap_url) and any(_norm_url(u) == _norm_url(ap_url) for u in urls)

    # Work on the text without links and without the race's own name (both may hold digits/capitals).
    spans = [m.span() for m in URL_RE.finditer(text)]
    if race.get("name"):
        spans += [m.span() for m in _word_re(race["name"]).finditer(text)]
    body = _blank(text, spans)

    # 3. commentary
    for m in _BANNED_RE.finditer(body):
        reasons.append(f"commentary, prediction or party word: {m.group(0)!r}")
    for m in _BANNED_SYMBOLS.finditer(body):
        reasons.append(f"party label or emoji not allowed: {m.group(0)!r}")
    if OTHER_SEARCH.search(body):
        reasons.append(f"names a search engine that is not in the measurement: {OTHER_SEARCH.search(body).group(0)!r}")

    # 4. calls and candidates
    if CALL_WORDS.search(body):
        if not called:
            reasons.append("talks about a call, but this race has no confirmed AP call")
        elif not has_ap_link:
            reasons.append("talks about the call but does not carry the race's AP link")
    winner = call.get("winner") if called else None
    name_spans: list[tuple[int, int]] = []
    own = race.get("candidates") or []
    for c in sorted(own, key=lambda c: -len(c.get("name") or "")):
        for m in _word_re(c["name"]).finditer(body):
            if any(a < m.end() and m.start() < b for a, b in name_spans):
                continue
            name_spans.append(m.span())
            if not called:
                reasons.append(f"names candidate {c['name']!r}, but the race is not called")
            elif c["name"] != winner:
                reasons.append(f"names {c['name']!r}, who is not the confirmed winner")
            elif not has_ap_link:
                reasons.append("names the winner without the race's AP link")
    after_full = _blank(body, name_spans)
    winner_surname = _surname(winner, own) if winner else None
    for c in own:
        s = _surname(c["name"], own)
        if not s or not _word_re(s).search(after_full):
            continue
        for m in _word_re(s).finditer(after_full):
            name_spans.append(m.span())
        if not called:
            reasons.append(f"names candidate {s!r}, but the race is not called")
        elif s != winner_surname:
            reasons.append(f"names {s!r}, who is not the confirmed winner")
        elif not has_ap_link:
            reasons.append("names the winner without the race's AP link")
    for other in state.get("races", []):
        if other.get("id") == race_id:
            continue
        for c in other.get("candidates") or []:
            if c.get("name") and c["name"] not in {x.get("name") for x in own} and _word_re(c["name"]).search(body):
                reasons.append(f"names {c['name']!r}, a candidate in another race ({other['id']})")
    body_no_names = _blank(body, name_spans)

    # 5. unknown names: every capitalised run must be known vocabulary
    eng = engine_mentions(body_no_names, state)
    vocab = VOCAB | {w for label, _ in _labels(state) for w in re.split(r"[\s(),]+", label) if w}
    if race.get("name"):
        vocab |= set(race["name"].split())
    for m in CAP_RUN.finditer(body_no_names):
        unknown = [w for w in m.group(0).split() if w.strip(".,:;!?'\"") not in vocab]
        if unknown:
            reasons.append(f"unrecognised name {m.group(0)!r}: only the race, engines and the confirmed winner "
                           "may be named")

    # 6. engines
    covered = set((race.get("engines") or {}).keys())
    baseline = next((k for k, v in engine_map(state).items() if v.get("baseline")), "serper")
    named = [eid for _, eid in eng]
    if named and not called:
        reasons.append("engine results exist only for called races; this race is not called")
    for eid in dict.fromkeys(named):
        if called and eid not in covered:
            reasons.append(f"{eid} does not cover this race")
    if re.search(r"\bfirst\b", body_no_names, re.IGNORECASE) and called:
        firsts = set(race.get("first") or [])
        claimed = next((e for e in named if e != baseline), None) or (baseline if baseline in named else None)
        if claimed is None:
            pass  # "first" with no engine named makes no engine claim
        elif claimed not in firsts:
            reasons.append(f"says {claimed} was first, but first is {sorted(firsts) or 'none yet'}")

    # 7. numbers and times
    allowed_times = {clock(t) for t in (call.get("ap_call_time"), race.get("poll_close_et")) if t}
    for m in CLOCK_RE.finditer(body_no_names):
        said = f"{int(m.group(1))}:{m.group(2) or '00'}{m.group(3).lower()}m"
        if said not in allowed_times:
            reasons.append(f"time {m.group(0)!r} is neither the AP call time nor poll close "
                           f"({', '.join(sorted(allowed_times)) or 'none'})")
    engines_data = race.get("engines") or {}
    claim_spans = []
    for m in MINUTE_CLAIM.finditer(body_no_names):
        claim_spans.append(m.span())
        value = float(m.group(1))
        rel, ref = (m.group(2) or "").lower(), (m.group(3) or "").lower()
        before = [eid for pos, eid in eng if pos < m.start() and eid != baseline]
        subject = before[-1] if before else ((race.get("first") or [None])[0])
        e = engines_data.get(subject) if subject and called else None
        if e is None:
            reasons.append(f"{m.group(0)!r}: no engine result to back this number")
            continue
        if ref.startswith("google"):
            lead = e.get("lead_vs_google")
            want = lead if rel == "ahead of" else (-lead if lead is not None else None)
            if want is None or abs(want - value) > 1e-9 or (lead is not None and rel == "ahead of" and lead <= 0):
                reasons.append(f"{m.group(0)!r} does not match {subject}'s lead over Google ({lead})")
        elif ref.startswith("poll"):
            if rel != "after" or e.get("minutes_after_close") != value:
                reasons.append(f"{m.group(0)!r} does not match {subject}'s minutes after poll close "
                               f"({e.get('minutes_after_close')})")
        elif ref:
            mvc = e.get("minutes_vs_call")
            want = mvc if rel == "after" else (-mvc if mvc is not None else None)
            if want is None or abs(want - value) > 1e-9:
                reasons.append(f"{m.group(0)!r} does not match {subject}'s minutes vs AP's call ({mvc})")
        else:
            vals = [e.get(k) for k in ("minutes_after_close", "minutes_vs_call", "lead_vs_google")]
            if not any(isinstance(v, (int, float)) and abs(abs(v) - value) < 1e-9 for v in vals):
                reasons.append(f"{m.group(0)!r} is not one of {subject}'s numbers")
    if re.search(r"\btied with Google\b", body_no_names, re.IGNORECASE):
        before = [eid for _, eid in eng if eid != baseline]
        e = engines_data.get(before[0]) if before and called else None
        if not e or e.get("lead_vs_google") != 0:
            reasons.append("says 'tied with Google' but the lead over Google is not 0")
    rest = _blank(body_no_names, claim_spans)
    rest = CLOCK_RE.sub(" ", rest)
    chk = check_text(rest, _race_numbers(race) if called else [])
    for raw in chk.unsupported:
        reasons.append(f"number {raw!r} is not in this race's results")

    return (not reasons), list(dict.fromkeys(reasons))


def _surname(name: str | None, candidates: list[dict]) -> str | None:
    if not name:
        return None
    for c in candidates:
        if c.get("name") == name and c.get("surname"):
            return c["surname"]
    parts = [p for p in name.replace(",", " ").split() if p not in ("Jr.", "Sr.", "Jr", "Sr", "II", "III", "IV")]
    return parts[-1] if len(parts) > 1 else None


# ---------- the fallback template (notes/LAUNCH.md, "Live post template") ----------

def _join(labels: list[str]) -> str:
    return labels[0] if len(labels) == 1 else ", ".join(labels[:-1]) + " and " + labels[-1]


def template_post(race_id: str, state: dict) -> str:
    """The fixed live post for a called race. Raises ValueError for a race that is not called."""
    race = race_by_id(state, race_id)
    if race is None:
        raise ValueError(f"no race {race_id!r}")
    if not is_called(race):
        raise ValueError(f"{race_id} is not called; there is no post for an uncalled race")
    call = race["call"]
    emap = engine_map(state)
    baseline = next((k for k, v in emap.items() if v.get("baseline")), "serper")
    head = f"{race.get('name') or race_id}. AP called it at {clock(call['ap_call_time'])} ET {call['ap_url']}"
    first = [e for e in (race.get("first") or []) if e in emap and e in (race.get("engines") or {})]
    if len(first) > 1 and "jevlin" in first:
        first.remove("jevlin")  # the computed union always ties the earliest engine
    if not first:
        line = "No search engine has found the result yet."
    else:
        others = [e for e in first if e != baseline]
        labels = [emap[e].get("label", e) for e in others]
        if not others:
            line = f"First search engine to find the result: {emap[baseline].get('label', baseline)}."
        else:
            lead = race["engines"][others[0]].get("lead_vs_google")
            who = _join(labels)
            noun = "engines" if len(labels) > 1 else "engine"
            # Decided from the lead alone, so inconsistent data can never produce a failing post.
            if lead == 0:
                line = f"First search {noun} to find the result: {who}, tied with Google."
            elif lead is None:
                line = (f"First search {noun} to find the result: {who}. "
                        f"{emap[baseline].get('label', baseline)} has not found it yet.")
            elif lead > 0:
                line = f"First search {noun} to find the result: {who}, {lead:g} min ahead of Google."
            else:
                line = f"First search {noun} to find the result: {who}."
    return f"{head}\n{line}\nLive: {LIVE_URL}"


# ---------- answers from `ask` ----------

def check_answer(text: str, state: dict) -> list[str]:
    """Problems with a free-text answer: a candidate of an uncalled race named (the model may know names
    from training; the tools never give it a winner for an uncalled race)."""
    problems = []
    for race in state.get("races", []):
        if is_called(race):
            continue
        for c in race.get("candidates") or []:
            if c.get("name") and _word_re(c["name"]).search(text):
                problems.append(f"names {c['name']!r} from {race['id']}, which is not called")
    return problems


# ---------- results thread (Wed morning): posts about the leaderboard, not one race ----------

SINGLE_KINDS = ("baseline", "computed", "agent")
THREAD_VOCAB = {"Which", "We", "AI", "Fastest", "Median", "One", "Agent", "Every", "Full", "Race", "Method",
                "Taking", "SHA-256", "Senate", "Governor"}
_HASH_RE = re.compile(r"\b[0-9a-f]{8,64}\b")
_SHA_LABEL = re.compile(r"SHA-256", re.IGNORECASE)
_AI_CLAIM = re.compile(r"faster than Google", re.IGNORECASE)
_GOOGLE_CLAIM = re.compile(r"\bGoogle (?:\(via Serper\) )?was (?:faster|as fast)", re.IGNORECASE)


def board_rows(state: dict) -> dict[str, dict]:
    return {r["engine"]: r for r in state.get("leaderboard", []) if r.get("engine")}


def single_engines(state: dict) -> list[str]:
    """The measured single-query engines (the 7), excluding the computed union and the agent."""
    return [e["id"] for e in state.get("engines", []) if not e.get("computed") and not e.get("agent")]


def results_outcome(state: dict) -> dict:
    """Which pre-written thread the leaderboard supports. AI search is "faster" only if the fastest
    AI engine, or the agent, has a strictly lower median minutes-after-close than Google (via Serper).
    A tie goes to Google: we never claim AI was faster unless the median says so. `jevlin` is reported
    beside it but never decides, because it includes Google."""
    rows = board_rows(state)
    emap = engine_map(state)
    baseline = next((k for k, v in emap.items() if v.get("baseline")), "serper")
    inf = float("inf")
    med = lambda e: (rows.get(e) or {}).get("median_minutes_after_close")
    key = lambda e: (med(e) if med(e) is not None else inf, -((rows.get(e) or {}).get("found_first") or 0),
                     (rows.get(e) or {}).get("wrong") or 0, e)
    ai = [e for e in single_engines(state) if e != baseline and e in rows]
    if not ai or baseline not in rows:
        raise ValueError("the leaderboard has no AI engine or no Google (via Serper) row")
    best = min(ai, key=key)
    g = med(baseline) if med(baseline) is not None else inf
    contenders = [e for e in (best, "agent") if e in rows and med(e) is not None]
    if med(best) is None and g == inf:
        raise ValueError("no engine found any result; there is no thread to write")
    faster = sorted((e for e in contenders if med(e) < g), key=key)
    ai_faster = bool(faster)
    return {"version": "ai_faster" if ai_faster else "google_faster", "best": best, "baseline": baseline,
            "lead": faster[0] if faster else best,  # the engine the headline shows against Google
            "tie": not ai_faster and med(best) is not None and med(best) == g,
            "agent_faster": "agent" in contenders and med("agent") < g}


def check_summary_post(text: str, state: dict, *, repo_url: str | None = None) -> tuple[bool, list[str]]:
    """The guard for a results-thread post. Numbers next to an engine must be that engine's leaderboard
    numbers ("N min" = median minutes after close, "N min ahead of Google" = median lead, "N wrong" =
    wrong answers, anything else one of its row's values); other numbers must be totals from the
    state. No candidate names, no commentary, only known links, and the headline must be the one the
    leaderboard supports."""
    reasons: list[str] = []
    text = text.strip()
    if not text:
        return False, ["empty post"]
    if (n := weighted_length(text)) > MAX_WEIGHTED:
        reasons.append(f"too long: {n} of {MAX_WEIGHTED} (links count 23)")
    prereg = state.get("prereg") or {}
    allowed_prefixes = [_norm_url(u) for u in (repo_url, prereg.get("url")) if u]
    for u in _urls(text):
        if _host(u) in ALLOWED_HOSTS or any(_norm_url(u).startswith(p) for p in allowed_prefixes):
            continue
        reasons.append(f"link not allowed: {u} (only jevlin.ai, the repo and the pre-registration)")
    body = _blank(text, [m.span() for m in URL_RE.finditer(text)])
    for m in _BANNED_RE.finditer(body):
        reasons.append(f"commentary, prediction or party word: {m.group(0)!r}")
    for m in _BANNED_SYMBOLS.finditer(body):
        reasons.append(f"party label or emoji not allowed: {m.group(0)!r}")
    if m := OTHER_SEARCH.search(body):
        reasons.append(f"names a search engine that is not in the measurement: {m.group(0)!r}")
    for race in state.get("races", []):
        for c in race.get("candidates") or []:
            if c.get("name") and _word_re(c["name"]).search(body):
                reasons.append(f"names candidate {c['name']!r}; the thread is about search, not candidates")

    # the hash: only a prefix of the pre-registration hash
    sha = (prereg.get("sha256") or "").lower()
    spans = [m.span() for m in _SHA_LABEL.finditer(body)]
    for m in _HASH_RE.finditer(body):
        if any(ch in "abcdef" for ch in m.group(0)) or len(m.group(0)) >= 12:
            if sha and sha.startswith(m.group(0)):
                spans.append(m.span())
            else:
                reasons.append(f"{m.group(0)!r} is not the pre-registration hash")
                spans.append(m.span())
    body = _blank(body, spans)

    # names
    vocab = VOCAB | THREAD_VOCAB | {w for label, _ in _labels(state) for w in re.split(r"[\s(),]+", label) if w}
    for m in CAP_RUN.finditer(body):
        if [w for w in m.group(0).split() if w.strip(".,:;!?'\"()") not in vocab]:
            reasons.append(f"unrecognised name {m.group(0)!r}")

    # headline
    try:
        out = results_outcome(state)
    except ValueError as e:
        return False, reasons + [str(e)]
    if _AI_CLAIM.search(body) and out["version"] != "ai_faster":
        reasons.append("says AI search was faster than Google, but the leaderboard says Google was as fast or faster")
    if _GOOGLE_CLAIM.search(body) and out["version"] != "google_faster":
        reasons.append("says Google was faster, but the leaderboard says an AI search was faster")

    # numbers, per engine segment
    rows = board_rows(state)
    mentions = engine_mentions(body, state)
    bounds = [m.start() for m in re.finditer(r"\n|\.\s", body)]
    segs = []
    for i, (pos, eid) in enumerate(mentions):
        nxt = [p for p in [*(p for p, _ in mentions[i + 1:]), *bounds] if p > pos]
        segs.append((pos, min(nxt) if nxt else len(body), eid))
    claimed = []
    for a, b, eid in segs:
        row = rows.get(eid)
        seg = body[a:b]
        for m in re.finditer(r"(?<![\w.])(\d+(?:\.\d+)?)\s*(?:min\b(\s+ahead of Google)?|wrong\b)?", seg):
            claimed.append((a + m.start(), a + m.end()))
            if row is None:
                reasons.append(f"{eid} has no leaderboard row for {m.group(0)!r}")
                continue
            v = float(m.group(1))
            tol = 0.5 * 10 ** -len((m.group(1).split(".") + [""])[1]) + 1e-9
            word = m.group(0)[len(m.group(1)):].strip().lower()
            if word.startswith("min") and m.group(2):
                want, what = row.get("median_lead_vs_google"), "median lead over Google"
            elif word.startswith("min"):
                want, what = row.get("median_minutes_after_close"), "median minutes after poll close"
            elif word == "wrong":
                want, what = row.get("wrong"), "wrong answers"
            else:
                vals = [x for k, x in row.items() if isinstance(x, (int, float)) and not isinstance(x, bool)
                        and k != "cost_per_check_usd"] + list((row.get("found_within") or {}).values())
                if not any(abs(x - v) <= tol for x in vals):
                    reasons.append(f"{m.group(0)!r} is not one of {eid}'s leaderboard numbers")
                continue
            if want is None or abs(float(want) - v) > tol:
                reasons.append(f"{m.group(0)!r} does not match {eid}'s {what} ({want})")
    rest = _blank(body, claimed)
    summary = state.get("summary") or {}
    totals = [float(x) for x in summary.values() if isinstance(x, (int, float)) and not isinstance(x, bool)]
    totals.append(float(len(single_engines(state))))
    for raw in check_text(rest, totals).unsupported:
        reasons.append(f"number {raw!r} is not a total in the results")
    return (not reasons), list(dict.fromkeys(reasons))
