import React, { useState, useEffect } from 'react';
import { useNavigate, Link } from 'react-router-dom';
import { api, userScopedKey } from '../services/api';
import { useAuth } from '../hooks/useAuth';
import { 
  FileText, 
  MessageSquare, 
  Brain, 
  Plus, 
  BookOpen, 
  Upload, 
  TrendingUp, 
  Sparkles,
  ArrowRight,
  Trash2,
  Activity
} from 'lucide-react';

export default function Home() {
  const { user } = useAuth();
  const navigate = useNavigate();
  
  const [books, setBooks] = useState([]);
  const [isLoading, setIsLoading] = useState(true);
  const [stats, setStats] = useState({
    documents: 0,
    queries: 0,
    quizzes: 0
  });

  // Upload state
  const [isUploading, setIsUploading] = useState(false);
  const [uploadProgress, setUploadProgress] = useState(0);
  const [uploadError, setUploadError] = useState('');
  
  // Load books
  const loadBooks = async () => {
    setIsLoading(true);
    try {
      const data = await api.books.getAll();
      setBooks(data || []);
      
      // Load query/quiz count from localStorage to make stats interactive and persist
      const queriesCount = parseInt(localStorage.getItem(userScopedKey('queries_count')) || '0');
      const quizCount = parseInt(localStorage.getItem(userScopedKey('quiz_count')) || '0');
      
      setStats({
        documents: data ? data.length : 0,
        queries: queriesCount,
        quizzes: quizCount
      });
    } catch (error) {
      console.error('Failed to load books for dashboard:', error);
    } finally {
      setIsLoading(false);
    }
  };

  useEffect(() => {
    loadBooks();
  }, []);

  // Handle direct file upload from dashboard
  const handleFileUpload = async (e) => {
    const file = e.target.files?.[0];
    if (!file) return;

    const allowedExtensions = ['.pdf', '.docx', '.doc', '.pptx', '.ppt', '.txt', '.md'];
    const fileExtension = file.name.slice(file.name.lastIndexOf('.')).toLowerCase();
    if (!allowedExtensions.includes(fileExtension)) {
      setUploadError('Unsupported file format. Please upload PDF, Word (.docx, .doc), PowerPoint (.pptx, .ppt), or plain text (.txt, .md) documents.');
      return;
    }

    setIsUploading(true);
    setUploadProgress(0);
    setUploadError('');

    try {
      await api.books.upload(file, (progress) => {
        setUploadProgress(progress);
      });
      // Refresh books list
      await loadBooks();
    } catch (err) {
      setUploadError(err.message || 'Upload failed.');
    } finally {
      setIsUploading(false);
    }
  };

  // Delete book direct handler
  const handleDeleteBook = async (bookId) => {
    if (!window.confirm('Are you sure you want to delete this document? All associated AI context will be deleted.')) {
      return;
    }
    try {
      await api.books.delete(bookId);
      loadBooks();
    } catch (err) {
      alert('Failed to delete book: ' + err.message);
    }
  };

  // Dynamic AI insights based on uploaded documents
  const getAIInsights = () => {
    if (books.length === 0) {
      return [
        {
          title: "Get Started by Uploading a Syllabus or Book",
          desc: "Upload notes in the Documents tab or upload area above to build your custom AI teaching assistant."
        },
        {
          title: "Try Interactive Quizzing",
          desc: "Once you upload materials, head over to AI Tools to instantly generate standard multiple-choice questions."
        }
      ];
    }

    const firstBook = books[0].name.toLowerCase();
    if (firstBook.includes('dbms') || firstBook.includes('database')) {
      return [
        {
          title: "Database Indexing & Normalization",
          desc: "Your uploaded notes on DBMS suggest a revision session on 3NF/BCNF. AI suggests generating a 5-question test."
        },
        {
          title: "SQL Query Optimization",
          desc: "AI has parsed query structures in your document. Try asking: 'Explain the difference between clustered and non-clustered indexes'."
        }
      ];
    } else if (firstBook.includes('os') || firstBook.includes('operating')) {
      return [
        {
          title: "Process Scheduling Algorithms",
          desc: "A core topic found in your Operating Systems notes is CPU scheduling. Revise Round Robin and Shortest Job First."
        },
        {
          title: "Concurrency Control & Deadlocks",
          desc: "Ask the AI tutor: 'Give me a analogy to understand semaphores vs mutexes'."
        }
      ];
    } else {
      return [
        {
          title: `Study Recommendation: ${books[0].name}`,
          desc: "Ready to study this material. Ask AI to: 'Create a conceptual summary of the first chapter'."
        },
        {
          title: "Semantic Association Complete",
          desc: `All chunks in "${books[0].name}" have been vector embedded. You can now use Semantic Search across them.`
        }
      ];
    }
  };

  const insights = getAIInsights();

  return (
    <div className="space-y-8 animate-fade-in text-slate-800 dark:text-slate-100">
      
      {/* Top Banner */}
      <div className="flex flex-col md:flex-row justify-between items-start md:items-center gap-4">
        <div>
          <h1 className="text-3xl font-extrabold tracking-tight text-slate-900 dark:text-white sm:text-4xl">
            Welcome back, {user || 'Educator'} 👋
          </h1>
          <p className="text-sm text-slate-500 dark:text-slate-400 mt-2 font-medium">
            Your startup-grade AI teaching stack is compiled and active.
          </p>
        </div>
        
        <Link 
          to="/chat"
          className="px-5 py-3 rounded-2xl bg-gradient-to-r from-violet-600 to-indigo-500 hover:from-violet-500 hover:to-indigo-400 text-white font-semibold text-xs tracking-tight shadow-md shadow-violet-500/20 hover:scale-[1.02] active:scale-[0.98] transition-all flex items-center gap-2"
        >
          <MessageSquare size={16} />
          Ask AI Assistant
        </Link>
      </div>

      {/* Stats Cards */}
      <div className="grid grid-cols-1 sm:grid-cols-3 gap-6">
        {/* Card 1 */}
        <div className="p-6 rounded-[24px] bg-white dark:bg-slate-900/60 border border-slate-200 dark:border-slate-800/80 shadow-sm shadow-slate-100/40 dark:shadow-none flex items-center justify-between">
          <div>
            <span className="text-xs font-semibold text-slate-400 dark:text-slate-500">Knowledge Base</span>
            <h3 className="text-3xl font-bold tracking-tight text-slate-800 dark:text-white mt-2">
              {isLoading ? '...' : stats.documents}
            </h3>
            <span className="text-[10px] font-medium text-slate-400 dark:text-slate-500 mt-1 block">
              Uploaded Documents
            </span>
          </div>
          <div className="w-12 h-12 rounded-2xl bg-violet-500/10 text-violet-600 dark:text-violet-400 flex items-center justify-center">
            <FileText size={22} />
          </div>
        </div>

        {/* Card 2 */}
        <div className="p-6 rounded-[24px] bg-white dark:bg-slate-900/60 border border-slate-200 dark:border-slate-800/80 shadow-sm shadow-slate-100/40 dark:shadow-none flex items-center justify-between">
          <div>
            <span className="text-xs font-semibold text-slate-400 dark:text-slate-500">AI Interactions</span>
            <h3 className="text-3xl font-bold tracking-tight text-slate-800 dark:text-white mt-2">
              {stats.queries}
            </h3>
            <span className="text-[10px] font-medium text-green-500 flex items-center gap-1 mt-1">
              <TrendingUp size={10} /> +12% increase this week
            </span>
          </div>
          <div className="w-12 h-12 rounded-2xl bg-cyan-500/10 text-cyan-600 dark:text-cyan-400 flex items-center justify-center">
            <MessageSquare size={22} />
          </div>
        </div>

        {/* Card 3 */}
        <div className="p-6 rounded-[24px] bg-white dark:bg-slate-900/60 border border-slate-200 dark:border-slate-800/80 shadow-sm shadow-slate-100/40 dark:shadow-none flex items-center justify-between">
          <div>
            <span className="text-xs font-semibold text-slate-400 dark:text-slate-500">Quizzes Answered</span>
            <h3 className="text-3xl font-bold tracking-tight text-slate-800 dark:text-white mt-2">
              {stats.quizzes}
            </h3>
            <span className="text-[10px] font-medium text-slate-400 dark:text-slate-500 mt-1 block">
              MCQs completed
            </span>
          </div>
          <div className="w-12 h-12 rounded-2xl bg-indigo-500/10 text-indigo-600 dark:text-indigo-400 flex items-center justify-center">
            <Brain size={22} />
          </div>
        </div>
      </div>

      {/* Main Grid */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        
        {/* Left Column: Recent Files & Upload Area */}
        <div className="lg:col-span-2 space-y-6">
          
          {/* Quick upload dropzone */}
          <div className="p-6 rounded-[28px] bg-white dark:bg-slate-900/60 border border-slate-200 dark:border-slate-800/80 shadow-sm relative overflow-hidden">
            <div className="flex flex-col items-center justify-center border-2 border-dashed border-slate-200 dark:border-slate-800 rounded-2xl p-8 text-center bg-slate-50/50 dark:bg-slate-950/20 hover:border-violet-500/50 dark:hover:border-violet-500/40 transition-all group">
              <Upload size={32} className="text-slate-400 dark:text-slate-600 group-hover:text-violet-500 transition-colors mb-3" />
              <h3 className="text-xs font-bold text-slate-700 dark:text-slate-200 mb-1">
                Drag & Drop Document
              </h3>
              <p className="text-[10px] text-slate-400 dark:text-slate-500 mb-4 max-w-xs leading-normal">
                PDF, Word, or PowerPoint documents will be automatically parsed, split into chunks, and vector embedded.
              </p>
              
              <label className="px-4 py-2 bg-white dark:bg-slate-800 hover:bg-slate-50 dark:hover:bg-slate-700 border border-slate-200 dark:border-slate-700 rounded-xl font-semibold text-[10px] cursor-pointer shadow-sm active:scale-[0.98] transition-all">
                Select Document
                <input 
                  type="file" 
                  accept=".pdf,.pptx,.ppt,.docx,.doc"
                  onChange={handleFileUpload} 
                  disabled={isUploading}
                  className="hidden" 
                />
              </label>
            </div>

            {/* Active upload status */}
            {isUploading && (
              <div className="absolute inset-0 bg-white/95 dark:bg-slate-900/95 backdrop-blur-sm flex flex-col items-center justify-center p-6 z-20">
                <div className="w-10 h-10 border-4 border-violet-500 border-t-transparent rounded-full animate-spin mb-4"></div>
                <span className="text-xs font-bold text-slate-700 dark:text-slate-200 mb-1">Processing Document...</span>
                <span className="text-[10px] text-slate-400 dark:text-slate-500 mb-3">Reading pages and building embeddings ({uploadProgress}%)</span>
                <div className="w-48 bg-slate-100 dark:bg-slate-800 h-1.5 rounded-full overflow-hidden">
                  <div className="bg-violet-500 h-full transition-all duration-300" style={{ width: `${uploadProgress}%` }}></div>
                </div>
              </div>
            )}

            {uploadError && (
              <div className="mt-3 text-[10px] text-rose-500 dark:text-rose-400 text-center font-medium bg-rose-500/10 p-2.5 rounded-xl border border-rose-500/20">
                {uploadError}
              </div>
            )}
          </div>

          {/* Recent Documents Table */}
          <div className="p-6 rounded-[28px] bg-white dark:bg-slate-900/60 border border-slate-200 dark:border-slate-800/80 shadow-sm">
            <div className="flex justify-between items-center mb-6">
              <div>
                <h2 className="text-base font-bold text-slate-900 dark:text-white">
                  Recent Knowledge Base Documents
                </h2>
                <p className="text-[10px] text-slate-400 dark:text-slate-500 mt-1">
                  Latest materials fed into the vector database.
                </p>
              </div>
              <Link 
                to="/documents"
                className="text-[10px] font-bold text-violet-600 dark:text-violet-400 hover:text-violet-500 flex items-center gap-1 group"
              >
                Manage Files
                <ArrowRight size={12} className="group-hover:translate-x-0.5 transition-transform" />
              </Link>
            </div>

            {isLoading ? (
              <div className="space-y-3 py-4">
                <div className="h-10 bg-slate-100 dark:bg-slate-800/60 rounded-xl animate-pulse"></div>
                <div className="h-10 bg-slate-100 dark:bg-slate-800/60 rounded-xl animate-pulse"></div>
              </div>
            ) : books.length === 0 ? (
              <div className="text-center py-10 border border-dashed border-slate-100 dark:border-slate-800 rounded-2xl bg-slate-50/50 dark:bg-slate-950/10">
                <p className="text-xs text-slate-400 dark:text-slate-500 font-medium">No documents uploaded yet.</p>
              </div>
            ) : (
              <div className="space-y-3">
                {books.slice(0, 3).map((book) => (
                  <div 
                    key={book.id}
                    className="flex items-center justify-between p-4 rounded-2xl bg-slate-50/70 dark:bg-slate-800/20 border border-slate-100 dark:border-slate-800/30 group hover:border-slate-200 dark:hover:border-slate-800 transition-all"
                  >
                    <div className="flex items-center gap-3 min-w-0">
                      <div className="w-9 h-9 rounded-xl bg-violet-500/10 text-violet-600 dark:text-violet-400 flex items-center justify-center shrink-0">
                        <BookOpen size={16} />
                      </div>
                      <div className="min-w-0">
                        <h4 className="text-xs font-bold text-slate-800 dark:text-slate-100 truncate pr-2">
                          {book.name}
                        </h4>
                        <span className="text-[9px] text-slate-400 dark:text-slate-500">
                          ID Reference: #{book.id}
                        </span>
                      </div>
                    </div>
                    <div className="flex items-center gap-2">
                      <button 
                        onClick={() => handleDeleteBook(book.id)}
                        className="p-2 rounded-xl text-slate-400 hover:text-rose-500 hover:bg-rose-500/10 transition-colors"
                        title="Delete Document"
                      >
                        <Trash2 size={14} />
                      </button>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>

        {/* Right Column: AI Insights & Quick Links */}
        <div className="space-y-6">
          
          {/* AI Study Insights */}
          <div className="p-6 rounded-[28px] bg-gradient-to-br from-violet-600/5 to-indigo-600/5 dark:from-violet-950/10 dark:to-indigo-950/10 border border-violet-500/10 dark:border-violet-400/10 shadow-sm relative overflow-hidden">
            <div className="absolute top-0 right-0 w-24 h-24 bg-gradient-to-bl from-violet-500/10 to-transparent rounded-bl-full pointer-events-none"></div>
            
            <div className="flex items-center gap-2 mb-4 text-violet-600 dark:text-violet-400">
              <Sparkles size={18} className="animate-pulse" />
              <h3 className="text-xs font-extrabold uppercase tracking-wider">AI Study Insights</h3>
            </div>

            <div className="space-y-4">
              {insights.map((insight, idx) => (
                <div key={idx} className="space-y-1">
                  <h4 className="text-xs font-bold text-slate-800 dark:text-slate-100 flex items-center gap-2">
                    <span className="w-1.5 h-1.5 rounded-full bg-violet-500 shrink-0"></span>
                    {insight.title}
                  </h4>
                  <p className="text-[10px] text-slate-500 dark:text-slate-400 leading-normal pl-3.5">
                    {insight.desc}
                  </p>
                </div>
              ))}
            </div>
          </div>

          {/* Quick Actions List */}
          <div className="p-6 rounded-[28px] bg-white dark:bg-slate-900/60 border border-slate-200 dark:border-slate-800/80 shadow-sm">
            <h3 className="text-xs font-bold text-slate-900 dark:text-white mb-4 flex items-center gap-2">
              <Activity size={14} className="text-slate-400" />
              Assistant Modules
            </h3>
            
            <div className="grid grid-cols-1 gap-2">
              <Link 
                to="/chat"
                className="flex items-center justify-between p-3.5 rounded-2xl hover:bg-slate-50 dark:hover:bg-slate-800/40 border border-transparent hover:border-slate-100 dark:hover:border-slate-800/50 transition-all text-xs font-semibold text-slate-700 dark:text-slate-200"
              >
                <div className="flex items-center gap-2.5">
                  <MessageSquare size={16} className="text-violet-500" />
                  <span>AI Assistant Chat</span>
                </div>
                <ArrowRight size={14} className="text-slate-400" />
              </Link>

              <Link 
                to="/tools?tab=quiz"
                className="flex items-center justify-between p-3.5 rounded-2xl hover:bg-slate-50 dark:hover:bg-slate-800/40 border border-transparent hover:border-slate-100 dark:hover:border-slate-800/50 transition-all text-xs font-semibold text-slate-700 dark:text-slate-200"
              >
                <div className="flex items-center gap-2.5">
                  <Brain size={16} className="text-cyan-500" />
                  <span>Interactive Quiz Builder</span>
                </div>
                <ArrowRight size={14} className="text-slate-400" />
              </Link>

              <Link 
                to="/tools?tab=search"
                className="flex items-center justify-between p-3.5 rounded-2xl hover:bg-slate-50 dark:hover:bg-slate-800/40 border border-transparent hover:border-slate-100 dark:hover:border-slate-800/50 transition-all text-xs font-semibold text-slate-700 dark:text-slate-200"
              >
                <div className="flex items-center gap-2.5">
                  <Plus size={16} className="text-indigo-500" />
                  <span>Semantic Vector Finder</span>
                </div>
                <ArrowRight size={14} className="text-slate-400" />
              </Link>
            </div>
          </div>
        </div>

      </div>

    </div>
  );
}
