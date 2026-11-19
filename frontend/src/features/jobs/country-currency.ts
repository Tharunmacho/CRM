/**
 * The currency a country pays in, used as the DEFAULT for a new job's salary currency when a country is chosen.
 * Pure: the country-name -> ISO code lookup (`countryCode` in shared/lib/country-flag) is passed in, so this is unit tested without the UI.
 * It never decides on its own: the job form only applies a result that is in the agency's active currency list, and never over a currency the
 * person picked by hand or the one stored on an existing job.
 */

type CodeOf = (country: string | null | undefined) => string | null

/** One row per currency: the ISO 3166-1 alpha-2 codes of the countries and territories that use it. */
const TABLE = `AED:AE|AFN:AF|ALL:AL|AMD:AM|AOA:AO|ARS:AR|AWG:AW|AUD:AU CC CX HM KI NF NR TV|AZN:AZ|BAM:BA|BBD:BB|BDT:BD|BHD:BH|BIF:BI|BMD:BM|BND:BN|BOB:BO|BRL:BR|BSD:BS|BTN:BT|BWP:BW|BYN:BY|BZD:BZ|CAD:CA|CDF:CD|CHF:CH LI|CLP:CL|CNY:CN|COP:CO|CRC:CR|CUP:CU|CVE:CV|CZK:CZ|DJF:DJ|DKK:DK FO GL|DOP:DO|DZD:DZ|EGP:EG|ERN:ER|ETB:ET|`
  + `EUR:AD AT AX BE BG BL CY DE EE ES FI FR GF GP GR HR IE IT LT LU LV MC ME MF MQ MT NL PM PT RE SI SK SM TF VA XK YT|`
  + `FJD:FJ|GBP:GB GG IM JE GI FK SH GS|GEL:GE|GHS:GH|GMD:GM|GNF:GN|GTQ:GT|GYD:GY|HKD:HK|HNL:HN|HTG:HT|HUF:HU|IDR:ID|ILS:IL PS|INR:IN|IQD:IQ|IRR:IR|ISK:IS|JMD:JM|JOD:JO|JPY:JP|KES:KE|KGS:KG|KHR:KH|KMF:KM|KPW:KP|KRW:KR|KWD:KW|KYD:KY|KZT:KZ|`
  + `LAK:LA|LBP:LB|LKR:LK|LRD:LR|LSL:LS|LYD:LY|MAD:MA EH|MDL:MD|MGA:MG|MKD:MK|MMK:MM|MNT:MN|MOP:MO|MRU:MR|MUR:MU|MVR:MV|MWK:MW|MXN:MX|MYR:MY|MZN:MZ|NAD:NA|NGN:NG|NIO:NI|NOK:NO BV SJ|NPR:NP|NZD:NZ CK NU PN TK|OMR:OM|`
  + `PAB:PA|PEN:PE|PGK:PG|PHP:PH|PKR:PK|PLN:PL|PYG:PY|QAR:QA|RON:RO|RSD:RS|RUB:RU|RWF:RW|SAR:SA|SBD:SB|SCR:SC|SDG:SD|SEK:SE|SGD:SG|SLE:SL|SOS:SO|SRD:SR|SSP:SS|STN:ST|SYP:SY|SZL:SZ|THB:TH|TJS:TJ|TMT:TM|TND:TN|TOP:TO|TRY:TR|TTD:TT|TWD:TW|TZS:TZ|`
  + `UAH:UA|UGX:UG|USD:US AS BQ EC FM GU IO MH MP PR PW SV TC TL UM VG VI|UYU:UY|UZS:UZ|VES:VE|VND:VN|VUV:VU|WST:WS|XAF:CM CF CG GA GQ TD|XCD:AG AI DM GD KN LC MS VC|XCG:CW SX|XOF:BJ BF CI GW ML NE SN TG|XPF:NC PF WF|YER:YE|ZAR:ZA|ZMW:ZM|ZWG:ZW`

/** Lower-case ISO alpha-2 code -> currency code. */
export const CURRENCY_BY_COUNTRY_CODE: Readonly<Record<string, string>> = (() => {
  const map: Record<string, string> = {}
  for (const row of TABLE.split('|')) {
    const [currency, countries] = row.split(':')
    for (const code of countries.split(' ')) map[code.toLowerCase()] = currency
  }
  return map
})()

/** The currency a country (any spelling `codeOf` understands: "Malaysia", "UAE", "Dubai") pays in, or null when the country is not recognised. */
export function currencyForCountry(country: string | null | undefined, codeOf: CodeOf): string | null {
  const code = codeOf(country)
  return code ? CURRENCY_BY_COUNTRY_CODE[code.toLowerCase()] ?? null : null
}

/** What the currency select should switch to for this country: its currency when the list on offer has it, otherwise nothing (keep the current value). */
export function defaultCurrencyFor(country: string | null | undefined, codeOf: CodeOf, offered: readonly string[]): string | null {
  const code = currencyForCountry(country, codeOf)
  return code && offered.includes(code) ? code : null
}
