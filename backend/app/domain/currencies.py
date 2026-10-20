"""
Currencies the agency deals in.

The advance carries its own currency rather than inheriting the order's: an advance is
collected from the candidate in India while the salary is paid abroad, so summing one
against the other would add rupees to dirhams and produce a meaningless total.
"""

CURRENCIES: list[tuple[str, str]] = [
    ("INR", "Indian Rupee"),
    ("USD", "US Dollar"),
    ("EUR", "Euro"),
    ("SGD", "Singapore Dollar"),
    ("AED", "UAE Dirham"),
    ("SAR", "Saudi Riyal"),
    ("QAR", "Qatari Riyal"),
    ("OMR", "Omani Rial"),
    ("KWD", "Kuwaiti Dinar"),
    ("BHD", "Bahraini Dinar"),
]

CURRENCY_CODES = [code for code, _ in CURRENCIES]

DEFAULT_ADVANCE_CURRENCY = "INR"
