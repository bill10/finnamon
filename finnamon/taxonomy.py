"""Plaid's personal-finance-category taxonomy plus a small synonym table. Deterministic name
resolution: exact code → synonym → token match on Plaid's names → (ambiguous) list to choose from.
No model here, on purpose: a wrong budget category silently miscounts for months."""
from __future__ import annotations

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
    "rent": "RENT_AND_UTILITIES_RENT", "utilities": "RENT_AND_UTILITIES", "internet": "RENT_AND_UTILITIES_INTERNET_AND_CABLE",
    "phone": "RENT_AND_UTILITIES_TELEPHONE", "electric": "RENT_AND_UTILITIES_GAS_AND_ELECTRICITY", "power": "RENT_AND_UTILITIES_GAS_AND_ELECTRICITY",
    "shopping": "GENERAL_MERCHANDISE", "amazon": "GENERAL_MERCHANDISE_ONLINE_MARKETPLACES", "clothes": "GENERAL_MERCHANDISE_CLOTHING_AND_ACCESSORIES",
    "clothing": "GENERAL_MERCHANDISE_CLOTHING_AND_ACCESSORIES", "electronics": "GENERAL_MERCHANDISE_ELECTRONICS",
    "pets": "GENERAL_MERCHANDISE_PET_SUPPLIES", "pet": "GENERAL_MERCHANDISE_PET_SUPPLIES", "vet": "MEDICAL_VETERINARY_SERVICES",
    "subscriptions": "ENTERTAINMENT", "streaming": "ENTERTAINMENT_TV_AND_MOVIES", "entertainment": "ENTERTAINMENT", "games": "ENTERTAINMENT_VIDEO_GAMES",
    "music": "ENTERTAINMENT_MUSIC_AND_AUDIO", "movies": "ENTERTAINMENT_TV_AND_MOVIES",
    "medical": "MEDICAL", "health": "MEDICAL", "doctor": "MEDICAL_PRIMARY_CARE", "pharmacy": "MEDICAL_PHARMACIES_AND_SUPPLEMENTS", "dentist": "MEDICAL_DENTAL_CARE",
    "gym": "PERSONAL_CARE_GYMS_AND_FITNESS_CENTERS", "fitness": "PERSONAL_CARE_GYMS_AND_FITNESS_CENTERS", "haircut": "PERSONAL_CARE_HAIR_AND_BEAUTY",
    "personal care": "PERSONAL_CARE", "beauty": "PERSONAL_CARE_HAIR_AND_BEAUTY",
    "travel": "TRAVEL", "flights": "TRAVEL_FLIGHTS", "hotels": "TRAVEL_LODGING", "lodging": "TRAVEL_LODGING",
    "insurance": "GENERAL_SERVICES_INSURANCE", "childcare": "GENERAL_SERVICES_CHILDCARE", "daycare": "GENERAL_SERVICES_CHILDCARE",
    "education": "GENERAL_SERVICES_EDUCATION", "tuition": "GENERAL_SERVICES_EDUCATION", "kids": "GENERAL_SERVICES_CHILDCARE",
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


def primary_of(code: str) -> str | None:
    if code in DETAILED:
        return code
    return CODES.get(code)


def resolve(text: str) -> tuple[str | None, list[str]]:
    """Returns (code, candidates). code is set when exactly one match; candidates lists options otherwise."""
    t = text.strip()
    up = t.upper().replace(" ", "_").replace("-", "_")
    if up in DETAILED or up in CODES:
        return up, []
    if t.lower() in EXACT:
        return EXACT[t.lower()], []
    if t.lower() in SYNONYMS:
        return SYNONYMS[t.lower()], []
    words = t.lower().replace("-", " ").replace("_", " ").split()
    syn_hits = {SYNONYMS[w] for w in words if w in SYNONYMS}
    if len(syn_hits) == 1:
        return syn_hits.pop(), []
    tokens = [w for w in t.upper().replace("-", " ").replace("_", " ").split() if len(w) > 2]
    hits = [c for c in list(DETAILED) + list(CODES) if all(w in c for w in tokens)] if tokens else []
    if len(hits) == 1:
        return hits[0], []
    return None, hits


def listing() -> str:
    lines = []
    for p, ds in DETAILED.items():
        lines.append(p)
        lines.extend(f"  {p}_{d}" for d in ds)
    return "\n".join(lines)
