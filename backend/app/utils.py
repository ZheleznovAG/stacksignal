import datetime
import re


def utcnow() -> datetime.datetime:
    """The current UTC time as a naive datetime -- what every DateTime column here stores.
    datetime.utcnow() gives the same value but is deprecated since Python 3.12."""
    return datetime.datetime.now(datetime.UTC).replace(tzinfo=None)


# Russian and Ukrainian letters -> Latin, so "Читалка" becomes "chitalka" instead of vanishing.
# ASCII text slugs exactly as before, so every existing slug stays what it was.
_TRANSLIT = str.maketrans({
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "e", "ж": "zh", "з": "z",
    "и": "i", "й": "y", "к": "k", "л": "l", "м": "m", "н": "n", "о": "o", "п": "p", "р": "r",
    "с": "s", "т": "t", "у": "u", "ф": "f", "х": "kh", "ц": "ts", "ч": "ch", "ш": "sh", "щ": "shch",
    "ъ": "", "ы": "y", "ь": "", "э": "e", "ю": "yu", "я": "ya",
    "є": "ye", "і": "i", "ї": "yi", "ґ": "g",
})


def slugify(text: str) -> str:
    text = text.strip().lower().translate(_TRANSLIT)
    text = re.sub(r"[^a-z0-9]+", "-", text)
    return text.strip("-") or "item"


def unique_slug(base: str, taken) -> str:
    """base, or base-2, base-3... -- the first one `taken(slug)` says is free."""
    slug, n = base, 2
    while taken(slug):
        slug, n = f"{base}-{n}", n + 1
    return slug
