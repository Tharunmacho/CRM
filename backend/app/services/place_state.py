"""The state shown beside a lead's city: a real state, or nothing.

Imported ad sheets leave junk in the state field ('H', '31', an id, the city repeated). The Lead sheet, the lead detail and the
Excel / CSV export all show `display_state(...)`, so the screen and the file agree. The value stored on the lead is never changed.

  1. `clean_state(raw, city)`: the stored text if it is a real state or union territory of India (spellings and abbreviations
     such as 'TN', 'Tamilnadu', 'Tamil Nadu, India', 'tamil_nadu' are accepted and written in full) or a country / region the sheets
     carry ('UAE', 'Qatar', 'Dubai'...). Single letters, numbers, ids, other short tokens, unknown words and the city repeated are junk.
  2. Missing or junk: `derive_state(city)`: the most common valid state of the OTHER leads with that city (`most_common_state_by_city`
     over one grouped read, kept with the leads cache by the caller), else the built-in map of Indian cities, towns and districts.
  3. Otherwise nothing. A value is never made up.

Pure: no database and no framework imports.
"""
from __future__ import annotations

import re
import unicodedata
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping

# ------------------------------------------------------------------ normalising text


def place_key(text: object) -> str:
    """Case, accent, punctuation and spacing insensitive form of a place or state name ('Tamil_Nadu', 'tamil  nadu.' -> 'tamil nadu')."""
    if text is None:
        return ""
    value = unicodedata.normalize("NFKD", str(text)).encode("ascii", "ignore").decode("ascii").lower()
    value = value.replace("&", " and ").replace("_", " ")
    value = re.sub(r"[^a-z0-9]+", " ", value)
    return " ".join(value.split())


# ------------------------------------------------------------------ states, union territories, countries and regions

# canonical name -> spellings and abbreviations (the canonical name itself is always accepted)
_INDIAN_STATES: dict[str, str] = {
    "Andhra Pradesh": "ap, a p, andhra, andra pradesh, andhrapradesh, andra",
    "Arunachal Pradesh": "ar, arunachal, arunachalpradesh",
    "Assam": "as, asom",
    "Bihar": "br, bihar state",
    "Chhattisgarh": "cg, chattisgarh, chhatisgarh, chattishgarh, chhattisghar, cgh",
    "Goa": "ga",
    "Gujarat": "gj, gujrat, gujarath, gujrath",
    "Haryana": "hr, hariyana",
    "Himachal Pradesh": "hp, himachal, himachalpradesh",
    "Jharkhand": "jh, jharkand, jharkhand state",
    "Karnataka": "ka, karnatak, karnatka, karnataka state",
    "Kerala": "kl, kerela, keralam, kerala state, god s own country",
    "Madhya Pradesh": "mp, m p, madhyapradesh, madhya pradesh state",
    "Maharashtra": "mh, maharastra, maharashtra state, maharshtra",
    "Manipur": "mn",
    "Meghalaya": "ml",
    "Mizoram": "mz",
    "Nagaland": "nl",
    "Odisha": "od, or, orissa, odissa",
    "Punjab": "pb, punjab state",
    "Rajasthan": "rj, rajsthan, rajastan",
    "Sikkim": "sk",
    "Tamil Nadu": "tn, t n, tamilnadu, tamilnad, tamil nad, tamizhagam, tamil nadu state, tamilnadu state, tamilnaadu",
    "Telangana": "ts, tg, telengana, telangana state, telangaana",
    "Tripura": "tr",
    "Uttar Pradesh": "up, u p, uttarpradesh, uttar pradesh state",
    "Uttarakhand": "uttaranchal, uttrakhand, uttarkhand",
    "West Bengal": "wb, w b, westbengal, bengal, paschim banga",
    # union territories
    "Andaman and Nicobar Islands": "an, andaman, andaman nicobar, andaman and nicobar, a and n islands, nicobar",
    "Chandigarh": "ch",
    "Dadra and Nagar Haveli and Daman and Diu": "dn, dd, dnhdd, dadra and nagar haveli, daman and diu, daman, diu, dadra",
    "Delhi": "dl, nct of delhi, nct delhi, new delhi, delhi ncr, ncr, national capital territory of delhi, delhi state",
    "Jammu and Kashmir": "jk, j and k, jammu kashmir, jammu, kashmir, jammu and kashmir state",
    "Ladakh": "la, leh ladakh",
    "Lakshadweep": "ld, lakshadweep islands",
    "Puducherry": "py, pondicherry, pondy, puducheri, pudhucherry, pudhuchery",
}

# countries / regions the sheets may carry in the state field (kept as the sheet wrote them, in full)
_REGIONS: dict[str, str] = {
    "United Arab Emirates": "uae, u a e, emirates, abu dhabi, dubai, sharjah, ajman, fujairah, ras al khaimah, umm al quwain, al ain",
    "Qatar": "doha",
    "Saudi Arabia": "ksa, saudi, riyadh, jeddah, dammam, kingdom of saudi arabia",
    "Kuwait": "kuwait city",
    "Oman": "muscat, sultanate of oman",
    "Bahrain": "manama",
    "Sri Lanka": "srilanka, colombo",
    "Nepal": "kathmandu",
    "Bangladesh": "dhaka",
    "Pakistan": "",
    "Bhutan": "",
    "Maldives": "",
    "Malaysia": "kuala lumpur",
    "Singapore": "",
    "Thailand": "",
    "Hong Kong": "hongkong",
    "Japan": "",
    "United States": "usa, u s a, united states of america",
    "United Kingdom": "uk, u k, england, great britain, britain, scotland, wales",
    "Canada": "",
    "Australia": "",
    "New Zealand": "",
    "Germany": "",
    "Poland": "",
    "Romania": "",
    "Croatia": "",
    "Jordan": "",
    "Iraq": "",
    "Lebanon": "",
    "Egypt": "",
    "Israel": "",
    "Mauritius": "",
    "Seychelles": "",
    "South Africa": "",
    "Nigeria": "",
    "Kenya": "",
}

_VALID: dict[str, str] = {}
for _table in (_INDIAN_STATES, _REGIONS):
    for _canonical, _aliases in _table.items():
        _VALID[place_key(_canonical)] = _canonical
        for _alias in _aliases.split(","):
            if place_key(_alias):
                _VALID.setdefault(place_key(_alias), _canonical)
# The state name alone ('Delhi', 'Goa') is a state; the country itself says nothing about where in it a person lives.
_NOT_A_PLACE = {"india", "bharat", "republic of india", "in", "ind", "na", "n a", "nil", "none", "null", "unknown", "other", "others", "not applicable"}


def known_states() -> list[str]:
    """Every canonical state / union territory of India, A to Z."""
    return sorted(_INDIAN_STATES)


def clean_state(raw: object, city: object = None) -> str | None:
    """The canonical name of `raw` when it is a real state, union territory or country / region, else None (junk).

    A comma separated value ('Tamil Nadu, India', 'Pollachi, Tamil Nadu') gives its first part that is a state. A value equal to the
    city is the city repeated, not a state."""
    if raw is None:
        return None
    text = str(raw).strip()
    if len(text) < 2:
        return None
    city_key = place_key(city)
    parts = [text] + [p for p in re.split(r"[,;/|]| - ", text) if p.strip()] if re.search(r"[,;/|]| - ", text) else [text]
    for part in parts:
        key = place_key(part)
        if not key or key in _NOT_A_PLACE or any(ch.isdigit() for ch in key):
            continue
        if city_key and key == city_key:
            continue
        found = _VALID.get(key)
        if found is None and key.endswith(" state"):
            found = _VALID.get(key[: -len(" state")])
        if found is not None:
            return found
    return None


# ------------------------------------------------------------------ built-in map: cities, towns and districts -> state

_PLACES: dict[str, str] = {
    "Tamil Nadu": """chennai, madras, coimbatore, kovai, madurai, tiruchirappalli, trichy, tiruchi, tiruchirapalli, salem, tirunelveli,
        tirunalveli, nellai, tiruppur, tirupur, erode, vellore, thoothukudi, tuticorin, thanjavur, tanjore, dindigul, nagercoil,
        kanyakumari, kanniyakumari, kanchipuram, kancheepuram, karur, cuddalore, hosur, krishnagiri, dharmapuri, namakkal, pollachi,
        mettupalayam, udumalaipettai, udumalpet, palani, theni, cumbum, virudhunagar, sivakasi, rajapalayam, ramanathapuram, rameswaram,
        sivaganga, karaikudi, pudukkottai, ariyalur, perambalur, mayiladuthurai, mayavaram, nagapattinam, nagapatinam, thiruvarur,
        tiruvarur, kumbakonam, chidambaram, villupuram, viluppuram, kallakurichi, tiruvannamalai, thiruvannamalai, ranipet, ambur,
        vaniyambadi, tirupattur, tirupathur, ooty, udhagamandalam, nilgiris, coonoor, kotagiri, gudalur, kodaikanal, avadi, tambaram,
        chengalpattu, chengalpet, tiruvallur, thiruvallur, sriperumbudur, poonamallee, gobichettipalayam, bhavani, sathyamangalam,
        attur, mettur, tenkasi, sankarankovil, kovilpatti, arakkonam, walajapet, panruti, neyveli, aranthangi, paramakudi, manapparai,
        musiri, lalgudi, srirangam, tiruchendur, marthandam, thuckalay, kuzhithurai, thiruchendur, thirunelveli, thiruppur, thiruchirappalli,
        thirupathur, thiruvarur, perundurai, vadalur, tindivanam, gingee, sholinganallur, velachery, tnagar, t nagar, anna nagar,
        ambattur, pallavaram, perambur, tiruttani, thiruthani, komarapalayam, rasipuram, tharamangalam, vedaranyam, needamangalam,
        mannargudi, orathanadu, papanasam, pattukkottai, peravurani, sirkazhi, tharangambadi, kollidam, sulur, annur, kinathukadavu,
        valparai, anaimalai, thondamuthur, madukkarai, karamadai, singanallur, ukkadam, peelamedu, saravanampatti, gandhipuram, rs puram""",
    "Kerala": """thiruvananthapuram, trivandrum, kochi, cochin, ernakulam, kozhikode, calicut, thrissur, trichur, kollam, quilon, alappuzha,
        alleppey, palakkad, palghat, kannur, cannanore, kottayam, pathanamthitta, malappuram, kasaragod, kasargod, wayanad, idukki,
        muvattupuzha, thodupuzha, kothamangalam, perumbavur, aluva, alwaye, angamaly, chalakudy, irinjalakuda, kodungallur, guruvayur,
        kunnamkulam, ottapalam, shoranur, mannarkkad, perinthalmanna, manjeri, tirur, kondotty, nilambur, vadakara, thalassery, tellicherry,
        payyannur, taliparamba, kanhangad, changanassery, changanacherry, pala, kattappana, adoor, thiruvalla, mavelikkara, chengannur,
        kayamkulam, neyyattinkara, attingal, varkala, nedumangad, kattakkada, punalur, karunagappally, kalpetta, mananthavady,
        sulthan bathery, sultan bathery, cherthala, tripunithura, kakkanad, edappally, thiruvalla, pathanamthitta, ranni, konni,
        perumbavoor, vaikom, ettumanoor, erattupetta, mundakayam, kanjirappally, thiruvambadi, koyilandy, perambra, mukkam, ponnani,
        valanchery, kuttippuram, pattambi, cherpulassery, chittur, nenmara, alathur, vadakkencherry, koduvayur, kuthuparamba, mattannur,
        iritty, sreekandapuram, kumily, munnar, thekkady, adimaly, nedumkandam, devikulam, haripad, ambalappuzha, kuttanad, chengannur,
        pandalam, kottarakkara, paravur, chavara, kilimanoor, venjaramoodu, balaramapuram, vizhinjam, kovalam, poovar, parassala""",
    "Karnataka": """bengaluru, bangalore, mysuru, mysore, mangaluru, mangalore, hubballi, hubli, dharwad, belagavi, belgaum, kalaburagi, gulbarga,
        ballari, bellary, vijayapura, bijapur, shivamogga, shimoga, tumakuru, tumkur, davanagere, davangere, udupi, hassan, mandya,
        chitradurga, raichur, bidar, hospet, hosapete, gadag, karwar, chikmagalur, chikkamagaluru, kolar, madikeri, coorg, kodagu,
        chamarajanagar, ramanagara, bagalkot, koppal, haveri, yadgir, sirsi, bhatkal, puttur, ankola, kundapura, kundapur, karkala,
        sagara, thirthahalli, chikkaballapur, chintamani, mulbagal, bangarpet, whitefield, electronic city, yelahanka, hebbal,
        jayanagar, koramangala, indiranagar, marathahalli, bommanahalli, hoskote, doddaballapur, nelamangala, anekal, kanakapura""",
    "Andhra Pradesh": """visakhapatnam, vizag, vijayawada, guntur, nellore, kurnool, rajahmundry, rajamahendravaram, kakinada, tirupati, tirupathi,
        kadapa, cuddapah, anantapur, anantapuram, eluru, ongole, machilipatnam, masulipatnam, srikakulam, vizianagaram, chittoor, amaravati,
        tenali, bhimavaram, tadepalligudem, nandyal, proddatur, hindupur, madanapalle, narasaraopet, chirala, tanuku, gudivada, palakollu,
        amalapuram, adoni, dharmavaram, nuzvid, puttaparthi, srikalahasti, kavali, gudur, sullurpeta, tadipatri, guntakal, rayachoti,
        markapur, bapatla, repalle, mangalagiri, jaggayyapeta, pithapuram, samalkot, narsipatnam, anakapalli, bheemunipatnam, parvathipuram,
        palasa, tekkali, rajam, salur, bobbili""",
    "Telangana": """hyderabad, secunderabad, warangal, hanamkonda, hanumakonda, karimnagar, nizamabad, khammam, nalgonda, mahbubnagar, mahabubnagar,
        adilabad, siddipet, suryapet, medak, sangareddy, ramagundam, miryalaguda, kamareddy, jagtial, mancherial, wanaparthy, vikarabad,
        kothagudem, bhongir, shamshabad, gachibowli, hitech city, kukatpally, lb nagar, uppal, ameerpet, dilsukhnagar, medchal,
        malkajgiri, secunderabad, patancheru, zaheerabad, narayanpet, gadwal, nagarkurnool, bodhan, armoor, metpally, sircilla""",
    "Maharashtra": """mumbai, bombay, pune, poona, nagpur, thane, nashik, nasik, aurangabad, chhatrapati sambhajinagar, solapur, kolhapur, amravati,
        navi mumbai, kalyan, dombivli, dombivali, vasai, virar, bhiwandi, mira road, mira bhayandar, sangli, jalgaon, akola, latur, dhule,
        ahmednagar, ahilyanagar, nanded, satara, ratnagiri, chandrapur, parbhani, jalna, beed, osmanabad, dharashiv, yavatmal, wardha,
        bhandara, gondia, malegaon, panvel, pimpri chinchwad, pimpri, chinchwad, ulhasnagar, ambernath, badlapur, lonavala, lonavla,
        shirdi, baramati, karad, ichalkaranji, sindhudurg, kudal, alibag, raigad, nandurbar, washim, buldhana, hingoli, gadchiroli,
        mulund, andheri, borivali, bandra, goregaon, kharghar, vashi, nerul, airoli, wagholi, hinjewadi, hadapsar, kothrud""",
    "Delhi": """delhi, new delhi, dwarka, rohini, saket, janakpuri, karol bagh, lajpat nagar, pitampura, shahdara, mayur vihar, vasant kunj,
        connaught place, okhla, nehru place, laxmi nagar, uttam nagar, najafgarh, narela, mehrauli, chandni chowk, paharganj""",
    "Gujarat": """ahmedabad, surat, vadodara, baroda, rajkot, bhavnagar, jamnagar, junagadh, gandhinagar, anand, nadiad, bharuch, morbi,
        mehsana, bhuj, porbandar, navsari, valsad, vapi, surendranagar, amreli, gandhidham, palanpur, godhra, patan, veraval, ankleshwar,
        dahod, himmatnagar, botad, kalol, kadi, deesa, vyara, dwarka gujarat, somnath, kheda, mundra, halol""",
    "Punjab": """ludhiana, amritsar, jalandhar, jullundur, patiala, bathinda, bhatinda, mohali, sas nagar, pathankot, hoshiarpur, moga, firozpur,
        ferozepur, batala, abohar, kapurthala, phagwara, barnala, sangrur, muktsar, sri muktsar sahib, fazilka, rupnagar, ropar, gurdaspur,
        faridkot, khanna, zirakpur, tarn taran, malerkotla, nawanshahr, nangal, rajpura, mandi gobindgarh, fatehgarh sahib, dera bassi""",
    "Haryana": """gurugram, gurgaon, faridabad, panipat, ambala, yamunanagar, rohtak, hisar, karnal, sonipat, sonepat, panchkula, bhiwani, sirsa,
        bahadurgarh, jind, kurukshetra, rewari, kaithal, palwal, fatehabad, nuh, mewat, jhajjar, charkhi dadri, mahendragarh, narnaul,
        manesar, thanesar, hansi, tohana, ratia""",
    "Uttar Pradesh": """lucknow, kanpur, cawnpore, ghaziabad, agra, varanasi, benares, banaras, meerut, prayagraj, allahabad, bareilly, aligarh,
        moradabad, saharanpur, gorakhpur, noida, greater noida, firozabad, jhansi, muzaffarnagar, mathura, vrindavan, rampur, shahjahanpur,
        ayodhya, faizabad, etawah, mirzapur, bulandshahr, sambhal, amroha, hardoi, fatehpur, raebareli, rae bareli, orai, sitapur, bahraich,
        unnao, jaunpur, lakhimpur kheri, basti, deoria, ballia, azamgarh, budaun, badaun, pilibhit, lalitpur, banda, chitrakoot, mau, ghazipur,
        sultanpur, barabanki, gonda, kushinagar, maharajganj, sonbhadra, obra, anpara, modinagar, loni, khurja, hathras, kasganj, etah""",
    "Rajasthan": """jaipur, jodhpur, udaipur, kota, ajmer, bikaner, alwar, bhilwara, sikar, bharatpur, pali, sri ganganagar, ganganagar, jaisalmer,
        chittorgarh, chittaurgarh, tonk, barmer, nagaur, hanumangarh, churu, jhunjhunu, jhunjhunun, dausa, bundi, sawai madhopur, banswara,
        dungarpur, mount abu, pushkar, rajsamand, nathdwara, beawar, kishangarh, sirohi, jalore, karauli, dholpur, baran, jhalawar""",
    "West Bengal": """kolkata, calcutta, howrah, durgapur, asansol, siliguri, bardhaman, burdwan, malda, english bazar, kharagpur, haldia, darjeeling,
        jalpaiguri, cooch behar, coochbehar, krishnanagar, barasat, bankura, purulia, baharampur, berhampore, raiganj, midnapore, medinipur,
        bidhannagar, salt lake, hooghly, chinsurah, serampore, kalyani, barrackpore, dum dum, jadavpur, behala, tamluk, bishnupur, alipurduar,
        balurghat, kalimpong, haora, cossipore, rajarhat, new town kolkata""",
    "Bihar": """patna, gaya, bhagalpur, muzaffarpur, purnia, purnea, darbhanga, bihar sharif, arrah, ara, begusarai, katihar, munger, monghyr, chhapra,
        samastipur, hajipur, sasaram, dehri, siwan, motihari, nawada, bettiah, saharsa, sitamarhi, jehanabad, buxar, kishanganj, madhubani,
        araria, supaul, jamui, lakhisarai, sheikhpura, banka, bodh gaya, danapur, phulwari sharif""",
    "Madhya Pradesh": """bhopal, indore, jabalpur, gwalior, ujjain, sagar, dewas, satna, ratlam, rewa, murwara, katni, singrauli, burhanpur, khandwa,
        bhind, chhindwara, guna, shivpuri, vidisha, damoh, mandsaur, khargone, neemuch, pithampur, hoshangabad, narmadapuram, itarsi, sehore,
        betul, seoni, balaghat, morena, datia, dhar, chhatarpur, panna, sidhi, mhow, tikamgarh, shajapur, rajgarh, harda, mandla""",
    "Chhattisgarh": """raipur, bhilai, bilaspur, korba, durg, rajnandgaon, raigarh, jagdalpur, ambikapur, dhamtari, mahasamund, kanker, naya raipur,
        champa, janjgir, kawardha, dantewada, bastar, surajpur, balod""",
    "Odisha": """bhubaneswar, cuttack, rourkela, berhampur, brahmapur, sambalpur, puri, balasore, baleshwar, bhadrak, baripada, jharsuguda, jeypore,
        angul, dhenkanal, koraput, kendrapara, paradip, rayagada, bargarh, keonjhar, kendujhar, talcher, sundargarh, jajpur, balangir,
        bolangir, nabarangpur, malkangiri, phulbani, jagatsinghpur, nayagarh, khordha, khurda, konark""",
    "Jharkhand": """ranchi, jamshedpur, dhanbad, bokaro, bokaro steel city, deoghar, hazaribagh, giridih, ramgarh, medininagar, daltonganj, phusro,
        chaibasa, dumka, chirkunda, sahibganj, lohardaga, gumla, koderma, pakur, godda, jharia, chas, adityapur, simdega, latehar""",
    "Assam": """guwahati, gauhati, dibrugarh, silchar, jorhat, nagaon, tinsukia, tezpur, bongaigaon, dhubri, diphu, north lakhimpur, lakhimpur assam,
        karimganj, sribhumi, sivasagar, sibsagar, goalpara, barpeta, golaghat, kokrajhar, nalbari, mangaldoi, dhemaji, hailakandi, haflong""",
    "Uttarakhand": """dehradun, haridwar, roorkee, haldwani, rudrapur, kashipur, rishikesh, nainital, mussoorie, pithoragarh, almora, kotdwar, pauri,
        tehri, rudraprayag, chamoli, bageshwar, champawat, uttarkashi, lansdowne, vikasnagar, kichha, bhimtal, ramnagar uttarakhand""",
    "Himachal Pradesh": """shimla, simla, dharamshala, dharamsala, solan, mandi, kullu, manali, palampur, baddi, nahan, una, kangra, chamba, kasauli,
        sundernagar, paonta sahib, parwanoo, nalagarh, kinnaur, lahaul, spiti, kufri, dalhousie, mcleodganj, kasol""",
    "Jammu and Kashmir": """srinagar, jammu, anantnag, baramulla, baramula, sopore, kathua, udhampur, pulwama, kupwara, budgam, kulgam, shopian, ganderbal,
        bandipora, rajouri, poonch, doda, kishtwar, ramban, reasi, samba, katra, pahalgam, gulmarg""",
    "Ladakh": "leh, kargil, nubra, zanskar",
    "Goa": """goa, panaji, panjim, margao, madgaon, vasco da gama, vasco, mapusa, ponda, calangute, candolim, baga goa, bicholim, curchorem,
        canacona, pernem, quepem, sanguem, valpoi, cortalim, verna goa""",
    "Chandigarh": "chandigarh, mani majra, manimajra",
    "Puducherry": "puducherry, pondicherry, karaikal, yanam, mahe, ariyankuppam, villianur, auroville",
    "Andaman and Nicobar Islands": "port blair, havelock, diglipur, car nicobar, mayabunder, rangat",
    "Lakshadweep": "kavaratti, agatti, minicoy, androth, amini",
    "Dadra and Nagar Haveli and Daman and Diu": "silvassa, daman, diu, dadra, nagar haveli, naroli",
    "Sikkim": "gangtok, namchi, gyalshing, geyzing, mangan, pelling, rangpo, jorethang",
    "Meghalaya": "shillong, tura, jowai, nongpoh, cherrapunji, sohra, baghmara, williamnagar",
    "Manipur": "imphal, thoubal, bishnupur manipur, churachandpur, ukhrul, senapati, kakching",
    "Mizoram": "aizawl, lunglei, champhai, serchhip, kolasib, saiha",
    "Nagaland": "kohima, dimapur, mokokchung, tuensang, wokha, zunheboto, mon nagaland, phek",
    "Tripura": "agartala, udaipur tripura, dharmanagar, kailashahar, belonia, ambassa",
    "Arunachal Pradesh": "itanagar, naharlagun, tawang, pasighat, ziro, bomdila, tezu, along",
}

_CITY_STATE: dict[str, str] = {}
for _state, _names in _PLACES.items():
    for _name in _names.split(","):
        _k = place_key(_name)
        if _k:
            _CITY_STATE.setdefault(_k, _state)

_PLACE_SUFFIXES = (" district", " dist", " city", " taluk", " taluka", " town", " rural", " urban", " municipality", " corporation")


def builtin_state(city: object) -> str | None:
    """The state of a well-known Indian city, town or district from the built-in map (None when it is not in it).

    'Chennai - 600001', 'Pollachi, Tamil Nadu', 'Kottayam District' and 'chennai' all find their place."""
    if city is None:
        return None
    text = str(city).strip()
    if not text:
        return None
    for part in [text] + (re.split(r"[,;/|]| - ", text) if re.search(r"[,;/|]| - ", text) else []):
        tokens = [t for t in place_key(part).split() if not t.isdigit()]
        key = " ".join(tokens)
        if not key:
            continue
        found = _CITY_STATE.get(key)
        if found is None:
            for suffix in _PLACE_SUFFIXES:
                if key.endswith(suffix):
                    found = _CITY_STATE.get(key[: -len(suffix)])
                    break
        if found is not None:
            return found
        explicit = clean_state(part)   # 'Pollachi, Tamil Nadu' in the city field
        if explicit is not None and explicit in _INDIAN_STATES:
            return explicit
    return None


_NO_CITY = {"n a", "na", "nil", "none", "null", "nan", "unknown", "not applicable", "not available", "not provided", "not given",
            "other", "others", "test", "xx", "xxx", "x"}


def clean_city(raw: object) -> str | None:
    """The city as it should be shown, or None when the sheet left junk: spaces trimmed and collapsed, a plain lower-case or UPPER-CASE
    name written in capitals ('POLLACHI' -> 'Pollachi'; 'UAE' and mixed case such as 'McLeodganj' are kept as typed). Dropped: numbers
    and ids, single letters, 'N/A', 'null', '-', and a value that is only the name of a state ('Tamil Nadu') unless it is also a
    well-known place ('Delhi', 'Goa', 'Chandigarh', 'Puducherry')."""
    if raw is None:
        return None
    text = " ".join(str(raw).split()).strip(" -_.,;:/|")
    if len(text) < 2:
        return None
    key = place_key(text)
    if not key or key in _NO_CITY or not any(ch.isalpha() for ch in key) or any(ch.isdigit() for ch in key) and key.replace(" ", "").isdigit():
        return None
    letters = [t for t in re.sub(r"[^A-Za-z ]", "", text).split() if t]
    if len(" ".join(letters)) < 2:
        return None
    state = clean_state(text)
    if state is not None and state in _INDIAN_STATES and key not in _CITY_STATE and place_key(state) == key:
        return None
    if text.islower() or (text.isupper() and (len(text) > 4 or " " in text)):
        return text.title()
    return text


# ------------------------------------------------------------------ from the data: the most common state of a city


def city_lookup_key(city: object) -> str:
    """The key under which a city's state is looked up in the data map: case, spacing and pin-code insensitive."""
    return " ".join(t for t in place_key(city).split() if not t.isdigit())


def most_common_state_by_city(pairs: Iterable[tuple[object, object, int]]) -> dict[str, str]:
    """`(city, state as stored, number of leads)` -> `{city key: the valid state most leads with that city carry}`.

    Junk states are not counted. A tie goes to the state that comes first alphabetically (stable between requests)."""
    counts: dict[str, Counter] = defaultdict(Counter)
    for city, state, n in pairs:
        key = city_lookup_key(city)
        if len(key) < 2:
            continue
        valid = clean_state(state, city)
        if valid is not None and valid in _INDIAN_STATES:   # a city of the sheet lies in India: a country is no answer for it
            counts[key][valid] += int(n or 0)
    return {key: sorted(c.items(), key=lambda item: (-item[1], item[0]))[0][0] for key, c in counts.items() if c}


def derive_state(city: object, data: Mapping[str, str] | None = None) -> str | None:
    """The state of a place when the lead's own state is no use: the data first, then the built-in map, else None."""
    key = city_lookup_key(city)
    if key and data:
        found = data.get(key)
        if found:
            return found
    return builtin_state(city)


def display_state(city: object, state: object, data: Mapping[str, str] | None = None) -> str | None:
    """What every screen and file shows as the lead's state: the stored one when real, else the one derived from the place, else None."""
    found = clean_state(state, city)
    if found is not None:
        return found
    found = derive_state(city, data)
    if found is not None and place_key(found) == place_key(city):
        return None   # the city repeated says nothing more
    return found
