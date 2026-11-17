"""Work out WHICH PERSON each file (and each page of a file) belongs to, from what is printed on it. File names are only a weaker,
flagged second source (see WEAKER EVIDENCE below).

A folder can hold the documents of several candidates under names that say nothing ("scan1.pdf", "IMG_2231.jpg"). `identify_people`
reads every file locally (embedded text, else Tesseract; never a network reader), pulls out the evidence of who a page is about
and clusters pages into people:

* STRONG links only join pages: the same passport number, the same checksum-valid Aadhaar number, the same phone or e-mail,
  or names that are one person's name (at least two name parts in common; a bare first name never links anything);
* a link is refused when the two sides disagree on a passport or Aadhaar number, or when their names share no word;
* a page with no evidence of its own is never guessed: it joins a person only because it sits between two pages of that
  person inside one file, or because its whole file is that one person's. Everything else stays `unplaced` for a human;
* a NAME counts only when it is plausible (`plausible_name`): English-only OCR reads Tamil / Hindi script as Latin-looking junk
  ("Aaharapef dio tdliadledf"), and junk never creates, names or links a person. A card with only an Aadhaar number and no
  readable name is not a person while somebody in the folder is known by name or passport: it waits, "unplaced", to be placed
  (when it is all the folder has it stays one candidate, "Unnamed candidate (Aadhaar ending 123)", flagged for a look);
* WEAKER EVIDENCE, only for pages the content could not place, flagged on the person, and never against an identity number:
  the name at the start of a FILE NAME ("Gunavelan-aadhar.pdf") and a first name alone on a page ("Dear Mr. Gunavellan") when
  exactly one person in the folder fits (see `_place_by_names`).

Guarantees (each is tested):

* PAGE ACCOUNTING. Every page of every file is in exactly one of `assigned`, `unplaced` (with a reason) or `unreadable` (with a
  reason); `totals` adds up. A file is never dropped: one that cannot be opened is listed as unreadable (it counts as one page).
* READ HARDER BEFORE GIVING UP. A page that carries no identity evidence after the normal read is read again with the full
  OCR ladder (rotations, higher resolution, second engine), within a fixed extra time per page and per file.
* NO SILENT MISTAKES. Two pieces of evidence that disagree (two passport numbers, two dates of birth, names with no word in
  common, a one-character difference between two passport numbers) mark the person `needsLook` with the exact files and pages.
  A full identity number is never put in the result's messages or in a log: only its last three characters.
* PER-PAGE CONFIDENCE and the way the page was placed (`evidence`, `adjacency`, `file`) are reported; a low-confidence page that
  was placed without evidence of its own is flagged.
* DETERMINISM. The same files give the same answer in any order.

The name in the result is only a stand-in until the online reading of the passport / Aadhaar / resume replaces it.
"""
from __future__ import annotations

import logging
import re
import time
import unicodedata
from dataclasses import dataclass, field, replace
from difflib import SequenceMatcher
from functools import lru_cache
from typing import Callable, Iterable

logger = logging.getLogger(__name__)

MAX_PAGES = 40
PAGE_TIMEOUT = 8
DOCUMENT_TIMEOUT = 120
# Reading harder: at most this many weak pages per file, this long each, this long for the file.
DEEP_MAX_PAGES = 6
DEEP_PAGE_TIMEOUT = 20
DEEP_FILE_BUDGET = 120
LOW_CONFIDENCE = 0.6
SUPPORTED = (".pdf", ".jpg", ".jpeg", ".png", ".webp", ".heic", ".tif", ".tiff", ".doc", ".docx")


def tail(value: str, size: int = 3) -> str:
    """The only part of an identity number that may be shown or logged."""
    value = str(value or "")
    return "…" + value[-size:] if value else ""


# --------------------------------------------------------------------------------------------- what was read

@dataclass
class PageRead:
    number: int
    status: str                 # "read" | "blank" | "unread"
    text: str = ""
    confidence: float = 0.0     # 0..1; native text is 1.0
    method: str = ""
    doc_type: str = "unknown"   # what the page classifier made of it (resume, passport, aadhaar ...)
    reason: str = ""


@dataclass
class FileRead:
    pages: list[PageRead] = field(default_factory=list)
    page_count: int = 0
    error: str | None = None    # the file could not be opened: why (shown to the person)


class LocalReader:
    """The real reader: the local inspection (no network). Replaceable in tests."""

    def __init__(self, tesseract_cmd: str | None = None, languages: str = "eng"):
        self.tesseract_cmd, self.languages = tesseract_cmd, languages

    def read(self, path: str, data: bytes) -> FileRead:
        from app.integrations.ocr.inspection import InspectionError, inspect_document
        if not path.lower().endswith(SUPPORTED):
            return FileRead(error="Only PDF, Word and image files can be read.")
        if not data:
            return FileRead(error="The file is empty.")
        try:
            inspected = inspect_document(data, tesseract_cmd=self.tesseract_cmd, languages=self.languages, max_pages=MAX_PAGES,
                                         page_timeout=PAGE_TIMEOUT, document_timeout=DOCUMENT_TIMEOUT)
        except InspectionError as exc:
            return FileRead(error=str(exc) or "The file could not be read.")
        except Exception:
            return FileRead(error="The file could not be opened. It may be damaged.")
        # A page's own confidence is capped by what the page classifier made of it (0 for "unknown"): the OCR's own figure is the one
        # that says how well the print was read.
        detail = {row.get("page"): row.get("confidence") for row in (inspected.completeness or {}).get("pages_detail", [])}
        return FileRead([PageRead(p.page_number, p.status, p.text or "",
                                  float(detail.get(p.page_number) if detail.get(p.page_number) is not None else p.confidence or 0.0), p.method,
                                  p.document_type, p.reason or "") for p in inspected.pages], inspected.page_count)

    def reread(self, path: str, data: bytes, numbers: list[int], deadline: float) -> dict[int, PageRead]:
        """These pages again with the full ladder (rotations, 300 dpi, second engine). Never raises."""
        import pymupdf as fitz
        from app.integrations.ocr.inspection import inspect_document
        if not numbers:
            return {}
        try:
            from app.integrations.ocr.inspection import _normalize
            pdf, _ = _normalize(data, time.monotonic() + 60)
            with fitz.open(stream=pdf, filetype="pdf") as source, fitz.open() as subset:
                for number in numbers:
                    subset.insert_pdf(source, from_page=number - 1, to_page=number - 1)
                piece = subset.tobytes(deflate=True)
            budget = max(10.0, min(deadline - time.monotonic(), DEEP_FILE_BUDGET))
            inspected = inspect_document(piece, tesseract_cmd=self.tesseract_cmd, languages=self.languages, max_pages=len(numbers),
                                         page_timeout=DEEP_PAGE_TIMEOUT, document_timeout=budget, need_more=lambda text, index: True,
                                         two_pass=False)
        except Exception:
            return {}
        detail = {row.get("page"): row.get("confidence") for row in (inspected.completeness or {}).get("pages_detail", [])}
        return {numbers[p.page_number - 1]: PageRead(
            numbers[p.page_number - 1], p.status, p.text or "",
            float(detail.get(p.page_number) if detail.get(p.page_number) is not None else p.confidence or 0.0), p.method, p.document_type,
            p.reason or "") for p in inspected.pages}


# --------------------------------------------------------------------------------------------- names

_TITLES = {"mr", "mrs", "ms", "miss", "shri", "smt", "sri", "shree", "dr", "mister"}
_STOP = {"s", "o", "so", "do", "wo", "co", "son", "daughter", "wife", "of", "holder", "aged", "age", "resident", "passport", "no", "dob",
         "date", "birth", "born", "bearing", "having", "has", "is", "was", "residing", "male", "female", "indian", "gender", "sex",
         "nationality", "kuwait", "dear", "sir", "madam", "the", "and", "to", "from", "subject", "with", "for", "at", "in", "on", "pax",
         "adult", "child", "infant", "seat", "baggage", "meals", "mobile", "phone", "contact", "address", "father", "mother", "who", "which",
         "that", "whose", "whom", "while", "where", "when", "male", "female", "fit", "unfit", "applied", "working"}
_NOT_NAME = {"government", "india", "unique", "identification", "authority", "aadhaar", "republic", "passport", "visa", "medical",
             "certificate", "clearance", "police", "hospital", "centre", "center", "limited", "ltd", "company", "trading", "general",
             "department", "ministry", "office", "resume", "curriculum", "vitae", "objective", "declaration", "offer", "employment",
             "position", "salary", "contract", "ticket", "booking", "flight", "airways", "airlines", "passenger", "passengers",
             "details", "total", "amount", "number", "name", "page", "education", "experience", "skills", "profile", "personal",
             "diagnostic", "laboratory", "examination", "report", "candidate", "applicant", "employer", "kingdom", "state", "electronic",
             "university", "board", "school", "college", "institute", "regional", "bank", "account", "invoice", "receipt", "insurance",
             "policy", "science", "computer", "commerce", "engineering", "bachelor", "diploma", "degree", "address", "sincerely", "regards", "signature", "authorised", "signatory", "manager", "director", "photo", "photograph"}
_VOWEL = re.compile(r"[aeiouy]")
# Words of a resume's section headings ("Educational Qualification", "Work Experience", "Personal Details", "Declaration"): never a name.
_HEADING_WORDS = frozenset({
    "educational", "qualification", "qualifications", "languages", "known", "objective", "career", "skills", "skill", "experience",
    "professional", "summary", "work", "history", "hobbies", "interests", "strengths", "achievements", "training", "declaration",
    "references", "reference", "particulars", "academic", "technical", "key", "projects", "certifications", "details", "information",
    "data", "profile", "employment", "duties", "responsibilities", "site", "equipment", "machinery", "handled", "areas", "expertise",
    "core", "competencies", "software", "safety", "about", "me", "visa", "status", "page", "of", "and", "the", "my", "in", "for", "bio",
    "biodata", "cv", "marital", "gender", "religion", "nationality", "father", "mother", "sex", "dob", "birth", "date", "name"})
_RELATION = re.compile(r"\b(?:s\s*/\s*o|d\s*/\s*o|w\s*/\s*o|c\s*/\s*o|son\s+of|daughter\s+of|wife\s+of)\b", re.I)


# --------------------------------------------------------------------------------------------- is it a person's name at all?
# English-only OCR reads Tamil / Hindi script as Latin letters ("Aaharapef dio tdliadledf"). Such a line passes every "is it letters"
# test, so a name is also judged on how it is built: real names are made of the letter triples that real names are made of. The list
# below is only the source of those triples (about 1400 common Indian first names and surnames of every region and community, plus
# the usual Christian and Muslim names); it is never matched as a whole, so any name made of the same sounds fits.
_NAME_LEXICON = """
aadhavan aadhi aarav aarthi aarti aaryan abbas abdul abdulaziz abdulhameed abdulkader abdulkalam abdullah
abdulrahman abdulsalam abhijit abhilash abhimanyu abhinav abhishek abubakar adarsh adhikari aditya agarwal
ahamed ahmad ahmed aiyappan aiyar ajay ajinkya ajith akash akhil akhila akila akshata akshay alagappan alagu
alamelu albert alfred ali amala amalraj amarnath ambika ameer ameerbasha ameerjan amit amjad amol amrit amruta
amudha anagha anand anandakumar anandan ananth ananya anbarasi anbazhagan anbu andal andrew anees aneesh aniket
anil aniruddh aniruddha anirudh anish anitha anjali anju ankit ankur annamalai annapoorani anoop anthony anto
antony antonysamy anupam anuradha anusha anushka anwar aravind aravindan archana arif arivalagan arivazhagan
arivu arjun arman arockia arockiam arokia arokiaswamy arora arshad arthur arul arulmozhi arulraj arumugam arun
arunkumar asad ashok ashokkumar ashraf ashwin ashwini asif aslam aswin athira athul atul avinash ayyadurai
ayyanar ayyappan ayyasamy ayyavu azhagan azhagarasan azhagu azhar aziz babu bahadur bajwa bala balaji
balakrishnan balamurugan balasubramanian banerjee bansal basavaiah basavaraj basha basheer bashir basil baskar
benjamin bhagwan bhagwat bhagyashri bhandari bharat bharath bharathi bharathy bhaskar bhat bhatia bhattacharya
bhavani bhavesh bhavya bhupendra bhuvaneshwari bijoy biju bilal binu bipin boopathi bose brar brindha
buvaneswari chaitanya chandra chandrakala chandrakumar chandran chandrasekar chandrasekaran charles chatterjee
chauhan chellamuthu chellappa cheran chetan chettiar chezhian chidambaram chinna chinnaiah chinnasamy chitra
chitrakala chokkalingam cholan chopra chowdhury christopher clement clinton cyril daniel danish darshan das
dasan david dayanand dcruz deepa deepak deepalakshmi deepika deepu desai deshmukh devansh devaraj devendra
devendran devi dhanalakshmi dhanapal dhanraj dhanush dharani dharmalingam dharmaraj dharmendra dhillon
dhinakaran dhruv dhruva dhruvan dickson digvijay dileep dilip dinakaran dinesh divya dominic donald dsouza dubey
durai duraisamy durga dutta easwaran easwari ebenezer edward edwin ejaz elango elangovan elizabeth elumalai
eswaramoorthy eswaran eswarasamy eswari ezhil ezhilan ezhilarasan ezhilvanan fahad fahim faizal fakhar
fakhruddin fakrudeen fareeda farhaan farhan farid farooq fathima fatima fayaz fazal felix fernandes
feroz feroze firoz francis gabriel gagan ganapathy gandhi ganesan ganesh gangadharan gaurang gaurav gautam
gautham gayathri geeta geetha geethanjali geo george gerald ghosh gill girdhar girija girish gokul
gokulakrishnan gomes gopal gopalakrishna gopalan gopalaswamy gopalsamy gopi gopikrishnan gopinath
gounder govardhan govindan govindaraj govindarajan gowda gowdru goyal gregory grewal gunavelan gupta gurmeet
gurpreet guru gurumoorthy gurunathan gurung gurusamy habib hakeem hameed hanif hanumantha hanumanthu hardeep
hardik hari haridas harihara hariharan harikrishnan harinath hariprasad harish harold harpreet harsh harshad
harshal harshvardhan hassan hegde hemalatha hemant hemanth hemavathi henry hiten hitesh hrishikesh hussain
ibrahim ilaiyaraaja ilamparithi ilamurugan ilanchezhian ilango ilavarasan ilavazhagan ilayaraja imran inbaraj
indhu indira indrajit indumathi iqbal irfan irudayaraj isaac islam ismail iyengar iyer jabbar jabir jackson
jacob jadhav jafar jagadish jagan jaganathan jagdish jagmohan jain jaleel jamal jamaluddin jamaludeen jamaludin
james janaki janakiraman jaspal jaswant javed jawahar jayachandran jayakumar jayanthi jayaprakash jayaraj
jayaraman jebakumar jebaraj jeevan jegan jerald jerome jesu jesudoss jeyakumar jeyalakshmi jeyaraj jeyaseelan
jeyasingh jibin jignesh jitendra jithin joby john johnson jose joseph joshi joshua jothi jothimani jude junaid
jyothi jyoti jyotsna kabir kader kadhar kailasam kailash kaimal kalaimani kalaiselvan kalaivani kalaiyarasan
kaleeswaran kalidas kalimuthu kalpana kalyanasundaram kalyani kamakshi kamal kamalakannan kamaluddin
kamatchi kamath kandasamy kanimozhi kannadasan kannan kapil kapoor karan kareem karki karpagam karthi
karthick karthiga karthik karthikeyan kartik karunakaran karuppaiah karuppan karuppiah karuppusamy kasi kasthuri
kathiravan kaustubh kaviarasan kavichelvan kavimani kavin kavinesh kavitha kaviya kavya kayalvizhi kedar kesavan
keshav ketaki ketan keyur khadar khalid khan khanna kingsley kiran kirubakaran kishan kishore kishorkumar
kodeeswaran kohli kokila kokilavani kottaiyan kowsalya kowshik krishna krishnakumar krishnamoorthy krishnan
krishnaswamy kulandai kulkarni kulothungan kumar kumaran kumarasamy kumaravel kumaresan kumudha kunal kunjumon
kuppusamy kurian kurup kuruvilla kuzhandai kuzhandaivel lakshman lakshmanan lakshmi lalit lalitha lama latha
latif lavanya lavesh lawrence leela lenin leo lijo limbu lingaiah lingam lobo loganathan logesh logeshwaran
lokanathan lokesh louis lourdes lourdusamy madan madhan madhavan madhavi madhu madhusudhan magar magesh mahadev
mahadevan mahadevi mahalakshmi mahalingam mahavir mahendra mahendrakumar mahendran mahesh maheshwari mahimai
mahimaidoss mahizhan majid malar malathi malhotra malini mallesh mallikarjun mani manickam manickavasagam
manikam manikandan manimaran manimegalai manimozhi maniraj manivannan manivel manjula manjunath manmohan
manoharan manoj mansoor mansur manu maria mariadoss mariappan marimuthu mariyappan martin mascarenhas mathan mathew
mathivanan maya mayank mayur meenakshi meenal meenatchi meera meeran meerasa mehboob mehta menon michael
michaelraj midhun milind mishra mitesh mittal mohamed mohan mohanasundaram mohanlal mohanraj mohideen mohiuddin
moin moinuddin moorthy more mozhi mubarak mudaliar mugunthan muhammad mujeeb mukesh mukherjee mukhtar mukund
mumtaz munir murali murtaza murthy murugan muruganantham murugappan mustafa muthu muthukumar muthuraman muthusamy muthuvel
muzammil mythili nachiket nadar nadeem naeem nafees nagalakshmi nagappan nagaraj nagarajan nagendran nageswara
nageswararao naicker naidu naik nair najeeb nalini nambiar namboothiri nambudiri nandakumar nandan nandhini
nandini narasimha narasimhulu narayanan narayanasamy narendra naresh naseer nasir nasreen nasrin nataraj
natarajan naveen naveenkumar navin nawaz nawazkhan nazeer nazir nazneen nazrul nedunchezhian neduncheziyan neelakandan
neelam nelson nicholas nidhin nikhil nilesh nirmala nishanth nithesh nithin nithya nitin nitish nivetha nizam
nizamuddin noor noorjahan nooruddin nurul nusrat oliver om omkar omprakash oommen oswald padma padmanabhan
padmavathi palani palaniappan palanisamy palanivel pandey pandi pandian pandiyan panicker pankaj paramasivam
parameshwaran paresh parth parthasarathy parthiban parvathi parvez patel patil patrick paul pavithra pawar
pazhani pazhaniswamy pereira periyasamy peter philip pillai pius piyush ponmudi ponnambalam ponni ponnusamy
ponraj ponvizhi pooja poojary poongodi poorani poornima poovarasan poudel prabha prabhakaran prabhu pradeep
prakash prakashchand pranav pranjal prasad prasada prasanna prasanth prashant prashanth pratap prathamesh pratik
praveen pravin preethi prem premchand premkumar prince pritam pritesh priya priyanka pugazhendhi punitha
purushothaman purvi pushpa pushpalatha pushpavalli puvi qadir rabia radha rafeeq rafi rafiq rafiuddin raheem
rahim rahman rahmat rahul rajagopal rajagopalan rajalakshmi rajammah rajammal rajan rajappa rajaram rajaraman
rajasekar rajasimman rajasingam rajasingh rajasundaram rajathi rajavel rajeev rajendiran rajendra rajendran
rajesh rajeshwari rajeswaran rajeswari rajini rajinikanth rajkumar rajput raju rakesh rakshit ramachandran
ramakrishna ramakrishnan ramalingam raman ramana ramanathan ramasamy ramaswamy ramchandra ramdas ramesh
ramkishan ramkumar ramnath rampal ramprasad ramsingh ramu ramya ramzan ranjan ranjani ranjith rao raphael
rasheed rashid rashida rashidkhan rathinam rathna rathore ravi ravichandran ravikumar ravindran raza razia reddy rehman
rekha renjith renuka reshma revathi reynold riaz richard rijo rishabh riyaz rizwan robert rocky rodrigues rohan
rohini rohit rohith ronald roshan roshni rosy roy rubina rubini rukmani ruksana rutuja saba sabari sabiha sabir
sabu sachin sadasivam sadiq sadiya saeed sagar sahayaraj saif saira sajan saji sajjad sakina sakthi
sakthivel salahuddin saleem salim salma salman sameer samina samir samuel sana sandeep sandhu sangeetha sanjana
sanjay sanjeev sanjiv sanju sankar sankaralingam sankaran sankaranarayanan sankarapandian sanket santhanam
santhosh saranya sarasvathi saraswathi sarath sarathkumar saravana saravanan sarfaraz sarkar saroja sarojini
sarvesh sasikala sasikumar satheesh sathish sathishkumar sathiyamoorthy sathiyan sathiyanathan sathiyaseelan
sathya sathyabama sathyamoorthy sathyanarayanan sathyaseelan sathyavan satish satyajit satyanarayana saurabh
savarimuthu sebastian sekar selvadurai selvakumar selvam selvaraj selvaraju selvarani selvi sen sengottuvelan
senthil senthilkumar sethi shabana shabbir shabnam shafi shagufta shah shahid shahin shahnawaz shahzad shaik
shaikh shaikhsalim shaista shaji shakeel shakir shamim shamshad shamsher shamsuddin shamsudeen shankar
shankaraiah shankarlal shanmugam shanmuganathan shanmugapriya shanmugasundaram shanmugavadivu shanmugavel
shantanu shanthi sharif sharma sharmila shaukat sheela shehnaz shekhawat sherpa shetty shibu shinde shiva
shivaji shivakumar shivani shivanna shivram shlok shoaib shobana shreya shreyansh shreyas shubham shweta shyam
shyamlal siddappa siddharth siddhesh siddiqui sidhu sijin simon simran singh siva sivagamasundari sivagami
sivaji sivakami sivakumar sivaraman sivasankar sivasankari sohail sohan sonal sornalatha soundar soundararajan
soundarrajan soundarya sowmya sreejith sreekumar sreenath sreenivasan sridhar srikanth srinivas srinivasan srinu
sriram srividya stanislaus stephen subash subbarao subramani subramaniam subramanian sudarshan sudesh sudha
sudhakar sudhir sufiyan sugumar suhail sujith sukumar sulaiman suleman sultan sumathi sumesh sumit sumitha
sundar sundaram sundarrajan sunil sunilkumar sunny suraj surendran suresh suriya suriyakumar suryanarayana
sushma swaminathan swapnil swaraj swaroop swathi swati syed tabassum tahir talha tamang tamilarasan tamilarasi
tamilmani tamilselvan tamizh tamizhselvan tandon tanmay tanveer tanvi tariq tarun tasleem tauseef tejas
tejaswini thakur thamizhselvi thampi thangadurai thangam thangaraj thangavel thankachan thapa tharani tharun
thenmozhi thevar thiagarajan thiru thirugnanam thirumal thirumalai thirumalaikumar thirumalairaj thirumalaisamy
thirumurthy thirumurugan thirunavukarasu thirunavukkarasu thirupathi thiruppathi thiruselvam thiruvengadam
thiyagarajan thomas thulasi tirumalai tirupathi tirupati tiwari tulasi tushar uday udhaya udhayakumar uma
umamaheswari umang umapathy umar umesh unni unnikrishnan unnimaya unnithan upendra usha usman uthayan uttam
vadivel vaibhav vaidyanathan vairamuthu vaishnavi vaitheeswaran vaithiyalingam vaiyapuri vallavan vallinayagam
vanitha vanmathi varadharajan varalakshmi varghese varier varshan varshini varun vasanth vasantha vasu vasudevan
vasuki vedant vedavalli vedhachalam veera veeramani veeran veeraraghavan veerasamy velan velavan velayudham
velayutham vellaiyan vellore velmani velmurugan velu veluchamy velusamy vendhan vengadesan vengatesh venkat
venkataiah venkataraman venkatesan venkatesh venkateshwaran venkatraman vennila venugopal verma vetri vetrivel
vidya vignesh vigneshwaran vihaan vijay vijayakumar vijayalakshmi vijayan vikas vikram vimal vinayagam vinayak
vincent vineeth vinod vinoth vinothkumar vinu vipin virendra vishal vishnu vishnuprasad vishwanathan viswanathan
vivek vizhi waheed wahid wajid warrier waseem wasim william wilson winston xavier xavierraj yadav yakub yamuna
yaseen yash yasin yasmin yogendra yogesh yogeshwaran yousuf yunus yusuf yuvaraj yuvraj zafar zaheer zahid zahir
zainab zakir zarina zeenath zeeshan zia zubaida zubair zulfiqar
"""


def _grams(word: str) -> list[str]:
    padded = "^" + word + "$"
    return [padded[i:i + 3] for i in range(len(padded) - 2)]


_KNOWN_GRAMS = frozenset(gram for word in _NAME_LEXICON.split() for gram in _grams(word))
NAME_WORD_FIT = 0.7          # a word is "made like a name" when this share of its letter triples occurs in real names
_CONSONANT_RUN = re.compile(r"[b-df-hj-np-tv-xz]{5,}")


def _odd_case(word: str) -> bool:
    """Two or more capitals inside a word that also has lower-case letters ("AaHarapEf"): script read as Latin letters."""
    inner = word[1:]
    return len(word) >= 3 and any(c.islower() for c in inner) and sum(c.isupper() for c in inner) >= 2


def _word_built_like_a_name(word: str) -> bool:
    low = word.lower()
    size = len(low)
    if size > 20 or (size >= 4 and not _VOWEL.search(low)):
        return False
    if _CONSONANT_RUN.search(low) or re.search(r"(.)\1\1", low) or _odd_case(word):
        return False
    if size >= 4 and (max(low.count(c) for c in set(low)) * 2 > size or (size >= 5 and len(set(low)) <= 2)):
        return False                       # mostly one letter
    return True


def plausible_name(text: str, *, lenient: bool = False) -> bool:
    """Is what OCR read here plausibly a person's name? Latin letters in one to five words (initials allowed), no word longer
    than 20 letters, a vowel in every word of four letters or more, no run of five consonants, no letter three times in a row,
    no word that is mostly one letter, no capitals scattered inside a word, and at least half of the words of four letters or more
    made like real names (see `_NAME_LEXICON`; half, so that one spelling slip in a two-word name does not sink it). `lenient`
    skips that last test: for a machine-readable passport zone, whose letters carry check digits and are not guessed. OCR junk is
    judged implausible, never repaired."""
    raw = unicodedata.normalize("NFKD", str(text or ""))
    raw = "".join(c for c in raw if not unicodedata.combining(c)).strip()
    if not raw or re.search(r"[^A-Za-z .'’\-]", raw):
        return False                       # digits, symbols or another script
    words = re.findall(r"[A-Za-z]+", raw)
    if not words or len(words) > 5 or max(len(w) for w in words) < 3:
        return False
    if not all(_word_built_like_a_name(w) for w in words):
        return False
    if lenient:
        return True
    long_words = [w.lower() for w in words if len(w) >= 4]
    if not long_words:
        return True
    fits = 0
    for word in long_words:
        grams = _grams(word)
        fits += sum(g in _KNOWN_GRAMS for g in grams) / len(grams) >= NAME_WORD_FIT
    return fits / len(long_words) >= 0.5


def _is_heading(text: str) -> bool:
    """A section heading of a resume or form ("EDUCATIONAL QUALIFICATION", "Work Experience", "Declaration"), judged by the list the
    page classifier uses and by the words such headings are made of."""
    from app.integrations.ocr import classification
    words = [w.lower() for w in re.findall(r"[A-Za-z]+", text)]
    if not words:
        return False
    if " ".join(words) in classification._HEADING_SET:
        return True
    return all(w in _HEADING_WORDS for w in words) or sum(w in _HEADING_WORDS for w in words) >= max(2, len(words) - 1) and len(words) <= 4


def name_tokens(raw: str, *, lenient: bool = False, sink: list | None = None) -> tuple[str, ...] | None:
    """The name written in `raw` as lower-case tokens, or None when it does not look like a person's name.

    Titles and everything after "S/O", a digit, a comma or a stop word are dropped. OCR junk (a word without a vowel, a run of
    consonants, a tripled letter, letters that are not built like a name: see `plausible_name`) makes the whole line unusable: it
    is never repaired by guessing. When `sink` is given, a line that read like a name but was judged implausible is added to it
    (somebody's name is printed there, it just could not be read)."""
    text = unicodedata.normalize("NFKC", raw or "")
    if _is_heading(text):
        return None
    text = _RELATION.split(text)[0]
    text = re.split(r"[,;:()\d@/|\\\[\]{}<>_=+*#&%$!?\"“”]", text)[0]
    words = re.findall(r"[A-Za-z]+", text.replace("'", "").replace("’", ""))
    tokens: list[str] = []
    shown: list[str] = []
    for word in words:
        low = word.lower()
        if not tokens and low in _TITLES:
            continue
        if low in _STOP:
            break
        if low in _NOT_NAME:
            return None
        tokens.append(low)
        shown.append(word)
    if not tokens or len(tokens) > 5:
        return None
    letters = sum(len(t) for t in tokens)
    full = [t for t in tokens if len(t) > 1]
    if letters < 4 or not full or max(len(t) for t in tokens) < 3:
        return None
    for token in full:
        if (len(token) > 3 and not _VOWEL.search(token)) or re.search(r"[b-df-hj-np-tv-xz]{5,}", token) or re.search(r"(.)\1\1", token) or len(token) > 24 and len(tokens) > 1:
            return None
    if not any(len(t) >= 4 and _VOWEL.search(t) for t in full):
        return None                       # initials only ("P K"), or junk: at least one real name word is needed
    if len(tokens) == 1 and len(tokens[0]) > 30:
        return None
    if not plausible_name(" ".join(shown), lenient=lenient):
        if sink is not None:
            sink.append(" ".join(tokens))
        return None
    return tuple(tokens)


def usable(tokens: tuple[str, ...]) -> bool:
    """A name that can tell two people apart: two name parts, or one long glued string. A bare first name is not."""
    full = [t for t in tokens if len(t) > 1]
    if len(full) == 1 and len(full[0]) >= 5 and len(tokens) > len(full):
        return True                       # "Gunavelan R": a real name word and an initial
    return len(full) >= 2 or len("".join(full)) >= 12


def _token_match(a: str, b: str) -> bool:
    if a == b:
        return True
    return min(len(a), len(b)) >= 6 and abs(len(a) - len(b)) <= 1 and SequenceMatcher(None, a, b).ratio() >= 0.88


@lru_cache(maxsize=65536)
def relation(a: tuple[str, ...], b: tuple[str, ...]) -> str:
    """'equal', 'contains' (the shorter name is wholly inside the longer: "Rashid Khan" in "Rashidkhan Nawazkhan Mansur"),
    'weak' (some word in common) or 'none' (no word in common). Spelling slips of one letter and glued words are tolerated."""
    sa, sb = "".join(a), "".join(b)
    if sa == sb:
        return "equal"
    if min(len(sa), len(sb)) >= 8 and SequenceMatcher(None, sa, sb).ratio() >= 0.93:
        return "equal"
    fa, fb = [t for t in a if len(t) > 1], [t for t in b if len(t) > 1]
    if not fa or not fb:
        return "none"
    short, long_ = (fa, fb) if len(fa) <= len(fb) else (fb, fa)
    free = list(long_)
    strength, consumed, index = 0, 0, 0
    while index < len(short):
        token = short[index]
        hit = next((o for o in free if _token_match(token, o)), None)
        if hit is not None:
            free.remove(hit); strength += 1; consumed += 1; index += 1
            continue
        if index + 1 < len(short):
            glued = token + short[index + 1]
            hit = next((o for o in free if _token_match(glued, o)), None)
            if hit is not None:
                free.remove(hit); strength += 2; consumed += 2; index += 2
                continue
        pair = next(((x, y) for x, y in zip(free, free[1:]) if _token_match(token, x + y)), None)
        if pair is not None:
            free.remove(pair[0]); free.remove(pair[1]); strength += 2; consumed += 1; index += 1
            continue
        index += 1
    if consumed == len(short) and strength >= 2:
        return "equal" if not free and len(fa) == len(fb) else "contains"
    if consumed == len(short) and strength == 1 and len(long_) > len(short):
        # "Gunavelan R" inside "Gunavelan Ramakrishnan": the one real word matches and an initial stands for another word
        initials = [t for t in (a if short is fa else b) if len(t) == 1]
        if any(t[0] == i for t in free for i in initials):
            return "contains"
    sm, lg = (sa, sb) if len(sa) <= len(sb) else (sb, sa)
    if len(sm) >= 8 and sm in lg:
        return "contains"
    return "weak" if strength else "none"


def display_name(tokens: Iterable[str]) -> str:
    return " ".join((t.upper() + ".") if len(t) == 1 else t.capitalize() for t in tokens)


# --------------------------------------------------------------------------------------------- evidence of one page

_LABELLED = re.compile(
    r"^[\s\W]{0,6}(?:candidate'?s?\s+name|name\s+of\s+(?:the\s+)?(?:candidate|applicant|employee|holder|passenger|patient|worker)|"
    r"(?:applicant|employee|passenger|patient|worker)'?s?\s+name|full\s+name|passenger\s+name|passenger|name)\s*[:=\-–]\s*(?P<v>[^\n]+)$",
    re.I | re.M)
_TABLE_HEAD = re.compile(r"^\s*name\b(?!\s*[:=\-–])[^\n]*\b(?:gender|sex|nationality|age|dob|passport)\b", re.I)
_SALUTATION = re.compile(r"\bDear\s+(?:(?:Mr|Mrs|Ms|Miss|Shri|Smt|Sri|Dr)\b\.?\s*)?(?P<v>[^\n,:;]{3,60})", re.I)
_TITLED = re.compile(r"\b(?:Mr|Mrs|Ms|Miss|Shri|Smt|Sri)\b\.?\s*(?P<v>[A-Za-z][A-Za-z .'’-]{2,60})")
_SURNAME = re.compile(r"\bsurname\b[\s:.\-]*(?P<same>[^\n]*)\n?(?P<next>[^\n]*)", re.I)
_GIVEN = re.compile(r"\bgiven\s+name\(?s?\)?\b[\s:.\-]*(?P<same>[^\n]*)\n?(?P<next>[^\n]*)", re.I)
_DOB = re.compile(r"(?:date\s+of\s+birth|d\.?\s?o\.?\s?b\.?|birth\s+date)\s*[:.\-]?\s*(\d{1,2})[/\-. ](\d{1,2})[/\-. ](\d{4})", re.I)
_PASSPORT_LABELLED = re.compile(r"passport\s*(?:no\.?|number|num\.?)?\s*[:.\-]?\s*([A-Z][0-9]{7,8}|[A-Z]{2}[0-9]{7})\b")
_PASSPORT_SHAPE = re.compile(r"(?<![A-Z0-9])([A-Z][0-9]{7})(?![A-Z0-9])")
_PHONE_LINE_SKIP = re.compile(r"father|mother|guardian|emergency|reference|referee|employer|office|toll\s*free|helpdesk|fax|hr\s+manager|spouse|"
                              r"wife|husband|brother|sister", re.I)
_GENERIC_MAIL = re.compile(r"^(?:info|hr|admin|support|helpdesk|noreply|no-reply|contact|careers|sales|enquiry|enquiries|mail|office|recruit\w*)@", re.I)
_RESUME_WORDS = re.compile(r"resume|curriculum|vitae|biodata|objective|profile|contact|address|declaration|career|summary|mobile|email|phone|passport", re.I)

# a name source and how far it is trusted (lower is better)
PRIORITY = {"passport": 1, "aadhaar": 2, "resume": 3, "label": 4, "titled": 5, "salutation": 6}


@dataclass(frozen=True)
class NameHit:
    tokens: tuple[str, ...]
    source: str
    weak: bool = False      # read from a page whose OCR confidence was low: it may link, but never starts a candidate on its own


@dataclass
class Evidence:
    names: list[NameHit] = field(default_factory=list)
    passports: dict[str, bool] = field(default_factory=dict)    # number -> checksum-verified (MRZ check digit)
    aadhaars: set[str] = field(default_factory=set)             # Verhoeff-valid only
    phones: set[str] = field(default_factory=set)
    emails: set[str] = field(default_factory=set)
    dobs: dict[str, str] = field(default_factory=dict)          # iso date -> "mrz" | "label"
    multi: bool = False                                         # names several different people: names are not used to link it
    garbled: int = 0                                            # name-like lines that were judged implausible (OCR junk): never a name

    def empty(self) -> bool:
        return not (self.names or self.passports or self.aadhaars or self.phones or self.emails)


def _norm_passport(value: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", (value or "").upper())


def _mrz_kind(text: str) -> str:
    """'visa' when the zone's first line starts with V (an e-visa), else 'passport'."""
    for raw in text.splitlines():
        flat = re.sub(r"[^A-Za-z0-9<]", "", raw).upper()
        if "<<" in flat and len(flat) >= 28:
            if flat.startswith("V"):
                return "visa"
            if flat.startswith("P"):
                return "passport"
    return "passport"


def _line_names(text: str, pattern: re.Pattern, source: str, sink: list | None = None) -> list[NameHit]:
    hits = []
    for match in pattern.finditer(text):
        tokens = name_tokens(match.group("v"), sink=sink)
        if tokens:
            hits.append(NameHit(tokens, source))
    return hits


def _printed_passport_name(text: str, sink: list | None = None) -> NameHit | None:
    surname = given = None
    for pattern, which in ((_SURNAME, "s"), (_GIVEN, "g")):
        match = pattern.search(text)
        if not match:
            continue
        for chunk in (match.group("same"), match.group("next")):
            letters = re.sub(r"[^A-Za-z ]", "", chunk or "").strip()
            if letters and letters.upper() == letters and name_tokens(letters, sink=sink):
                if which == "s":
                    surname = name_tokens(letters)
                else:
                    given = name_tokens(letters)
                break
    return NameHit(given + surname, "passport") if given and surname else None


def _aadhaar_names(text: str, sink: list | None = None) -> list[NameHit]:
    lines = [line.strip() for line in text.splitlines()]
    hits = []
    for i, line in enumerate(lines):
        if re.match(r"^(?:dob|date\s+of\s+birth|year\s+of\s+birth|yob)\b", line, re.I) and i:
            for back in range(i - 1, max(-1, i - 3), -1):
                if lines[back] and not re.match(r"^(?:s\s*/\s*o|d\s*/\s*o|w\s*/\s*o|c\s*/\s*o|address)", lines[back], re.I):
                    tokens = name_tokens(lines[back], sink=sink)
                    if tokens:
                        hits.append(NameHit(tokens, "aadhaar"))
                    break
        if re.fullmatch(r"to:?", line, re.I) and i + 1 < len(lines):
            tokens = name_tokens(lines[i + 1], sink=sink)
            if tokens:
                hits.append(NameHit(tokens, "aadhaar"))
    return hits


def _resume_name(text: str, sink: list | None = None) -> NameHit | None:
    seen = 0
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        seen += 1
        if seen > 6 or _is_heading(line):
            break                           # the name stands above the first section heading of the resume
        if ":" in line or _RESUME_WORDS.search(line) or _RELATION.search(line[:3] + " " + line[3:]) and line.lower().startswith(("s/", "d/", "w/")):
            continue
        tokens = name_tokens(line, sink=sink)
        if tokens and len(tokens) >= 2 and len(re.findall(r"[A-Za-z]+", line)) <= 5:
            return NameHit(tokens, "resume")
    return None


def _aadhaar_numbers(text: str) -> list:
    """Verhoeff-valid Aadhaar numbers on a page. The first twelve digits of a 16-digit Virtual ID (printed next to the number,
    "VID : 7945 2669 8572 4917") pass the check by chance one time in ten and are not the Aadhaar number: they are skipped."""
    from app.integrations.ocr import validators
    found: list = []
    try:
        for match in validators._AADHAAR_GROUPS.finditer(text):
            if sum(c.isdigit() for g in match.groups() for c in g) < 8:
                continue
            if re.match(r"[ \t-]?[0-9OoIlBSZG]{4}(?![A-Za-z0-9])", text[match.end():match.end() + 6]):
                continue                                           # a fourth group: a 16-digit VID
            if re.search(r"v[il1j][dbo0]\W{0,3}$", text[max(0, match.start() - 8):match.start()], re.I):
                continue
            outcome = validators.check_aadhaar("".join(match.groups()))
            if outcome and outcome.valid and outcome.number not in {n.number for n in found}:
                found.append(outcome)
    except Exception:
        return []
    return found


def extract(text: str, doc_type: str = "unknown") -> Evidence:
    """Everything on one page that says who it is about. Only checksum-backed numbers are kept as numbers: a passport number
    from the machine-readable zone (with its own check digit) or printed next to the word "passport"; an Aadhaar number only
    when it passes the Verhoeff check."""
    from app.integrations.ocr import validators
    ev = Evidence()
    text = unicodedata.normalize("NFKC", text or "")
    if not text.strip():
        return ev
    names: list[NameHit] = []
    garbled: list[str] = []
    mrz = None
    try:
        mrz = validators.find_mrz(text)
    except Exception:
        mrz = None
    if mrz is not None and (mrz.valid or sum(bool(v) for v in mrz.checks.values()) >= 3):
        # a zone with correct check digits is not a guess: its letters are not judged by how they sound
        given = name_tokens(re.sub(r"[^A-Za-z ]", " ", mrz.given_names or ""), lenient=True)
        surname = name_tokens(re.sub(r"[^A-Za-z ]", " ", mrz.surname or ""), lenient=True)
        full = (given or ()) + (surname or ())
        if full and (given or surname):
            names.append(NameHit(full, "passport"))
        if _mrz_kind(text) == "passport":
            number = _norm_passport(mrz.document_number)
            if number and mrz.checks.get("number") and re.fullmatch(r"[A-Z0-9]{6,9}", number) and sum(c.isdigit() for c in number) >= 4:
                ev.passports[number] = True
        if mrz.birth_date and mrz.checks.get("birth"):
            ev.dobs[mrz.birth_date.isoformat()] = "mrz"
    printed = _printed_passport_name(text, garbled) if not names else None
    if printed:
        names.append(printed)
    candidate_text = text
    for match in _PASSPORT_LABELLED.finditer(text):
        before = text[max(0, match.start() - 8):match.start()].lower()
        if "old" in before or "new" in before:
            continue
        ev.passports.setdefault(_norm_passport(match.group(1)), False)
    if re.search(r"passport", text, re.I):
        for match in _PASSPORT_SHAPE.finditer(text):
            before = text[max(0, match.start() - 30):match.start()].lower()
            if "old passport" in before:
                continue
            ev.passports.setdefault(_norm_passport(match.group(1)), False)
    found = _aadhaar_numbers(text)
    phone_like = {re.sub(r"\D", "", m.group()) for m in re.finditer(r"(?:\+|00)?91[\s-]?[6-9]\d{4}[\s-]?\d{5}", text)}
    for number in found:
        if number.number not in phone_like:
            ev.aadhaars.add(number.number)
    if ev.aadhaars or doc_type == "aadhaar":
        names += _aadhaar_names(text, garbled)
    if doc_type == "resume":
        heading = _resume_name(text, garbled)
        if heading:
            names.append(heading)
    names += _line_names(candidate_text, _LABELLED, "label", garbled)
    for i, line in enumerate(text.splitlines()):
        if _TABLE_HEAD.match(line):
            rest = text.splitlines()[i + 1:i + 3]
            for nxt in rest:
                tokens = name_tokens(nxt, sink=garbled) if nxt.strip() else None
                if tokens:
                    names.append(NameHit(tokens, "label"))
                    break
    names += _line_names(text, _SALUTATION, "salutation", garbled)
    names += _line_names(text, _TITLED, "titled", garbled)
    seen: set[tuple] = set()
    for hit in names:
        if (hit.tokens, hit.source) not in seen:
            seen.add((hit.tokens, hit.source))
            ev.names.append(hit)
    ev.garbled = len(garbled)
    strong = [h for h in ev.names if usable(h.tokens)]
    ev.multi = any(relation(a.tokens, b.tokens) == "none" for i, a in enumerate(strong) for b in strong[i + 1:])
    for raw in text.splitlines():
        if _PHONE_LINE_SKIP.search(raw):
            continue
        for number in validators.find_mobiles(raw):
            ev.phones.add(number)
    for mail in validators.find_emails(text):
        if not _GENERIC_MAIL.match(mail):
            ev.emails.add(mail)
    for match in _DOB.finditer(text):
        day, month, year = (int(g) for g in match.groups())
        if 1 <= day <= 31 and 1 <= month <= 12 and 1900 <= year <= 2100:
            ev.dobs.setdefault(f"{year:04d}-{month:02d}-{day:02d}", "label")
    return ev


# --------------------------------------------------------------------------------------------- the result

@dataclass
class FileOut:
    path: str
    pages: list[int] | None            # None: the whole file
    carried: list[int] = field(default_factory=list)       # pages with no evidence of their own that travel with the file
    adjacent: list[int] = field(default_factory=list)      # pages placed only because they sit between two of this person's
    low_confidence: list[int] = field(default_factory=list)
    unreadable: list[int] = field(default_factory=list)


@dataclass
class PeopleResult:
    people: list[dict]
    unplaced: list[dict]
    unreadable: list[dict]
    files: list[dict]
    totals: dict
    stopped: bool = False

    def to_dict(self) -> dict:
        # keys starting with "_" are private (full identifiers for the server's own look-ups): never part of an answer
        return {"people": [{key: value for key, value in person.items() if not key.startswith("_")} for person in self.people],
                "unplaced": self.unplaced, "unreadable": self.unreadable, "files": self.files, "totals": self.totals, "stopped": self.stopped}


def _hamming1(a: str, b: str) -> bool:
    return len(a) == len(b) and sum(x != y for x, y in zip(a, b)) == 1


# --------------------------------------------------------------------------------------------- clustering

class _Cluster:
    __slots__ = ("nodes", "names", "passports", "aadhaars", "phones", "emails", "dobs", "linked_by", "ambiguous", "probable")

    def __init__(self):
        self.nodes: set[tuple[str, int]] = set()
        self.names: list[NameHit] = []
        self.passports: dict[str, bool] = {}
        self.aadhaars: set[str] = set()
        self.phones: set[str] = set()
        self.emails: set[str] = set()
        self.dobs: dict[str, str] = {}
        self.linked_by: set[str] = set()
        self.ambiguous = False
        self.probable: list[tuple[str, str]] = []

    def absorb(self, other: "_Cluster") -> None:
        self.nodes |= other.nodes
        self.names += other.names
        for number, verified in other.passports.items():
            self.passports[number] = self.passports.get(number, False) or verified
        self.aadhaars |= other.aadhaars
        self.phones |= other.phones
        self.emails |= other.emails
        for day, source in other.dobs.items():
            self.dobs.setdefault(day, source)
        self.linked_by |= other.linked_by
        self.probable += other.probable

    def usable_names(self) -> list[tuple[str, ...]]:
        return sorted({h.tokens for h in self.names if usable(h.tokens)})

    @property
    def first(self) -> tuple[str, int]:
        return min(self.nodes)


class _Clusters:
    def __init__(self, evidence: dict[tuple[str, int], Evidence]):
        self.evidence = evidence
        self.parent: dict[tuple[str, int], tuple[str, int]] = {}
        self.cluster: dict[tuple[str, int], _Cluster] = {}
        self.blocked: list[tuple[tuple[str, int], tuple[str, int]]] = []
        # placed by the name in the file name, or by a bare first name (see `_place_by_names`); and the clusters that a file name
        # pointed at one person but whose identity number said otherwise: (person's first node, nodes of the other cluster)
        self.by_filename: set[tuple[str, int]] = set()
        self.by_firstname: set[tuple[str, int]] = set()
        self.mismatch: list[tuple[tuple[str, int], list[tuple[str, int]]]] = []
        self.folder_person: tuple[str, int] | None = None
        for node in sorted(evidence):
            ev = evidence[node]
            c = _Cluster()
            c.nodes.add(node)
            if not ev.multi:
                c.names = list(ev.names)
            else:
                c.names = []
            c.passports = dict(ev.passports)
            c.aadhaars = set(ev.aadhaars)
            c.phones = set(ev.phones)
            c.emails = set(ev.emails)
            c.dobs = dict(ev.dobs)
            self.parent[node] = node
            self.cluster[node] = c

    def find(self, node):
        while self.parent[node] != node:
            self.parent[node] = self.parent[self.parent[node]]
            node = self.parent[node]
        return node

    def of(self, node) -> _Cluster:
        return self.cluster[self.find(node)]

    def merge(self, a, b, via: str = "") -> _Cluster:
        ra, rb = self.find(a), self.find(b)
        if ra == rb:
            return self.cluster[ra]
        keep, drop = (ra, rb) if self.cluster[ra].first <= self.cluster[rb].first else (rb, ra)
        self.cluster[keep].absorb(self.cluster.pop(drop))
        self.parent[drop] = keep
        if via:
            self.cluster[keep].linked_by.add(via)
        return self.cluster[keep]

    def live(self) -> list[_Cluster]:
        return sorted(self.cluster.values(), key=lambda c: c.first)

    # -- compatibility of two clusters --------------------------------------------------------------
    def veto(self, x: _Cluster, y: _Cluster, allow_probable: bool = False) -> str | None:
        """Why two clusters must not be joined: 'ids' (a different passport or Aadhaar number on each side), 'names' (no word in
        common). 'probable' is returned instead of 'ids' for passport numbers one character apart (the caller flags it)."""
        if x.aadhaars and y.aadhaars and x.aadhaars.isdisjoint(y.aadhaars):
            return "ids"
        if x.passports and y.passports and set(x.passports).isdisjoint(y.passports):
            one_apart = all(any(_hamming1(a, b) for b in y.passports) for a in x.passports) and \
                all(any(_hamming1(a, b) for a in x.passports) for b in y.passports)
            if not (allow_probable and one_apart and self._same_dob(x, y)):
                return "ids"
            return "probable"
        nx, ny = x.usable_names(), y.usable_names()
        if nx and ny and all(relation(a, b) == "none" for a in nx for b in ny):
            return "names"
        return None

    @staticmethod
    def _same_dob(x: _Cluster, y: _Cluster) -> bool:
        return not (x.dobs and y.dobs and set(x.dobs).isdisjoint(y.dobs))


def _cluster_pages(evidence: dict[tuple[str, int], Evidence]) -> _Clusters:
    cl = _Clusters(evidence)
    nodes = sorted(evidence)

    # 1. the same passport number on two pages (a number seen on a single page links nothing)
    by_passport: dict[str, list] = {}
    for node in nodes:
        for number in evidence[node].passports:
            by_passport.setdefault(number, []).append(node)
    for number in sorted(by_passport):
        group = by_passport[number]
        for other in group[1:]:
            cl.merge(group[0], other, "passport")
    # 2. the same (valid) Aadhaar number
    by_aadhaar: dict[str, list] = {}
    for node in nodes:
        for number in evidence[node].aadhaars:
            by_aadhaar.setdefault(number, []).append(node)
    for number in sorted(by_aadhaar):
        group = by_aadhaar[number]
        for other in group[1:]:
            if cl.veto(cl.of(group[0]), cl.of(other)) != "ids" or cl.find(group[0]) == cl.find(other):
                cl.merge(group[0], other, "aadhaar")
    # 3. the same phone or e-mail, unless it belongs to an agency (it sits with several different passports / Aadhaar numbers)
    for kind, attr in (("phone", "phones"), ("email", "emails")):
        by_contact: dict[str, list] = {}
        for node in nodes:
            for value in getattr(evidence[node], attr):
                by_contact.setdefault(value, []).append(node)
        for value in sorted(by_contact):
            group = by_contact[value]
            roots = sorted({cl.find(n) for n in group})
            if len(roots) < 2 and len(group) < 2:
                continue
            ids = [(set(cl.cluster[r].passports), set(cl.cluster[r].aadhaars)) for r in roots]
            distinct_passports = {frozenset(p) for p, _ in ids if p}
            distinct_aadhaars = {frozenset(a) for _, a in ids if a}
            if len(distinct_passports) >= 2 or len(distinct_aadhaars) >= 2:
                continue    # shared by several different people: an agency's number, never a link
            anchor = group[0]
            for other in group[1:]:
                if cl.find(anchor) == cl.find(other):
                    cl.cluster[cl.find(anchor)].linked_by.add(kind)
                    continue
                if cl.veto(cl.of(anchor), cl.of(other)) is None:
                    cl.merge(anchor, other, kind)
    # 4. equal names; then 5. a shorter name wholly inside one (and only one) longer name
    changed = True
    while changed:
        changed = False
        clusters = cl.live()
        for i, x in enumerate(clusters):
            if cl.find(x.first) != x.first and x.first not in cl.cluster:
                continue
            for y in clusters[i + 1:]:
                if x is y or y.first not in cl.cluster or x.first not in cl.cluster:
                    continue
                if not any(relation(a, b) == "equal" for a in x.usable_names() for b in y.usable_names()):
                    continue
                verdict = cl.veto(x, y, allow_probable=True)
                if verdict == "ids":
                    cl.blocked.append((x.first, y.first))
                    continue
                if verdict == "names":
                    continue
                merged = cl.merge(x.first, y.first)
                if verdict == "probable":
                    merged.probable.append(("passport", ""))
                changed = True
                break
            if changed:
                break
    changed = True
    while changed:
        changed = False
        clusters = cl.live()
        for small in sorted(clusters, key=lambda c: (min((len("".join(n)) for n in c.usable_names()), default=0), c.first)):
            if small.first not in cl.cluster or not small.usable_names():
                continue
            hosts = []
            for host in clusters:
                if host is small or host.first not in cl.cluster:
                    continue
                inside = any(relation(a, b) == "contains" and len("".join(a)) < len("".join(b)) for a in small.usable_names() for b in host.usable_names())
                if not inside:
                    continue
                verdict = cl.veto(small, host)
                if verdict == "ids":
                    cl.blocked.append((small.first, host.first))
                    continue
                if verdict is None:
                    hosts.append(host)
            if len(hosts) == 1:
                cl.merge(hosts[0].first, small.first)
                changed = True
                break
            if len(hosts) > 1 and not (small.passports or small.aadhaars):
                small.ambiguous = True
    # 6. a card with a number but no readable name (an Aadhaar whose name the reader missed) joins the ONE named person who has the same
    #    date of birth. Flagged as a warning on that person: it rests on the date of birth alone.
    for small in cl.live():
        if small.first not in cl.cluster or small.usable_names() or not (small.aadhaars or small.passports) or not small.dobs:
            continue
        hosts = [h for h in cl.live() if h is not small and h.usable_names() and set(h.dobs) & set(small.dobs) and cl.veto(small, h) is None]
        if len(hosts) == 1:
            cl.merge(hosts[0].first, small.first, "dob")
    return cl


# --------------------------------------------------------------------------------------------- identify

def _has_strong(c: _Cluster) -> bool:
    return bool(any(usable(h.tokens) and not h.weak for h in c.names) or c.passports or c.aadhaars or (len(c.nodes) >= 2 and (c.phones or c.emails) and c.linked_by & {"phone", "email"}))


def _best_name(c: _Cluster) -> tuple[tuple[str, ...] | None, str]:
    if _aadhaar_only(c):
        return None, ""                   # an identity card whose name was only read doubtfully: never named from that
    hits = [h for h in c.names if usable(h.tokens)]
    if not hits:
        return None, ""
    key = lambda h: (h.weak, PRIORITY.get(h.source, 9), -len([t for t in h.tokens if len(t) > 1]), h.tokens)
    first = sorted(hits, key=key)[0]
    # Names are noisy: among the renderings of the first choice's own name, the spelling most pages agree on wins; the more trusted
    # source (the machine-readable zone before a printed name) only decides between spellings that are equally common.
    group = [h for h in hits if relation(h.tokens, first.tokens) == "equal"]
    votes = {t: sum(1 for h in group if h.tokens == t) for t in {h.tokens for h in group}}
    best = sorted([h for h in group if votes[h.tokens] == max(votes.values())], key=key)[0]
    tokens = list(best.tokens)
    # A letter or two of filler read into the end of a machine-readable name ("NAWAZKHANK"): another rendering without it wins.
    if best.source == "passport":
        others = {t for h in hits if h.source != "passport" for t in h.tokens}
        for i, token in enumerate(tokens):
            if len(token) >= 6 and token[:-1] in others and token not in others:
                tokens[i] = token[:-1]
    return tuple(tokens), best.source


# --------------------------------------------------------------------------------------------- names in file names, first names

def _anchored(c: _Cluster) -> bool:
    """Someone by name or by passport: a trusted plausible name that tells people apart, or a passport number."""
    return bool(c.passports or any(usable(h.tokens) and not h.weak for h in c.names))


def _aadhaar_only(c: _Cluster) -> bool:
    """An identity card (Aadhaar number) and no name that can be trusted: whose it is cannot be said from the card."""
    return bool(c.aadhaars) and not _anchored(c)


def _person_clusters(cl: _Clusters) -> list[_Cluster]:
    """The clusters that are someone. A card with only an Aadhaar number is a person only when nobody in the folder is known by name
    or passport: next to a named person it is a card whose name could not be read, and waits to be placed."""
    live = cl.live()
    anchored = any(_anchored(c) for c in live)
    return [c for c in live if _has_strong(c) and not (c.ambiguous and not (c.passports or c.aadhaars)) and not (anchored and _aadhaar_only(c))]


def file_name_key(path: str) -> tuple[str, ...] | None:
    """The person's name at the start of a file name ("Gunavelan-aadhar.jpeg" -> ("gunavelan",)), by the rules the folder import
    uses for names (it stops at a hyphen or underscore, and skips documents, trades and the names scanners give files); None when
    the name says nobody or is under five letters."""
    from app.services.bulk_candidate_import import _name_in
    try:
        found = _name_in(path.split(_DUP, 1)[0])
    except Exception:
        return None
    if not found:
        return None
    key = tuple(word.lower() for word in found[1])
    return key if len("".join(key)) >= 5 else None


def _spelled_alike(a: str, b: str) -> bool:
    """Two names written as one word each: equal, a slip of a letter, or one is the other with at least three more letters
    ("gunavelan" / "gunavelanramakrishnan"; not "kumar" / "kumaran", who are different people)."""
    if a == b:
        return True
    short, long_ = sorted((a, b), key=len)
    if len(short) < 5:
        return False
    return SequenceMatcher(None, a, b).ratio() >= 0.9 or (long_.startswith(short) and len(long_) - len(short) >= 3)


def _key_matches(key: tuple[str, ...], tokens: tuple[str, ...]) -> bool:
    """Does the name in a file name say this person: all of it, or one whole word of the name (a surname alone), spelled alike."""
    flat = "".join(key)
    full = [t for t in tokens if len(t) > 1]
    if len(flat) < 5 or not full:
        return False
    return _spelled_alike(flat, "".join(full)) or any(len(t) >= 5 and _spelled_alike(flat, t) for t in full)


def _first_word(tokens: tuple[str, ...]) -> str:
    return next((t for t in tokens if len(t) > 1), "")


def _first_name_fits(bare: str, first: str) -> bool:
    """A first name on a page ("Dear Mr. Gunavellan") and a person's first name: equal, or five letters or more with a small slip."""
    if not bare or not first:
        return False
    return bare == first or (min(len(bare), len(first)) >= 5 and SequenceMatcher(None, bare, first).ratio() >= 0.8)


def _bare_names(c: _Cluster) -> list[str]:
    """The first names a cluster's pages print alone (not enough to tell two people apart)."""
    return sorted({_first_word(h.tokens) for h in c.names if not usable(h.tokens) and _first_word(h.tokens)})


def _place_by_names(cl: _Clusters, file_keys: dict[str, tuple[str, ...] | None], evidence: dict[tuple[str, int], Evidence]) -> None:
    """Weaker evidence, used only where the content of the pages could not place them, and never against an identity number.

    1. FILE NAMES. When every file of the folder is named for one and the same person and exactly one person found by content has
       that name, everything that is not in conflict with that person (another identity number, or another name with no word in
       common) joins them. Otherwise a cluster that is not someone by itself (a card whose name could not be read, a letter that only
       has a first name ...) joins the one person that the file names of its files point at; two persons that fit, or none, place nothing.
    2. FIRST NAMES. A cluster whose only name is a first name joins the one person in the folder whose first name it is (or a
       small slip of it); when two fit it stays where it is.
    Joined clusters are remembered in `cl.by_filename` / `cl.by_firstname` so that the result can say how each page was placed."""
    def named() -> list[_Cluster]:
        return [c for c in _person_clusters(cl) if c.usable_names()]

    def matching(key) -> list[_Cluster]:
        return [c for c in named() if any(_key_matches(key, n) for n in c.usable_names())]

    def own_evidence(c: _Cluster) -> bool:
        return not all(evidence[n].empty() for n in c.nodes)

    def file_target(c: _Cluster) -> tuple[_Cluster | None, bool]:
        found: dict[tuple[str, int], _Cluster] = {}
        for path in sorted({n[0] for n in c.nodes}):
            key = file_keys.get(path)
            hits = matching(key) if key else []
            if len(hits) > 1:
                return None, True
            if hits:
                found[hits[0].first] = hits[0]
        return (next(iter(found.values())), False) if len(found) == 1 else (None, False)

    def names_someone_else(c: _Cluster, target: _Cluster) -> bool:
        bare = _bare_names(c)
        if not bare or c.usable_names():
            return False
        words = {t for n in target.usable_names() for t in n if len(t) > 1}
        return not any(b in words or any(_spelled_alike(b, t) for t in words) or any(_first_name_fits(b, _first_word(n)) for n in target.usable_names()) for b in bare)

    keys = list(file_keys.values())
    if keys and all(keys) and all(_spelled_alike("".join(a), "".join(b)) for i, a in enumerate(keys) for b in keys[i + 1:]):
        both = [c for c in named() if all(any(_key_matches(k, n) for n in c.usable_names()) for k in keys)]
        if len(both) == 1:
            cl.folder_person = both[0].first
    for root in [c.first for c in cl.live()]:
        if root not in cl.cluster:
            continue
        c = cl.cluster[root]
        is_person = any(p is c for p in _person_clusters(cl))
        if cl.folder_person is not None:
            target = cl.of(cl.folder_person)
            if c is target or (not is_person and not own_evidence(c)):
                continue                      # a page with nothing on it travels with its file
        else:
            if is_person or not own_evidence(c):
                continue
            target, ambiguous = file_target(c)
            if target is None or ambiguous:
                continue
        if names_someone_else(c, target):
            continue
        verdict = cl.veto(target, c)
        if verdict == "ids":
            cl.mismatch.append((target.first, sorted(c.nodes)))
            continue
        if verdict is not None:
            continue
        cl.by_filename |= c.nodes
        cl.merge(target.first, c.first, "filename")
    for root in [c.first for c in cl.live()]:
        if root not in cl.cluster:
            continue
        c = cl.cluster[root]
        bare = _bare_names(c)
        if not bare or c.usable_names() or any(p is c for p in _person_clusters(cl)):
            continue
        people = named()
        fits = [p for p in people if any(_first_name_fits(b, _first_word(n)) for b in bare for n in p.usable_names())]
        if len(fits) != 1 or not all(any(_first_name_fits(b, _first_word(n)) for n in fits[0].usable_names()) for b in bare):
            continue
        hint, _ = file_target(c)
        if hint is not None and hint is not fits[0]:
            continue                          # the file name points at someone else: not guessed
        if cl.veto(fits[0], c) is not None:
            continue
        cl.by_firstname |= c.nodes
        cl.merge(fits[0].first, c.first, "firstname")


def identify_people(files: list[tuple[str, bytes]], *, reader=None, rejected: list[tuple[str, str]] | None = None,
                    should_stop: Callable[[], bool] | None = None, total_timeout: float | None = 1500.0,
                    workers: int = 2, deep: bool = True) -> PeopleResult:
    """See the module doc. `files` are (path, bytes); `rejected` are (path, reason) files that were not even read (too big ...):
    they are still listed as unreadable. `should_stop()` is polled between files; what was not read is listed as unreadable."""
    from concurrent.futures import ThreadPoolExecutor
    reader = reader or _default_reader()
    started = time.monotonic()
    deadline = started + total_timeout if total_timeout else None
    # Two files with the same path must stay two files (every page accounted for): the second and later ones get a unique
    # internal key (path + NUL + index); the original path is what the result reports.
    keyed: dict[str, tuple[str, bytes]] = {}
    for path, data in files:
        key = path if path not in keyed else f"{path}{_DUP}{len(keyed)}"
        keyed[key] = (path, data)
    ordered = sorted(keyed.items(), key=lambda item: item[0])
    reads: dict[str, FileRead] = {}
    stopped = False

    def read_one(item: tuple[str, tuple[str, bytes]]) -> tuple[str, FileRead]:
        key, (path, data) = item
        if (should_stop and should_stop()) or (deadline and time.monotonic() > deadline):
            return key, FileRead(error="Not read: the reading was stopped or ran out of time. Read the folder again.")
        try:
            result = reader.read(path, data)
        except Exception:
            return key, FileRead(error="The file could not be read.")
        if result.error is None and deep:
            try:
                _read_harder(reader, path, data, result, deadline)
            except Exception:
                logger.debug("deep read failed for one file")
        return key, result

    if workers > 1 and len(ordered) > 1:
        with ThreadPoolExecutor(max_workers=min(workers, len(ordered))) as pool:
            for key, result in pool.map(read_one, ordered):
                reads[key] = result
    else:
        for item in ordered:
            reads[item[0]] = read_one(item)[1]
    stopped = bool(should_stop and should_stop()) or any(
        r.error and r.error.startswith("Not read:") for r in reads.values())
    result = _analyse(reads, rejected or [], stopped)
    if any(_DUP in key for key in reads):
        _report_original_paths(result)
    return result


_DUP = "\x00"


def _report_original_paths(result: PeopleResult) -> None:
    """Strip the internal duplicate marker from every `path` of the result (in place)."""
    def walk(value):
        if isinstance(value, dict):
            for name, item in value.items():
                if name == "path" and isinstance(item, str) and _DUP in item:
                    value[name] = item.split(_DUP, 1)[0]
                else:
                    walk(item)
        elif isinstance(value, list):
            for item in value:
                walk(item)
    for part in (result.people, result.unplaced, result.unreadable, result.files):
        walk(part)


def _default_reader():
    from app.core.config import get_settings
    settings = get_settings()
    return LocalReader(getattr(settings, "tesseract_cmd", None), getattr(settings, "ocr_languages", "eng"))


_KEYWORDS = re.compile(r"passport|visa|medical|offer|ticket|aadhaar|government|certificate|booking|itinerary|resume|curriculum", re.I)


def _read_harder(reader, path: str, data: bytes, read: FileRead, deadline: float | None) -> None:
    """Pages with no identity evidence after the normal read are read again with the full OCR ladder, within a fixed bound."""
    weak = []
    for page in read.pages:
        if page.status == "blank" and page.method in ("photo_no_text", "rendered_blank"):
            continue
        if page.method == "native_text" or not hasattr(reader, "reread"):
            continue
        found = extract(page.text, page.doc_type)
        # nothing at all, or an identity card read badly enough that its name was missed (the number alone cannot say whose it is)
        nameless_card = page.doc_type in ("aadhaar", "passport") and page.confidence < LOW_CONFIDENCE and not any(usable(h.tokens) for h in found.names)
        if found.empty() or nameless_card:
            hint = (page.status == "unread", bool(_KEYWORDS.search(page.text)), page.confidence < LOW_CONFIDENCE)
            weak.append((tuple(not x for x in hint), page.number))
    numbers = [n for _, n in sorted(weak)][:DEEP_MAX_PAGES]
    if not numbers:
        return
    limit = time.monotonic() + DEEP_FILE_BUDGET
    if deadline:
        limit = min(limit, deadline)
    again = reader.reread(path, data, sorted(numbers), limit)
    for number, new in again.items():
        old = read.pages[number - 1]
        if new.status == "unread" and old.status != "unread":
            continue
        after, before = extract(new.text, new.doc_type), extract(old.text, old.doc_type)
        gained = (not after.empty() and before.empty()) or (any(usable(h.tokens) for h in after.names) and not any(usable(h.tokens) for h in before.names))
        if gained or (new.confidence > old.confidence + 0.05 and len(new.text) > len(old.text)) or (old.status == "unread" and new.status == "read"):
            new.method = (new.method or "tesseract") + "+deep"
            read.pages[number - 1] = new


def _analyse(reads: dict[str, FileRead], rejected: list[tuple[str, str]], stopped: bool) -> PeopleResult:
    evidence: dict[tuple[str, int], Evidence] = {}
    pages: dict[tuple[str, int], PageRead] = {}
    file_pages: dict[str, list[int]] = {}
    unreadable_files: dict[str, str] = {path: reason for path, reason in rejected if path not in reads}
    for path, read in sorted(reads.items()):
        if read.error is not None:
            unreadable_files[path] = read.error
            continue
        file_pages[path] = [p.number for p in read.pages]
        for page in read.pages:
            pages[(path, page.number)] = page
    # a checksum-verified passport number also counts where the page prints it without the word "passport" next to it
    texts = {key: page.text for key, page in pages.items() if page.status in ("read", "unread") and page.text}
    for key in sorted(pages):
        page = pages[key]
        if page.status == "read" or (page.status == "unread" and page.text.strip()):
            evidence[key] = extract(page.text, page.doc_type) if page.status == "read" else Evidence()
            if page.confidence < LOW_CONFIDENCE and page.method != "native_text":
                evidence[key].names = [replace(h, weak=True) for h in evidence[key].names]
    verified = sorted({n for ev in evidence.values() for n, ok in ev.passports.items() if ok})
    for key, text in sorted(texts.items()):
        if key not in evidence or pages[key].status != "read":
            continue
        squashed = re.sub(r"[^A-Z0-9]", " ", text.upper())
        for number in verified:
            if re.search(rf"(?<![A-Z0-9]){re.escape(number)}(?![A-Z0-9])", squashed):
                evidence[key].passports.setdefault(number, False)
    cl = _cluster_pages(evidence)
    file_keys = {path: file_name_key(path) for path in sorted(set(file_pages) | set(unreadable_files))}
    _place_by_names(cl, file_keys, evidence)

    # people: clusters with evidence strong enough to be someone
    person_of: dict[tuple[str, int], int] = {}
    people_clusters = _person_clusters(cl)
    for i, c in enumerate(people_clusters):
        for node in c.nodes:
            person_of[node] = i
    people_names = [c.usable_names() for c in people_clusters]

    def person_for_file(path: str) -> int | None:
        """The one person a whole file belongs to by its name (a file with nothing of its own on any page), else None."""
        if cl.folder_person is not None:
            return person_of.get(cl.folder_person)
        key = file_keys.get(path)
        hits = [i for i, names in enumerate(people_names) if key and any(_key_matches(key, n) for n in names)]
        return hits[0] if len(hits) == 1 else None

    # per page placement
    status: dict[tuple[str, int], dict] = {}
    for path in sorted(file_pages):
        numbers = file_pages[path]
        for n in numbers:
            key = (path, n)
            page = pages[key]
            info = {"n": n, "status": "unplaced", "person": None, "via": None, "reason": "", "confidence": round(page.confidence, 3),
                    "method": page.method, "deep": page.method.endswith("+deep"), "low": False, "carried": False, "probable": False}
            if page.status == "unread":
                info.update(status="unreadable", reason=page.reason or "The page could not be read.")
            elif key in person_of:
                via = "filename" if key in cl.by_filename else "firstname" if key in cl.by_firstname else "evidence"
                info.update(status="assigned", person=person_of[key], via=via, probable=via == "firstname")
            else:
                info["reason"] = _why_unplaced(page, evidence.get(key), cl, key)
                info.update(_unplaced_kind(evidence.get(key), cl, key))
            status[key] = info
        # a file with nothing placed by what is on its pages, and no page of its own that says otherwise (a photograph, a stamp, a
        # blank page), belongs to the one person its name says
        placed = {status[(path, n)]["person"] for n in numbers if status[(path, n)]["status"] == "assigned"}
        target = person_for_file(path) if not placed else None
        if target is not None:
            for n in numbers:
                info = status[(path, n)]
                ev = evidence.get((path, n))
                if info["status"] == "unplaced" and (ev is None or ev.empty()):
                    info.update(status="assigned", person=target, via="filename", reason="", kind="")
        # a file's own pages: adjacency inside a mixed file, or the whole file for one person
        placed = {status[(path, n)]["person"] for n in numbers if status[(path, n)]["status"] == "assigned"}
        loose = [n for n in numbers if status[(path, n)]["status"] != "assigned"]
        if len(placed) == 1:
            person = next(iter(placed))
            for n in loose:
                info = status[(path, n)]
                if info["status"] == "unreadable":
                    info["person"] = person          # travels with its file; still reported as unreadable
                    continue
                ev = evidence.get((path, n))
                own = [h.tokens for h in (ev.names if ev else []) if usable(h.tokens)]
                cluster = cl.of((path, n)) if (path, n) in cl.parent else None
                if ev is not None and (ev.aadhaars or ev.passports):
                    continue                         # an identity card of its own that nobody claimed: not carried along by its neighbours
                if cluster is not None and cluster.ambiguous:
                    pass
                elif own and people_names[person] and all(relation(a, b) == "none" for a in own for b in people_names[person]):
                    info["reason"] = "It names a different person than the rest of this file, but not with enough to start a candidate."
                    continue
                info.update(status="assigned", person=person, via="file", carried=True, reason="", kind="")
        elif len(placed) >= 2:
            sequence = [(n, status[(path, n)]) for n in numbers]
            for index, (n, info) in enumerate(sequence):
                if info["status"] != "unplaced":
                    continue
                own_ev = evidence.get((path, n))
                if own_ev is not None and (own_ev.aadhaars or own_ev.passports):
                    continue                         # an identity card nobody claimed: not placed by its neighbours
                before = next((s["person"] for _, s in reversed(sequence[:index]) if s["status"] == "assigned"), None)
                after = next((s["person"] for _, s in sequence[index + 1:] if s["status"] == "assigned"), None)
                if before is not None and before == after:
                    info.update(status="assigned", person=before, via="adjacency", reason="")
        for n in numbers:
            info = status[(path, n)]
            info["low"] = info["confidence"] < LOW_CONFIDENCE and info["method"] != "native_text"
    for path, reason in sorted(unreadable_files.items()):
        status[(path, 1)] = {"n": 1, "status": "unreadable", "person": None, "via": None, "reason": reason, "confidence": 0.0,
                             "method": "", "deep": False, "low": False, "carried": False, "unknown": True}
        file_pages.setdefault(path, [1])
    return _build(people_clusters, people_names, cl, status, file_pages, unreadable_files, pages, evidence, stopped)


def _why_unplaced(page: PageRead, ev: Evidence | None, cl: _Clusters, key) -> str:
    if page.status == "blank":
        return "A blank page." if page.method != "photo_no_text" else "A picture with no text on it."
    if ev is not None and ev.empty() and ev.garbled:
        return "A name is printed on it, but it could not be read."
    if ev is None or ev.empty():
        return "No name, number or contact on this page that says whose it is."
    if ev.multi:
        return "It names more than one person."
    cluster = cl.of(key)
    if _aadhaar_only(cluster):
        return f"This Aadhaar card (ending {tail(sorted(cluster.aadhaars)[0])}) has no readable name."
    if cluster.ambiguous:
        return "The name on it fits more than one person."
    if any(usable(h.tokens) and h.weak for h in ev.names) and not (ev.passports or ev.aadhaars):
        return "The name on it could only be read with low confidence, so it was not trusted to start a candidate."
    if not (ev.passports or ev.aadhaars) and not any(usable(h.tokens) for h in ev.names):
        if ev.names:
            return "Only a first name is on it: not enough to tell who it is."
        return "Only a phone number or e-mail that no other page shares."
    return "Not enough evidence to start a new candidate."


def _unplaced_kind(ev: Evidence | None, cl: _Clusters, key) -> dict:
    """Extra facts about an unplaced page for the screen: an Aadhaar card with no readable name is offered with a choice of person."""
    if ev is None or ev.empty() or ev.multi:
        return {"kind": ""}
    cluster = cl.of(key)
    if _aadhaar_only(cluster):
        return {"kind": "aadhaar_no_name", "ending": tail(sorted(cluster.aadhaars)[0])}
    return {"kind": ""}


def _build(people_clusters, people_names, cl, status, file_pages, unreadable_files, pages, evidence, stopped) -> PeopleResult:
    people_out: list[dict] = []
    # which person each blocked pair belongs to (same name, different numbers)
    person_idx = {c.first: i for i, c in enumerate(people_clusters)}
    blocked: dict[int, set[int]] = {}
    for a, b in cl.blocked:
        pa, pb = person_idx.get(cl.of(a).first), person_idx.get(cl.of(b).first)
        if pa is not None and pb is not None and pa != pb:
            blocked.setdefault(pa, set()).add(pb)
            blocked.setdefault(pb, set()).add(pa)
    per_person: dict[int, dict[str, list[int]]] = {i: {} for i in range(len(people_clusters))}
    for (path, n), info in sorted(status.items()):
        if info["person"] is not None:
            per_person[info["person"]].setdefault(path, []).append(n)
    for i, c in enumerate(people_clusters):
        tokens, source = _best_name(c)
        # Page lists by file
        files_out = []
        for path, numbers in sorted(per_person[i].items()):
            all_pages = file_pages[path]
            attached = sorted(numbers)
            whole = attached == all_pages
            infos = {n: status[(path, n)] for n in attached}
            files_out.append({
                "path": path, "pages": None if whole else attached,
                "carried": [n for n in attached if infos[n]["carried"]],
                "adjacent": [n for n in attached if infos[n]["via"] == "adjacency"],
                "lowConfidence": [n for n in attached if infos[n]["low"] and infos[n]["via"] in ("file", "adjacency")],
                "unreadable": [n for n in attached if infos[n]["status"] == "unreadable"],
                "byFileName": [n for n in attached if infos[n]["via"] == "filename"],
                "probable": [n for n in attached if infos[n]["via"] == "firstname"],
            })
        evidence_kinds = sorted({f"{h.source} name" for h in c.names if usable(h.tokens)} |
                                ({"passport number"} if c.passports else set()) | ({"aadhaar number"} if c.aadhaars else set()) |
                                ({"phone"} if "phone" in c.linked_by else set()) | ({"email"} if "email" in c.linked_by else set()) | ({"date of birth"} if "dob" in c.linked_by else set()) |
                                ({"file name"} if any(f["byFileName"] for f in files_out) else set()) | ({"first name only"} if any(f["probable"] for f in files_out) else set()))
        conflicts = _conflicts(c, per_person[i], status, evidence, blocked.get(i, set()), people_clusters, tokens)
        if not tokens:
            conflicts.append({"kind": "no_name", "message": "No name could be read on this person's documents: check whose they are.", "values": []})
        mismatched = [nodes for first, nodes in cl.mismatch if cl.find(first) == cl.find(c.first)]
        if mismatched:
            where: dict[str, list[int]] = {}
            for nodes in mismatched:
                for path, number in nodes:
                    where.setdefault(path, []).append(number)
            conflicts.append({"kind": "file_name_id_mismatch", "message": "A file named like this person holds an identity card with a different number: it was not added. Check whose it is.",
                              "values": [{"where": [{"path": p, "pages": sorted(set(n))} for p, n in sorted(where.items())]}]})
        warnings = []
        for via, kind, words in (("filename", "placed_by_file_name", "by their file name"), ("firstname", "placed_by_first_name", "by a first name only")):
            where_pages = [{"path": f["path"], "pages": f["byFileName" if via == "filename" else "probable"]} for f in files_out if f["byFileName" if via == "filename" else "probable"]]
            count = sum(len(w["pages"]) for w in where_pages)
            if count:
                warnings.append({"kind": kind, "message": (f"{count} pages were placed {words}: check." if count != 1 else f"1 page was placed {words.replace('their', 'its')}: check."),
                                 "where": where_pages})
        if "dob" in c.linked_by:
            warnings.append({"kind": "joined_by_birth_date", "message": "A card with no readable name was joined to this person by the date of birth alone."})
        low_adj = [{"path": f["path"], "pages": f["lowConfidence"]} for f in files_out if f["lowConfidence"]]
        if low_adj:
            warnings.append({"kind": "low_confidence_placement", "message": "Some pages were placed only by their neighbours and read with low confidence.",
                             "where": low_adj})
        ids = {}
        if c.passports:
            ids["passport"] = tail(sorted(c.passports, key=lambda n: (not c.passports[n], n))[0])
        if c.aadhaars:
            ids["aadhaar"] = tail(sorted(c.aadhaars)[0])
        if c.phones:
            ids["phone"] = tail(sorted(c.phones)[0])
        people_out.append({
            "key": "", "name": display_name(tokens) if tokens else "", "nameSource": source, "evidence": evidence_kinds, "identifiers": ids,
            "files": files_out, "pageCount": sum(len(v) for v in per_person[i].values()),
            "needsLook": bool(conflicts), "conflicts": conflicts, "warnings": warnings, "first": c.first,
            # The full identifiers, for the server's own look-up of people the CRM already has. `to_dict` leaves it out:
            # numbers are never returned (see `identifiers`, which is masked).
            "_match": {"passports": sorted(n for n, verified in c.passports.items() if verified), "aadhaars": sorted(c.aadhaars),
                       "phones": sorted(c.phones), "emails": sorted(c.emails)},
        })
    people_out.sort(key=lambda p: p["first"])
    unnamed = 0
    by_first = {c.first: c for c in people_clusters}
    for n, person in enumerate(people_out, 1):
        person["key"] = f"p{n}"
        if not person["name"]:
            cluster = by_first[person["first"]]
            if _aadhaar_only(cluster):
                # never a made-up name: the card's number is all there is
                person["name"] = f"Unnamed candidate (Aadhaar ending {sorted(cluster.aadhaars)[0][-3:]})"
            else:
                unnamed += 1
                person["name"] = f"Candidate {unnamed}"
            person["nameSource"] = ""
        person.pop("first")
    # keys follow the sort; map cluster index -> key
    index_key = {}
    sorted_firsts = sorted(range(len(people_clusters)), key=lambda i: people_clusters[i].first)
    for n, i in enumerate(sorted_firsts):
        index_key[i] = f"p{n + 1}"
    unplaced: list[dict] = []
    unreadable: list[dict] = []
    files_out: list[dict] = []
    totals = {"files": len(file_pages), "pages": 0, "assigned": 0, "unplaced": 0, "unreadable": 0}
    for path in sorted(file_pages):
        entries = []
        by_reason: dict[tuple[str, str], list[int]] = {}
        extras: dict[tuple[str, str], dict] = {}
        for n in file_pages[path]:
            info = status[(path, n)]
            totals["pages"] += 1
            totals[info["status"]] += 1
            entries.append({"n": n, "status": info["status"], "person": index_key.get(info["person"]) if info["person"] is not None else None,
                            "via": info["via"], "confidence": info["confidence"], "method": info["method"], "low": info["low"],
                            "deep": info["deep"], **({"probable": True} if info.get("probable") else {}),
                            **({"reason": info["reason"]} if info["reason"] else {})})
            if info["status"] in ("unplaced", "unreadable") and (info["person"] is None):
                by_reason.setdefault((info["status"], info["reason"]), []).append(n)
                if info.get("kind"):
                    extras[(info["status"], info["reason"])] = {"kind": info["kind"], "ending": info.get("ending", "")}
        whole_file = len(file_pages[path])
        for (kind, reason), numbers in sorted(by_reason.items(), key=lambda item: (item[0][0], item[1][0])):
            row = {"path": path, "pages": None if sorted(numbers) == file_pages[path] else sorted(numbers), "reason": reason,
                   **extras.get((kind, reason), {})}
            (unplaced if kind == "unplaced" else unreadable).append(row)
        files_out.append({"path": path, "pageCount": whole_file, "readable": path not in unreadable_files,
                          "lowConfidence": [e["n"] for e in entries if e["low"]], "pages": entries})
    return PeopleResult(people_out, unplaced, unreadable, files_out, totals, stopped)


def _conflicts(c: _Cluster, where: dict[str, list[int]], status, evidence, blocked: set[int], clusters, tokens) -> list[dict]:
    out: list[dict] = []

    def pages_with(predicate) -> list[dict]:
        found: dict[str, list[int]] = {}
        for node in sorted(c.nodes):
            ev = evidence.get(node)
            if ev is not None and predicate(ev):
                found.setdefault(node[0], []).append(node[1])
        return [{"path": p, "pages": sorted(n)} for p, n in sorted(found.items())]

    if len(c.passports) >= 2:
        numbers = sorted(c.passports)
        probable = len(numbers) == 2 and _hamming1(*numbers)
        out.append({"kind": "passport_numbers_differ", "probable": probable,
                    "message": ("Two passport numbers that differ by one character (" if probable else "More than one passport number (")
                               + " and ".join(tail(n) for n in numbers) + ")"
                               + (": probably one person and a misread digit. Check the passport page." if probable else ": check which pages belong here."),
                    "values": [{"tail": tail(n), "verified": c.passports[n], "where": pages_with(lambda ev, n=n: n in ev.passports)} for n in numbers]})
    if len(c.aadhaars) >= 2:
        numbers = sorted(c.aadhaars)
        out.append({"kind": "aadhaar_numbers_differ", "message": "More than one Aadhaar number (" + " and ".join(tail(n) for n in numbers) + "): check which pages belong here.",
                    "values": [{"tail": tail(n), "where": pages_with(lambda ev, n=n: n in ev.aadhaars)} for n in numbers]})
    if len(c.dobs) >= 2:
        reliable = {d for d, s in c.dobs.items() if s == "mrz"} | {d for d in c.dobs}
        out.append({"kind": "birth_dates_differ", "message": f"{len(c.dobs)} different dates of birth are printed on this person's pages: check them.",
                    "values": [{"where": pages_with(lambda ev, d=d: d in ev.dobs)} for d in sorted(c.dobs)]})
    names = c.usable_names()
    for i, a in enumerate(names):
        for b in names[i + 1:]:
            if relation(a, b) == "none":
                out.append({"kind": "names_differ", "message": "Two names with no word in common are on this person's pages: " +
                            display_name(a) + " and " + display_name(b) + ".",
                            "values": [{"where": pages_with(lambda ev, t=t: any(h.tokens == t for h in ev.names))} for t in (a, b)]})
    if blocked:
        out.append({"kind": "same_name_other_numbers", "message": "Another candidate here has the same name but a different passport or Aadhaar number: check they are two people.",
                    "values": []})
    return out
