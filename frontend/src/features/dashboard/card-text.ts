/**
 * The spoken name and tooltip of a Dashboard card. The card itself shows only the icon, the label and the number; whatever used to be
 * written under it ("3 need review", "84 overdue") lives here, so a screen reader and the hover tooltip lose nothing.
 * "Tasks", "87", "84 overdue" -> "Tasks: 87, 84 overdue".
 */
export function cardSpeech(label: string, value: string, ...notes: (string | null | undefined | false)[]): string {
  const extra = notes.map(note => (note ? note.trim() : '')).filter(Boolean)
  return extra.length ? `${label}: ${value}, ${extra.join(', ')}` : `${label}: ${value}`
}
