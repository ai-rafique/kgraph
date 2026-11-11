import re

STOP = set("""a about above after again against all also am an and any are as at be because been before
being below between both but by can could did do does doing down during each few for from further had has
have having he her here hers herself him himself his how i if in into is it its itself just me more most my
myself no nor not now of off on once only or other our ours ourselves out over own same she should so some
such than that the their theirs them themselves then there these they this those through to too under until
up very was we were what when where which while who whom why will with would you your yours yourself
yourselves one two three may might must shall also use used using get gets set sets new via per etc e.g i.e
eg ie like make makes made see seen within without however therefore thus hence since whether either neither
yet still well much many way ways first second third last next return returns value values true false none
null self cls def class function var let const import from file files line lines code example examples
note notes section sections figure table chapter page pages paper show shows shown result results based
following given different able need needs want wants case cases time times data type types number
added fixed removed changed deprecated updated thanks contributors version release released pull request
issue issues todo fixme sample rows row columns column sheet table tables types empty""".split())

_CAMEL = re.compile(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])")
_WORD = re.compile(r"[A-Za-z][A-Za-z0-9]+")


def split_ident(s: str):
    """verifyJWTToken / verify_jwt_token -> ['verify', 'jwt', 'token']"""
    out = []
    for w in _WORD.findall(s):
        for p in _CAMEL.split(w):
            p = p.lower()
            if p:
                out.append(p)
    return out


def stem(w: str) -> str:
    """Very light stemmer so 'timeouts'/'timeout', 'parsing'/'parse' meet."""
    for suf in ("ings", "ing", "ies", "es", "ed", "s"):
        if len(w) > len(suf) + 3 and w.endswith(suf):
            if suf == "ies":
                return w[:-3] + "y"
            if suf == "es" and not w[-3] in "sxz":
                return w[:-1]
            return w[: -len(suf)]
    return w


def tokens(text: str):
    return [w.lower() for w in _WORD.findall(text)]


def est_tokens(text_or_len) -> int:
    n = text_or_len if isinstance(text_or_len, int) else len(text_or_len)
    return max(1, n // 4)
