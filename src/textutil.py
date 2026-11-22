"""Small text helpers used in several places."""
import unicodedata


def digits_only(value):
    """Keep only the digits. Arabic-Indic digits (٠١٢٣) become normal digits."""
    out = []
    for ch in str(value or ""):
        if ch.isdigit():
            try:
                out.append(str(unicodedata.digit(ch)))
            except ValueError:
                pass
    return "".join(out)


def phones_match(a, b):
    """True if two phone numbers are the same number.

    It compares the last 10 digits, so 01012345678 and +201012345678 match.
    Numbers shorter than 10 digits never match (too easy to guess).
    """
    da, db_ = digits_only(a), digits_only(b)
    if len(da) < 10 or len(db_) < 10:
        return False
    return da[-10:] == db_[-10:]
