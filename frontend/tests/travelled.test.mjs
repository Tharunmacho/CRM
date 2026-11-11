import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { test } from 'node:test'
import ts from 'typescript'

async function load(relative) {
  const source = await readFile(new URL(relative, import.meta.url), 'utf8')
  const { outputText } = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext } })
  return import(`data:text/javascript;base64,${Buffer.from(outputText).toString('base64')}`)
}

const model = await load('../src/features/travelled/travelled-model.ts')
const {
  EMPTY_FILTERS, activeChips, activeQuick, agencyToday, barPercent, chooseTravelDate, clearChip, dateBarLabel, dateLevel, dateSummary,
  daysInMonth, documentsLabel, documentsTitle, drillTo, exportName, filtersFromParams, formatTravelDay,
  queryFor, quickRanges, setFilter, toggleFilter, toggleIncomplete, toggleQuick, toggleUndated, validIsoDate, withChosen,
} = model

const parse = (query) => filtersFromParams(new URLSearchParams(query))

test('an address becomes filters and back, in a fixed order', () => {
  const filters = parse('country=UAE&client=Century%20Mechanical&month=05&year=2026&agency=ETA&q=raj')
  assert.equal(filters.year, '2026')
  assert.equal(filters.month, '05')
  assert.equal(filters.client, 'Century Mechanical')
  assert.equal(queryFor(filters), 'year=2026&month=05&client=Century+Mechanical&agency=ETA&country=UAE&q=raj')
  assert.deepEqual(parse(queryFor(filters)), filters)
  assert.equal(queryFor(EMPTY_FILTERS), '')
})

test('a month without a year, or a day without a month, means nothing and is dropped', () => {
  assert.equal(parse('month=05').month, '')
  assert.equal(parse('year=2026&day=09').day, '')
  assert.equal(queryFor(parse('month=05&day=09')), '')
  assert.deepEqual([parse('year=2026&month=5&day=9').month, parse('year=2026&month=5&day=9').day], ['05', '09'])
})

test('impossible dates are not sent to the server', () => {
  assert.equal(parse('year=26').year, '')
  assert.equal(parse('year=2026&month=13').month, '')
  assert.equal(parse('year=2026&month=02&day=30').day, '')
  assert.equal(parse('year=2024&month=02&day=29').day, '29')
  assert.equal(parse('year=2026&month=02&day=29').day, '')
  assert.equal(parse('from=2026-02-31').from, '')
  assert.equal(parse('from=2026-05-09&to=2026-06-01').to, '2026-06-01')
  assert.equal(validIsoDate('2026-5-9'), '')
  assert.equal(validIsoDate('0000-05-09'), '')
  assert.equal(validIsoDate('2101-05-09'), '')
  assert.equal(daysInMonth(2024, 2), 29)
  assert.equal(daysInMonth(2026, 2), 28)
  assert.equal(daysInMonth(2026, 5), 31)
})

test('changing the year or month clears what depends on it, and a date part replaces a From / To range', () => {
  const start = parse('year=2026&month=05&day=09&client=A')
  assert.equal(queryFor(setFilter(start, 'year', '2025')), 'year=2025&client=A')
  assert.equal(queryFor(setFilter(start, 'month', '06')), 'year=2026&month=06&client=A')
  assert.equal(queryFor(setFilter(start, 'day', '10')), 'year=2026&month=05&day=10&client=A')
  assert.equal(queryFor(setFilter(start, 'year', '')), 'client=A')
  const ranged = parse('from=2026-05-01&to=2026-05-31')
  assert.equal(queryFor(setFilter(ranged, 'year', '2026')), 'year=2026')
  assert.equal(queryFor(setFilter(ranged, 'client', 'B')), 'from=2026-05-01&to=2026-05-31&client=B')
})

test('clicking a year, month and day bar walks down the date chain, keeping the other filters', () => {
  const all = parse('client=A')
  const year = drillTo(all, '2026')
  assert.equal(queryFor(year), 'year=2026&client=A')
  const month = drillTo(year, '2026-05')
  assert.equal(queryFor(month), 'year=2026&month=05&client=A')
  const day = drillTo(month, '2026-05-09')
  assert.equal(queryFor(day), 'year=2026&month=05&day=09&client=A')
  assert.equal(queryFor(drillTo(day, '2026')), 'year=2026&client=A')   // back up to the year
  assert.equal(queryFor(drillTo(day, '')), 'client=A')                  // back to every year
  assert.equal(queryFor(drillTo(parse('from=2026-01-01&to=2026-02-01'), '2026-05')), 'year=2026&month=05')
})

test('the breakdown shown follows how far the date is narrowed', () => {
  assert.equal(dateLevel(parse('')), 'year')
  assert.equal(dateLevel(parse('year=2026')), 'month')
  assert.equal(dateLevel(parse('year=2026&month=05')), 'day')
  assert.equal(dateLevel(parse('year=2026&month=05&day=09')), 'day')
})

test('clicking a client, agency or country chooses it, and clicking it again clears it', () => {
  const none = parse('year=2026')
  const chosen = toggleFilter(none, 'client', 'Century')
  assert.equal(queryFor(chosen), 'year=2026&client=Century')
  assert.equal(queryFor(toggleFilter(chosen, 'client', 'Century')), 'year=2026')
  assert.equal(queryFor(toggleFilter(chosen, 'client', 'Other')), 'year=2026&client=Other')
})

test('a job or order is bookmarked by identity and changing its client clears the old order', () => {
  const start = parse('client=Century&order=order-42&year=2026')
  assert.equal(queryFor(start), 'year=2026&client=Century&order=order-42')
  assert.equal(queryFor(setFilter(start, 'client', 'Arabian')), 'year=2026&client=Arabian')
  assert.equal(queryFor(toggleFilter(start, 'client', 'Arabian')), 'year=2026&client=Arabian')
  assert.equal(queryFor(setFilter(start, 'year', '2025')), 'year=2025&client=Century&order=order-42')
  assert.equal(queryFor(clearChip(start, 'order')), 'year=2026&client=Century')
  assert.equal(activeChips(start).find(chip => chip.key === 'order').label, 'Selected job / order')
})

test('the date in words, the chips and removing a chip', () => {
  assert.equal(dateSummary(parse('year=2026')), '2026')
  assert.equal(dateSummary(parse('year=2026&month=05')), 'May 2026')
  assert.equal(dateSummary(parse('year=2026&month=05&day=09')), '9 May 2026')
  assert.equal(dateSummary(parse('from=2026-05-01&to=2026-05-31')), '01 May 2026 to 31 May 2026')
  assert.equal(dateSummary(parse('')), '')
  const filters = parse('year=2026&month=05&client=Century&q=ali')
  assert.deepEqual(activeChips(filters).map((chip) => [chip.key, chip.label]), [['date', 'May 2026'], ['client', 'Century'], ['q', '"ali"']])
  assert.equal(queryFor(clearChip(filters, 'date')), 'client=Century&q=ali')
  assert.equal(queryFor(clearChip(filters, 'client')), 'year=2026&month=05&q=ali')
  assert.deepEqual(activeChips(EMPTY_FILTERS), [])
})

test('dates are written the same way everywhere and never shift with the browser time zone', () => {
  assert.equal(formatTravelDay('2026-05-09'), '09 May 2026')
  assert.equal(formatTravelDay('2026-06-01'), '01 Jun 2026')
  assert.equal(formatTravelDay(null), '')
  assert.equal(formatTravelDay('not a date'), '')
  assert.equal(dateBarLabel('year', '2026'), '2026')
  assert.equal(dateBarLabel('month', '2026-05'), 'May')
  assert.equal(dateBarLabel('day', '2026-05-09'), 'Sat 9')   // 9 May 2026 is a Saturday
  assert.equal(dateBarLabel('day', '2026-06-01'), 'Mon 1')
})

test('bar widths, picker lists and file names', () => {
  assert.equal(barPercent(10, 10), 100)
  assert.equal(barPercent(5, 10), 50)
  assert.equal(barPercent(1, 1000), 4)     // a real count is never invisible
  assert.equal(barPercent(0, 10), 0)
  assert.equal(barPercent(3, 0), 0)
  assert.deepEqual(withChosen(['A', 'B'], 'B'), ['A', 'B'])
  assert.deepEqual(withChosen(['A', 'B'], 'Typed by hand'), ['Typed by hand', 'A', 'B'])
  assert.deepEqual(withChosen(['A'], ''), ['A'])
  assert.equal(exportName(parse('year=2026&month=05'), 'xlsx'), 'travelled-2026-05.xlsx')
  assert.equal(exportName(parse(''), 'csv'), 'travelled-all.csv')
})

test('a full travel date works without selecting a year or month first and keeps unrelated filters', () => {
  const start = parse('client=A&order=42&country=UAE&agency=Agency&q=alex&incomplete=true')
  const selected = chooseTravelDate(start, '2026-06-01')
  assert.equal(queryFor(selected), 'year=2026&month=06&day=01&client=A&order=42&agency=Agency&country=UAE&q=alex&incomplete=true')
  assert.equal(queryFor(chooseTravelDate(EMPTY_FILTERS, '1990-01-01')), 'year=1990&month=01&day=01')
  assert.equal(queryFor(chooseTravelDate(EMPTY_FILTERS, '2100-12-31')), 'year=2100&month=12&day=31')
  assert.equal(queryFor(chooseTravelDate(parse('from=2026-05-01&to=2026-05-31&client=A'), '2026-06-01')), 'year=2026&month=06&day=01&client=A')
  assert.equal(queryFor(chooseTravelDate(parse('undated=true&client=A'), '2026-06-01')), 'year=2026&month=06&day=01&client=A')
})

test('clearing a travel date retains its month and year; clearing the month retains its year', () => {
  const selected = chooseTravelDate(parse('client=A'), '2026-06-01')
  const month = chooseTravelDate(selected, '')
  assert.equal(queryFor(month), 'year=2026&month=06&client=A')
  assert.equal(queryFor(setFilter(month, 'month', '')), 'year=2026&client=A')
  assert.equal(queryFor(setFilter(selected, 'month', '')), 'year=2026&client=A')
  assert.equal(queryFor(setFilter(selected, 'year', '2025')), 'year=2025&client=A')
  const range = parse('from=2026-09-01&to=2026-11-30&client=A')
  assert.deepEqual(chooseTravelDate(range, ''), range)
  assert.deepEqual(chooseTravelDate(EMPTY_FILTERS, ''), EMPTY_FILTERS)
})

test('invalid direct travel dates preserve the current view and leap dates are accepted only in leap years', () => {
  const selected = parse('year=2026&month=05&client=A')
  for (const date of ['2026-02-29', '2024-02-30', '2026-13-01', '1989-12-31', '2101-01-01', '2026-6-1', 'bad']) {
    assert.deepEqual(chooseTravelDate(selected, date), selected, date)
  }
  assert.equal(queryFor(chooseTravelDate(selected, '2024-02-29')), 'year=2024&month=02&day=29&client=A')
})

test('date not recorded toggles independently and replaces all date filters while preserving other filters', () => {
  const selected = parse('year=2026&month=05&day=09&client=A&order=42&incomplete=true')
  const undated = toggleUndated(selected)
  assert.equal(queryFor(undated), 'client=A&order=42&incomplete=true&undated=true')
  assert.equal(queryFor(toggleUndated(undated)), 'client=A&order=42&incomplete=true')
  assert.equal(queryFor(toggleUndated(parse('from=2026-09-01&to=2026-11-30&country=UAE'))), 'country=UAE&undated=true')
})

test('"date not recorded" and a date are opposites; a date in the address wins', () => {
  assert.equal(parse('undated=true').undated, 'true')
  assert.equal(parse('undated=yes').undated, '')
  assert.equal(parse('year=2026&undated=true').undated, '')
  assert.equal(parse('from=2026-05-01&undated=true').undated, '')
  assert.equal(parse('to=2026-05-31&undated=true').undated, '')
  const undated = parse('undated=true&client=A')
  assert.equal(queryFor(setFilter(undated, 'year', '2026')), 'year=2026&client=A')
  assert.equal(queryFor(drillTo(undated, '2026-05')), 'year=2026&month=05&client=A')
  assert.equal(dateSummary(undated), 'Date not recorded')
  assert.deepEqual(activeChips(undated).map((chip) => [chip.key, chip.label]), [['date', 'Date not recorded'], ['client', 'A']])
  assert.equal(queryFor(clearChip(undated, 'date')), 'client=A')
  assert.equal(exportName(undated, 'xlsx'), 'travelled-date-not-recorded.xlsx')
})

test('the quick ranges are whole calendar months and years', () => {
  const [thisMonth, lastMonth, lastThree, thisYear] = quickRanges('2026-11-11')
  assert.deepEqual([thisMonth.from, thisMonth.to], ['2026-11-01', '2026-11-30'])
  assert.deepEqual([lastMonth.from, lastMonth.to], ['2026-10-01', '2026-10-31'])
  assert.deepEqual([lastThree.from, lastThree.to], ['2026-09-01', '2026-11-30'])   // this month and the two before it
  assert.deepEqual([thisYear.from, thisYear.to], ['2026-01-01', '2026-12-31'])
  assert.deepEqual(quickRanges('2026-11-11').map((r) => r.label), ['This month', 'Last month', 'Last 3 months', 'This year'])
  const january = quickRanges('2026-01-15')                                           // crossing the new year
  assert.deepEqual([january[1].from, january[1].to], ['2025-12-01', '2025-12-31'])
  assert.deepEqual([january[2].from, january[2].to], ['2025-11-01', '2026-01-31'])
  const february = quickRanges('2024-02-10')                                          // a leap year
  assert.equal(february[0].to, '2024-02-29')
})

test('"today" is the agency\'s day (Asia/Kolkata), whatever the browser time zone is', () => {
  assert.equal(agencyToday(new Date('2026-11-10T20:00:00Z')), '2026-11-11')   // 01:30 the next morning in India
  assert.equal(agencyToday(new Date('2026-11-11T18:29:00Z')), '2026-11-11')
  assert.equal(agencyToday(new Date('2026-11-11T18:30:00Z')), '2026-11-12')   // midnight in India
})

test('calendar quick choices populate year and month, replace other dates and clear when clicked again', () => {
  const today = '2026-11-11'
  const on = toggleQuick(parse('year=2026&month=05&client=A'), 'last-month', today)
  assert.equal(queryFor(on), 'year=2026&month=10&client=A')
  assert.equal(activeQuick(on, today), 'last-month')
  assert.equal(queryFor(toggleQuick(on, 'last-month', today)), 'client=A')
  const year = toggleQuick(on, 'this-year', today)
  assert.equal(queryFor(year), 'year=2026&client=A')
  assert.equal(activeQuick(year, today), 'this-year')
  assert.equal(queryFor(toggleQuick(year, 'this-year', today)), 'client=A')
  const month = toggleQuick(parse('undated=true'), 'this-month', today)
  assert.equal(queryFor(month), 'year=2026&month=11')
  assert.equal(activeQuick(month, today), 'this-month')
  assert.equal(queryFor(setFilter(on, 'month', '')), 'year=2026&client=A')
  assert.equal(queryFor(setFilter(on, 'year', '2026')), 'year=2026&client=A')
  assert.equal(queryFor(toggleQuick(EMPTY_FILTERS, 'last-month', '2026-01-15')), 'year=2025&month=12')
})

test('quick choices, month selection and exact travel dates remain in sync', () => {
  const today = '2026-11-11'
  const year = toggleQuick(parse('client=A'), 'this-year', today)
  const month = setFilter(year, 'month', '11')
  assert.equal(activeQuick(month, today), 'this-month')
  const date = chooseTravelDate(month, '2026-11-09')
  assert.equal(queryFor(date), 'year=2026&month=11&day=09&client=A')
  assert.equal(activeQuick(date, today), '')
  assert.equal(activeQuick(chooseTravelDate(date, ''), today), 'this-month')
  assert.equal(queryFor(toggleQuick(date, 'last-month', today)), 'year=2026&month=10&client=A')
})

test('last three months remains a range and old quick-range bookmarks still activate and clear', () => {
  const today = '2026-11-11'
  const range = toggleQuick(parse('year=2026&month=05&day=09&client=A'), 'last-3-months', today)
  assert.equal(queryFor(range), 'from=2026-09-01&to=2026-11-30&client=A')
  assert.equal(activeQuick(range, today), 'last-3-months')
  assert.equal(queryFor(toggleQuick(range, 'last-3-months', today)), 'client=A')
  for (const quick of quickRanges(today)) {
    const legacy = parse(`from=${quick.from}&to=${quick.to}&client=A`)
    assert.equal(activeQuick(legacy, today), quick.key)
    assert.equal(queryFor(toggleQuick(legacy, quick.key, today)), 'client=A')
  }
  assert.equal(activeQuick(parse('from=2026-10-01&to=2026-10-30'), today), '')
  assert.equal(activeQuick(parse('year=2026&from=2026-10-01&to=2026-10-31'), today), '')
})

test('"missing documents only" is in the address and shown as a chip', () => {
  assert.equal(parse('incomplete=true').incomplete, 'true')
  assert.equal(parse('incomplete=yes').incomplete, '')
  assert.equal(queryFor(toggleIncomplete(parse('year=2026'))), 'year=2026&incomplete=true')
  assert.equal(queryFor(toggleIncomplete(parse('year=2026&incomplete=true'))), 'year=2026')
  assert.deepEqual(activeChips(parse('incomplete=true')).map((chip) => [chip.key, chip.label]), [['incomplete', 'Missing documents']])
  assert.equal(queryFor(clearChip(parse('incomplete=true&client=A'), 'incomplete')), 'client=A')
  assert.equal(queryFor(setFilter(parse('incomplete=true&year=2026'), 'year', '2025')), 'year=2025&incomplete=true')   // dates keep it
})

test('the Documents cell: a count, and what is missing when you point at it', () => {
  const some = { documents: { have: 6, required: 12, missing: ['Signed offer', 'Air ticket'] } }
  const all = { documents: { have: 12, required: 12, missing: [] } }
  assert.equal(documentsLabel(some), '6 / 12')
  assert.equal(documentsTitle(some), 'Missing: Signed offer, Air ticket')
  assert.equal(documentsTitle(all), 'All mandatory documents are on file')
  assert.equal(documentsLabel({}), '')
  assert.equal(documentsTitle({}), '')
})
