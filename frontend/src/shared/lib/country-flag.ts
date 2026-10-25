/**
 * Country name -> ISO 3166-1 alpha-2 code. This is a standards lookup (like the ISO list itself),
 * not business data: it only decides which flag file to ask for. It is the single place that
 * knows country names; every screen that shows a flag goes through it.
 *
 * Matching is by whole words after folding case, accents and punctuation, so "Oman" never
 * matches inside "Romania" and "Niger" never matches inside "Nigeria".
 */

const ISO_COUNTRIES = `AD Andorra|AE United Arab Emirates|AF Afghanistan|AG Antigua and Barbuda|AI Anguilla|AL Albania|AM Armenia|AO Angola|AQ Antarctica|AR Argentina|AS American Samoa|AT Austria|AU Australia|AW Aruba|AX Aland Islands|AZ Azerbaijan|BA Bosnia and Herzegovina|BB Barbados|BD Bangladesh|BE Belgium|BF Burkina Faso|BG Bulgaria|BH Bahrain|BI Burundi|BJ Benin|BL Saint Barthelemy|BM Bermuda|BN Brunei Darussalam|BO Bolivia|BQ Bonaire Sint Eustatius and Saba|BR Brazil|BS Bahamas|BT Bhutan|BV Bouvet Island|BW Botswana|BY Belarus|BZ Belize|CA Canada|CC Cocos Keeling Islands|CD Democratic Republic of the Congo|CF Central African Republic|CG Congo|CH Switzerland|CI Cote d Ivoire|CK Cook Islands|CL Chile|CM Cameroon|CN China|CO Colombia|CR Costa Rica|CU Cuba|CV Cabo Verde|CW Curacao|CX Christmas Island|CY Cyprus|CZ Czechia|DE Germany|DJ Djibouti|DK Denmark|DM Dominica|DO Dominican Republic|DZ Algeria|EC Ecuador|EE Estonia|EG Egypt|EH Western Sahara|ER Eritrea|ES Spain|ET Ethiopia|FI Finland|FJ Fiji|FK Falkland Islands|FM Micronesia|FO Faroe Islands|FR France|GA Gabon|GB United Kingdom|GD Grenada|GE Georgia|GF French Guiana|GG Guernsey|GH Ghana|GI Gibraltar|GL Greenland|GM Gambia|GN Guinea|GP Guadeloupe|GQ Equatorial Guinea|GR Greece|GS South Georgia and the South Sandwich Islands|GT Guatemala|GU Guam|GW Guinea-Bissau|GY Guyana|HK Hong Kong|HM Heard Island and McDonald Islands|HN Honduras|HR Croatia|HT Haiti|HU Hungary|ID Indonesia|IE Ireland|IL Israel|IM Isle of Man|IN India|IO British Indian Ocean Territory|IQ Iraq|IR Iran|IS Iceland|IT Italy|JE Jersey|JM Jamaica|JO Jordan|JP Japan|KE Kenya|KG Kyrgyzstan|KH Cambodia|KI Kiribati|KM Comoros|KN Saint Kitts and Nevis|KP North Korea|KR South Korea|KW Kuwait|KY Cayman Islands|KZ Kazakhstan|LA Laos|LB Lebanon|LC Saint Lucia|LI Liechtenstein|LK Sri Lanka|LR Liberia|LS Lesotho|LT Lithuania|LU Luxembourg|LV Latvia|LY Libya|MA Morocco|MC Monaco|MD Moldova|ME Montenegro|MF Saint Martin|MG Madagascar|MH Marshall Islands|MK North Macedonia|ML Mali|MM Myanmar|MN Mongolia|MO Macao|MP Northern Mariana Islands|MQ Martinique|MR Mauritania|MS Montserrat|MT Malta|MU Mauritius|MV Maldives|MW Malawi|MX Mexico|MY Malaysia|MZ Mozambique|NA Namibia|NC New Caledonia|NE Niger|NF Norfolk Island|NG Nigeria|NI Nicaragua|NL Netherlands|NO Norway|NP Nepal|NR Nauru|NU Niue|NZ New Zealand|OM Oman|PA Panama|PE Peru|PF French Polynesia|PG Papua New Guinea|PH Philippines|PK Pakistan|PL Poland|PM Saint Pierre and Miquelon|PN Pitcairn|PR Puerto Rico|PS Palestine|PT Portugal|PW Palau|PY Paraguay|QA Qatar|RE Reunion|RO Romania|RS Serbia|RU Russia|RW Rwanda|SA Saudi Arabia|SB Solomon Islands|SC Seychelles|SD Sudan|SE Sweden|SG Singapore|SH Saint Helena|SI Slovenia|SJ Svalbard and Jan Mayen|SK Slovakia|SL Sierra Leone|SM San Marino|SN Senegal|SO Somalia|SR Suriname|SS South Sudan|ST Sao Tome and Principe|SV El Salvador|SX Sint Maarten|SY Syria|SZ Eswatini|TC Turks and Caicos Islands|TD Chad|TF French Southern Territories|TG Togo|TH Thailand|TJ Tajikistan|TK Tokelau|TL Timor-Leste|TM Turkmenistan|TN Tunisia|TO Tonga|TR Turkiye|TT Trinidad and Tobago|TV Tuvalu|TW Taiwan|TZ Tanzania|UA Ukraine|UG Uganda|UM United States Minor Outlying Islands|US United States|UY Uruguay|UZ Uzbekistan|VA Holy See|VC Saint Vincent and the Grenadines|VE Venezuela|VG British Virgin Islands|VI US Virgin Islands|VN Vietnam|VU Vanuatu|WF Wallis and Futuna|WS Samoa|XK Kosovo|YE Yemen|YT Mayotte|ZA South Africa|ZM Zambia|ZW Zimbabwe`

/** Common alternative spellings and abbreviations people type, by code. */
const ALIASES: Record<string, string[]> = {
  ae: ['uae', 'u.a.e', 'emirates', 'dubai', 'abu dhabi', 'sharjah'], sa: ['ksa', 'saudi'], gb: ['uk', 'u.k', 'great britain', 'britain', 'england', 'scotland', 'wales', 'northern ireland'],
  us: ['usa', 'u.s.a', 'united states of america', 'america'], in: ['bharat'], kr: ['korea', 'republic of korea'], kp: ['dprk'], ru: ['russian federation'],
  ci: ['ivory coast'], tr: ['turkey'], cz: ['czech republic'], mm: ['burma'], vn: ['viet nam'], sz: ['swaziland'], mk: ['macedonia'], cv: ['cape verde'],
  tl: ['east timor'], va: ['vatican', 'vatican city'], cd: ['drc', 'dr congo'], cg: ['republic of the congo'], la: ['lao pdr'], ir: ['islamic republic of iran'],
  sy: ['syrian arab republic'], bo: ['plurinational state of bolivia'], ve: ['bolivarian republic of venezuela'], tz: ['united republic of tanzania'], md: ['republic of moldova'],
  mo: ['macau'], bs: ['the bahamas'], gm: ['the gambia'], nl: ['holland'], ps: ['state of palestine'],
}

/** Fold case, accents and punctuation so "Côte d'Ivoire", "u.a.e." and "UAE" compare equal. */
function fold(value: string) {
  return value.normalize('NFD').replace(/[̀-ͯ]/g, '').toLowerCase().replace(/&/g, ' and ').replace(/[^a-z0-9]+/g, ' ').trim()
}

const EXACT = new Map<string, string>()
const PHRASES: { phrase: string; code: string }[] = []

for (const row of ISO_COUNTRIES.split('|')) {
  const code = row.slice(0, 2).toLowerCase()
  const name = fold(row.slice(3))
  EXACT.set(code, code)
  EXACT.set(name, code)
  PHRASES.push({ phrase: name, code })
}
for (const [code, names] of Object.entries(ALIASES)) {
  for (const alias of names) {
    EXACT.set(fold(alias), code)
    PHRASES.push({ phrase: fold(alias), code })
  }
}
// Longest phrase first so "Papua New Guinea" wins over "Guinea" and "South Sudan" over "Sudan".
const BY_LENGTH = PHRASES.filter((entry) => entry.phrase.length >= 3).sort((a, b) => b.phrase.length - a.phrase.length)

const memo = new Map<string, string | null>()

/** Lower-case ISO alpha-2 code for a country name as typed ("United Arab Emirates", "UAE", "Dubai, UAE"), or null when unknown. */
export function countryCode(country?: string | null): string | null {
  const key = fold(country ?? '')
  if (!key) return null
  const known = memo.get(key)
  if (known !== undefined) return known
  let code = EXACT.get(key) ?? null
  if (!code) {
    const padded = ` ${key} `
    code = BY_LENGTH.find((entry) => padded.includes(` ${entry.phrase} `))?.code ?? null
  }
  memo.set(key, code)
  return code
}

/** Where the flag image for a code lives. Only some codes ship a file; callers fall back when it fails to load. */
export const flagSrc = (code: string) => `/flags/${code}.svg`
