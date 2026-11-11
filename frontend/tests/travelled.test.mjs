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
  EMPTY_FILTERS, UNDATED, activeChips, activeQuick, agencyToday, barPercent, chooseMonth, clearChip, dateBarLabel, dateLevel, dateSummary,
  daysInMonth, documentsLabel, documentsTitle, drillTo, exportName, filtersFromParams, formatTravelDay, monthChoice, monthChoices,
  queryFor, quickRanges, setFilter, toggleFilter, toggleIncomplete, toggleQuick, validIsoDate, withChosen,
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

const MONTHS = [{ month: '2026-10', count: 3 }, { month: '2026-05', count: 4 }]

test('the Travel month select lists the months somebody travelled in, newest first, then "date not recorded"', () => {
  assert.deepEqual(monthChoices(MONTHS, parse(''), 2).map((m) => [m.value, m.label]), [
    ['2026-10', 'October 2026 · 3'], ['2026-05', 'May 2026 · 4'], [UNDATED, 'Date not recorded · 2']])
  assert.deepEqual(monthChoices(MONTHS, parse(''), 0).map((m) => m.value), ['2026-10', '2026-05'])     // no entry when nobody is undated
  assert.equal(monthChoice(parse('year=2026&month=10')), '2026-10')
  assert.equal(monthChoice(parse('year=2026')), '2026')
  assert.equal(monthChoice(parse('')), '')
  assert.equal(monthChoice(parse('undated=true')), UNDATED)
})

test('the select never lies: a chosen month or year the server does not list is still shown', () => {
  assert.deepEqual(monthChoices(MONTHS, parse('year=2025&month=12')).map((m) => m.label), ['December 2025', 'October 2026 · 3', 'May 2026 · 4'])
  assert.equal(monthChoices(MONTHS, parse('year=2026'))[0].label, 'All of 2026')
  assert.equal(monthChoices([], parse('undated=true'))[0].label, 'Date not recorded')
})

test('choosing a travel month sets year and month in one go, and clears the days, ranges and "date not recorded"', () => {
  const start = parse('year=2026&month=05&day=09&client=A')
  assert.equal(queryFor(chooseMonth(start, '2026-10')), 'year=2026&month=10&client=A')
  assert.equal(queryFor(chooseMonth(start, '2026')), 'year=2026&client=A')
  assert.equal(queryFor(chooseMonth(start, '')), 'client=A')
  assert.equal(queryFor(chooseMonth(parse('from=2026-05-01&to=2026-05-31'), '2026-10')), 'year=2026&month=10')
  assert.equal(queryFor(chooseMonth(parse('undated=true&client=A'), '2026-10')), 'year=2026&month=10&client=A')
  assert.equal(queryFor(chooseMonth(start, UNDATED)), 'client=A&undated=true')
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

test('a quick range sets From / To, shows as active, replaces other dates and clears when clicked again', () => {
  const today = '2026-11-11'
  const on = toggleQuick(parse('year=2026&month=05&client=A'), 'last-month', today)
  assert.equal(queryFor(on), 'from=2026-10-01&to=2026-10-31&client=A')
  assert.equal(activeQuick(on, today), 'last-month')
  assert.equal(activeQuick(parse('from=2026-10-01&to=2026-10-30'), today), '')        // not exactly a quick range
  assert.equal(activeQuick(parse('year=2026&from=2026-10-01&to=2026-10-31'), today), '')
  assert.equal(queryFor(toggleQuick(on, 'last-month', today)), 'client=A')
  assert.equal(queryFor(toggleQuick(on, 'this-year', today)), 'from=2026-01-01&to=2026-12-31&client=A')
  assert.equal(queryFor(toggleQuick(parse('undated=true'), 'this-month', today)), 'from=2026-11-01&to=2026-11-30')
  assert.equal(queryFor(setFilter(on, 'month', '')), 'client=A')   // another date filter takes the range away (month needs a year: dropped)
  assert.equal(queryFor(setFilter(on, 'year', '2026')), 'year=2026&client=A')
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
