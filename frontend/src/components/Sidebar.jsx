import { useState } from 'react';
import { NavLink, useNavigate } from 'react-router-dom';
import {
  Home,
  MessageSquare,
  FileText,
  Brain,
  BarChart3,
  LogOut,
  Sun,
  Moon,
  ChevronLeft,
  Menu,
  GraduationCap
} from 'lucide-react';
import { useAuth } from '../hooks/useAuth';
import { confirmNavigationAllowed } from '../utils/navigationGuard';

export default function Sidebar({ darkMode, setDarkMode }) {
  const { user, logout } = useAuth();
  const navigate = useNavigate();
  const [isCollapsed, setIsCollapsed] = useState(false);
  const [mobileOpen, setMobileOpen] = useState(false);

  const menuItems = [
    { name: 'Home', path: '/', icon: Home },
    { name: 'AI Chat', path: '/chat', icon: MessageSquare },
    { name: 'Documents', path: '/documents', icon: FileText },
    { name: 'AI Tools', path: '/tools', icon: Brain },
    { name: 'Analytics', path: '/analytics', icon: BarChart3 },
  ];

  const handleLogout = () => {
    logout();
    navigate('/login');
  };

  return (
    <>
      {/* Mobile Header Bar */}
      <div className="md:hidden flex items-center justify-between p-4 bg-white dark:bg-slate-900 border-b border-slate-200 dark:border-slate-800 text-slate-800 dark:text-slate-100 sticky top-0 z-40">
        <div className="flex items-center gap-2">
          <div className="w-8 h-8 rounded-full bg-slate-900 dark:bg-indigo-500 flex items-center justify-center text-white">
            <GraduationCap size={16} strokeWidth={2.2} />
          </div>
          <span className="font-serif text-sm tracking-tight">AI Teacher</span>
        </div>
        <button 
          onClick={() => setMobileOpen(!mobileOpen)}
          className="p-2 rounded-lg hover:bg-slate-100 dark:hover:bg-slate-800"
        >
          <Menu size={20} />
        </button>
      </div>

      {/* Backdrop for mobile */}
      {mobileOpen && (
        <div 
          className="md:hidden fixed inset-0 bg-slate-900/40 backdrop-blur-sm z-40"
          onClick={() => setMobileOpen(false)}
        />
      )}

      {/* Sidebar Container */}
      <div className={`
        fixed inset-y-0 left-0 z-50 md:relative md:z-10
        flex flex-col h-screen border-r theme-transition
        ${mobileOpen ? 'translate-x-0' : '-translate-x-full md:translate-x-0'}
        ${isCollapsed ? 'w-20' : 'w-72'}
        bg-white/80 dark:bg-slate-900/85 backdrop-blur-lg
        border-slate-200 dark:border-slate-800/80
        p-4 md:p-6
        transition-all duration-300 ease-in-out
      `}>
        {/* Sidebar Header & Brand */}
        <div className="flex items-center justify-between mb-8">
          <div className="flex items-center gap-3 overflow-hidden">
            <div className="min-w-10 h-10 rounded-full bg-slate-900 dark:bg-indigo-500 flex items-center justify-center text-white">
              <GraduationCap size={19} strokeWidth={2.2} />
            </div>
            {!isCollapsed && (
              <div className="flex flex-col">
                <span className="font-serif text-slate-800 dark:text-white tracking-tight text-base leading-tight">AI Assistant</span>
                <span className="text-[10px] text-violet-600 dark:text-violet-400 font-semibold tracking-wider uppercase">For Educators</span>
              </div>
            )}
          </div>
          
          {/* Collapse toggle (desktop only) */}
          <button 
            onClick={() => setIsCollapsed(!isCollapsed)}
            className="hidden md:flex p-1.5 rounded-lg text-slate-400 hover:text-slate-600 dark:hover:text-slate-200 hover:bg-slate-100 dark:hover:bg-slate-800"
          >
            <ChevronLeft size={16} className={`transition-transform duration-300 ${isCollapsed ? 'rotate-180' : ''}`} />
          </button>
        </div>

        {/* User Profile Info */}
        <div className={`flex items-center gap-3 p-3 rounded-xl mb-8 bg-slate-50 dark:bg-slate-800/50 border border-slate-100 dark:border-slate-800/30 overflow-hidden`}>
          <div className="w-9 h-9 rounded-lg bg-gradient-to-br from-indigo-500 to-cyan-400 flex items-center justify-center text-white font-semibold text-xs select-none">
            {user ? user.substring(0, 2).toUpperCase() : 'TA'}
          </div>
          {!isCollapsed && (
            <div className="flex flex-col min-w-0 flex-1">
              <span className="font-semibold text-slate-700 dark:text-slate-200 text-xs truncate">{user || 'Assistant User'}</span>
              <span className="text-[10px] text-green-600 dark:text-green-400 font-medium flex items-center gap-1.5">
                <span className="w-1.5 h-1.5 bg-green-500 rounded-full"></span>
                Online
              </span>
            </div>
          )}
        </div>

        {/* Menu Items */}
        <nav className="flex-1 space-y-1">
          {menuItems.map((item) => {
            const Icon = item.icon;
            return (
              <NavLink
                key={item.name}
                to={item.path}
                onClick={(e) => {
                  // Lets the current page (e.g. the OCR review page, mid-edit)
                  // ask for confirmation before this navigation is allowed to
                  // proceed -- see utils/navigationGuard.js.
                  if (!confirmNavigationAllowed()) {
                    e.preventDefault();
                    return;
                  }
                  setMobileOpen(false);
                }}
                className={({ isActive }) => `
                  flex items-center gap-4 px-4 py-3 rounded-lg font-medium text-xs tracking-tight transition-all duration-200 border-l-2
                  ${isActive
                    ? 'bg-violet-600/10 text-violet-700 dark:bg-violet-500/15 dark:text-violet-300 border-indigo-500'
                    : 'border-transparent text-slate-500 dark:text-slate-400 hover:text-slate-900 dark:hover:text-slate-200 hover:bg-slate-50 dark:hover:bg-slate-800/30'}
                `}
              >
                <Icon size={18} className="min-w-[18px]" />
                {!isCollapsed && <span>{item.name}</span>}
              </NavLink>
            );
          })}
        </nav>

        {/* Sidebar Footer Action */}
        <div className="mt-auto space-y-2 pt-4 border-t border-slate-100 dark:border-slate-800/50">
          {/* Light/Dark mode switcher */}
          <button 
            onClick={() => setDarkMode(!darkMode)}
            className="w-full flex items-center gap-4 px-4 py-3 rounded-xl text-slate-500 dark:text-slate-400 hover:text-slate-900 dark:hover:text-slate-200 hover:bg-slate-50 dark:hover:bg-slate-800/30 transition-all duration-200"
          >
            {darkMode ? (
              <>
                <Sun size={18} className="text-amber-500" />
                {!isCollapsed && <span className="text-xs font-medium">Light Mode</span>}
              </>
            ) : (
              <>
                <Moon size={18} className="text-indigo-500" />
                {!isCollapsed && <span className="text-xs font-medium">Dark Mode</span>}
              </>
            )}
          </button>

          {/* Logout Button */}
          <button 
            onClick={handleLogout}
            className="w-full flex items-center gap-4 px-4 py-3 rounded-xl text-rose-500 hover:bg-rose-50 dark:hover:bg-rose-950/20 transition-all duration-200"
          >
            <LogOut size={18} />
            {!isCollapsed && <span className="text-xs font-medium">Log out</span>}
          </button>
        </div>
      </div>
    </>
  );
}
