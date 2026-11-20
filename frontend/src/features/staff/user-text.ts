/** Plain text rules of the Users screen, kept free of React so they are easy to test. */

/** The server's own sentence; the technical "phone: Value error, " lead-in of a form check is dropped so the person reads plain words. */
export function plainServerText(message: string): string {
  return message.replace(/(^|; )(?:[\w.]+: )?Value error, /g, '$1')
}

/**
 * One CSV cell. A name or email that starts like a spreadsheet formula (= @ + - tab) gets a leading apostrophe so it never runs when the
 * file is opened; a phone number keeps its leading + or -. Cells with a comma, quote or line break are quoted.
 */
export function csvCell(value: string | number | null | undefined): string {
  let text = String(value ?? '')
  if (/^[=@+\-\t\r]/.test(text) && !/^[+-]?[\d\s().-]+$/.test(text)) text = "'" + text
  return /[",\r\n]/.test(text) ? '"' + text.replaceAll('"', '""') + '"' : text
}
