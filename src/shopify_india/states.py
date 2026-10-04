from __future__ import annotations

import re


GST_STATE_CODES = {
    "01": "Jammu and Kashmir", "02": "Himachal Pradesh", "03": "Punjab",
    "04": "Chandigarh", "05": "Uttarakhand", "06": "Haryana", "07": "Delhi",
    "08": "Rajasthan", "09": "Uttar Pradesh", "10": "Bihar", "11": "Sikkim",
    "12": "Arunachal Pradesh", "13": "Nagaland", "14": "Manipur", "15": "Mizoram",
    "16": "Tripura", "17": "Meghalaya", "18": "Assam", "19": "West Bengal",
    "20": "Jharkhand", "21": "Odisha", "22": "Chhattisgarh", "23": "Madhya Pradesh",
    "24": "Gujarat", "26": "Dadra and Nagar Haveli and Daman and Diu",
    "27": "Maharashtra", "29": "Karnataka", "30": "Goa", "31": "Lakshadweep",
    "32": "Kerala", "33": "Tamil Nadu", "34": "Puducherry",
    "35": "Andaman and Nicobar Islands", "36": "Telangana", "37": "Andhra Pradesh",
    "38": "Ladakh", "97": "Other Territory",
}

STATE_ALIASES = {
    **{name.lower(): name for name in GST_STATE_CODES.values() if name != "Other Territory"},
    "nct of delhi": "Delhi", "new delhi": "Delhi", "orissa": "Odisha",
    "uttaranchal": "Uttarakhand", "pondicherry": "Puducherry",
    "j&k": "Jammu and Kashmir", "tamilnadu": "Tamil Nadu",
    "westbengal": "West Bengal", "andaman & nicobar": "Andaman and Nicobar Islands",
}

GSTIN_RE = re.compile(r"\b(0[1-9]|[12][0-9]|3[0-8]|97)[A-Z]{5}[0-9]{4}[A-Z][1-9A-Z]Z[0-9A-Z]\b", re.I)
PIN_RE = re.compile(r"(?<!\d)[1-9][0-9]{5}(?!\d)")

# Conservative postal prefixes whose first two/three digits belong to one
# state. Ambiguous postal circles are intentionally omitted rather than guessed.
PIN_SPECIAL_PREFIXES = {
    "160": "Chandigarh", "194": "Ladakh", "403": "Goa", "605": "Puducherry",
    "682": "Lakshadweep", "737": "Sikkim", "744": "Andaman and Nicobar Islands",
}
PIN_TWO_DIGIT_STATES = {
    "11": "Delhi", "12": "Haryana", "13": "Haryana", "14": "Punjab", "15": "Punjab",
    "16": "Punjab", "17": "Himachal Pradesh", "30": "Rajasthan", "31": "Rajasthan",
    "32": "Rajasthan", "33": "Rajasthan", "34": "Rajasthan", "36": "Gujarat",
    "37": "Gujarat", "38": "Gujarat", "39": "Gujarat", "40": "Maharashtra",
    "41": "Maharashtra", "42": "Maharashtra", "43": "Maharashtra", "44": "Maharashtra",
    "45": "Madhya Pradesh", "46": "Madhya Pradesh", "47": "Madhya Pradesh",
    "48": "Madhya Pradesh", "49": "Chhattisgarh", "50": "Telangana",
    "51": "Andhra Pradesh", "52": "Andhra Pradesh", "53": "Andhra Pradesh",
    "56": "Karnataka", "57": "Karnataka", "58": "Karnataka", "59": "Karnataka",
    "60": "Tamil Nadu", "61": "Tamil Nadu", "62": "Tamil Nadu", "63": "Tamil Nadu",
    "64": "Tamil Nadu", "67": "Kerala", "68": "Kerala", "69": "Kerala",
    "70": "West Bengal", "71": "West Bengal", "72": "West Bengal", "73": "West Bengal",
    "74": "West Bengal", "75": "Odisha", "76": "Odisha", "77": "Odisha", "78": "Assam",
}


def find_gstins(text: str) -> list[str]:
    return sorted({match.group(0).upper() for match in GSTIN_RE.finditer(text)})


def state_from_gstin(gstin: str) -> str:
    return GST_STATE_CODES.get(gstin[:2], "")


def find_states(text: str) -> list[str]:
    lowered = re.sub(r"\s+", " ", text.lower())
    found = set()
    for alias, canonical in sorted(STATE_ALIASES.items(), key=lambda item: -len(item[0])):
        if re.search(rf"(?<![a-z]){re.escape(alias)}(?![a-z])", lowered):
            found.add(canonical)
    return sorted(found)


def has_indian_pin(text: str) -> bool:
    return bool(PIN_RE.search(text))


def find_pins(text: str) -> list[str]:
    return sorted({match.group(0) for match in PIN_RE.finditer(text)})


def state_from_pin(pin: str) -> str:
    digits = re.sub(r"\D", "", pin)
    if len(digits) != 6:
        return ""
    return PIN_SPECIAL_PREFIXES.get(digits[:3], PIN_TWO_DIGIT_STATES.get(digits[:2], ""))

