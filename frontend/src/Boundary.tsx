import { Component, type ReactNode } from 'react'

/** Keeps one broken panel from blanking the whole app (the header and its safety controls stay up). */
export class Boundary extends Component<{ name: string; children: ReactNode }, { error?: Error }> {
  state: { error?: Error } = {}
  static getDerivedStateFromError(error: Error) {
    return { error }
  }
  render() {
    if (!this.state.error) return this.props.children
    return (
      <section className="pad error-text">
        {this.props.name} failed: {this.state.error.message}{' '}
        <button onClick={() => this.setState({ error: undefined })}>Retry</button>
      </section>
    )
  }
}
