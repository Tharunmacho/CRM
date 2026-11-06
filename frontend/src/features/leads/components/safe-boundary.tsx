import { Component, type ErrorInfo, type ReactNode } from 'react'
import { AlertTriangle, RotateCcw } from 'lucide-react'

interface Props {
  /** What broke, in the person's words ("the lead table"). */
  what: string
  /** Changing this value (the list, the open lead) clears an earlier failure. */
  resetKey?: unknown
  /** Called by "Try again", e.g. to reload the list. */
  onRetry?: () => void
  children: ReactNode
}

/**
 * One bad row or value must never blank the page. A render error inside shows a short message with "Try again" and leaves
 * everything around it (key figures, filters, the other tab) working. The technical detail goes to the console only.
 */
export class SafeBoundary extends Component<Props, { failed: boolean; resetKey: unknown }> {
  state = { failed: false, resetKey: this.props.resetKey }

  static getDerivedStateFromError() { return { failed: true } }

  static getDerivedStateFromProps(props: Props, state: { failed: boolean; resetKey: unknown }) {
    return props.resetKey !== state.resetKey ? { failed: false, resetKey: props.resetKey } : null
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error(`Lead sheet: ${this.props.what} could not be drawn`, error, info.componentStack)
  }

  render() {
    if (!this.state.failed) return this.props.children
    return (
      <div role="alert" className="ld-crash">
        <AlertTriangle size={18} aria-hidden="true" />
        <div>
          <strong>We could not show {this.props.what}.</strong>
          <p>Something in the data did not display. Nothing was lost. Try again, or refresh the page.</p>
        </div>
        <button type="button" className="ld-btn" onClick={() => { this.setState({ failed: false }); this.props.onRetry?.() }}><RotateCcw size={14} aria-hidden="true" />Try again</button>
      </div>
    )
  }
}
