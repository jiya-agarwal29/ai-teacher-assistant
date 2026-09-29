import { useState, useEffect } from 'react';
import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom';
import { AuthProvider } from './hooks/AuthProvider';
import ProtectedRoute from './components/ProtectedRoute';
import PageLayout from './components/PageLayout';
import ErrorBoundary from './components/ErrorBoundary';

// Pages
import Login from './pages/Login';
import Home from './pages/Home';
import Chat from './pages/Chat';
import Documents from './pages/Documents';
import AITools from './pages/AITools';
import Analytics from './pages/Analytics';

export default function App() {
  // Sync global dark mode preference
  const [darkMode, setDarkMode] = useState(() => {
    const saved = localStorage.getItem('theme');
    if (saved) return saved === 'dark';
    // Fallback to system settings
    return window.matchMedia('(prefers-color-scheme: dark)').matches;
  });

  useEffect(() => {
    const root = document.documentElement;
    if (darkMode) {
      root.classList.add('dark');
      localStorage.setItem('theme', 'dark');
    } else {
      root.classList.remove('dark');
      localStorage.setItem('theme', 'light');
    }
  }, [darkMode]);

  return (
    <ErrorBoundary>
      <AuthProvider>
        <BrowserRouter>
          <Routes>
            {/* Public Auth Route */}
            <Route path="/login" element={<Login />} />

            {/* Protected Main SaaS Routes */}
            <Route
              element={
                <ProtectedRoute>
                  <PageLayout darkMode={darkMode} setDarkMode={setDarkMode} />
                </ProtectedRoute>
              }
            >
              <Route path="/" element={<Home />} />
              <Route path="/chat" element={<Chat />} />
              <Route path="/documents" element={<Documents />} />
              <Route path="/tools" element={<AITools />} />
              <Route path="/analytics" element={<Analytics />} />
            </Route>

            {/* Fallback Catch-All Redirect */}
            <Route path="*" element={<Navigate to="/" replace />} />
          </Routes>
        </BrowserRouter>
      </AuthProvider>
    </ErrorBoundary>
  );
}