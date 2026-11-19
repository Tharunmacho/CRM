"""More currencies in the managed list, so the job form can follow the country.

The job (order) form now switches its salary currency to the one the chosen country uses (Malaysia -> MYR, Sri Lanka ->
LKR ...). Migration 0017 only seeded eleven codes; this adds every other currency a country can map to (150 in all with
the first eleven), so every country the job form offers has its currency on the list. Nothing else changes.

Additive and idempotent: every row is INSERT .. ON CONFLICT (code) DO NOTHING, so a code that already exists keeps
whatever an administrator did to it (renamed, switched off, re-ordered), running it twice is harmless, and nothing is
ever updated or deleted. New rows are appended after the current last sort position and are active. An administrator
can still switch any of them off or delete an unused one under Users > Agency settings.

The downgrade removes only the rows this revision added, and only when no order, stored advance or saved advance draft
uses them and they are not the agency defaults; used ones are left in place.
"""
import sqlalchemy as sa
from alembic import op

revision = "0045_more_currencies"
down_revision = "0044_simple_continuous_ids"
branch_labels = None
depends_on = None

# (code, name), in the order they are appended to the list: first the currencies of the countries the agency recruits for
# or places people in, then every other currency the job form's country -> currency mapping can pick, A to Z by code.
# frontend/src/features/jobs/country-currency.ts lists the same codes; tests/test_agency_settings.py keeps the two equal.
PRIORITY = [
    ("MYR", "Malaysian Ringgit"), ("LKR", "Sri Lankan Rupee"), ("NPR", "Nepalese Rupee"), ("BDT", "Bangladeshi Taka"),
    ("PKR", "Pakistani Rupee"), ("JOD", "Jordanian Dinar"), ("MVR", "Maldivian Rufiyaa"), ("CAD", "Canadian Dollar"),
    ("AUD", "Australian Dollar"), ("NZD", "New Zealand Dollar"), ("CHF", "Swiss Franc"), ("THB", "Thai Baht"),
    ("IDR", "Indonesian Rupiah"), ("PHP", "Philippine Peso"), ("CNY", "Chinese Yuan"), ("HKD", "Hong Kong Dollar"),
    ("TRY", "Turkish Lira"), ("EGP", "Egyptian Pound"), ("ZAR", "South African Rand"), ("KES", "Kenyan Shilling"),
]
OTHERS = {
    "AFN": "Afghan Afghani", "ALL": "Albanian Lek", "AMD": "Armenian Dram", "AOA": "Angolan Kwanza", "ARS": "Argentine Peso",
    "AWG": "Aruban Florin", "AZN": "Azerbaijani Manat", "BAM": "Bosnia-Herzegovina Convertible Mark", "BBD": "Barbadian Dollar",
    "BIF": "Burundian Franc", "BMD": "Bermudian Dollar", "BND": "Brunei Dollar", "BOB": "Bolivian Boliviano", "BRL": "Brazilian Real",
    "BSD": "Bahamian Dollar", "BTN": "Bhutanese Ngultrum", "BWP": "Botswana Pula", "BYN": "Belarusian Ruble", "BZD": "Belize Dollar",
    "CDF": "Congolese Franc", "CLP": "Chilean Peso", "COP": "Colombian Peso", "CRC": "Costa Rican Colon", "CUP": "Cuban Peso",
    "CVE": "Cape Verdean Escudo", "CZK": "Czech Koruna", "DJF": "Djiboutian Franc", "DKK": "Danish Krone", "DOP": "Dominican Peso",
    "DZD": "Algerian Dinar", "ERN": "Eritrean Nakfa", "ETB": "Ethiopian Birr", "FJD": "Fijian Dollar", "GEL": "Georgian Lari",
    "GHS": "Ghanaian Cedi", "GMD": "Gambian Dalasi", "GNF": "Guinean Franc", "GTQ": "Guatemalan Quetzal", "GYD": "Guyanese Dollar",
    "HNL": "Honduran Lempira", "HTG": "Haitian Gourde", "HUF": "Hungarian Forint", "ILS": "Israeli New Shekel", "IQD": "Iraqi Dinar",
    "IRR": "Iranian Rial", "ISK": "Icelandic Krona", "JMD": "Jamaican Dollar", "JPY": "Japanese Yen", "KGS": "Kyrgyzstani Som",
    "KHR": "Cambodian Riel", "KMF": "Comorian Franc", "KPW": "North Korean Won", "KRW": "South Korean Won", "KYD": "Cayman Islands Dollar",
    "KZT": "Kazakhstani Tenge", "LAK": "Lao Kip", "LBP": "Lebanese Pound", "LRD": "Liberian Dollar", "LSL": "Lesotho Loti",
    "LYD": "Libyan Dinar", "MAD": "Moroccan Dirham", "MDL": "Moldovan Leu", "MGA": "Malagasy Ariary", "MKD": "Macedonian Denar",
    "MMK": "Myanmar Kyat", "MNT": "Mongolian Tugrik", "MOP": "Macanese Pataca", "MRU": "Mauritanian Ouguiya", "MUR": "Mauritian Rupee",
    "MWK": "Malawian Kwacha", "MXN": "Mexican Peso", "MZN": "Mozambican Metical", "NAD": "Namibian Dollar", "NGN": "Nigerian Naira",
    "NIO": "Nicaraguan Cordoba", "NOK": "Norwegian Krone", "PAB": "Panamanian Balboa", "PEN": "Peruvian Sol", "PGK": "Papua New Guinean Kina",
    "PLN": "Polish Zloty", "PYG": "Paraguayan Guarani", "RON": "Romanian Leu", "RSD": "Serbian Dinar", "RUB": "Russian Ruble",
    "RWF": "Rwandan Franc", "SBD": "Solomon Islands Dollar", "SCR": "Seychellois Rupee", "SDG": "Sudanese Pound", "SEK": "Swedish Krona",
    "SLE": "Sierra Leonean Leone", "SOS": "Somali Shilling", "SRD": "Surinamese Dollar", "SSP": "South Sudanese Pound",
    "STN": "Sao Tome and Principe Dobra", "SYP": "Syrian Pound", "SZL": "Eswatini Lilangeni", "TJS": "Tajikistani Somoni",
    "TMT": "Turkmenistani Manat", "TND": "Tunisian Dinar", "TOP": "Tongan Paanga", "TTD": "Trinidad and Tobago Dollar",
    "TWD": "New Taiwan Dollar", "TZS": "Tanzanian Shilling", "UAH": "Ukrainian Hryvnia", "UGX": "Ugandan Shilling", "UYU": "Uruguayan Peso",
    "UZS": "Uzbekistani Som", "VES": "Venezuelan Bolivar", "VND": "Vietnamese Dong", "VUV": "Vanuatu Vatu", "WST": "Samoan Tala",
    "XAF": "Central African CFA Franc", "XCD": "East Caribbean Dollar", "XCG": "Caribbean Guilder", "XOF": "West African CFA Franc",
    "XPF": "CFP Franc", "YER": "Yemeni Rial", "ZMW": "Zambian Kwacha", "ZWG": "Zimbabwe Gold",
}
CURRENCIES = PRIORITY + sorted(OTHERS.items())


def upgrade():
    bind = op.get_bind()
    start = bind.scalar(sa.text("SELECT COALESCE(MAX(sort_order), 0) FROM currencies")) or 0
    for index, (code, name) in enumerate(CURRENCIES, start=1):
        bind.execute(sa.text("INSERT INTO currencies (code, name, sort_order) VALUES (:code, :name, :order) ON CONFLICT (code) DO NOTHING"),
                     {"code": code, "name": name, "order": start + index * 10})


def downgrade():
    bind = op.get_bind()
    for code, _ in CURRENCIES:
        bind.execute(sa.text("""
            DELETE FROM currencies c WHERE c.code = :code
              AND NOT EXISTS (SELECT 1 FROM crm_job_orders o WHERE o.currency = c.code)
              AND NOT EXISTS (SELECT 1 FROM agency_settings s WHERE c.code IN (s.default_order_currency, s.default_advance_currency))
              AND NOT EXISTS (SELECT 1 FROM crm_application_stages a WHERE a.amount_currency = c.code OR a.data ->> 'amountCurrency' = c.code)
              AND NOT EXISTS (SELECT 1 FROM crm_applications p WHERE p.draft ->> 'amountCurrency' = c.code)
        """), {"code": code})
