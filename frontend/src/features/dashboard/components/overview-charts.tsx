import { useState, type ReactNode } from 'react'
import { ArrowDown, ArrowRight, ArrowUp, type LucideIcon } from 'lucide-react'
import { SOURCE_META } from '@/features/dashboard/components/identity'
import type { Point } from '@/features/dashboard/metrics'

export const number = (value: number) => value.toLocaleString()

export function Trend({ percent }: { percent: number | null }) {
  if (percent === null) return <span className="dr-hint">Nothing earlier to compare with</span>
  const down = percent < 0
  return <span className={`dr-trend${down ? ' is-down' : ''}`}>{Math.abs(percent).toFixed(1)}%{down ? <ArrowDown size={11} /> : <ArrowUp size={11} />}<em>than the period before</em></span>
}

/** Small SVG chart: area, line, step or bars. No hover tooltip library; each point has a hit area and a title. */
export function MiniChart({ points, label, kind = 'line', max, unit = '', empty = 'No activity in this period' }: {
  points: Point[]; label: string; kind?: 'line' | 'area' | 'step' | 'bars'; max?: number; unit?: string; empty?: string
}) {
  const [hovered, setHovered] = useState<number | null>(null)
  const peak = Math.max(...points.map(point => point.value), 0)
  const ceiling = max || Math.max(4, Math.ceil(peak / 4) * 4)
  const step = 198 / Math.max(1, points.length - 1)
  const x = (index: number) => 32 + index * step
  const y = (value: number) => 126 - value / ceiling * 104
  const line = points.map((point, index) => `${index ? 'L' : 'M'}${x(index)},${y(point.value)}`).join(' ')
  const stepped = points.map((point, index) => index ? `H${x(index)} V${y(point.value)}` : `M${x(index)},${y(point.value)}`).join(' ')
  const hasData = points.some(point => point.value > 0)
  const selected = hovered ?? (hasData ? Math.max(0, points.length - 2) : null)
  const barWidth = Math.min(18, step * 0.62)
  const every = points.length > 8 ? 2 : 1
  return <div className="dr-chart">
    <svg viewBox="0 0 242 156" role="img" aria-label={`${label}. ${points.map(point => `${point.label}: ${point.value}${unit}`).join(', ')}`}>
      {[0, 1, 2, 3, 4].map(tick => <g key={tick}><line x1="32" x2="230" y1={126 - tick * 26} y2={126 - tick * 26} className="dr-grid" /><text x="24" y={130 - tick * 26} textAnchor="end" className="dr-axis">{Math.round(ceiling * tick / 4)}{unit}</text></g>)}
      {points.map((point, index) => <g key={index}>{kind !== 'bars' && <line x1={x(index)} x2={x(index)} y1="22" y2="126" className="dr-grid" />}{(index % every === 0 || index === points.length - 1) && <text x={x(index)} y="147" textAnchor="middle" className="dr-axis">{point.label}</text>}</g>)}
      {kind === 'area' && <path d={`${line} L230,126 L32,126 Z`} className="dr-area" />}
      {kind === 'bars'
        ? points.map((point, index) => <rect key={index} x={x(index) - barWidth / 2} y={y(point.value)} width={barWidth} height={Math.max(0, 126 - y(point.value))} rx="3" className={`dr-bar${selected === index ? ' is-active' : ''}`} />)
        : <path d={kind === 'step' ? stepped : line} className="dr-line" />}
      {selected !== null && hasData && kind !== 'bars' && <g><line x1={x(selected)} x2={x(selected)} y1="18" y2="126" className="dr-cursor" /><circle cx={x(selected)} cy={y(points[selected].value)} r="4.5" className="dr-dot" /></g>}
      {selected !== null && hasData && hovered !== null && <g><rect x={Math.min(190, Math.max(32, x(selected) - 20))} y={Math.max(2, y(points[selected].value) - 28)} width="40" height="21" rx="5" className="dr-tip" /><text x={Math.min(210, Math.max(52, x(selected)))} y={Math.max(16, y(points[selected].value) - 14)} textAnchor="middle" className="dr-tip-text">{points[selected].value}{unit}</text></g>}
      {points.map((point, index) => <rect key={`hit-${index}`} x={x(index) - Math.max(6, step / 2)} y="16" width={Math.max(12, step)} height="114" fill="transparent" onMouseEnter={() => setHovered(index)} onMouseLeave={() => setHovered(null)}><title>{point.label}: {point.value}{unit}</title></rect>)}
      {!hasData && <text x="132" y="79" textAnchor="middle" className="dr-nodata">{empty}</text>}
    </svg>
  </div>
}

export function Distribution({ items }: { items: { key: string; label: string; value: number }[] }) {
  const total = items.reduce((sum, item) => sum + item.value, 0)
  return <div className="dr-dist">
    <div className="dr-dist-blocks" role="img" aria-label={items.map(item => `${item.label}: ${item.value}`).join(', ')}>
      {total ? items.filter(item => item.value > 0).map(item => <div key={item.key} className={`dr-fill-${item.key}`} style={{ flex: Math.max(0.08, item.value / total) }} title={`${item.label}: ${item.value}`} />) : <div className="dr-dist-empty">No candidates in this period</div>}
    </div>
    <ul className="dr-legend">{items.map(item => { const Icon = SOURCE_META[item.key]?.icon; return <li key={item.key} className={`dr-tone-${item.key}`}>{Icon && <Icon size={13} aria-hidden="true" />}<span>{item.label}</span><strong>{number(item.value)}</strong></li> })}</ul>
  </div>
}

/** KPI card with an accent line on top and a tinted icon circle: label, number, caption, chart, then a titled link. */
export function Metric({ label, value, hint, icon: Icon, title, description, linkLabel, onClick, children }: {
  label: string; value: string; hint?: ReactNode; icon: LucideIcon; title: string; description: string; linkLabel: string; onClick: () => void; children: ReactNode
}) {
  return <article className="dr-metric">
    <div className="dr-metric-head">
      <div className="dr-metric-top"><p className="dr-metric-label">{label}</p><span className="dr-icon-circle" aria-hidden="true"><Icon size={17} strokeWidth={1.9} /></span></div>
      <div className="dr-metric-reading"><strong>{value}</strong>{hint}</div>
    </div>
    <div className="dr-metric-visual">{children}</div>
    <div className="dr-metric-foot">
      <div><h3>{title}</h3><p>{description}</p></div>
      <button type="button" className="dr-metric-link" onClick={onClick}>{linkLabel}<ArrowRight size={13} /></button>
    </div>
  </article>
}
