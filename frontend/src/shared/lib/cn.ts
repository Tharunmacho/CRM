import { clsx, type ClassValue } from 'clsx'
import { extendTailwindMerge } from 'tailwind-merge'

/**
 * tailwind-merge cannot tell a custom font size from a text colour — both are `text-*`.
 * Left to itself it treats `text-stat` or `text-micro` as a colour, so pairing one with
 * a real colour silently drops the size. Declaring the scale here keeps both.
 */
const twMerge = extendTailwindMerge({
  extend: {
    classGroups: {
      'font-size': [{ text: ['micro', 'lead', 'h3', 'h2', 'h1', 'stat'] }],
    },
  },
})

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs))
}
