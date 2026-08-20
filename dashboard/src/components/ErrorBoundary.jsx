import { Component } from 'react';
import HelpContactLink from './HelpContactLink';

// No page in this app had any error boundary — a single uncaught render error
// (bad/unexpected API data, a null field, etc.) unmounts the whole React tree,
// which is what a "blank page" actually is. This is the last line of defense:
// it never changes normal behavior, it only stops one bad page from taking
// down the whole app.
export default class ErrorBoundary extends Component {
  constructor(props) {
    super(props);
    this.state = { error: null };
  }

  static getDerivedStateFromError(error) {
    return { error };
  }

  componentDidCatch(error, info) {
    console.error('[ErrorBoundary] Caught render error:', error, info?.componentStack);
  }

  render() {
    if (this.state.error) {
      return (
        <div style={{ padding: '48px', textAlign: 'center' }}>
          <div style={{ fontSize: '15px', fontWeight: 600, marginBottom: '8px' }}>Something went wrong loading this page.</div>
          <div style={{ fontSize: '13px', color: 'var(--text-muted)', marginBottom: '16px' }}>
            {this.state.error.message || 'Unexpected error'}
          </div>
          <button
            onClick={() => { this.setState({ error: null }); window.location.reload(); }}
            style={{ padding: '8px 16px', fontSize: '13px', borderRadius: '4px', border: '1px solid var(--border)', background: 'var(--bg-primary)', cursor: 'pointer' }}
          >
            Reload
          </button>
          <div style={{ marginTop: '16px', fontSize: '12px', color: 'var(--text-muted)' }}>
            Still stuck? <HelpContactLink variant="inline" context="App render error" label="Email us" />
          </div>
        </div>
      );
    }
    return this.props.children;
  }
}
