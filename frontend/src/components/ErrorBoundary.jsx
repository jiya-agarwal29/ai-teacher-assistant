import { Component } from 'react';

export default class ErrorBoundary extends Component {
  constructor(props) {
    super(props);
    this.state = { hasError: false };
  }

  static getDerivedStateFromError() {
    return { hasError: true };
  }

  componentDidCatch(error, errorInfo) {
    console.error('Unhandled error in app:', error, errorInfo);
  }

  render() {
    if (this.state.hasError) {
      return (
        <div className="min-h-screen flex items-center justify-center bg-cyan-100 dark:bg-slate-950 text-slate-700 dark:text-slate-300 p-6">
          <div className="max-w-md w-full text-center space-y-4 bg-white dark:bg-slate-900 border border-slate-200 dark:border-slate-800 rounded-2xl p-8 shadow-sm">
            <h1 className="text-lg font-bold text-slate-900 dark:text-white">Something went wrong</h1>
            <p className="text-sm text-slate-500 dark:text-slate-400">
              The app hit an unexpected error. If the backend server isn't running, start it and reload the page.
            </p>
            <button
              onClick={() => window.location.reload()}
              className="px-5 py-2.5 rounded-full bg-indigo-500 hover:bg-indigo-600 text-white text-sm font-semibold transition-colors"
            >
              Reload
            </button>
          </div>
        </div>
      );
    }

    return this.props.children;
  }
}
