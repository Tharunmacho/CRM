/* The only file that imports recharts: it is loaded on demand (report-charts.tsx) so the library stays in its own chunk. */
import { useId } from 'react'
import { Area, AreaChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'

/** Chart ink comes from the theme tokens, so it follows light and dark. */
const AXIS = { fontSize: 12, fill: 'var(--text-muted)', fontFamily: 'Poppins, system-ui, sans-serif' }
const GRID = 'var(--border-main)'

function TooltipBox({ active, payload, label, unit }: { active?: boolean; payload?: { name?: string; value?: number | string; color?: string; stroke?: string }[]; label?: string | number; unit?: string }) {
  if (!active || !payload?.length) return null
  return <div className="rv-tooltip">
    <p>{label}</p>
    {payload.map(item => <div key={item.name}><i style={{ background: item.stroke || item.color }} /><span>{item.name}</span><b className="tnum">{typeof item.value === 'number' ? item.value.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 }) : item.value}{unit ? ` ${unit}` : ''}</b></div>)}
  </div>
}

/** A step line over a soft area: money collected per month. Each currency gets its own chart, never mixed. */
export function StepArea({ data, unit, label, height = 260, colour = 'var(--rv-c1)' }: { data: { name: string; amount: number }[]; unit: string; label: string; height?: number; colour?: string }) {
  const id = useId().replace(/:/g, '')
  return <div style={{ height }} className="rv-plot" role="img" aria-label={`${label}. ${data.map(row => `${row.name}: ${row.amount.toLocaleString()} ${unit}`).join(', ')}`}>
    <ResponsiveContainer width="100%" height="100%">
      <AreaChart data={data} margin={{ top: 14, right: 16, bottom: 4, left: 4 }}>
        <defs><linearGradient id={`step-${id}`} x1="0" y1="0" x2="0" y2="1"><stop offset="0" stopColor={colour} stopOpacity={0.3} /><stop offset="1" stopColor={colour} stopOpacity={0} /></linearGradient></defs>
        <CartesianGrid vertical={false} stroke={GRID} strokeDasharray="2 5" />
        <XAxis dataKey="name" tick={AXIS} axisLine={false} tickLine={false} interval="preserveStartEnd" minTickGap={24} />
        <YAxis tick={AXIS} axisLine={false} tickLine={false} width={70} tickFormatter={(value: number) => value.toLocaleString()} />
        <Tooltip content={<TooltipBox unit={unit} />} cursor={{ stroke: colour, strokeOpacity: 0.35, strokeDasharray: '3 4' }} />
        <Area type="stepAfter" dataKey="amount" name="Collected" stroke={colour} strokeWidth={2.5} fill={`url(#step-${id})`} dot={{ r: 3.5, fill: 'var(--bg-card)', stroke: colour, strokeWidth: 2 }} activeDot={{ r: 5.5, fill: colour, stroke: 'var(--bg-card)', strokeWidth: 2 }} isAnimationActive animationDuration={900} />
      </AreaChart>
    </ResponsiveContainer>
  </div>
}
