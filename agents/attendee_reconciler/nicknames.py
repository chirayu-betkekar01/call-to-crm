"""Nickname / short-form equivalence for first names (Matt == Matthew, Sam == Samuel/Samantha).

The LLM does the first pass of name resolution; this table backs up the deterministic
merge step so the result doesn't depend only on the model. Add your own entries to
NICKNAME_GROUPS or to a `nicknames_extra.txt` file (one comma-separated group per line).
"""
from __future__ import annotations

import re
import unicodedata
from functools import lru_cache
from pathlib import Path

# Each line is one group of interchangeable first names. A name may appear in several
# groups (e.g. "sam" -> Samuel and Samantha), so equivalence is "share any group".
NICKNAME_GROUPS: list[str] = [
    "matthew, matt, matty, mat",
    "matthias, matt, mathias",
    "samuel, sam, sammy, sammie",
    "samantha, sam, sammy, sammie",
    "william, will, bill, billy, liam, willy",
    "robert, rob, bob, bobby, robbie, bert",
    "richard, rich, rick, ricky, dick, richie",
    "michael, mike, mikey, mick, mickey, micky",
    "christopher, chris, topher, kit",
    "christine, chris, christy, tina, chrissy",
    "christina, chris, tina, chrissy",
    "kristen, kris, kristy",
    "jonathan, jon, john, jonny, johnny, nate",
    "john, jack, johnny, jon",
    "james, jim, jimmy, jamie, jay",
    "joseph, joe, joey, jo",
    "josephine, jo, josie",
    "daniel, dan, danny",
    "danielle, dani, danny",
    "david, dave, davey",
    "thomas, tom, tommy",
    "anthony, tony, ant",
    "antonio, tony, toni",
    "andrew, andy, drew",
    "andrea, andy, andi",
    "alexander, alex, al, xander, sasha",
    "alexandra, alex, alexa, lexi, sasha, sandra",
    "alexis, alex, lexi",
    "edward, ed, eddie, ted, ned",
    "theodore, theo, ted, teddy",
    "benjamin, ben, benny, benji",
    "nicholas, nick, nicky, nico",
    "nicole, nikki, nicky, nic",
    "steven, steve, stevie",
    "stephen, steve, stevie",
    "stephanie, steph, stephie",
    "gregory, greg",
    "jeffrey, jeff",
    "geoffrey, jeff, geoff",
    "kenneth, ken, kenny",
    "ronald, ron, ronnie",
    "donald, don, donnie",
    "timothy, tim, timmy",
    "patrick, pat, paddy",
    "patricia, pat, patty, trish, tricia",
    "peter, pete",
    "charles, charlie, chuck, chas",
    "charlotte, charlie, lottie",
    "frederick, fred, freddie, rick",
    "frances, fran, frannie",
    "francis, frank, fran",
    "franklin, frank",
    "lawrence, larry, laurie",
    "leonard, leo, len, lenny",
    "raymond, ray",
    "gerald, gerry, jerry",
    "jerome, jerry",
    "harold, harry, hal",
    "henry, harry, hank, hal",
    "zachary, zach, zack",
    "jacob, jake",
    "joshua, josh",
    "nathaniel, nate, nathan, nat",
    "nathan, nate",
    "samson, sam",
    "elizabeth, liz, beth, betty, lizzie, eliza, libby, betsy",
    "katherine, kate, katie, kathy, kat, kay",
    "catherine, cathy, cat, kate, katie",
    "kathleen, kathy, kate",
    "margaret, maggie, meg, peggy, marge, greta",
    "rebecca, becky, becca",
    "jennifer, jen, jenny",
    "jessica, jess, jessie",
    "susan, sue, susie",
    "suzanne, sue, suzy",
    "deborah, deb, debbie",
    "victoria, vicky, tori",
    "abigail, abby, gail",
    "amanda, mandy",
    "melissa, mel, missy",
    "melanie, mel",
    "kimberly, kim",
    "cynthia, cindy",
    "pamela, pam",
    "barbara, barb, babs",
    "dorothy, dot, dottie",
    "judith, judy",
    "eleanor, ellie, nora, elle",
    "gabriel, gabe",
    "gabrielle, gabby, elle",
    "isabella, bella, izzy",
    "isabel, izzy, bel",
    "olivia, liv",
    "madeline, maddie",
    "allison, ali, allie",
    "alison, ali, allie",
    "valerie, val",
    "vincent, vince, vinny",
    "douglas, doug",
    "phillip, phil",
    "philip, phil",
    "russell, russ",
    "samir, sam",
    "sanjay, sanj",
    "mohammed, mo, moe",
    "muhammad, mo, moe",
    "abdullah, abdul",
    "rajesh, raj",
    "rajiv, raj",
    "venkatesh, venky",
    "krishna, kris",
    "aleksandr, sasha, alex",
    "dmitri, dima",
    "evgeny, zhenya",
    "francesco, frank",
    "giuseppe, joe, beppe",
    "maximilian, max",
    "maxwell, max",
    "terrence, terry",
    "terence, terry",
    "dennis, denny",
    "walter, walt",
    "wesley, wes",
    "bradley, brad",
    "cameron, cam",
    "benedict, ben",
    "fernando, nando",
    "roberto, beto, rob",
    "guillermo, memo, will",
    "jose, pepe",
    "francisco, paco, pancho, frank",
    "ignacio, nacho",
]

_TITLES = {"mr", "mrs", "ms", "miss", "dr", "prof", "sir", "madam"}
_SUFFIXES = {"jr", "sr", "ii", "iii", "iv", "phd", "md", "mba"}


def normalize(token: str | None) -> str:
    """Lowercase, strip accents and punctuation."""
    if not token:
        return ""
    t = unicodedata.normalize("NFKD", token)
    t = "".join(c for c in t if not unicodedata.combining(c))
    return re.sub(r"[^a-z\-' ]", "", t.lower()).strip()


@lru_cache(maxsize=1)
def _index() -> dict[str, set[int]]:
    groups = list(NICKNAME_GROUPS)
    extra = Path(__file__).with_name("nicknames_extra.txt")
    if extra.exists():
        groups += [l for l in extra.read_text(encoding="utf-8").splitlines() if l.strip() and not l.startswith("#")]
    idx: dict[str, set[int]] = {}
    for gi, line in enumerate(groups):
        for name in line.split(","):
            n = normalize(name)
            if n:
                idx.setdefault(n, set()).add(gi)
    return idx


def first_names_match(a: str | None, b: str | None) -> bool:
    """True if two first names can refer to the same person (Matt ~ Matthew)."""
    na, nb = normalize(a), normalize(b)
    if not na or not nb:
        return False
    if na == nb:
        return True
    idx = _index()
    if idx.get(na, set()) & idx.get(nb, set()):
        return True
    # Common short-form pattern not in the table: "Alexandr" vs "Alex", "Fred" vs "Freddy".
    short, long_ = sorted((na, nb), key=len)
    return len(short) >= 3 and long_.startswith(short) and len(long_) - len(short) <= 4


def split_name(full: str | None) -> tuple[str | None, str | None]:
    """'Dr. Matthew J. Collins Jr.' -> ('Matthew', 'Collins')."""
    if not full:
        return None, None
    parts = [p for p in re.split(r"\s+", full.strip()) if p]
    parts = [p for p in parts if normalize(p).replace("'", "") not in _TITLES | _SUFFIXES]
    parts = [p for p in parts if not re.fullmatch(r"[A-Za-z]\.?", p)] or parts  # drop middle initials
    if not parts:
        return None, None
    if len(parts) == 1:
        return parts[0], None
    return parts[0], parts[-1]


def last_names_match(a: str | None, b: str | None) -> bool:
    na, nb = normalize(a).replace("-", " "), normalize(b).replace("-", " ")
    return bool(na and nb and (na == nb or na in nb.split() or nb in na.split()))
