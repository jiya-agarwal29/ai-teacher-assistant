import { Outlet } from 'react-router-dom';
import Sidebar from './Sidebar';
import SessionExpiryModal from './SessionExpiryModal';
import ImportedDataToast from './ImportedDataToast';
import AnimatedBackground from '../AnimatedBackground';
import { useActiveTimeTracker } from '../hooks/useActiveTime';
import { useAuth } from '../hooks/useAuth';

export default function PageLayout({ darkMode, setDarkMode }) {
  // Reports real "study session" time to the server — only while the tab
  // is visible and the user has interacted recently, not just left the
  // screen on.
  useActiveTimeTracker();

  const {
    showSessionWarning, sessionExpiresAt, refreshSession, logout,
    importToasts, dismissImportToast
  } = useAuth();

  return (
    <div className="flex h-screen w-screen overflow-hidden bg-cyan-100 dark:bg-slate-950 text-slate-700 dark:text-slate-300 theme-transition print:h-auto print:w-auto print:overflow-visible print:bg-white">
      {/* Animated Glowing background -- never printed */}
      <div className="print:hidden">
        <AnimatedBackground />
      </div>

      {/* Collapsible/Responsive Left Sidebar -- never printed */}
      <div className="print:hidden">
        <Sidebar darkMode={darkMode} setDarkMode={setDarkMode} />
      </div>

      {/* Main Screen Content Scrollbox */}
      <div className="flex-1 flex flex-col h-full overflow-y-auto relative z-10 px-4 py-6 md:p-10 no-scrollbar print:h-auto print:overflow-visible print:p-0 print:block">
        <Outlet />
      </div>

      {showSessionWarning && (
        <SessionExpiryModal expiresAt={sessionExpiresAt} onStayLoggedIn={refreshSession} onLogout={logout} />
      )}

      {importToasts.map((t, idx) => (
        <ImportedDataToast
          key={t.id}
          heading="Data moved"
          message={t.message}
          offset={idx}
          onDismiss={() => dismissImportToast(t.id)}
        />
      ))}
    </div>
  );
}
