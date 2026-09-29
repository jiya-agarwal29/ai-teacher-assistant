import { Outlet } from 'react-router-dom';
import Sidebar from './Sidebar';
import AnimatedBackground from '../AnimatedBackground';
import { useActiveTimeTracker } from '../hooks/useActiveTime';

export default function PageLayout({ darkMode, setDarkMode }) {
  // Accumulates real "study session" time — only while the tab is visible
  // and the user has interacted recently, not just left the screen on.
  useActiveTimeTracker();

  return (
    <div className="flex h-screen w-screen overflow-hidden bg-cyan-100 dark:bg-slate-950 text-slate-700 dark:text-slate-300 theme-transition">
      {/* Animated Glowing background */}
      <AnimatedBackground />

      {/* Collapsible/Responsive Left Sidebar */}
      <Sidebar darkMode={darkMode} setDarkMode={setDarkMode} />

      {/* Main Screen Content Scrollbox */}
      <div className="flex-1 flex flex-col h-full overflow-y-auto relative z-10 px-4 py-6 md:p-10 no-scrollbar">
        <Outlet />
      </div>
    </div>
  );
}
