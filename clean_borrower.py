#!/usr/bin/env python3
"""
Usage:
    python clean_borrowers.py [input.csv] [output.csv]
"""
import csv
import re
import sys
import unicodedata


try:
    from helpers import clean_unicode  # noqa: F401
except ImportError:
    def clean_unicode(s):
        """Fix common mojibake, normalize Unicode, drop control/zero-width chars."""
        if s is None:
            return ""
        s = str(s)
        # Common UTF-8-read-as-Latin-1 artifacts
        for bad, good in {"â€™": "'", "â€˜": "'", "â€œ": '"', "â€\x9d": '"',
                          "â€“": "-", "â€”": "-", "Ã©": "é", "Â": ""}.items():
            s = s.replace(bad, good)
        s = unicodedata.normalize("NFKC", s)
        s = s.replace("’", "'").replace("‘", "'")
        s = s.replace("“", '"').replace("”", '"')
        s = s.replace("–", "-").replace("—", "-")
        s = "".join(ch for ch in s
                    if unicodedata.category(ch)[0] != "C" or ch in " ")
        return re.sub(r"\s+", " ", s).strip()

CARD_ID_RE = re.compile(r"^ID(\d{6})$")
STREET_ABBR_UPPER = {"NE", "NW", "SE", "SW", "PO", "N", "S", "E", "W"}


def name_case(s):
    """Title-case a person's name, keeping O'Brien / McDonald / Smith-Jones."""
    s = clean_unicode(s)
    if not s:
        return ""

    def fix_word(w):
        if not w:
            return w
        w = w.lower()
        w = w[0].upper() + w[1:]
        if w.startswith("Mc") and len(w) > 2:
            w = "Mc" + w[2].upper() + w[3:]
        return w

    out = []
    for word in s.split(" "):
        parts = re.split(r"([-'])", word)  # keep separators
        out.append("".join(fix_word(p) if p not in "-'" else p for p in parts))
    return " ".join(out)


def address_case(s):
    """Title-case a street address; keep 1st/2nd, directions and unit codes sane."""
    s = clean_unicode(s).strip("'\"`,. ")
    words = []
    for w in s.split(" "):
        bare = w.strip(".,").upper()
        if bare in STREET_ABBR_UPPER:
            words.append(w.upper())
        elif re.match(r"^\d+(st|nd|rd|th)$", w, re.I):
            words.append(w.lower())          
        elif re.match(r"^\d+[A-Za-z]$", w):
            words.append(w.upper())          #
        else:
            words.append(w.capitalize())
    return " ".join(words)


def clean_ssn(s):
    digits = re.sub(r"\D", "", clean_unicode(s))
    if len(digits) != 9:
        return None
    return f"{digits[:3]}-{digits[3:5]}-{digits[5:]}"


def clean_phone(s):
    digits = re.sub(r"\D", "", clean_unicode(s))
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    if len(digits) != 10:
        return ""
    return f"({digits[:3]}) {digits[3:6]}-{digits[6:]}"


def build_address(street, city, state):
    street = address_case(street)
    city = name_case(city)
    state = re.sub(r"[^A-Za-z]", "", clean_unicode(state)).upper()
    return ", ".join(p for p in (street, city, state) if p)


def get(row, *names):
    """Read a column by any of several header spellings (case-insensitive)."""
    lowered = {k.strip().lower(): v for k, v in row.items() if k}
    for n in names:
        if n.lower() in lowered:
            return lowered[n.lower()] or ""
    return ""


def clean_borrowers(in_path, out_path, reject_path="borrowers_rejected.csv"):
    with open(in_path, newline="", encoding="utf-8-sig", errors="replace") as f:
        rows = list(csv.DictReader(f))

    # First pass: find the highest valid Card_id so new IDs never collide.
    max_id = 0
    for r in rows:
        m = CARD_ID_RE.match(clean_unicode(get(r, "ID0000id", "card_id", "id")).upper())
        if m:
            max_id = max(max_id, int(m.group(1)))

    cleaned, rejected = [], []
    seen_ids, seen_ssn = set(), set()
    reassigned = phone_nulled = 0

    for line_no, r in enumerate(rows, start=2):  # header is line 1
        raw_id = clean_unicode(get(r, "ID0000id", "card_id", "id")).upper()
        ssn = clean_ssn(get(r, "ssn"))
        bname = " ".join(p for p in (name_case(get(r, "first_name")),
                                     name_case(get(r, "last_name"))) if p)

        reason = None
        if not ssn:
            reason = "missing or invalid SSN"
        elif ssn in seen_ssn:
            reason = "duplicate SSN"
        elif not bname:
            reason = "missing name"
        if reason:
            rejected.append({"line": line_no, "reason": reason, **r})
            continue

        if CARD_ID_RE.match(raw_id) and raw_id not in seen_ids:
            card_id = raw_id
        else:
            max_id += 1
            card_id = f"ID{max_id:06d}"
            reassigned += 1

        raw_phone = get(r, "phone")
        phone = clean_phone(raw_phone)
        if raw_phone.strip() and not phone:
            phone_nulled += 1

        seen_ids.add(card_id)
        seen_ssn.add(ssn)
        cleaned.append({
            "Card_id": card_id,
            "Ssn": ssn,
            "Bname": bname,
            "Address": build_address(get(r, "address"), get(r, "city"), get(r, "state")),
            "Phone": phone,
        })

    cleaned.sort(key=lambda x: x["Card_id"])
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["Card_id", "Ssn", "Bname", "Address", "Phone"])
        w.writeheader()
        w.writerows(cleaned)

    if rejected:
        fields = ["line", "reason"] + [k for k in rejected[0] if k not in ("line", "reason")]
        with open(reject_path, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
            w.writeheader()
            w.writerows(rejected)

    print(f"Read {len(rows)} rows -> wrote {len(cleaned)} to {out_path}")
    print(f"  rejected: {len(rejected)}" + (f" (see {reject_path})" if rejected else ""))
    print(f"  Card_ids reassigned: {reassigned}")
    print(f"  phones set to NULL (unparseable): {phone_nulled}")
    return cleaned, rejected


if __name__ == "__main__":
    src = sys.argv[1] if len(sys.argv) > 1 else "borrowers_raw.csv"
    dst = sys.argv[2] if len(sys.argv) > 2 else "borrowers.csv"
    clean_borrowers(src, dst)