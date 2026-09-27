import React, { useState, useEffect, useRef } from 'react';
import { useNavigate, useLocation } from 'react-router-dom';
import { useAuth } from '../hooks/useAuth';
import {
  Lock, User, GraduationCap, AlertCircle, CheckCircle2, Moon, Sun,
  Plus, ShieldCheck, BookOpenCheck, Sparkle
} from 'lucide-react';

export default function Login() {
  const { login, register, error, clearError, isLoading, isAuthenticated } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();

  const [isLoginTab, setIsLoginTab] = useState(true);
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [confirmPassword, setConfirmPassword] = useState('');
  const [validationError, setValidationError] = useState('');
  const [successMessage, setSuccessMessage] = useState('');

  const formRef = useRef(null);
  const usernameRef = useRef(null);

  // Theme state specifically for the login screen (standalone or synced)
  const [darkMode, setDarkMode] = useState(() => {
    return document.documentElement.classList.contains('dark');
  });

  const from = location.state?.from?.pathname || '/';

  // Toggle theme
  useEffect(() => {
    if (darkMode) {
      document.documentElement.classList.add('dark');
    } else {
      document.documentElement.classList.remove('dark');
    }
  }, [darkMode]);

  // Clear errors when toggling tabs
  useEffect(() => {
    clearError();
    setValidationError('');
    setSuccessMessage('');
  }, [isLoginTab]);

  // If already authenticated, redirect immediately
  useEffect(() => {
    if (isAuthenticated) {
      navigate(from, { replace: true });
    }
  }, [isAuthenticated, navigate, from]);

  const handleSubmit = async (e) => {
    e.preventDefault();
    setValidationError('');
    setSuccessMessage('');
    clearError();

    if (!username.trim() || !password.trim()) {
      setValidationError('Please fill in all fields.');
      return;
    }

    if (!isLoginTab) {
      if (password.length < 8) {
        setValidationError('Password must be at least 8 characters long.');
        return;
      }

      if (password !== confirmPassword) {
        setValidationError('Passwords do not match.');
        return;
      }
    }

    if (isLoginTab) {
      const success = await login(username, password);
      if (success) {
        setSuccessMessage('Logged in successfully!');
        setTimeout(() => {
          navigate(from, { replace: true });
        }, 800);
      }
    } else {
      const success = await register(username, password);
      if (success) {
        setSuccessMessage('Account created and logged in!');
        setTimeout(() => {
          navigate(from, { replace: true });
        }, 800);
      }
    }
  };

  const handleHeroCta = () => {
    setIsLoginTab(false);
    formRef.current?.scrollIntoView({ behavior: 'smooth', block: 'center' });
    setTimeout(() => usernameRef.current?.focus(), 350);
  };

  const trustCards = [
    {
      variant: 'dark',
      icon: ShieldCheck,
      title: 'Local-first',
      text: 'Your documents and questions are processed on this machine. Nothing is sent to a third-party AI API.',
    },
    {
      variant: 'quote',
      icon: Sparkle,
      quote: 'The best answer is the one your own material actually contains.',
      caption: 'Our approach to grounding',
    },
    {
      variant: 'feature',
      icon: BookOpenCheck,
      title: 'Grounded quizzes',
      text: 'Quizzes are generated from what you upload, not the open internet — with the source page cited.',
    },
  ];

  return (
    <div className="min-h-screen flex flex-col bg-cyan-100 dark:bg-slate-950 text-slate-700 dark:text-slate-300 theme-transition">
      {/* Slim utility bar */}
      <div className="w-full bg-[#1A1A1A] text-slate-300 text-[11px] tracking-wide">
        <div className="max-w-7xl mx-auto px-6 lg:px-12 py-2 flex items-center justify-between">
          <span>Runs locally · Grounded in your material</span>
          <span className="hidden sm:inline text-slate-500">No data leaves your machine</span>
        </div>
      </div>

      {/* Nav */}
      <div className="w-full px-6 lg:px-12 py-5 flex items-center justify-between max-w-7xl mx-auto">
        <div className="flex items-center gap-3">
          <div className="w-9 h-9 rounded-full bg-slate-900 dark:bg-indigo-500 flex items-center justify-center text-white">
            <GraduationCap size={18} strokeWidth={2.2} />
          </div>
          <span className="font-serif text-lg tracking-tight text-slate-900 dark:text-white">AI Teacher Assistant</span>
        </div>

        <button
          onClick={() => setDarkMode(!darkMode)}
          className="w-10 h-10 rounded-full bg-slate-50 dark:bg-slate-900 border border-slate-200 dark:border-slate-800 flex items-center justify-center text-slate-600 dark:text-slate-300 hover:border-slate-300 dark:hover:border-slate-700 transition-colors"
        >
          {darkMode ? <Sun size={16} className="text-amber-500" /> : <Moon size={16} className="text-slate-700" />}
        </button>
      </div>

      {/* Hero */}
      <div className="flex-1 w-full max-w-7xl mx-auto px-6 lg:px-12 py-8 lg:py-14 grid lg:grid-cols-2 gap-14 items-center">
        {/* Left — headline + CTA + stat */}
        <div>
          <span className="inline-block text-[11px] font-semibold tracking-[0.18em] uppercase text-violet-700 dark:text-violet-300 mb-5">
            For Teachers, Not Benchmarks
          </span>

          <h1 className="font-serif text-4xl xl:text-[3.4rem] leading-[1.08] tracking-tight text-slate-900 dark:text-white mb-6 text-balance">
            Prepare Your Classroom With Less Noise
          </h1>

          <p className="text-sm leading-relaxed text-slate-600 dark:text-slate-400 max-w-md mb-8">
            Upload your material once. Get answers, quizzes, and search that stay grounded in what you actually teach — nothing else.
          </p>

          <button
            onClick={handleHeroCta}
            className="inline-flex items-center gap-3 pl-6 pr-2 py-2 rounded-full bg-indigo-500 hover:bg-indigo-600 text-white text-sm font-semibold tracking-tight transition-colors shadow-sm"
          >
            Create Free Account
            <span className="w-7 h-7 rounded-full bg-white/20 flex items-center justify-center">
              <Plus size={14} strokeWidth={2.5} />
            </span>
          </button>

          {/* Stat callout */}
          <div className="mt-14 max-w-xs">
            <div className="w-10 h-px bg-slate-300 dark:bg-slate-700 mb-3"></div>
            <div className="flex items-baseline gap-2">
              <span className="font-serif text-4xl text-slate-900 dark:text-white">6+</span>
              <span className="font-serif text-lg text-slate-500 dark:text-slate-400">hrs / week</span>
            </div>
            <p className="text-xs text-slate-500 dark:text-slate-500 mt-1">
              Typical lesson-prep time saved once material is uploaded*
            </p>
          </div>
        </div>

        {/* Right — auth card */}
        <div ref={formRef} className="w-full max-w-sm mx-auto lg:mx-0 lg:ml-auto">
          <div className="bg-slate-50 dark:bg-slate-900 border border-slate-200 dark:border-slate-800 rounded-2xl p-7 shadow-sm">
            <h2 className="font-serif text-xl text-slate-900 dark:text-white mb-1">
              {isLoginTab ? 'Welcome back' : 'Create your account'}
            </h2>
            <p className="text-xs text-slate-500 dark:text-slate-400 mb-6">
              {isLoginTab ? 'Sign in to pick up where you left off.' : 'A few seconds and you are in.'}
            </p>

            {/* Tabs */}
            <div className="flex bg-slate-100 dark:bg-slate-950 p-1 rounded-full mb-6 border border-slate-200/70 dark:border-slate-800">
              <button
                type="button"
                onClick={() => setIsLoginTab(true)}
                className={`flex-1 py-2 rounded-full text-xs font-semibold tracking-tight transition-all duration-200 ${
                  isLoginTab
                    ? 'bg-white dark:bg-slate-800 text-slate-900 dark:text-white shadow-sm'
                    : 'text-slate-500 dark:text-slate-400 hover:text-slate-700 dark:hover:text-slate-200'
                }`}
              >
                Sign In
              </button>
              <button
                type="button"
                onClick={() => setIsLoginTab(false)}
                className={`flex-1 py-2 rounded-full text-xs font-semibold tracking-tight transition-all duration-200 ${
                  !isLoginTab
                    ? 'bg-white dark:bg-slate-800 text-slate-900 dark:text-white shadow-sm'
                    : 'text-slate-500 dark:text-slate-400 hover:text-slate-700 dark:hover:text-slate-200'
                }`}
              >
                Create Account
              </button>
            </div>

            {/* Feedback alerts */}
            {(validationError || error) && (
              <div className="flex items-start gap-3 p-4 rounded-2xl bg-rose-500/10 border border-rose-500/20 text-rose-600 dark:text-rose-400 mb-6 text-xs font-medium">
                <AlertCircle size={16} className="shrink-0 mt-0.5" />
                <span>{validationError || error}</span>
              </div>
            )}

            {successMessage && (
              <div className="flex items-start gap-3 p-4 rounded-2xl bg-emerald-500/10 border border-emerald-500/20 text-emerald-600 dark:text-emerald-400 mb-6 text-xs font-medium">
                <CheckCircle2 size={16} className="shrink-0 mt-0.5" />
                <span>{successMessage}</span>
              </div>
            )}

            {/* Form */}
            <form onSubmit={handleSubmit} className="space-y-5">
              <div>
                <label className="block text-xs font-semibold text-slate-500 dark:text-slate-400 mb-2">
                  Username
                </label>
                <div className="relative">
                  <span className="absolute inset-y-0 left-0 pl-4 flex items-center text-slate-400 dark:text-slate-500">
                    <User size={16} />
                  </span>
                  <input
                    ref={usernameRef}
                    type="text"
                    placeholder="e.g. jiya"
                    value={username}
                    onChange={(e) => setUsername(e.target.value)}
                    disabled={isLoading}
                    className="w-full pl-11 pr-4 py-3 rounded-xl text-xs font-medium bg-white dark:bg-slate-800/70 border border-slate-200 dark:border-slate-700 text-slate-900 dark:text-white placeholder-slate-400 dark:placeholder-slate-500 focus:outline-none focus:ring-2 focus:ring-indigo-500/20 focus:border-indigo-500 transition-all"
                  />
                </div>
              </div>

              <div>
                <label className="block text-xs font-semibold text-slate-500 dark:text-slate-400 mb-2">
                  Password
                </label>
                <div className="relative">
                  <span className="absolute inset-y-0 left-0 pl-4 flex items-center text-slate-400 dark:text-slate-500">
                    <Lock size={16} />
                  </span>
                  <input
                    type="password"
                    placeholder="••••••••"
                    value={password}
                    onChange={(e) => setPassword(e.target.value)}
                    disabled={isLoading}
                    className="w-full pl-11 pr-4 py-3 rounded-xl text-xs font-medium bg-white dark:bg-slate-800/70 border border-slate-200 dark:border-slate-700 text-slate-900 dark:text-white placeholder-slate-400 dark:placeholder-slate-500 focus:outline-none focus:ring-2 focus:ring-indigo-500/20 focus:border-indigo-500 transition-all"
                  />
                </div>
              </div>

              {!isLoginTab && (
                <div>
                  <label className="block text-xs font-semibold text-slate-500 dark:text-slate-400 mb-2">
                    Confirm Password
                  </label>
                  <div className="relative">
                    <span className="absolute inset-y-0 left-0 pl-4 flex items-center text-slate-400 dark:text-slate-500">
                      <Lock size={16} />
                    </span>
                    <input
                      type="password"
                      placeholder="••••••••"
                      value={confirmPassword}
                      onChange={(e) => setConfirmPassword(e.target.value)}
                      disabled={isLoading}
                      className="w-full pl-11 pr-4 py-3 rounded-xl text-xs font-medium bg-white dark:bg-slate-800/70 border border-slate-200 dark:border-slate-700 text-slate-900 dark:text-white placeholder-slate-400 dark:placeholder-slate-500 focus:outline-none focus:ring-2 focus:ring-indigo-500/20 focus:border-indigo-500 transition-all"
                    />
                  </div>
                </div>
              )}

              <button
                type="submit"
                disabled={isLoading}
                className="w-full py-3.5 mt-2 rounded-full bg-indigo-500 hover:bg-indigo-600 text-white font-semibold text-xs tracking-tight shadow-sm transition-colors flex items-center justify-center gap-2 disabled:opacity-75 disabled:pointer-events-none"
              >
                {isLoading ? (
                  <div className="w-4 h-4 border-2 border-white border-t-transparent rounded-full animate-spin"></div>
                ) : (
                  <>
                    {isLoginTab ? 'Sign In' : 'Get Started Free'}
                    <span className="w-5 h-5 rounded-full bg-white/20 flex items-center justify-center">
                      <Plus size={11} strokeWidth={2.5} />
                    </span>
                  </>
                )}
              </button>
            </form>
          </div>
        </div>
      </div>

      {/* Trust row */}
      <div className="w-full max-w-7xl mx-auto px-6 lg:px-12 pb-16">
        <div className="grid md:grid-cols-3 gap-5">
          {trustCards.map((card, i) => {
            const Icon = card.icon;
            if (card.variant === 'dark') {
              return (
                <div key={i} className="rounded-2xl bg-[#1A1A1A] text-white p-6 flex flex-col">
                  <span className="w-9 h-9 rounded-full bg-white/10 flex items-center justify-center mb-4">
                    <Icon size={16} className="text-indigo-400" />
                  </span>
                  <h3 className="font-serif text-base mb-2">{card.title}</h3>
                  <p className="text-xs leading-relaxed text-slate-400">{card.text}</p>
                </div>
              );
            }
            if (card.variant === 'quote') {
              return (
                <div key={i} className="rounded-2xl bg-slate-50 dark:bg-slate-900 border border-slate-200 dark:border-slate-800 p-6 flex flex-col justify-between">
                  <Icon size={18} className="text-violet-500 mb-4" />
                  <p className="font-serif italic text-lg leading-snug text-slate-900 dark:text-white mb-4">
                    "{card.quote}"
                  </p>
                  <span className="text-[11px] uppercase tracking-wide text-slate-500 dark:text-slate-500">{card.caption}</span>
                </div>
              );
            }
            return (
              <div key={i} className="rounded-2xl overflow-hidden bg-slate-50 dark:bg-slate-900 border border-slate-200 dark:border-slate-800 flex flex-col">
                <div className="h-28 bg-gradient-to-br from-cyan-400 to-violet-700 dark:from-cyan-700 dark:to-violet-900"></div>
                <div className="p-6">
                  <h3 className="font-serif text-base text-slate-900 dark:text-white mb-2">{card.title}</h3>
                  <p className="text-xs leading-relaxed text-slate-600 dark:text-slate-400">{card.text}</p>
                </div>
              </div>
            );
          })}
        </div>
        <p className="text-[11px] text-slate-500 dark:text-slate-600 mt-6">
          *Illustrative estimate — replace with real usage data once available.
        </p>
      </div>
    </div>
  );
}
