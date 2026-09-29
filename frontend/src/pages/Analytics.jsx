import { useState, useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  Clock,
  Award,
  TrendingUp,
  HelpCircle,
  Sparkles,
  BookOpen
} from 'lucide-react';
import { api } from '../services/api';
import { getActiveSecondsInLastNDays, formatDuration } from '../hooks/useActiveTime';
import { getQuizStats, getTopicBreakdown } from '../hooks/useQuizHistory';
import { getTotalQueries, getQueriesByDay } from '../hooks/useQueryHistory';

const DONUT_COLORS = ['#3F7566', '#D97706', '#3D8B37', '#0EA5E9', '#A855F7', '#8CA6A0'];

export default function Analytics() {
  const navigate = useNavigate();

  // Line chart interactive hover state
  const [hoveredData, setHoveredData] = useState(null);

  // Real data, all sourced from actual usage — re-polled so the page stays
  // live if you take a quiz / ask a question and come back.
  const [studySeconds, setStudySeconds] = useState(() => getActiveSecondsInLastNDays(14));
  const [quizStats, setQuizStats] = useState(() => getQuizStats());
  const [topicBreakdown, setTopicBreakdown] = useState(() => getTopicBreakdown());
  const [totalQueries, setTotalQueries] = useState(() => getTotalQueries());
  const [lineChartData, setLineChartData] = useState(() => getQueriesByDay(7));

  // Real uploaded documents, fetched from the backend — the donut chart is
  // built from your actual library, not fixed placeholder subjects.
  const [books, setBooks] = useState([]);
  const [booksLoading, setBooksLoading] = useState(true);
  const [booksError, setBooksError] = useState('');

  useEffect(() => {
    const refresh = () => {
      setStudySeconds(getActiveSecondsInLastNDays(14));
      setQuizStats(getQuizStats());
      setTopicBreakdown(getTopicBreakdown());
      setTotalQueries(getTotalQueries());
      setLineChartData(getQueriesByDay(7));
    };
    const interval = setInterval(refresh, 5000);
    return () => clearInterval(interval);
  }, []);

  useEffect(() => {
    api.books.getAll()
      .then((data) => setBooks(data || []))
      .catch((err) => setBooksError(err.message || 'Could not load documents.'))
      .finally(() => setBooksLoading(false));
  }, []);

  const pct = (n) => `${Math.round(n * 10) / 10}%`;

  // Statistics summaries — every value below is computed from real usage,
  // not hardcoded. Cards show an honest "no data yet" state until you've
  // actually done the thing they measure.
  const stats = [
    { name: "Active study time", value: formatDuration(studySeconds), detail: "Last 14 days · counts only real interaction", icon: Clock, color: "text-violet-500 bg-violet-500/10" },
    {
      name: "Average Quiz Score",
      value: quizStats.attempts > 0 ? pct(quizStats.averagePercentage) : '—',
      detail: quizStats.attempts > 0 ? `Mean of ${quizStats.attempts} quiz${quizStats.attempts === 1 ? '' : 'zes'} taken` : 'Take a quiz to see this',
      icon: Award,
      color: "text-emerald-500 bg-emerald-500/10"
    },
    { name: "Questions Answered", value: String(totalQueries), detail: "Real questions sent in Chat", icon: HelpCircle, color: "text-cyan-500 bg-cyan-500/10" },
    {
      name: "Answer Accuracy",
      value: quizStats.attempts > 0 ? pct(quizStats.weightedAccuracy) : '—',
      detail: quizStats.attempts > 0 ? `Correct across ${quizStats.totalQuestions} question${quizStats.totalQuestions === 1 ? '' : 's'} answered` : 'Take a quiz to see this',
      icon: TrendingUp,
      color: "text-indigo-500 bg-indigo-500/10"
    },
  ];

  // ---- Line chart geometry (real per-day query counts, last 7 days) ----
  const width = 500;
  const height = 180;
  const padding = 25;
  const chartWidth = width - padding * 2;
  const chartHeight = height - padding * 2;

  const maxVal = Math.max(...lineChartData.map((d) => d.queries)) + 2;

  const points = lineChartData.map((d, i) => {
    const x = padding + (i / (lineChartData.length - 1)) * chartWidth;
    const y = padding + chartHeight - (d.queries / maxVal) * chartHeight;
    return { x, y, day: d.day, val: d.queries };
  });

  let pathD = '';
  if (points.length > 0) {
    pathD = `M ${points[0].x} ${points[0].y}`;
    for (let i = 0; i < points.length - 1; i++) {
      const curr = points[i];
      const next = points[i + 1];
      const cpX1 = curr.x + (next.x - curr.x) / 2;
      const cpY1 = curr.y;
      const cpX2 = curr.x + (next.x - curr.x) / 2;
      const cpY2 = next.y;
      pathD += ` C ${cpX1} ${cpY1}, ${cpX2} ${cpY2}, ${next.x} ${next.y}`;
    }
  }

  const fillD = points.length > 0
    ? `${pathD} L ${points[points.length - 1].x} ${padding + chartHeight} L ${points[0].x} ${padding + chartHeight} Z`
    : '';

  const hasAnyQueries = lineChartData.some((d) => d.queries > 0);

  // ---- Donut chart geometry (real uploaded documents) ----
  const MAX_SLICES = 6;
  const donutSegments = (() => {
    if (books.length === 0) return [];
    const sorted = [...books];
    const visible = sorted.slice(0, MAX_SLICES);
    const rest = sorted.slice(MAX_SLICES);
    const slices = visible.map((b) => ({ name: b.name, count: 1 }));
    if (rest.length > 0) slices.push({ name: `${rest.length} more`, count: rest.length });

    const totalCount = slices.reduce((sum, s) => sum + s.count, 0);
    return slices.map((s, i) => ({
      ...s,
      percentage: (s.count / totalCount) * 100,
      color: DONUT_COLORS[i % DONUT_COLORS.length],
    }));
  })();

  const radius = 50;
  const circumference = 2 * Math.PI * radius;
  let cumulative = 0;
  const donutArcs = donutSegments.map((seg) => {
    const arcLength = (seg.percentage / 100) * circumference;
    const arc = { ...seg, dasharray: `${arcLength} ${circumference - arcLength}`, dashoffset: -cumulative };
    cumulative += arcLength;
    return arc;
  });

  // ---- Advisor recommendation, generated from real quiz history ----
  const weakestTopic = topicBreakdown.length > 0 ? topicBreakdown[0] : null;
  const advisorText = quizStats.attempts === 0
    ? "Take a quiz in AI Tools and this card will analyze your real results — your actual average score and weakest topic, not a canned message."
    : weakestTopic
      ? `Your average quiz score is ${pct(quizStats.averagePercentage)}. "${weakestTopic.topic}" is your lowest-scoring topic so far at ${pct(weakestTopic.percentage)} correct across ${weakestTopic.attempts} attempt${weakestTopic.attempts === 1 ? '' : 's'} — worth another focused pass.`
      : `Your average quiz score is ${pct(quizStats.averagePercentage)} across ${quizStats.attempts} attempt${quizStats.attempts === 1 ? '' : 's'}. Keep going to build a topic-by-topic breakdown.`;

  return (
    <div className="space-y-8 animate-fade-in text-slate-800 dark:text-slate-100">

      {/* Top Header */}
      <div>
        <h1 className="text-3xl font-extrabold tracking-tight text-slate-900 dark:text-white">
          Learning & Usage Analytics
        </h1>
        <p className="text-sm text-slate-500 dark:text-slate-400 mt-2 font-medium">
          Real numbers from what you've actually done — no placeholder data.
        </p>
      </div>

      {/* Grid: Key Performance Metrics */}
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-6">
        {stats.map((s, idx) => {
          const Icon = s.icon;
          return (
            <div
              key={idx}
              className="p-6 rounded-[24px] bg-white dark:bg-slate-900/60 border border-slate-200 dark:border-slate-800/80 shadow-sm flex items-center justify-between"
            >
              <div>
                <span className="text-[10px] font-bold text-slate-400 dark:text-slate-500 uppercase tracking-wider">{s.name}</span>
                <h3 className="text-2xl font-black text-slate-850 dark:text-white mt-1.5">{s.value}</h3>
                <span className="text-[9px] font-semibold text-slate-400 dark:text-slate-500 block mt-1">{s.detail}</span>
              </div>
              <div className={`w-11 h-11 rounded-xl flex items-center justify-center ${s.color}`}>
                <Icon size={18} />
              </div>
            </div>
          );
        })}
      </div>

      {/* Charts Layout Grid */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">

        {/* SVG Line Chart: real per-day query activity */}
        <div className="lg:col-span-2 p-6 rounded-[28px] bg-white dark:bg-slate-900/60 border border-slate-200 dark:border-slate-800/80 shadow-sm flex flex-col justify-between">
          <div className="flex justify-between items-center mb-6">
            <div>
              <h3 className="text-xs font-bold text-slate-900 dark:text-white">
                RAG Inference Activity
              </h3>
              <p className="text-[10px] text-slate-400 dark:text-slate-500 mt-1">
                Your real questions sent to Chat, by day, over the last 7 days.
              </p>
            </div>

            <div className="px-3 py-1.5 rounded-xl bg-violet-600/5 dark:bg-violet-500/10 border border-violet-500/15 text-[10px] font-bold text-violet-600 dark:text-violet-400 flex items-center gap-1.5">
              <Sparkles size={12} />
              <span>
                {hoveredData
                  ? `${hoveredData.day}: ${hoveredData.val} question${hoveredData.val === 1 ? '' : 's'}`
                  : 'Hover nodes for details'}
              </span>
            </div>
          </div>

          {!hasAnyQueries && (
            <p className="text-[10px] text-slate-400 dark:text-slate-500 mb-2">
              No questions sent yet this week — the line below is real, it's just flat at zero.
            </p>
          )}

          <div className="w-full">
            <svg
              viewBox={`0 0 ${width} ${height}`}
              className="w-full h-auto overflow-visible select-none"
            >
              <defs>
                <linearGradient id="chartGradient" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="0%" stopColor="#3F7566" stopOpacity="0.18" />
                  <stop offset="100%" stopColor="#3F7566" stopOpacity="0.00" />
                </linearGradient>
                <linearGradient id="lineColor" x1="0" y1="0" x2="1" y2="0">
                  <stop offset="0%" stopColor="#3F7566" />
                  <stop offset="100%" stopColor="#3D8B37" />
                </linearGradient>
              </defs>

              {[0, 0.25, 0.5, 0.75, 1].map((ratio, idx) => {
                const y = padding + ratio * chartHeight;
                return (
                  <line
                    key={idx}
                    x1={padding}
                    y1={y}
                    x2={padding + chartWidth}
                    y2={y}
                    stroke="currentColor"
                    className="text-slate-100 dark:text-slate-800/40"
                    strokeWidth="1"
                    strokeDasharray="4 4"
                  />
                );
              })}

              {fillD && <path d={fillD} fill="url(#chartGradient)" />}

              {pathD && (
                <path
                  d={pathD}
                  fill="none"
                  stroke="url(#lineColor)"
                  strokeWidth="2.5"
                  strokeLinecap="round"
                  strokeLinejoin="round"
                />
              )}

              {points.map((pt, idx) => (
                <g
                  key={idx}
                  onMouseEnter={() => setHoveredData(pt)}
                  onMouseLeave={() => setHoveredData(null)}
                  className="cursor-pointer"
                >
                  <circle cx={pt.x} cy={pt.y} r="12" fill="transparent" />
                  <circle
                    cx={pt.x}
                    cy={pt.y}
                    r={hoveredData?.day === pt.day && hoveredData?.val === pt.val ? "5.5" : "4"}
                    fill={hoveredData?.day === pt.day && hoveredData?.val === pt.val ? "#3D8B37" : "#3F7566"}
                    stroke="white"
                    strokeWidth="2"
                    className="transition-all duration-150"
                  />
                </g>
              ))}

              {points.map((pt, idx) => (
                <text
                  key={idx}
                  x={pt.x}
                  y={height - 2}
                  textAnchor="middle"
                  fill="currentColor"
                  className="text-[9px] font-bold text-slate-400 dark:text-slate-500 font-sans"
                >
                  {pt.day}
                </text>
              ))}
            </svg>
          </div>
        </div>

        {/* SVG Donut Chart: real uploaded document distribution */}
        <div className="p-6 rounded-[28px] bg-white dark:bg-slate-900/60 border border-slate-200 dark:border-slate-800/80 shadow-sm flex flex-col justify-between">
          <div>
            <h3 className="text-xs font-bold text-slate-900 dark:text-white">
              Document Library Split
            </h3>
            <p className="text-[10px] text-slate-400 dark:text-slate-500 mt-1">
              Your actual uploaded documents, by name.
            </p>
          </div>

          {booksLoading ? (
            <div className="my-8 text-center text-[11px] text-slate-400">Loading your documents…</div>
          ) : booksError ? (
            <div className="my-8 text-center text-[11px] text-rose-500">{booksError}</div>
          ) : books.length === 0 ? (
            <div className="my-8 flex flex-col items-center text-center gap-2">
              <BookOpen size={22} className="text-slate-300 dark:text-slate-700" />
              <p className="text-[11px] text-slate-400 dark:text-slate-500">
                No documents uploaded yet — upload one in Documents to see this fill in.
              </p>
            </div>
          ) : (
            <>
              <div className="my-4 flex justify-center relative">
                <svg viewBox="0 0 160 160" className="w-32 h-32 overflow-visible select-none">
                  <g transform="rotate(-90 80 80)">
                    {donutArcs.map((arc, idx) => (
                      <circle
                        key={idx}
                        cx="80" cy="80" r={radius}
                        fill="transparent"
                        stroke={arc.color}
                        strokeWidth="12"
                        strokeDasharray={arc.dasharray}
                        strokeDashoffset={arc.dashoffset}
                      />
                    ))}
                  </g>
                </svg>

                <div className="absolute inset-0 flex flex-col items-center justify-center text-center">
                  <span className="text-[10px] font-bold text-slate-400">Total library</span>
                  <span className="text-sm font-black text-slate-800 dark:text-white">
                    {books.length} Document{books.length === 1 ? '' : 's'}
                  </span>
                </div>
              </div>

              <div className="space-y-2 pt-2 border-t border-slate-100 dark:border-slate-850">
                {donutArcs.map((seg, idx) => (
                  <div key={idx} className="flex justify-between items-center text-[10px] font-bold">
                    <div className="flex items-center gap-2 min-w-0">
                      <span className="w-2.5 h-2.5 rounded-full shrink-0" style={{ backgroundColor: seg.color }}></span>
                      <span className="text-slate-600 dark:text-slate-300 truncate max-w-[140px]">{seg.name}</span>
                    </div>
                    <span className="text-slate-400 shrink-0">{Math.round(seg.percentage)}%</span>
                  </div>
                ))}
              </div>
            </>
          )}
        </div>

      </div>

      {/* Lower Row: AI Study Advisor Feedback, generated from real quiz history */}
      <div className="p-6 rounded-[28px] bg-gradient-to-br from-violet-600/5 to-indigo-600/5 dark:from-violet-950/10 dark:to-indigo-950/10 border border-violet-500/10 dark:border-violet-400/10 shadow-sm relative overflow-hidden flex flex-col sm:flex-row justify-between items-start sm:items-center gap-4">

        <div className="flex items-start gap-4">
          <div className="w-11 h-11 rounded-2xl bg-violet-600/15 text-violet-600 dark:text-violet-400 flex items-center justify-center shrink-0">
            <Sparkles size={20} />
          </div>
          <div>
            <h3 className="text-xs font-extrabold uppercase tracking-wider text-violet-600 dark:text-violet-400">
              AI Study Advisor
            </h3>
            <p className="text-[10px] text-slate-500 dark:text-slate-400 leading-normal max-w-2xl mt-1 font-medium">
              "{advisorText}"
            </p>
          </div>
        </div>

        <button
          onClick={() => navigate('/tools?tab=quiz')}
          className="px-4 py-2 bg-violet-600 hover:bg-violet-500 text-white rounded-xl text-xs font-bold transition-all shadow-sm shadow-violet-500/10 shrink-0 self-end sm:self-center cursor-pointer"
        >
          Build Test Now
        </button>
      </div>

    </div>
  );
}
