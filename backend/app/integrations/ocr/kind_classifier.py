"""Which candidate document a page is, from what is printed on it (never from a file name, never over the network).

`classification.py` decides the three identity documents (resume, passport, Aadhaar). This module decides the rest of the
candidate's document fields for a page that is *not* one of those: signed offer, visa, visa stamping, medical, police clearance
(PCC), emigration clearance, insurance, invoice, advance receipt, undertaking / self declaration, air ticket, education
certificate, experience certificate, Google review and the job application form.

It is deliberately conservative. A kind needs several independent signals (distinctive wording, not one stray word), must
clearly beat every other kind, and a page that fails either test is `None`: the caller keeps what it had (the file name's kinds,
or "other"). Filing a page in the wrong field is worse than leaving it unclassified.

    classify_kind(text)      one page
    classify_kinds(texts)    a document's pages; a page with no evidence of its own may take the kind of the page before it,
                             but only when both carry the same "Page N of M" marker (flagged ``inherited``)
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, replace
from typing import Sequence

from . import classification as _identity

KINDS = (
    "signed_offer", "visa", "visa_stamping", "medical", "pcc", "emigration", "insurance", "invoice", "advance_receipt",
    "undertaking_letter", "air_ticket", "education_certificate", "experience_certificate", "google_review", "application_form",
)

MIN_TEXT = 40            # a page with less text than this carries nothing to judge
STRONG = 3               # a signal of this weight or more is distinctive wording (a title, a field only this document has)
MIN_SCORE = 4            # with at least one strong signal ...
MIN_SCORE_NO_STRONG = 7  # ... or this much weaker evidence on its own
MARGIN = 3               # a kind must beat the next one by this much once that one is itself plausible
NOISY_REAL_WORDS = 0.3   # below this share of real words a page is noisy
NOISY_MIN_SCORE = 8      # the bar on a page that does not read like language
OVERRIDE_CONFIDENCE = 0.7  # what it takes to overrule an identity guess that had no solid evidence of its own


@dataclass(frozen=True)
class KindGuess:
    kind: str | None
    confidence: float = 0.0
    reason: str = ""
    # The page had no evidence of its own and took the kind of the previous page of the same "Page N of M" document.
    inherited: bool = False
    # classification.py took this page for a passport or an Aadhaar card on evidence that is not the document itself (a visa's
    # machine-readable zone, a checksum-valid number on a flight itinerary); this kind is better supported.
    overrides_identity: bool = False
    # The page is also a resume page (an education certificate or an application form read with the resume): it stays in the
    # resume AND belongs in this kind's field.
    alongside_identity: bool = False


NONE = KindGuess(None, 0.0, "")


# ---------------------------------------------------------------------------------------------- pattern building

# What OCR typically turns letters into; applied to words of five letters or more so short words never turn into noise.
_FUZZ = {"i": "[il1!|]", "l": "[li1|]", "o": "[o0]", "a": "[a@]", "s": "[s5$]"}
_SEP = r"[\W_]{0,3}"


def _word(word: str) -> str:
    return "".join(_FUZZ.get(c, c) for c in word) if len(word) >= 5 else word


def ph(*phrases: str) -> str:
    """Alternation of plain phrases: words may be glued ("VisaNumber"), spaced, hyphenated or slashed, and OCR-noisy."""
    out = []
    for phrase in phrases:
        words = phrase.split()
        body = _SEP.join(_word(w) for w in words)
        out.append((r"\b" if len(words[0]) <= 4 else "") + body)
    return "(?:" + "|".join(out) + ")"


def _sig(name: str, weight: int, pattern: str, flags: int = 0) -> tuple[str, int, "re.Pattern[str]"]:
    return name, weight, re.compile(pattern, re.I | flags)


# ---------------------------------------------------------------------------------------------- the signals

_SIGNALS: dict[str, tuple[tuple[str, int, "re.Pattern[str]"], ...]] = {
    "signed_offer": (
        _sig("offer title", 3, ph("offer letter", "letter of offer", "job offer", "appointment letter", "letter of appointment",
                                  "offer of employment", "employment offer", "offer of appointment", "offer you the position",
                                  "offer you the post", "offer you employment")),
        _sig("contract title", 3, r"^\W*(?:employment\W+(?:contract|agreement)|contract\W+of\W+employment)\W*$", re.M),
        _sig("pleased to offer", 3, ph("pleased to extend", "pleased to offer", "pleasure to extend", "delighted to offer",
                                       "happy to offer", "pleased to confirm your appointment")),
        _sig("accept and sign", 2, ph("accept this offer", "sign this letter", "sign and return", "accept the offer",
                                      "acceptance of this offer", "accept the terms", "signed copy of this")),
        _sig("commencement", 2, ph("commencement date", "date of joining", "joining date", "commence work", "report for duty",
                                   "anticipated commencement")),
        _sig("salary", 1, r"\b(?:basic|monthly|gross|total)\W+(?:monthly\W+)?(?:salary|wages?|pay)\b|\bsalary\W{0,3}(?:kd|aed|sar|qar|omr|bhd|rs|inr|usd|\d)"),
        _sig("accommodation", 1, ph("accommodation")),
        _sig("transportation", 1, ph("transportation", "transport provided")),
        _sig("annual leave", 1, ph("annual leave", "paid leave")),
        _sig("working hours", 1, ph("working hours", "working days", "weekly off")),
        _sig("overtime", 1, ph("overtime", "paid ot")),
        _sig("indemnity", 1, ph("indemnity", "end of service", "gratuity")),
        _sig("contract period", 1, ph("contract period", "period of contract", "duration of contract")),
        _sig("probation", 1, ph("probation")),
        _sig("offer valid", 1, ph("offer is valid", "this offer")),
    ),
    "visa": (
        _sig("evisa", 3, ph("e visa", "electronic visa", "evisa")),
        _sig("visa number", 3, ph("visa number", "visa no", "visa ref", "visa reference")),
        _sig("entry permit", 3, ph("entry permit", "residence permit", "entry permit number")),
        _sig("visa type", 2, ph("visa type", "type of visa", "visa category", "class of visa")),
        _sig("visa document words", 2, ph("visa application", "visa approval", "visa validity", "visa issued", "visa copy",
                                          "visa letter", "visa details", "visa expiry", "visa status")),
        _sig("entries", 2, r"\bnumber\W+of\W+entries\b|\b(?:single|multiple)\W+entry\b"),
        _sig("work visa", 1, ph("work visa", "employment visa", "residence visa", "visit visa", "business visa", "work permit")),
        _sig("visa word", 1, r"\bvisa\b"),
        _sig("ministry of interior", 1, ph("ministry of interior", "general department of immigration", "immigration department",
                                           "directorate general of immigration", "gdrfa")),
        _sig("gulf state", 1, ph("state of kuwait", "kingdom of saudi arabia", "united arab emirates", "sultanate of oman",
                                 "state of qatar", "kingdom of bahrain")),
        _sig("occupation", 1, ph("occupation", "profession")),
        _sig("sponsor", 1, ph("sponsor", "employer name")),
        _sig("entry exit stamp", 1, r"\b(?:entry|exit)\W+stamp\b"),
        _sig("issue expiry", 1, r"\bissue\W+date\b[\s\S]{0,200}\bexpiry\W+date\b|\bexpiry\W+date\b[\s\S]{0,200}\bissue\W+date\b"),
    ),
    "visa_stamping": (
        _sig("stamping word", 3, ph("visa stamping", "visa stamp", "stamping slip", "stamping appointment", "stamping date")),
        _sig("embassy", 2, r"\bembass(?:y|ies)\W{0,3}o[ft]\b|\bconsulate\W+general\b|\bhigh\W+commission\W+of\b"),
        _sig("entry transit visa", 3, r"\b\w{0,2}ntr\w{0,3}\W{0,3}tr\w{2,8}\W{0,3}visa\b"),
        _sig("good for", 3, r"\bgood\W*for\W*a?\W*(?:single|several|severl|multiple)"),
        _sig("stay validity", 1, ph("residence valid", "valid for stay", "duration of stay", "period of stay")),
        _sig("vfs", 2, r"\bvfs\b|\bbls\W+international\b"),
        _sig("visa word", 1, r"\bvisa\b|\bvisia\b"),
        _sig("outside only", 1, r"\buse\W+outside\W+the\W+state\b"),
    ),
    "medical": (
        _sig("gamca", 3, ph("gamca", "wafid", "gulf health council", "gcc approved medical")),
        _sig("medical report", 3, ph("detailed candidate report", "medical examination", "medical report", "medical certificate",
                                     "medical fitness report", "medical fitness certificate", "medical test report")),
        _sig("fit statement", 3, r"\b(?:medically\W+(?:un)?fit|(?:is|was|found|declared)\W+(?:to\W+be\W+)?(?:un)?fit\W+(?:for|to)\b|(?:un)?fit\W+for\W+(?:the\W+)?(?:above|job|work|employment))"),
        _sig("gcc code", 3, r"\bg\.?\s?[cgh]\.?\s?c\.?\W{0,3}(?:code|slip)\b"),
        _sig("medical centre", 2, ph("medical center", "medical centre", "diagnostic center", "diagnostic centre", "medical examination center")),
        _sig("medical fitness", 2, ph("medical fitness", "fit to work")),
        _sig("laboratory", 2, ph("laboratory investigation", "serology", "biochemistry", "haematology", "hematology")),
        _sig("infection tests", 2, r"\b(?:hiv|hbs\W{0,2}ag|vdrl|anti\W{0,2}hcv|micro\W{0,2}filaria|malaria)\b"),
        _sig("vitals", 1, ph("blood pressure", "pulse", "haemoglobin", "hemoglobin", "blood group", "bmi")),
        _sig("exam parts", 1, ph("chest x ray", "visual acuity", "hearing", "vaccination", "urine", "stool", "cardiovascular", "genitourinary")),
        _sig("doctor", 1, r"\b(?:dr\W|mbbs|physician|doctor['’]?s?\W+(?:name|signature))"),
    ),
    "pcc": (
        _sig("pcc title", 3, ph("police clearance certificate", "police clearance", "police verification", "police certificate",
                                "police report", "certificate of good conduct", "character certificate", "police verification certificate")
            # ... or the same title with its words split or run together by OCR ("POLICE CLEARANC E CERTIFICATE").
            + r"|\bp[o0]l[il1!|]c[ec]\W{0,3}c[il1|]e[ea]r(?:\W?\w){0,6}\W{0,3}c[ec]rt\W?[il1|]f\w{2,8}"),
        _sig("no adverse", 3, ph("no adverse information", "no adverse report", "no adverse remarks", "no criminal record",
                                 "no criminal case", "criminal record", "no criminal antecedents")),
        _sig("pcc word", 2, r"\bpcc\b"),
        _sig("pcc purpose", 1, r"\bissued\W+for\W+the\W+purpose\W+of\W+(?:\w+\W+){0,3}employment|\bineligible\W+for\W+employment\b"),
        _sig("passport office", 2, ph("regional passport office", "regional passport officer", "passport seva", "passport officer",
                                      "passportindia gov in", "ministry of external affairs")),
        _sig("not valid other purpose", 1, ph("not valid for any other purpose", "not valid for any other country")),
        _sig("issuance", 1, ph("pcc issuance", "issuance date", "file number")),
        _sig("stamp of office", 1, ph("stamp of the office of issue", "signature of passport authority")),
    ),
    "emigration": (
        _sig("protector", 3, ph("protector of emigrants", "protector general of emigrants", "emigration clearance", "emigration check",
                                "emigration act", "emigrate", "e migrate", "emigration clearance certificate")),
        _sig("ecr ecnr", 3, r"\b(?:ecr|ecnr|pge|pdos?)\b"),
        _sig("emigration word", 2, ph("emigration", "emigrant")),
        _sig("pre departure", 2, ph("pre departure orientation", "pre departure")),
        _sig("recruiting agent", 2, ph("registered recruiting agent", "recruiting agent", "recruitment agency", "overseas employment")),
        _sig("migrant", 1, ph("migrant", "overseas workers", "overseas indian affairs", "ministry of external affairs")),
        _sig("clearance", 1, ph("clearance")),
    ),
    "insurance": (
        _sig("policy", 3, ph("insurance policy", "policy number", "policy no", "certificate of insurance", "sum insured",
                             "pravasi bharatiya bima", "insurance certificate", "insurance cover", "insurance coverage", "policy schedule")),
        _sig("pbby", 3, r"\bpbby\b|\birdai\b"),
        _sig("premium", 2, ph("premium")),
        _sig("insured", 2, r"\b(?:insured|insurer|policy\W*holder)\b"),
        _sig("policy period", 2, ph("policy period", "period of insurance", "date of commencement of policy")),
        _sig("insurance company", 2, ph("insurance company", "general insurance", "life insurance", "insurance co")),
        _sig("benefits", 1, ph("accidental death", "death benefit", "permanent disability", "total disability", "nominee", "beneficiary")),
        _sig("claim", 1, ph("claim")),
        _sig("insurance word", 1, ph("insurance")),
    ),
    "invoice": (
        _sig("invoice title", 3, ph("tax invoice", "invoice no", "invoice number", "invoice date", "proforma invoice", "invoice #",
                                    "commercial invoice")),
        _sig("invoice word", 1, r"\binvoices?\b"),
        _sig("bill to", 2, ph("bill to", "billed to", "ship to", "invoice to")),
        _sig("gst", 1, r"\b(?:gstin|gst\W+no|hsn|sac\W+code|vat\W+(?:no|number|reg))\b"),
        _sig("totals", 2, ph("sub total", "subtotal", "amount due", "balance due", "grand total", "total amount", "amount in words")),
        _sig("lines", 1, ph("quantity", "unit price", "description of services", "qty")),
        _sig("bank", 1, ph("bank details", "ifsc", "account number", "account no", "payment terms", "due date")),
    ),
    "advance_receipt": (
        _sig("receipt title", 3, ph("advance receipt", "payment receipt", "money receipt", "cash receipt", "receipt no", "receipt number",
                                    "receipt #", "received with thanks", "acknowledgement of payment", "acknowledgment of payment")),
        _sig("received", 2, r"\breceived\W+(?:a\W+sum|an\W+amount|rs|inr|₹|from|with\W+thanks|the\W+sum)"),
        _sig("receipt word", 2, ph("receipt")),
        _sig("towards", 2, ph("towards advance", "towards service charge", "towards registration", "towards processing", "service charge",
                              "service charges", "advance amount", "advance payment")),
        _sig("payment mode", 1, ph("mode of payment", "payment mode", "paid by", "cash", "upi", "transaction id", "neft", "cheque")),
        _sig("rupees", 1, r"\brupees\b[\s\S]{0,80}\bonly\b|₹|\brs\W?\s?\d"),
    ),
    "undertaking_letter": (
        _sig("self declaration", 4, ph("self declaration", "undertaking letter", "letter of undertaking", "declaration letter")),
        _sig("undertake", 3, ph("i hereby undertake", "i undertake", "do hereby undertake", "this undertaking", "undertaking")),
        _sig("declare", 2, ph("i hereby declare", "i do hereby declare", "i solemnly declare", "willful consent", "willing consent",
                              "of my own free will", "of my own will", "agree to abide", "obey all the above", "abide by")),
        _sig("candidate signature", 2, ph("candidate signature", "signature of candidate", "signature of the candidate", "signature of applicant",
                                          "candidate thumb impression", "thumb impression")),
        _sig("terms", 1, ph("terms and conditions", "terms conditions")),
        _sig("witness", 1, ph("witness")),
        _sig("repay", 1, ph("liable to repay", "bear the cost", "bear all expenses")),
    ),
    "air_ticket": (
        _sig("ticket words", 3, ph("e ticket", "eticket", "itinerary", "boarding pass", "ticket number", "ticket no", "booking reference",
                                   "pnr", "passenger name", "flight no", "flight number", "flight details", "air ticket")),
        _sig("booking confirmed", 2, ph("booking confirmed", "booking confirmation", "booking id", "booking status", "confirmed booking")),
        _sig("airline", 2, r"\b(?:airways|airlines?|air\W+(?:india|arabia)|indigo|emirates|etihad|kuwait\W+airways|jazeera|flydubai|spicejet|vistara|oman\W+air|qatar\W+airways|saudia)\b"),
        _sig("route", 2, r"\b(?:depart(?:s|ure)?|arriv(?:es|al))\b[\s\S]{0,300}\b(?:depart(?:s|ure)?|arriv(?:es|al))\b"),
        _sig("flight", 1, ph("flight")),
        _sig("passenger", 1, ph("passenger", "passengers")),
        _sig("baggage", 1, ph("baggage", "checked in", "cabin bag", "check in counter", "check in counters")),
        _sig("fare", 1, ph("fare rules", "fare", "carrier surcharge", "non refundable")),
        _sig("terminal", 1, ph("terminal", "gate", "seat")),
    ),
    "education_certificate": (
        _sig("certificate title", 3, ph("degree certificate", "provisional certificate", "mark sheet", "marks sheet", "marks card", "marks memo",
                                        "statement of marks", "consolidated marks", "grade card", "statement of grades",
                                        "transfer certificate", "school leaving certificate", "convocation", "hereby makes known",
                                        "admitted to the degree", "awarded the degree", "diploma certificate", "national trade certificate",
                                        "marks statement", "memorandum of marks", "grade sheet", "syndicate of the", "provisional degree",
                                        "course completion certificate")),
        _sig("university exam", 3, r"\buniversity\b[\s\S]{0,500}\b(?:examination|semester|marks|degree|bachelor|master)\b"),
        _sig("board", 3, ph("secondary education examination", "pre university", "higher secondary", "senior secondary", "board of secondary",
                            "board of school", "board of technical", "board of intermediate", "examination board", "technical education",
                            "council of higher", "directorate of government examinations", "department of school education")),
        _sig("sslc", 3, r"\bs\W?s\W?l\W?c\b|\bhsc\b|\bssc\b|\bpuc\b|\bi\W?t\W?i\b\W+(?:certificate|trade)"),
        _sig("register", 2, ph("register no", "registration no", "roll no", "seat no", "reg no", "enrolment no", "enrollment no")),
        _sig("marks", 2, ph("marks obtained", "total marks", "max marks", "maximum marks", "marks in words", "grade points")),
        _sig("passed exam", 2, r"\b(?:has|have)\W+(?:successfully\W+)?(?:passed|completed|qualified)\b[\s\S]{0,80}\bexamination\b|\bpassed\W+(?:the\W+)?(?:\w+\W+){0,3}examination\b"),
        _sig("institution", 1, r"\b(?:university|college|polytechnic|institute|school)\b"),
        _sig("award", 1, r"\b(?:bachelor|master)\W+of\b|\bdiploma\W+in\b|\bb\W?tech\b|\bm\W?tech\b|\bb\W?com\b|\bb\W?sc\b|\bm\W?sc\b|\bmba\b"),
        _sig("class", 1, r"\b(?:first|second|third)\W+class\b|\bdistinction\b|\bcgpa\b"),
        _sig("result date", 1, ph("date of result", "month year", "year of passing", "date of issue")),
    ),
    "experience_certificate": (
        _sig("experience title", 3, ph("experience certificate", "experience letter", "service certificate", "relieving letter",
                                       "relieving certificate", "experience cum relieving", "employment certificate",
                                       "certificate of employment", "employment verification", "employment letter",
                                       "work experience certificate")),
        _sig("to whom", 2, ph("to whom it may concern", "to whomsoever it may concern", "to whom so ever it may concern", "to whom soever it may concern")),
        _sig("certify employment", 3, r"\bcertify\W+that\W+[\s\S]{0,250}\b(?:worked|working|employed|serving|served|associated|joined|employee)\b"),
        _sig("has worked", 2, r"\b(?:has|have|had)\W+(?:been\W+)?(?:worked|working|served|serving)\b"),
        _sig("designation", 1, ph("designation", "designated as")),
        _sig("joined", 1, ph("date of joining", "joined on", "joined the company", "joined us", "joined as")),
        _sig("was employed", 2, r"\b(?:was|is|has\W+been|had\W+been)\W+(?:employed|working|associated|engaged)\W+(?:with|in|at|by|as)\b"),
        _sig("worked with us", 2, r"\bworked\W+(?:with|for|in|as|at)\W+(?:us|our|the\W+company|m/s)\b|\bworked\W+as\b"),
        _sig("capacity", 2, ph("in the capacity of", "in the capacity as", "in the position of", "in the role of")),
        _sig("conduct", 1, r"\b(?:his|her)\W+(?:conduct|character|performance|work)\W+(?:was|is|has|during)\b|\bwish\W+(?:him|her)\W+(?:all|success|the\W+best)"),
        _sig("leaving", 2, ph("date of leaving", "last working day", "relieved from", "resigned", "relieved of his", "relieved of her")),
        _sig("issued at request", 2, ph("issued at his request", "issued at her request", "issued on his request", "issued upon his request")),
        _sig("period", 1, r"\bfrom\W+\d{1,2}\W{0,3}[a-z0-9]{2,9}\W{0,3}\d{2,4}\W+(?:to|till|until)\W+\d{1,2}\W{0,3}[a-z0-9]{2,9}\W{0,3}\d{2,4}"),
        _sig("responsibilities", 1, ph("responsibilities included", "key responsibilities", "duties included", "his duties", "her duties")),
        _sig("signatory", 1, ph("general manager", "hr manager", "human resource manager", "human resources", "authorised signatory", "authorized signatory")),
    ),
    "google_review": (
        _sig("google review", 3, ph("google review", "google reviews", "google maps", "local guide", "write a review", "review on google",
                                    "reviewed on google", "rate and review", "posted publicly", "your review")),
        _sig("stars", 2, r"★{2,}|⭐|\b[1-5]\W{0,2}(?:out\W+of\W+5\W+)?stars?\b|\bstar\W+rating\b"),
        _sig("review word", 1, r"\breviews?\b|\brating\b"),
        _sig("google word", 2, r"\bgoogle\b"),
        _sig("ago", 1, r"\b\d+\W+(?:days?|weeks?|months?|years?)\W+ago\b|\ba\W+(?:day|week|month|year)\W+ago\b"),
    ),
    "application_form": (
        _sig("form title", 3, r"^\W*(?:job\W+)?application\W+form\W*$|\bjob\W+application\W+form\b", re.M),
        _sig("post applied", 2, ph("post applied for", "position applied for", "applied for the post")),
        _sig("interview date", 1, ph("interview date", "date of interview")),
        _sig("salary expected", 1, ph("salary expected", "expected salary", "salary offered")),
        _sig("languages known", 1, ph("languages known")),
        _sig("employers", 1, ph("name of employer", "name of employers", "previous employer")),
        _sig("applicant", 1, ph("applicant's name", "applicants name", "name of applicant", "signature of applicant", "signature of the applicant")),
        _sig("qualification", 1, ph("educational qualification", "educational qualifications")),
    ),
}


# ---------------------------------------------------------------------------------------------- scoring

_SPACED = re.compile(r"\b(?:[A-Za-z]\s){3,}[A-Za-z]\b")


def _normalise(text: str) -> str:
    """Text as the patterns want it: compatibility characters folded, soft hyphens and zero-width marks removed, a heading
    OCR has letter-spaced ("P O L I C E") put back together."""
    text = unicodedata.normalize("NFKC", text or "")
    text = text.replace("­", "").replace("​", "").replace("‌", "").replace("‍", "")
    return _SPACED.sub(lambda match: match.group(0).replace(" ", ""), text)


def _scores(text: str) -> dict[str, tuple[int, list[str], bool]]:
    """kind -> (score, names of the signals that matched, whether one of them is strong)."""
    out: dict[str, tuple[int, list[str], bool]] = {}
    for kind, signals in _SIGNALS.items():
        hits = [(name, weight) for name, weight, pattern in signals if pattern.search(text)]
        out[kind] = (sum(w for _, w in hits), [n for n, _ in hits], any(w >= STRONG for _, w in hits))
    return out


def _confidence(score: int) -> float:
    return round(min(0.95, 0.4 + 0.06 * score), 2)


def _decide(scores: dict[str, tuple[int, list[str], bool]], noisy: bool = False) -> tuple[str | None, int, list[str], str]:
    """The winning kind of a page, or None with the reason there is none. A page that does not read like language (a poor scan,
    text read sideways) throws up chance matches, so it must clear a much higher bar."""
    floor, floor_no_strong = (NOISY_MIN_SCORE, NOISY_MIN_SCORE) if noisy else (MIN_SCORE, MIN_SCORE_NO_STRONG)
    # A letter or a marks statement is mostly ordinary wording: several modest signals together ("was employed", a date range,
    # "to whom it may concern") are the document even when none of them is a title. These two kinds need less on their own.
    own_floor = {} if noisy else {"experience_certificate": 3, "education_certificate": 4}
    plausible = {kind: value for kind, value in scores.items()
                 if (value[2] and value[0] >= floor) or value[0] >= own_floor.get(kind, floor_no_strong)}
    if not plausible:
        best = max(scores.items(), key=lambda item: item[1][0])
        return None, best[1][0], [], f"No document kind has enough evidence (best: {best[0]} {best[1][0]})."
    ranked = sorted(plausible.items(), key=lambda item: item[1][0], reverse=True)
    kind, (score, names, _) = ranked[0]
    rival_kind, rival = max(((k, v[0]) for k, v in scores.items() if k != kind), key=lambda item: item[1])
    # Two documents on one sheet (both plausible, neither far ahead) or a close call: not a guess to make.
    if (rival_kind in plausible and rival * 2 >= score) or (rival >= 3 and score - rival < MARGIN):
        return None, score, names, f"Ambiguous between {kind} ({score}) and {rival_kind} ({rival}); left unclassified."
    return kind, score, names, ""


def _noisy(text: str) -> bool:
    """A page that does not read like language: a poor scan or text read sideways is mostly one- and two-letter fragments and
    runs of consonants. Plain text, tables and forms have about half of their tokens as real words of four letters or more."""
    tokens = re.findall(r"[A-Za-z]+", text)
    if len(tokens) < 40:
        return False
    real = sum(1 for token in tokens if len(token) >= 4 and re.search(r"[aeiouy]", token, re.I)
               and not re.search(r"[^aeiouy\W\d_]{5,}", token, re.I) and not re.search(r"(.)\1\1", token, re.I))
    return real / len(tokens) < NOISY_REAL_WORDS


# ---------------------------------------------------------------------------------------------- identity guard

_MRZ_LINE = re.compile(r"<<|[A-Z0-9<]{9}[0-9O<][A-Z<]{3}[0-9OIBS]{6}")


def _passport_on_its_own_captions(text: str) -> bool:
    """Whether the page is a passport even if its machine-readable zone is ignored (printed captions, a "P<" zone). A visa
    carries a zone of its own ("V<"), which is why a zone alone does not make a page a passport page."""
    upper = text.upper().translate(_identity._MRZ_FILLER)
    if any(_identity._MRZ_LINE1.match(re.sub(r"\s+", "", line)) for line in upper.splitlines() if len(line.strip()) >= 28):
        return True
    kept = "\n".join(line for line in text.splitlines() if not _MRZ_LINE.search(line.upper().translate(_identity._MRZ_FILLER)))
    return _identity._is_passport(kept)


def _identity_guard(text: str) -> tuple[str, bool]:
    """(what classification.py takes the page for, whether that is solid). Solid means the page is that document: a passport
    page with its own captions or a "P<" zone, an Aadhaar card with the issuer / enrolment / VID structure, a resume page."""
    kind = _identity.classify_page(text)
    types = {kind.document_type, *kind.also}
    if "passport" in types and not _passport_on_its_own_captions(text):
        return "passport", False
    if "aadhaar" in types and not _identity._aadhaar_structure(text):
        return "aadhaar", False
    if "passport" in types:
        return "passport", True
    if "aadhaar" in types:
        return "aadhaar", True
    return kind.document_type, True


# ---------------------------------------------------------------------------------------------- the classifier


def classify_kind(text: str) -> KindGuess:
    """The candidate document a page is, or ``kind=None`` when nothing is confidently one of them (see the module docstring)."""
    raw = text or ""
    if len(raw.strip()) < MIN_TEXT:
        return KindGuess(None, 0.0, "Too little text to tell.")
    normalised = _normalise(raw)
    scores = _scores(normalised)
    kind, score, names, why = _decide(scores, _noisy(raw))
    identity, solid = _identity_guard(raw)

    if identity == "resume":
        # A resume page is the resume. Only a certificate or a form that is read together with it is also filed in its own field.
        if kind in {"education_certificate", "application_form"} and (kind == "application_form" or _identity._structure_strength(raw) < 3):
            return KindGuess(kind, _confidence(score), f"{kind}: " + ", ".join(names) + f" (score {score}); also read with the resume.",
                             alongside_identity=True)
        return KindGuess(None, 0.0, "Resume page.")
    if identity in {"passport", "aadhaar"}:
        if solid or kind is None or _confidence(score) < OVERRIDE_CONFIDENCE:
            return KindGuess(None, 0.0, f"{identity.capitalize()} page.")
        return KindGuess(kind, _confidence(score), f"{kind}: " + ", ".join(names) + f" (score {score}); the {identity} guess rested on "
                         "a machine-readable zone or a bare number, not on the document itself.", overrides_identity=True)
    if kind is None:
        return KindGuess(None, 0.0, why)
    return KindGuess(kind, _confidence(score), f"{kind}: " + ", ".join(names) + f" (score {score}).")


# "Page 2 of 3", "Page 2/3", "page 2 of 3".
_PAGE_MARK = re.compile(r"\bpage\W{0,3}(\d{1,2})\W{0,3}(?:of|/)\W{0,3}(\d{1,2})\b", re.I)


def _page_marker(text: str) -> tuple[int, int] | None:
    found = {(int(n), int(m)) for n, m in _PAGE_MARK.findall(text or "") if 1 <= int(n) <= int(m) and int(m) >= 2}
    return next(iter(found)) if len(found) == 1 else None


def classify_kinds(texts: Sequence[str]) -> list[KindGuess]:
    """`classify_kind` for each page of a document. A page with no kind of its own takes the kind of the page just before it only
    when both carry a "Page N of M" marker with the same M and consecutive numbers (a continuation of the same document); that
    guess is flagged ``inherited`` and never outranks a page's own evidence."""
    guesses = [classify_kind(text) for text in texts]
    markers = [_page_marker(text) for text in texts]
    for index in range(1, len(texts)):
        here, before = markers[index], markers[index - 1]
        previous = guesses[index - 1]
        if (guesses[index].kind is None and previous.kind is not None and here and before
                and here[1] == before[1] and here[0] == before[0] + 1 and not previous.alongside_identity):
            guesses[index] = replace(
                previous, confidence=round(min(previous.confidence, 0.6), 2), inherited=True, overrides_identity=False,
                reason=f"Page {here[0]} of {here[1]} continues page {before[0]}, which is {previous.kind}; no evidence of its own.")
    return guesses
