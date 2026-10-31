"""The detector's cheap filter: does a page's text mention one of this race's candidates next to a
win word? Recall matters more than precision here (the model check rejects false hits); a page the
filter drops is never shown to the model, so the filter must not drop real calls.

Name matching is per race, never global: a page counts for a race only if it names that race's
candidates. Surnames shared by two candidates in the same race (e.g. two Sullivans) only match on
first name + surname.
"""
import re
import unicodedata

WIN_WORDS = [
    r"won", r"wins", r"winner", r"winning", r"victory", r"victorious", r"prevails?", r"prevailed",
    r"defeats?", r"defeated", r"beats?", r"beaten", r"re-?elected", r"elected", r"flips?", r"flipped",
    r"holds? off", r"held off", r"concedes?", r"conceded", r"concession",
    r"calls? the race", r"called the race", r"race call", r"projected", r"projects", r"declared",
    r"ap calls?", r"decision desk",
]
WIN_RE = re.compile(r"\b(?:" + "|".join(WIN_WORDS) + r")\b")
SUFFIXES = {"jr", "jr.", "sr", "sr.", "ii", "iii", "iv"}


def fold(s):
    """Lowercase, strip accents, unify dashes and apostrophes, squeeze spaces."""
    s = unicodedata.normalize("NFKD", s or "")
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = s.lower().replace("’", "'").replace("‘", "'")
    s = re.sub(r"[‐-―]", "-", s)
    return re.sub(r"\s+", " ", s).strip()


def _strip_suffix(words):
    while words and words[-1].strip(",") in SUFFIXES:
        words = words[:-1]
    return words


def name_patterns(candidates):
    """For each candidate, the folded strings that identify them in this race.

    Returns {candidate_name: [patterns]}. The surname alone is a pattern only when no other
    candidate in the race shares it; otherwise first + last name is required.
    """
    info = []
    for c in candidates:
        full = _strip_suffix(fold(c["name"]).split())
        surname = fold(c.get("surname") or (full[-1] if full else ""))
        surname = " ".join(_strip_suffix(surname.split()))
        info.append((c["name"], full, surname))

    surname_count = {}
    for _, _, s in info:
        surname_count[s] = surname_count.get(s, 0) + 1

    out = {}
    for name, full, surname in info:
        pats = {" ".join(full)}
        if len(full) >= 2:
            pats.add(f"{full[0]} {full[-1]}")
        if surname and surname_count[surname] == 1:
            pats.add(surname)
            pats.add(surname.replace("-", " "))
            parts = surname.replace("-", " ").split()
            # A compound surname ("Gluesenkamp Perez", "Padilla Stout") also matches its last part,
            # unless another candidate in the race uses that word.
            if len(parts) > 1 and not any(parts[-1] in o_s.split() for n, _, o_s in info if n != name):
                pats.add(parts[-1])
        out[name] = sorted(p for p in pats if len(p) >= 3)
    return out


def _has(text, pat):
    return re.search(r"(?<![a-z0-9])" + re.escape(pat) + r"(?![a-z0-9])", text) is not None


def filter_hit(text, patterns):
    """Candidates whose name appears in `text` (folded) when the text also has a win word.
    Returns the list of candidate names (possibly several), or [] if no hit."""
    t = fold(text)
    if not WIN_RE.search(t):
        return []
    return [name for name, pats in patterns.items() if any(_has(t, p) for p in pats)]
