import { Component, type ErrorInfo, type ReactNode } from "react";

interface Props {
  children: ReactNode;
  /** Shown in the fallback so the user knows which panel failed. */
  label?: string;
}

interface State {
  error: Error | null;
}

/**
 * Contains a render failure to one panel. The research reports are large
 * nested JSON documents read from disk; a partially written or older-schema
 * report used to throw during render and blank the whole application.
 */
export default class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error(`Render failed${this.props.label ? ` (${this.props.label})` : ""}`, error, info);
  }

  render() {
    if (this.state.error) {
      return (
        <div className="state error">
          <strong>{this.props.label ?? "This section"} could not be displayed.</strong>
          <code>{this.state.error.message.slice(0, 300)}</code>
          <button className="btn" onClick={() => this.setState({ error: null })}>
            Try again
          </button>
        </div>
      );
    }
    return this.props.children;
  }
}
