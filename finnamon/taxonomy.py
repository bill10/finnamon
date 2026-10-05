"""Plaid's personal-finance-category taxonomy plus a small synonym table. Deterministic name
resolution: exact code → synonym → token match on Plaid's names → (ambiguous) list to choose from.
No model here, on purpose: a wrong budget category silently miscounts for months."""
from __future__ import annotations

import difflib

# https://plaid.com/docs/api/products/transactions/#personal-finance-category-taxonomy
DETAILED: dict[str, list[str]] = {
    "INCOME": ["DIVIDENDS", "INTEREST_EARNED", "RETIREMENT_PENSION", "TAX_REFUND", "UNEMPLOYMENT", "WAGES", "OTHER_INCOME"],
    "TRANSFER_IN": ["CASH_ADVANCES_AND_LOANS", "DEPOSIT", "INVESTMENT_AND_RETIREMENT_FUNDS", "SAVINGS", "ACCOUNT_TRANSFER", "OTHER_TRANSFER_IN"],
    "TRANSFER_OUT": ["INVESTMENT_AND_RETIREMENT_FUNDS", "SAVINGS", "WITHDRAWAL", "ACCOUNT_TRANSFER", "OTHER_TRANSFER_OUT"],
    "LOAN_PAYMENTS": ["CAR_PAYMENT", "CREDIT_CARD_PAYMENT", "PERSONAL_LOAN_PAYMENT", "MORTGAGE_PAYMENT", "STUDENT_LOAN_PAYMENT", "OTHER_PAYMENT"],
    "BANK_FEES": ["ATM_FEES", "FOREIGN_TRANSACTION_FEES", "INSUFFICIENT_FUNDS", "INTEREST_CHARGE", "OVERDRAFT_FEES", "OTHER_BANK_FEES"],
    "ENTERTAINMENT": ["CASINOS_AND_GAMBLING", "MUSIC_AND_AUDIO", "SPORTING_EVENTS_AMUSEMENT_PARKS_AND_MUSEUMS", "TV_AND_MOVIES", "VIDEO_GAMES", "OTHER_ENTERTAINMENT"],
    "FOOD_AND_DRINK": ["BEER_WINE_AND_LIQUOR", "COFFEE", "FAST_FOOD", "GROCERIES", "RESTAURANT", "VENDING_MACHINES", "OTHER_FOOD_AND_DRINK"],
    "GENERAL_MERCHANDISE": ["BOOKSTORES_AND_NEWSSTANDS", "CLOTHING_AND_ACCESSORIES", "CONVENIENCE_STORES", "DEPARTMENT_STORES", "DISCOUNT_STORES",
                            "ELECTRONICS", "GIFTS_AND_NOVELTIES", "OFFICE_SUPPLIES", "ONLINE_MARKETPLACES", "PET_SUPPLIES", "SPORTING_GOODS",
                            "SUPERSTORES", "TOBACCO_AND_VAPE", "OTHER_GENERAL_MERCHANDISE"],
    "HOME_IMPROVEMENT": ["FURNITURE", "HARDWARE", "REPAIR_AND_MAINTENANCE", "SECURITY", "OTHER_HOME_IMPROVEMENT"],
    "MEDICAL": ["DENTAL_CARE", "EYE_CARE", "NURSING_CARE", "PHARMACIES_AND_SUPPLEMENTS", "PRIMARY_CARE", "VETERINARY_SERVICES", "OTHER_MEDICAL"],
    "PERSONAL_CARE": ["GYMS_AND_FITNESS_CENTERS", "HAIR_AND_BEAUTY", "LAUNDRY_AND_DRY_CLEANING", "OTHER_PERSONAL_CARE"],
    "GENERAL_SERVICES": ["ACCOUNTING_AND_FINANCIAL_PLANNING", "AUTOMOTIVE", "CHILDCARE", "CONSULTING_AND_LEGAL", "EDUCATION", "INSURANCE",
                         "POSTAGE_AND_SHIPPING", "STORAGE", "OTHER_GENERAL_SERVICES"],
    "GOVERNMENT_AND_NON_PROFIT": ["DONATIONS", "GOVERNMENT_DEPARTMENTS_AND_AGENCIES", "TAX_PAYMENT", "OTHER_GOVERNMENT_AND_NON_PROFIT"],
    "TRANSPORTATION": ["BIKES_AND_SCOOTERS", "GAS", "PARKING", "PUBLIC_TRANSIT", "TAXIS_AND_RIDE_SHARES", "TOLLS", "OTHER_TRANSPORTATION"],
    "TRAVEL": ["FLIGHTS", "LODGING", "RENTAL_CARS", "OTHER_TRAVEL"],
    "RENT_AND_UTILITIES": ["GAS_AND_ELECTRICITY", "INTERNET_AND_CABLE", "RENT", "SEWAGE_AND_WASTE_MANAGEMENT", "TELEPHONE", "WATER", "OTHER_UTILITIES"],
}

CODES: dict[str, str] = {}  # detailed code → primary
for _p, _ds in DETAILED.items():
    for _d in _ds:
        CODES[f"{_p}_{_d}"] = _p

SYNONYMS: dict[str, str] = {
    "groceries": "FOOD_AND_DRINK_GROCERIES", "grocery": "FOOD_AND_DRINK_GROCERIES", "food": "FOOD_AND_DRINK",
    "dining": "FOOD_AND_DRINK_RESTAURANT", "restaurants": "FOOD_AND_DRINK_RESTAURANT", "restaurant": "FOOD_AND_DRINK_RESTAURANT",
    "eating out": "FOOD_AND_DRINK_RESTAURANT", "takeout": "FOOD_AND_DRINK_FAST_FOOD", "fast food": "FOOD_AND_DRINK_FAST_FOOD",
    "coffee": "FOOD_AND_DRINK_COFFEE", "alcohol": "FOOD_AND_DRINK_BEER_WINE_AND_LIQUOR", "drinks": "FOOD_AND_DRINK_BEER_WINE_AND_LIQUOR",
    "gas": "TRANSPORTATION_GAS", "fuel": "TRANSPORTATION_GAS", "car": "TRANSPORTATION", "auto": "TRANSPORTATION", "transport": "TRANSPORTATION",
    "transportation": "TRANSPORTATION", "parking": "TRANSPORTATION_PARKING", "rideshare": "TRANSPORTATION_TAXIS_AND_RIDE_SHARES",
    "uber": "TRANSPORTATION_TAXIS_AND_RIDE_SHARES", "transit": "TRANSPORTATION_PUBLIC_TRANSIT",
    "rent": "RENT_AND_UTILITIES_RENT", "internet": "RENT_AND_UTILITIES_INTERNET_AND_CABLE",
    "phone": "RENT_AND_UTILITIES_TELEPHONE", "electric": "RENT_AND_UTILITIES_GAS_AND_ELECTRICITY", "power": "RENT_AND_UTILITIES_GAS_AND_ELECTRICITY",
    "shopping": "GENERAL_MERCHANDISE", "amazon": "GENERAL_MERCHANDISE_ONLINE_MARKETPLACES", "clothes": "GENERAL_MERCHANDISE_CLOTHING_AND_ACCESSORIES",
    "clothing": "GENERAL_MERCHANDISE_CLOTHING_AND_ACCESSORIES", "electronics": "GENERAL_MERCHANDISE_ELECTRONICS",
    "pets": "GENERAL_MERCHANDISE_PET_SUPPLIES", "pet": "GENERAL_MERCHANDISE_PET_SUPPLIES", "vet": "MEDICAL_VETERINARY_SERVICES",
    "streaming": "ENTERTAINMENT_TV_AND_MOVIES", "entertainment": "ENTERTAINMENT", "games": "ENTERTAINMENT_VIDEO_GAMES",
    "music": "ENTERTAINMENT_MUSIC_AND_AUDIO", "movies": "ENTERTAINMENT_TV_AND_MOVIES",
    "medical": "MEDICAL", "health": "MEDICAL", "doctor": "MEDICAL_PRIMARY_CARE", "pharmacy": "MEDICAL_PHARMACIES_AND_SUPPLEMENTS", "dentist": "MEDICAL_DENTAL_CARE",
    "gym": "PERSONAL_CARE_GYMS_AND_FITNESS_CENTERS", "fitness": "PERSONAL_CARE_GYMS_AND_FITNESS_CENTERS", "haircut": "PERSONAL_CARE_HAIR_AND_BEAUTY",
    "personal care": "PERSONAL_CARE", "beauty": "PERSONAL_CARE_HAIR_AND_BEAUTY",
    "travel": "TRAVEL", "flights": "TRAVEL_FLIGHTS", "hotels": "TRAVEL_LODGING", "lodging": "TRAVEL_LODGING",
    "insurance": "GENERAL_SERVICES_INSURANCE", "childcare": "GENERAL_SERVICES_CHILDCARE", "daycare": "GENERAL_SERVICES_CHILDCARE",
    "education": "GENERAL_SERVICES_EDUCATION", "tuition": "GENERAL_SERVICES_EDUCATION",
    "home": "HOME_IMPROVEMENT", "furniture": "HOME_IMPROVEMENT_FURNITURE", "hardware": "HOME_IMPROVEMENT_HARDWARE", "repairs": "HOME_IMPROVEMENT_REPAIR_AND_MAINTENANCE",
    "donations": "GOVERNMENT_AND_NON_PROFIT_DONATIONS", "charity": "GOVERNMENT_AND_NON_PROFIT_DONATIONS", "taxes": "GOVERNMENT_AND_NON_PROFIT_TAX_PAYMENT",
    "fees": "BANK_FEES", "bank fees": "BANK_FEES",
}
# Whole-phrase only, never a word inside a longer one: an override to either takes the payee out of spending in both
# directions (tx_now's flow, migration 008), so "transfer fee" or "mortgage interest" must not land here by accident.
EXACT: dict[str, str] = {
    "transfer": "TRANSFER_OUT_ACCOUNT_TRANSFER", "internal transfer": "TRANSFER_OUT_ACCOUNT_TRANSFER", "own transfer": "TRANSFER_OUT_ACCOUNT_TRANSFER",
    "mortgage": "LOAN_PAYMENTS_MORTGAGE_PAYMENT",
}


# A name that is several categories, never one: "utilities" is not rent (a renter would be over budget on the 1st).
# Used where a list fits (a budget's name); `resolve` alone still refuses them with these as the candidates.
# "subscriptions" and "kids" are in no category at all (a gym, Netflix, school shoes): they resolve to nothing, so a budget
# by that name is refused and asks for its categories or merchants.
GROUPS: dict[str, list[str]] = {
    "utilities": [f"RENT_AND_UTILITIES_{d}" for d in DETAILED["RENT_AND_UTILITIES"] if d != "RENT"],
}


def primary_of(code: str) -> str | None:
    if code in DETAILED:
        return code
    return CODES.get(code)


def label(code: str) -> str:
    """FOOD_AND_DRINK_FAST_FOOD → "Fast food", FOOD_AND_DRINK → "Food and drink": for people, not for matching."""
    p = CODES.get(code)
    return (code[len(p) + 1:] if p else code).replace("_", " ").capitalize()


def describe(code: str) -> str:
    """What a category covers, as a person reads it: "Food and drink › Restaurant", or a primary with its parts
    ("Rent and utilities: gas and electricity, internet and cable, rent, …"), so "all of it" is never a surprise."""
    p = CODES.get(code)
    if p:
        return f"{label(p)} › {label(code)}"
    return f"{label(code)} (all of it: {', '.join(label(f'{code}_{d}').lower() for d in DETAILED.get(code, []))})"


def guessed(text: str, code: str) -> bool:
    """True when `text` named `code` only through a synonym or a word match ("dining" → Restaurant), not as its code or label."""
    t = text.strip().lower().replace("-", " ").replace("_", " ")
    return t not in (code.lower().replace("_", " "), label(code).lower())


def suggestions(text: str, limit: int = 4) -> list[str]:
    """Categories a name that matched none might mean: each word's synonym ("car insurance" → Transportation, Insurance), then close spellings."""
    words = text.lower().replace("-", " ").replace("_", " ").split()
    hits = [SYNONYMS[w] for w in words if w in SYNONYMS]
    names = {**SYNONYMS, **{label(c).lower(): c for c in list(DETAILED) + list(CODES)}}
    hits += [names[m] for m in difflib.get_close_matches(text.lower().strip(), list(names), n=limit, cutoff=0.7)]
    return list(dict.fromkeys(hits))[:limit]


def resolve(text: str) -> tuple[str | None, list[str]]:
    """Returns (code, candidates). code is set when exactly one match; candidates lists options otherwise."""
    t = text.strip()
    up = t.upper().replace(" ", "_").replace("-", "_")
    if up in DETAILED or up in CODES:
        return up, []
    if t.lower() in EXACT:
        return EXACT[t.lower()], []
    by_label = [c for c in list(DETAILED) + list(CODES) if label(c).lower() == t.lower()]   # the name a picker or a reply showed: "Gas and electricity"
    if len(by_label) == 1:
        return by_label[0], []
    if t.lower() in SYNONYMS:
        return SYNONYMS[t.lower()], []
    if t.lower() in GROUPS:
        return None, GROUPS[t.lower()]
    words = t.lower().replace("-", " ").replace("_", " ").split()
    syn_hits = {SYNONYMS[w] for w in words if w in SYNONYMS}
    if len(syn_hits) == 1:
        return syn_hits.pop(), []
    tokens = [w for w in t.upper().replace("-", " ").replace("_", " ").split() if len(w) > 2]
    hits = [c for c in list(DETAILED) + list(CODES) if all(w in c for w in tokens)] if tokens else []
    if len(hits) == 1:
        return hits[0], []
    return None, hits or sorted(syn_hits)


def listing() -> str:
    lines = []
    for p, ds in DETAILED.items():
        lines.append(p)
        lines.extend(f"  {p}_{d}" for d in ds)
    return "\n".join(lines)
