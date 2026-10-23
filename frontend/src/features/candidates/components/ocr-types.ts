/**
 * The Veris output stored on the candidate (GET /candidates/:id/ocr), typed after the
 * `veriis` package models. Kept in Veris's own snake_case, exactly as it arrived, and
 * every field is optional: no two documents come back with the same parts filled in.
 */

/** One OCR'd page. Per-line boxes are removed before storage. `page_number` is the page of the ORIGINAL file. */
export interface OcrPage {
  page_number?: number
  text?: string
  html?: string
  language?: string | null
  width?: number
  height?: number
  average_confidence?: number
  processing_time_ms?: number
  /** The page's position in the pages actually sent to Veris. */
  provider_page_number?: number
}

/** The local page classification report that decided what was sent to Veris. */
export interface LocalInspectionPage {
  page_number?: number
  status?: string
  document_type?: string
  method?: string
  confidence?: number
  reason?: string
}

export interface LocalInspection {
  source_format?: string
  page_count?: number
  pages?: LocalInspectionPage[]
  resume_pages?: number[]
  passport_pages?: number[]
  aadhaar_pages?: number[]
  unread_pages?: number[]
  unknown_pages?: number[]
  selected_pages?: number[]
  warnings?: string[]
  complete?: boolean
  provider_page_count?: number
}

/** Fields every stored Veris document carries beside its own details. */
export interface OcrDocumentBase {
  request_id?: string
  warnings?: string[]
  processing_time_ms?: number
  pages?: OcrPage[]
  local_inspection?: LocalInspection
  source_sha256?: string
}

export interface SocialLink {
  platform?: string
  url?: string
}

export interface ResumeOcr extends OcrDocumentBase {
  name?: string | null
  contact?: {
    emails?: string[]
    phones?: string[]
    linkedin?: string | null
    github?: string | null
    social?: SocialLink[]
    websites?: string[]
    address?: string | null
    whatsapp?: string | null
  }
  personal_info?: {
    date_of_birth?: string | null
    gender?: string | null
    father_name?: string | null
    mother_name?: string | null
    place_of_birth?: string | null
    nationality?: string | null
    religion?: string | null
    marital_status?: string | null
    languages_known?: string[]
  }
  passport_details?: {
    passport_number?: string | null
    date_of_issue?: string | null
    date_of_expiry?: string | null
    place_of_issue?: string | null
  }
  designation?: string | null
  summary?: string | null
  industry?: string | null
  highest_qualification?: string | null
  experience?: {
    title?: string | null
    company?: string | null
    location?: string | null
    country?: string | null
    is_overseas?: boolean
    start?: string | null
    end?: string | null
    duration_months?: number
    duration_human?: string
    description?: string | null
  }[]
  total_experience_months?: number
  total_experience_years?: number
  total_experience_human?: string
  indian_experience_months?: number
  indian_experience_years?: number
  indian_experience_human?: string
  overseas_experience_months?: number
  overseas_experience_years?: number
  overseas_experience_human?: string
  skills?: string[]
  certifications?: string[]
  achievements?: string[]
  projects?: { name?: string | null; description?: string | null }[]
  education?: { degree?: string | null; institution?: string | null; start?: string | null; end?: string | null; grade?: string | null }[]
}

export interface MrzCheckDigits {
  passport_number?: boolean | null
  date_of_birth?: boolean | null
  expiry_date?: boolean | null
  personal_number?: boolean | null
  composite?: boolean | null
}

export interface MrzData {
  document_type?: string | null
  issuing_country?: string | null
  surname?: string | null
  given_names?: string | null
  passport_number?: string | null
  nationality?: string | null
  date_of_birth?: string | null
  sex?: string | null
  expiry_date?: string | null
  date_of_issue?: string | null
  personal_number?: string | null
  all_check_digits_valid?: boolean
  individual_check_digits?: MrzCheckDigits | null
}

export interface ExtractedField {
  label?: string
  value?: string
  category?: string
  page?: number
  source?: string
  confidence?: number
}

export interface PassportOcr extends OcrDocumentBase {
  mrz?: MrzData | null
  mrz_source?: string
  raw_mrz?: string | null
  confidence?: number
  fields?: ExtractedField[]
}

export interface AadhaarData {
  name?: string | null
  aadhaar_number?: string | null
  masked_aadhaar_number?: string | null
  aadhaar_number_valid?: boolean | null
  date_of_birth?: string | null
  year_of_birth?: number | null
  gender?: string | null
  mobile_number?: string | null
  address?: string | null
  care_of?: string | null
  pincode?: string | null
  vid?: string | null
  enrollment_id?: string | null
  document_side?: string | null
}

export interface AadhaarOcr extends OcrDocumentBase {
  aadhaar?: AadhaarData | null
  page_count?: number
  document_type?: string
  languages_used?: string
  total_processing_time_ms?: number
}

export interface CandidateOcr {
  resume?: ResumeOcr
  passport?: PassportOcr
  aadhaar?: AadhaarOcr
  /** Further documents of a kind already stored: `[{ kind: payload, _source?: provenance }]`. */
  additional_documents?: Partial<Record<string, unknown>>[]
  /**
   * Where each document above came from (current storage shape): per kind the received
   * file, the local page classification, its hash and the original pages that were sent.
   */
  _sources?: Record<string, unknown>
  /** Provenance of a multi-person document that was split before reading. */
  person_split?: unknown
  [key: string]: unknown
}
