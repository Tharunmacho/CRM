import { useState } from 'react'
import { Globe2 } from 'lucide-react'
import { countryCode, flagSrc } from '@/shared/lib/country-flag'

/** Codes whose /flags/<code>.svg failed to load; remembered so a missing file is asked for once, not on every row. */
const missing = new Set<string>()

interface CountryFlagProps {
  country?: string | null
  /** Class of the flag image. */
  className?: string
  /** Class of the neutral globe shown when the country is unknown or has no flag file. */
  globeClassName?: string
  width?: number
  height?: number
  globeSize?: number
  title?: string
}

/** The country's flag when /flags/<code>.svg exists, otherwise a neutral globe. Never an emoji. */
export function CountryFlag({ country, className, globeClassName, width = 20, height = 14, globeSize = 15, title }: CountryFlagProps) {
  const [, retry] = useState(0)
  const code = countryCode(country)
  if (code && !missing.has(code)) {
    return <img src={flagSrc(code)} alt="" width={width} height={height} className={className} loading="lazy" title={title}
      onError={() => { missing.add(code); retry((n) => n + 1) }} />
  }
  return <Globe2 size={globeSize} aria-hidden="true" className={globeClassName}>{title ? <title>{title}</title> : null}</Globe2>
}
