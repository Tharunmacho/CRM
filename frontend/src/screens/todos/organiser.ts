export const sections = {
  meetings: { label: 'Meetings & appointments', title: 'Meeting with', when: 'Meeting date and time', fields: ['Purpose', 'Location or meeting link'] },
  interviews: { label: 'Interviews', title: 'Job title', when: 'Interview date and time', fields: ['Country', 'Client or agency', 'Number of candidates'] },
  calls: { label: 'Calls & follow-ups', title: 'Contact name', when: 'Follow-up date and time', fields: ['Country', 'Purpose'] },
  recruitment: { label: 'Recruitment activities', title: 'Activity', when: 'Date and time', fields: ['Target', 'Completed', 'Pending'] },
  payables: { label: 'Payables', title: 'Pay to', when: 'Due date and time', fields: ['Purpose', 'Amount', 'Currency'] },
  receivables: { label: 'Receivables', title: 'Receive from', when: 'Expected date and time', fields: ['Purpose', 'Amount', 'Currency'] },
  deployment: { label: 'Immigration & deployment', title: 'Candidate or worker', when: 'Follow-up date and time', fields: ['Country', 'Process', 'Current status', 'Next action'] },
  reminders: { label: 'Reminders', title: 'Task', when: 'Due date and time', fields: ['Responsible staff'] },
  review: { label: 'End-of-day review', title: 'Review title', when: 'Review date and time', fields: ['Work completed', 'Pending matters', 'Payments due', "Tomorrow's appointments", "Tomorrow's priorities", 'Management remarks', 'Reviewed by'] },
} as const

export type SectionKey = keyof typeof sections
export const sectionKeys = Object.keys(sections) as SectionKey[]
export const sectionKey = (key?: string | null): SectionKey =>
  sectionKeys.find(value => value === key) ?? 'reminders'
