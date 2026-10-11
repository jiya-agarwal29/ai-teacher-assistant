import { useState, useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  Clock,
  Award,
  HelpCircle,
  Sparkles,
  FileText,
  Database,
  ClipboardList,
  AlertCircle,
  RefreshCw,
  Loader2,
  Trash2,
  BookOpen
} from 'lucide-react';
import { api } from '../services/api';
import { formatDuration } from '../hooks/useActiveTime';
import useBloomLevels from '../hooks/useBloomLevels';
import { getBloomColor } from '../utils/bloomColors';

const RANGE_OPTIONS = [7, 30, 90];

function pct(n) {
  return `${Math.round(n * 10) / 10}%`;
}

function formatAttemptDate(iso) {
  try {
    return new Date(iso).toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
  } catch {
    return '';
  }
}

// Only every Nth x-axis label is shown once the range gets wide enough
// that printing every single day's label would just overlap into mush.
function labelStride(pointCount) {
  if (pointCount <= 10) return 1;
  if (pointCount <= 31) return 5;
  return 15;
}

function weekdayLabel(dateStr) {
  try {
    return new Date(`${dateStr}T00:00:00`).toLocaleDateString(undefined, { weekday: 'short' });
  } catch {
    return dateStr;
  }
}

function monthDayLabel(dateStr) {
  try {
    return new Date(`${dateStr}T00:00:00`).toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
  } catch {
    return dateStr;
  }
}

function AnalyticsSkeleton() {
  return (
    <div className="space-y-8 animate-fade-in">
      <div className="h-10 w-72 bg-slate-100 dark:bg-slate-800/60 rounded-xl animate-pulse" />
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-6">
        {Array.from({ length: 6 }).map((_, i) => (
          <div key={i} className="h-28 rounded-[24px] bg-white dark:bg-slate-900/60 border border-slate-200 dark:border-slate-800/80">
            <div className="h-full w-full animate-pulse bg-slate-100/80 dark:bg-slate-800/40 rounded-[24px]" />
          </div>
        ))}
      </div>
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        <div className="h-56 rounded-[28px] bg-slate-100 dark:bg-slate-800/40 animate-pulse" />
        <div className="h-56 rounded-[28px] bg-slate-100 dark:bg-slate-800/40 animate-pulse" />
      </div>
    </div>
  );
}

export default function Analytics() {
  const navigate = useNavigate();
  const { levels: bloomLevels } = useBloomLevels();

  const [days, setDays] = useState(7);
  // Bumped to re-run the fetch effect below without changing `days` --
  // Retry and "Clear my activity data" both need that, and `days` itself
  // already covers every range-switch case.
  const [refreshKey, setRefreshKey] = useState(0);
  const [summary, setSummary] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  const [hoveredQueryPoint, setHoveredQueryPoint] = useState(null);
  const [hoveredActiveBar, setHoveredActiveBar] = useState(null);

  const [clearing, setClearing] = useState(false);
  const [clearError, setClearError] = useState('');

  // The loading/error reset for a new fetch happens in whichever event
  // handler below triggers it (so it's immediate, and keeps this effect
  // body itself free of synchronous setState calls), not here.
  useEffect(() => {
    let cancelled = false;
    api.analytics.summary(days)
      .then((data) => {
        if (cancelled) return;
        setSummary(data);
        setError('');
      })
      .catch((err) => {
        if (!cancelled) setError(err.message || 'Could not load your analytics.');
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [days, refreshKey]);

  const handleRangeChange = (opt) => {
    setLoading(true);
    setDays(opt);
  };

  const handleRetry = () => {
    setLoading(true);
    setError('');
    setRefreshKey((k) => k + 1);
  };

  const handleClearData = async () => {
    if (!window.confirm(
      "Clear all your activity data? This removes your query/study-time history and quiz attempts, "
      + "but keeps your documents, Question Bank, and papers. This cannot be undone."
    )) {
      return;
    }
    setClearing(true);
    setClearError('');
    try {
      await api.analytics.remove();
      setLoading(true);
      setRefreshKey((k) => k + 1);
    } catch (err) {
      setClearError(err.message || 'Could not clear your activity data.');
    } finally {
      setClearing(false);
    }
  };

  if (error && !summary) {
    return (
      <div className="space-y-8 animate-fade-in">
        <h1 className="text-3xl font-extrabold tracking-tight text-slate-900 dark:text-white">
          Learning &amp; Usage Analytics
        </h1>
        <div className="p-8 rounded-[28px] border-2 border-dashed border-rose-200 dark:border-rose-900/40 bg-rose-50/30 dark:bg-rose-950/10 text-center space-y-4 max-w-md mx-auto">
          <AlertCircle size={32} className="mx-auto text-rose-400 dark:text-rose-500" />
          <p className="text-xs text-rose-600 dark:text-rose-400 font-medium">{error}</p>
          <button
            type="button"
            onClick={handleRetry}
            className="inline-flex items-center gap-2 px-5 py-2.5 rounded-xl text-xs font-bold bg-gradient-to-tr from-violet-600 to-indigo-500 text-white hover:scale-[1.01] transition-all"
          >
            <RefreshCw size={13} /> Retry
          </button>
        </div>
      </div>
    );
  }

  if (loading && !summary) {
    return <AnalyticsSkeleton />;
  }

  const totals = summary.totals;
  const queriesByDay = summary.queries_by_day;
  const activeByDay = summary.active_seconds_by_day;
  const stride = labelStride(queriesByDay.length);
  const labelFn = days <= 7 ? weekdayLabel : monthDayLabel;

  const statCards = [
    { name: 'Questions asked', value: String(totals.queries), detail: `Last ${days} days`, icon: HelpCircle, color: 'text-cyan-500 bg-cyan-500/10' },
    {
      name: 'Quizzes taken', value: String(totals.quizzes_taken),
      detail: totals.average_score_pct != null ? `Average score ${pct(totals.average_score_pct)}` : 'No quizzes taken yet',
      icon: Award, color: 'text-emerald-500 bg-emerald-500/10'
    },
    { name: 'Study time', value: formatDuration(totals.active_seconds), detail: `Last ${days} days`, icon: Clock, color: 'text-violet-500 bg-violet-500/10' },
    { name: 'Documents', value: String(totals.documents_ready), detail: 'Ready in your library', icon: FileText, color: 'text-indigo-500 bg-indigo-500/10' },
    { name: 'Question Bank', value: String(totals.questions_in_bank), detail: 'Questions saved', icon: Database, color: 'text-amber-500 bg-amber-500/10' },
    { name: 'Papers', value: String(totals.papers_ready), detail: 'Ready to export', icon: ClipboardList, color: 'text-rose-500 bg-rose-500/10' },
  ];

  // ---- Line chart geometry: questions per day ----
  const width = 500;
  const height = 180;
  const padding = 25;
  const chartWidth = width - padding * 2;
  const chartHeight = height - padding * 2;
  const maxQueries = Math.max(...queriesByDay.map((d) => d.count), 1) + 1;

  const queryPoints = queriesByDay.map((d, i) => {
    const x = padding + (queriesByDay.length > 1 ? (i / (queriesByDay.length - 1)) * chartWidth : chartWidth / 2);
    const y = padding + chartHeight - (d.count / maxQueries) * chartHeight;
    return { x, y, date: d.date, val: d.count };
  });

  let queryPathD = '';
  if (queryPoints.length > 0) {
    queryPathD = `M ${queryPoints[0].x} ${queryPoints[0].y}`;
    for (let i = 0; i < queryPoints.length - 1; i++) {
      const curr = queryPoints[i];
      const next = queryPoints[i + 1];
      const cpX = curr.x + (next.x - curr.x) / 2;
      queryPathD += ` C ${cpX} ${curr.y}, ${cpX} ${next.y}, ${next.x} ${next.y}`;
    }
  }
  const queryFillD = queryPoints.length > 0
    ? `${queryPathD} L ${queryPoints[queryPoints.length - 1].x} ${padding + chartHeight} L ${queryPoints[0].x} ${padding + chartHeight} Z`
    : '';
  const hasAnyQueries = queriesByDay.some((d) => d.count > 0);

  // ---- Bar chart geometry: study minutes per day ----
  const maxMinutes = Math.max(...activeByDay.map((d) => Math.round(d.seconds / 60)), 1);
  const hasAnyActiveTime = activeByDay.some((d) => d.seconds > 0);

  const weakTopics = summary.weak_topics || [];
  const recentAttempts = summary.recent_attempts || [];
  const paperCoverage = summary.paper_coverage;
  const hasPapers = paperCoverage && paperCoverage.papers > 0;

  return (
    <div className="space-y-8 animate-fade-in text-slate-800 dark:text-slate-100">
      <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4">
        <div>
          <h1 className="text-3xl font-extrabold tracking-tight text-slate-900 dark:text-white">
            Learning &amp; Usage Analytics
          </h1>
          <p className="text-sm text-slate-500 dark:text-slate-400 mt-2 font-medium">
            Real numbers from what you've actually done — tracked on the server, not your browser.
          </p>
        </div>
        <div className="flex items-center gap-2 bg-slate-100 dark:bg-slate-800/60 rounded-xl p-1 self-start">
          {RANGE_OPTIONS.map((opt) => (
            <button
              key={opt}
              type="button"
              onClick={() => handleRangeChange(opt)}
              className={`px-3.5 py-1.5 rounded-lg text-[11px] font-bold transition-all ${
                days === opt
                  ? 'bg-white dark:bg-slate-900 text-violet-600 dark:text-violet-400 shadow-sm'
                  : 'text-slate-500 dark:text-slate-400 hover:text-slate-700 dark:hover:text-slate-200'
              }`}
            >
              {opt}d
            </button>
          ))}
        </div>
      </div>

      {loading && (
        <div className="flex items-center gap-2 text-[10px] font-bold text-slate-400 dark:text-slate-500">
          <Loader2 size={12} className="animate-spin" /> Refreshing…
        </div>
      )}

      {/* Stat cards */}
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-6">
        {statCards.map((s, idx) => {
          const Icon = s.icon;
          return (
            <div
              key={idx}
              className="p-6 rounded-[24px] bg-white dark:bg-slate-900/60 border border-slate-200 dark:border-slate-800/80 shadow-sm flex items-center justify-between"
            >
              <div>
                <span className="text-[10px] font-bold text-slate-400 dark:text-slate-500 uppercase tracking-wider">{s.name}</span>
                <h3 className="text-2xl font-black text-slate-900 dark:text-white mt-1.5">{s.value}</h3>
                <span className="text-[9px] font-semibold text-slate-400 dark:text-slate-500 block mt-1">{s.detail}</span>
              </div>
              <div className={`w-11 h-11 rounded-xl flex items-center justify-center ${s.color}`}>
                <Icon size={18} />
              </div>
            </div>
          );
        })}
      </div>

      {/* Charts */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        {/* Questions per day -- line chart */}
        <div className="p-6 rounded-[28px] bg-white dark:bg-slate-900/60 border border-slate-200 dark:border-slate-800/80 shadow-sm flex flex-col justify-between">
          <div className="flex justify-between items-center mb-6">
            <div>
              <h3 className="text-xs font-bold text-slate-900 dark:text-white">Questions per day</h3>
              <p className="text-[10px] text-slate-400 dark:text-slate-500 mt-1">Chat and AI Tutor answers, last {days} days.</p>
            </div>
            <div className="px-3 py-1.5 rounded-xl bg-violet-600/5 dark:bg-violet-500/10 border border-violet-500/15 text-[10px] font-bold text-violet-600 dark:text-violet-400 flex items-center gap-1.5">
              <Sparkles size={12} />
              <span>{hoveredQueryPoint ? `${hoveredQueryPoint.date}: ${hoveredQueryPoint.val}` : 'Hover nodes for details'}</span>
            </div>
          </div>

          {!hasAnyQueries && (
            <p className="text-[10px] text-slate-400 dark:text-slate-500 mb-2">
              No questions asked yet in this range — the line below is real, it's just flat at zero.
            </p>
          )}

          <svg viewBox={`0 0 ${width} ${height}`} className="w-full h-auto overflow-visible select-none">
            <defs>
              <linearGradient id="queryChartGradient" x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%" stopColor="#3F7566" stopOpacity="0.18" />
                <stop offset="100%" stopColor="#3F7566" stopOpacity="0.00" />
              </linearGradient>
              <linearGradient id="queryLineColor" x1="0" y1="0" x2="1" y2="0">
                <stop offset="0%" stopColor="#3F7566" />
                <stop offset="100%" stopColor="#3D8B37" />
              </linearGradient>
            </defs>

            {[0, 0.25, 0.5, 0.75, 1].map((ratio, idx) => (
              <line
                key={idx}
                x1={padding} y1={padding + ratio * chartHeight}
                x2={padding + chartWidth} y2={padding + ratio * chartHeight}
                stroke="currentColor" className="text-slate-100 dark:text-slate-800/40"
                strokeWidth="1" strokeDasharray="4 4"
              />
            ))}

            {queryFillD && <path d={queryFillD} fill="url(#queryChartGradient)" />}
            {queryPathD && (
              <path d={queryPathD} fill="none" stroke="url(#queryLineColor)" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round" />
            )}

            {queryPoints.map((pt, idx) => (
              <g key={idx} onMouseEnter={() => setHoveredQueryPoint(pt)} onMouseLeave={() => setHoveredQueryPoint(null)} className="cursor-pointer">
                <circle cx={pt.x} cy={pt.y} r="12" fill="transparent" />
                <circle
                  cx={pt.x} cy={pt.y}
                  r={hoveredQueryPoint?.date === pt.date ? '5.5' : '4'}
                  fill={hoveredQueryPoint?.date === pt.date ? '#3D8B37' : '#3F7566'}
                  stroke="white" strokeWidth="2" className="transition-all duration-150"
                />
              </g>
            ))}

            {queryPoints.map((pt, idx) => (
              idx % stride === 0 && (
                <text
                  key={idx} x={pt.x} y={height - 2} textAnchor="middle" fill="currentColor"
                  className="text-[9px] font-bold text-slate-400 dark:text-slate-500 font-sans"
                >
                  {labelFn(pt.date)}
                </text>
              )
            ))}
          </svg>
        </div>

        {/* Study minutes per day -- bar chart */}
        <div className="p-6 rounded-[28px] bg-white dark:bg-slate-900/60 border border-slate-200 dark:border-slate-800/80 shadow-sm flex flex-col justify-between">
          <div className="flex justify-between items-center mb-6">
            <div>
              <h3 className="text-xs font-bold text-slate-900 dark:text-white">Study minutes per day</h3>
              <p className="text-[10px] text-slate-400 dark:text-slate-500 mt-1">Active time in the app, last {days} days.</p>
            </div>
            <div className="px-3 py-1.5 rounded-xl bg-violet-600/5 dark:bg-violet-500/10 border border-violet-500/15 text-[10px] font-bold text-violet-600 dark:text-violet-400 flex items-center gap-1.5">
              <Clock size={12} />
              <span>{hoveredActiveBar ? `${hoveredActiveBar.date}: ${hoveredActiveBar.minutes}m` : 'Hover bars for details'}</span>
            </div>
          </div>

          {!hasAnyActiveTime && (
            <p className="text-[10px] text-slate-400 dark:text-slate-500 mb-2">
              No active study time recorded yet in this range.
            </p>
          )}

          <div className="flex items-end gap-0.5 h-32.5">
            {activeByDay.map((d, idx) => {
              const minutes = Math.round(d.seconds / 60);
              const heightPct = Math.max(2, (minutes / maxMinutes) * 100);
              const show = idx % stride === 0;
              return (
                <div
                  key={d.date}
                  className="flex-1 flex flex-col items-center justify-end h-full group relative"
                  onMouseEnter={() => setHoveredActiveBar({ date: d.date, minutes })}
                  onMouseLeave={() => setHoveredActiveBar(null)}
                >
                  <div
                    className={`w-full rounded-t-sm transition-all ${minutes > 0 ? 'bg-violet-500 group-hover:bg-violet-400' : 'bg-slate-100 dark:bg-slate-800'}`}
                    style={{ height: `${heightPct}%` }}
                  />
                  {show && (
                    <span className="text-[8px] font-bold text-slate-400 dark:text-slate-500 mt-1.5 whitespace-nowrap">
                      {labelFn(d.date)}
                    </span>
                  )}
                </div>
              );
            })}
          </div>
        </div>
      </div>

      {/* Accuracy by Bloom's level */}
      <div className="p-6 rounded-[28px] bg-white dark:bg-slate-900/60 border border-slate-200 dark:border-slate-800/80 shadow-sm space-y-4">
        <div>
          <h3 className="text-xs font-bold text-slate-900 dark:text-white">Accuracy by Bloom&apos;s level</h3>
          <p className="text-[10px] text-slate-400 dark:text-slate-500 mt-1">
            How you've scored at each level of thinking, across quizzes taken in the last {days} days.
          </p>
        </div>
        <div className="space-y-3">
          {bloomLevels.map((lvl) => {
            const stat = summary.accuracy_by_level[lvl.key];
            const colors = getBloomColor(lvl.key);
            const roundedPct = stat && stat.pct != null ? Math.round(stat.pct) : 0;
            return (
              <div key={lvl.key} className="space-y-1.5">
                <div className="flex justify-between items-center text-[10px] font-bold">
                  <span className="text-slate-600 dark:text-slate-300">{lvl.label}</span>
                  <span className="text-slate-400 dark:text-slate-500">
                    {stat && stat.pct != null ? `${roundedPct}% (${stat.correct}/${stat.total})` : 'No data yet'}
                  </span>
                </div>
                <div className="w-full bg-slate-100 dark:bg-slate-800 h-2.5 rounded-full overflow-hidden">
                  <div
                    className={`h-full rounded-full transition-all duration-500 ${colors.bar}`}
                    style={{ width: stat && stat.pct != null ? `${roundedPct}%` : '0%' }}
                  />
                </div>
              </div>
            );
          })}
        </div>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        {/* Topics to revise */}
        <div className="p-6 rounded-[28px] bg-white dark:bg-slate-900/60 border border-slate-200 dark:border-slate-800/80 shadow-sm space-y-4">
          <h3 className="text-xs font-bold text-slate-900 dark:text-white">Topics to revise</h3>
          {weakTopics.length === 0 ? (
            <div className="py-8 text-center">
              <Sparkles size={22} className="text-slate-300 dark:text-slate-700 mx-auto mb-2" />
              <p className="text-[11px] text-slate-400 dark:text-slate-500">No weak topics yet — keep practising</p>
            </div>
          ) : (
            <div className="space-y-2.5">
              {weakTopics.map((t, idx) => (
                <div key={idx} className="flex items-center justify-between gap-3 p-3 rounded-xl bg-amber-500/5 border border-amber-500/15">
                  <div className="min-w-0">
                    <p className="text-xs font-bold text-slate-800 dark:text-slate-100 truncate">{t.topic}</p>
                    <p className="text-[9px] text-slate-400 dark:text-slate-500">{t.attempts} attempt{t.attempts === 1 ? '' : 's'}</p>
                  </div>
                  <span className="text-sm font-black text-amber-600 dark:text-amber-400 shrink-0">{pct(t.average_pct)}</span>
                </div>
              ))}
            </div>
          )}
        </div>

        {/* Recent quizzes */}
        <div className="p-6 rounded-[28px] bg-white dark:bg-slate-900/60 border border-slate-200 dark:border-slate-800/80 shadow-sm space-y-4">
          <h3 className="text-xs font-bold text-slate-900 dark:text-white">Recent quizzes</h3>
          {recentAttempts.length === 0 ? (
            <div className="py-8 text-center">
              <BookOpen size={22} className="text-slate-300 dark:text-slate-700 mx-auto mb-2" />
              <p className="text-[11px] text-slate-400 dark:text-slate-500">
                No quizzes taken yet — try one in{' '}
                <button type="button" onClick={() => navigate('/tools?tab=quiz')} className="font-bold text-violet-600 dark:text-violet-400 hover:underline">
                  AI Tools
                </button>.
              </p>
            </div>
          ) : (
            <div className="space-y-2">
              {recentAttempts.map((a, idx) => (
                <div key={idx} className="flex items-center justify-between gap-3 text-[11px]">
                  <div className="min-w-0 flex-1">
                    <p className="font-bold text-slate-700 dark:text-slate-200 truncate">{a.topic}</p>
                    <p className="text-[9px] text-slate-400 dark:text-slate-500">{formatAttemptDate(a.created_at)} · {a.correct}/{a.total} correct</p>
                  </div>
                  <span className={`font-black shrink-0 ${a.pct >= 60 ? 'text-emerald-600 dark:text-emerald-400' : 'text-rose-500'}`}>{pct(a.pct)}</span>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>

      {/* Bloom's mix across your papers */}
      {hasPapers && (
        <div className="p-6 rounded-[28px] bg-white dark:bg-slate-900/60 border border-slate-200 dark:border-slate-800/80 shadow-sm space-y-4">
          <div>
            <h3 className="text-xs font-bold text-slate-900 dark:text-white">Bloom&apos;s mix across your papers</h3>
            <p className="text-[10px] text-slate-400 dark:text-slate-500 mt-1">
              Marks by level across {paperCoverage.papers} ready paper{paperCoverage.papers === 1 ? '' : 's'} (a flagged question counts at its detected level).
            </p>
          </div>
          <div className="space-y-3">
            {bloomLevels.map((lvl) => {
              const marks = paperCoverage.by_level[lvl.key] || 0;
              const maxMarks = Math.max(1, ...Object.values(paperCoverage.by_level));
              const colors = getBloomColor(lvl.key);
              return (
                <div key={lvl.key} className="flex items-center gap-3">
                  <span className="w-24 text-[10px] font-bold text-slate-500 dark:text-slate-400 shrink-0">{lvl.label}</span>
                  <div className="flex-1 bg-slate-100 dark:bg-slate-800 h-2 rounded-full overflow-hidden">
                    <div className={`h-full rounded-full transition-all duration-500 ${colors.bar}`} style={{ width: `${(marks / maxMarks) * 100}%` }} />
                  </div>
                  <span className="w-16 text-right text-[10px] font-bold text-slate-400 dark:text-slate-500">{marks} marks</span>
                </div>
              );
            })}
          </div>
        </div>
      )}

      {/* Clear activity data */}
      <div className="p-6 rounded-[28px] border border-dashed border-rose-200 dark:border-rose-900/40 bg-rose-50/30 dark:bg-rose-950/10 flex flex-col sm:flex-row items-start sm:items-center justify-between gap-4">
        <div>
          <h3 className="text-xs font-bold text-rose-700 dark:text-rose-400">Clear my activity data</h3>
          <p className="text-[10px] text-slate-500 dark:text-slate-400 mt-1 max-w-md">
            Removes your query/study-time history and quiz attempts. Documents, Question Bank, and papers are kept.
          </p>
          {clearError && <p className="text-[10px] font-semibold text-rose-500 mt-2">{clearError}</p>}
        </div>
        <button
          type="button"
          onClick={handleClearData}
          disabled={clearing}
          className="flex items-center gap-1.5 px-4 py-2.5 rounded-xl text-[11px] font-bold bg-rose-600 hover:bg-rose-500 text-white transition-colors disabled:opacity-50 shrink-0"
        >
          {clearing ? <Loader2 size={12} className="animate-spin" /> : <Trash2 size={12} />}
          {clearing ? 'Clearing…' : 'Clear my activity data'}
        </button>
      </div>
    </div>
  );
}
