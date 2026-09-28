"""Checks that stop the same person ending up with two accounts: a mistyped
email domain ("gamil.com") or the same phone number written differently."""
import re
from typing import Optional

# Frequent misspellings of popular email providers -> the real domain. An
# explicit list, so a genuine domain is never "corrected" by guesswork.
_DOMAIN_TYPOS = {
    **dict.fromkeys(
        ["gamil.com", "gmial.com", "gmai.com", "gmal.com", "gmil.com", "gnail.com", "gmaill.com",
         "gmail.co", "gmail.con", "gmail.cm", "gmail.om", "gmail.in", "gmail.co.in", "gmailcom",
         "gamil.co", "gmaik.com", "gmsil.com"],
        "gmail.com",
    ),
    **dict.fromkeys(["yaho.com", "yahooo.com", "yahoo.co", "yahoo.con", "yhoo.com", "yahho.com"], "yahoo.com"),
    **dict.fromkeys(["yaho.co.in", "yahoo.co.i", "yahoo.coin"], "yahoo.co.in"),
    **dict.fromkeys(["hotmial.com", "hotmai.com", "hotmail.co", "hotmail.con", "hotmil.com"], "hotmail.com"),
    **dict.fromkeys(["outlok.com", "outlook.co", "outlook.con", "outllok.com"], "outlook.com"),
    **dict.fromkeys(["redifmail.com", "rediffmail.co", "rediffmail.con", "rediff.com"], "rediffmail.com"),
    **dict.fromkeys(["iclod.com", "icloud.co", "icloud.con"], "icloud.com"),
}


def suggest_email_fix(email: str) -> Optional[str]:
    """"ramesh@gamil.com" -> "ramesh@gmail.com"; None when the domain looks fine."""
    local, _, domain = email.rpartition("@")
    fixed = _DOMAIN_TYPOS.get(domain.lower())
    return f"{local}@{fixed}" if local and fixed else None


def phone_key(phone: Optional[str]) -> Optional[str]:
    """Comparable form of a phone number: digits only, with India's +91 / leading 0
    dropped, so "+91 74105 38585", "07410538585" and "7410538585" all match."""
    digits = re.sub(r"\D", "", phone or "")
    if len(digits) == 12 and digits.startswith("91"):
        digits = digits[2:]
    elif len(digits) == 11 and digits.startswith("0"):
        digits = digits[1:]
    return digits or None
