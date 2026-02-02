import { AnimatePresence, motion } from 'framer-motion';
import {
    Activity,
    ChevronRight,
    Database,
    History,
    Search,
    Zap
} from 'lucide-react';
import { useEffect, useRef, useState } from 'react';
import {
    Area,
    AreaChart,
    CartesianGrid,
    ResponsiveContainer,
    Tooltip,
    XAxis, YAxis
} from 'recharts';

// Глобальные стили для графиков
const chartColors = ['#3b82f6', '#8b5cf6', '#10b981', '#f59e0b', '#ef4444'];

const Dashboard = () => {
  const [progress, setProgress] = useState({
    overall: { total: 0, completed: 0, status: "Idle", speed: 0 },
    chats: {},
    active_downloads: {}
  });
  const [stats, setStats] = useState({ enabled: false, chats: [], history: [] });
  const [connected, setConnected] = useState(false);
  const ws = useRef(null);

  useEffect(() => {
    connectWS();
    fetchStats();
    return () => ws.current?.close();
  }, []);

  const connectWS = () => {
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const host = window.location.hostname === 'localhost' ? 'localhost:8000' : window.location.host;
    ws.current = new WebSocket(`${protocol}//${host}/ws/progress`);

    ws.current.onopen = () => setConnected(true);
    ws.current.onclose = () => {
      setConnected(false);
      setTimeout(connectWS, 3000);
    };
    ws.current.onmessage = (event) => {
      setProgress(JSON.parse(event.data));
    };
  };

  const fetchStats = async () => {
    try {
      const res = await fetch('/api/stats');
      const data = await res.json();
      setStats(data);
    } catch (e) {
      console.error("Failed to fetch stats", e);
    }
  };

  const overallPercentage = progress.overall.total > 0
    ? Math.round((progress.overall.completed / progress.overall.total) * 100)
    : 0;

  return (
    <div className="app-container p-4 md:p-8 max-w-7xl mx-auto space-y-8">
      {/* Header */}
      <header className="flex flex-col md:flex-row justify-between items-start md:items-center gap-4">
        <div>
          <h1 className="text-3xl font-extrabold premium-gradient-text tracking-tight">
            TMD Dashboard
          </h1>
          <p className="text-sm text-slate-400 mt-1 flex items-center gap-2">
            <Database size={14} /> ClickHouse Engine Active
          </p>
        </div>
        <div className={`flex items-center gap-2 px-3 py-1.5 rounded-full border text-xs font-medium ${
          connected ? 'bg-emerald-500/10 border-emerald-500/20 text-emerald-400' : 'bg-rose-500/10 border-rose-500/20 text-rose-400'
        }`}>
          <div className={`w-2 h-2 rounded-full ${connected ? 'bg-emerald-400 animate-pulse' : 'bg-rose-400'}`} />
          {connected ? 'CONNECTED' : 'DISCONNECTED'}
        </div>
      </header>

      {/* Hero Progress Section */}
      <motion.section
        initial={{ opacity: 0, y: 20 }}
        animate={{ opacity: 1, y: 0 }}
        className="glass-card p-6 md:p-8 relative overflow-hidden"
      >
        <div className="relative z-10 grid grid-cols-1 md:grid-cols-2 gap-8 items-center">
          <div className="space-y-4">
            <h2 className="text-xl font-bold flex items-center gap-2 text-slate-200">
              <Zap size={20} className="text-blue-400" /> Overall Progress
            </h2>
            <div className="flex items-end gap-3">
              <span className="text-5xl font-black text-white">{overallPercentage}%</span>
              <span className="text-blue-400 font-medium mb-1">{progress.overall.status}</span>
            </div>

            <div className="space-y-2">
              <div className="h-3 w-full bg-slate-800 rounded-full overflow-hidden border border-slate-700/50">
                <motion.div
                  initial={{ width: 0 }}
                  animate={{ width: `${overallPercentage}%` }}
                  transition={{ duration: 1, ease: "easeOut" }}
                  className="h-full bg-gradient-to-r from-blue-500 to-indigo-500 rounded-full"
                />
              </div>
              <div className="flex justify-between text-xs text-slate-400 font-medium">
                <span>{progress.overall.completed} CHATS</span>
                <span>{progress.overall.total} TOTAL</span>
              </div>
            </div>
          </div>

          <div className="grid grid-cols-2 gap-4">
            <div className="bg-slate-900/50 p-4 rounded-xl border border-white/5">
              <p className="text-xs text-slate-500 uppercase tracking-wider font-bold">Speed</p>
              <p className="text-2xl font-bold text-slate-100">{progress.overall.speed || '---'} MB/s</p>
            </div>
            <div className="bg-slate-900/50 p-4 rounded-xl border border-white/5">
              <p className="text-xs text-slate-500 uppercase tracking-wider font-bold">Time Left</p>
              <p className="text-2xl font-bold text-slate-100">Calculating...</p>
            </div>
          </div>
        </div>
      </motion.section>

      {/* Main Grid */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-8">

        {/* Left Column: Active Downloads */}
        <div className="lg:col-span-2 space-y-8">
          <section className="glass-card p-6">
            <h3 className="card-title text-slate-200"><Activity size={18} className="text-blue-400" /> Active Threads</h3>
            <div className="space-y-4 mt-4">
              <AnimatePresence>
                {Object.entries(progress.active_downloads).length === 0 ? (
                  <motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }} className="text-center py-10 text-slate-500 bg-slate-900/20 rounded-xl border border-dashed border-slate-800">
                    <p>No active media downloads</p>
                  </motion.div>
                ) : (
                  Object.entries(progress.active_downloads).map(([id, dl]) => {
                    const pct = dl.total > 0 ? Math.round((dl.completed / dl.total) * 100) : 0;
                    return (
                      <motion.div
                        key={id}
                        initial={{ opacity: 0, x: -20 }}
                        animate={{ opacity: 1, x: 0 }}
                        exit={{ opacity: 0, x: 20 }}
                        className="bg-slate-900/40 p-4 rounded-xl border border-white/5 hover:border-blue-500/30 transition-all group"
                      >
                        <div className="flex justify-between items-center mb-2">
                          <span className="text-sm font-semibold text-slate-200 truncate pr-4">{dl.description}</span>
                          <span className="text-xs font-mono text-blue-400">{pct}%</span>
                        </div>
                        <div className="h-1.5 w-full bg-slate-800 rounded-full overflow-hidden">
                          <motion.div
                            initial={{ width: 0 }}
                            animate={{ width: `${pct}%` }}
                            className="h-full bg-blue-500 shadow-[0_0_8px_rgba(59,130,246,0.5)]"
                          />
                        </div>
                      </motion.div>
                    );
                  })
                )}
              </AnimatePresence>
            </div>
          </section>

          {/* Charts Section */}
          {stats.enabled && (
             <section className="glass-card p-6">
                <h3 className="card-title text-slate-200"><History size={18} className="text-indigo-400" /> Download History</h3>
                <div className="h-64 mt-6">
                  <ResponsiveContainer width="100%" height="100%">
                    <AreaChart data={stats.history}>
                      <defs>
                        <linearGradient id="colorCount" x1="0" y1="0" x2="0" y2="1">
                          <stop offset="5%" stopColor="#3b82f6" stopOpacity={0.3}/>
                          <stop offset="95%" stopColor="#3b82f6" stopOpacity={0}/>
                        </linearGradient>
                      </defs>
                      <CartesianGrid strokeDasharray="3 3" stroke="#334155" vertical={false} />
                      <XAxis dataKey="date" stroke="#64748b" fontSize={10} tickLine={false} axisLine={false} />
                      <YAxis stroke="#64748b" fontSize={10} tickLine={false} axisLine={false} />
                      <Tooltip
                        contentStyle={{ backgroundColor: '#1e293b', border: '1px solid #334155', borderRadius: '8px' }}
                        itemStyle={{ color: '#fff' }}
                      />
                      <Area type="monotone" dataKey="count" stroke="#3b82f6" fillOpacity={1} fill="url(#colorCount)" strokeWidth={2} />
                    </AreaChart>
                  </ResponsiveContainer>
                </div>
             </section>
          )}
        </div>

        {/* Right Column: Chat Queue & Stats */}
        <div className="space-y-8">
          <section className="glass-card p-6 h-full flex flex-col">
            <h3 className="card-title text-slate-200"><Search size={18} className="text-emerald-400" /> Chat Statistics</h3>
            <div className="flex-1 overflow-y-auto mt-4 space-y-3 pr-2 scroll-list">
               {stats.chats.map((chat, idx) => (
                 <div key={idx} className="bg-slate-900/40 p-3 rounded-lg border border-white/5 flex items-center justify-between hover:bg-slate-800/50 transition-colors cursor-default">
                    <div>
                      <p className="text-sm font-bold text-slate-200">{chat.title}</p>
                      <p className="text-[10px] text-slate-500 font-medium">{(chat.size / (1024*1024*1024)).toFixed(2)} GB • {chat.count} msgs</p>
                    </div>
                    <ChevronRight size={14} className="text-slate-600" />
                 </div>
               ))}
               {stats.chats.length === 0 && (
                 <p className="text-center py-8 text-slate-600 italic">No historical data found</p>
               )}
            </div>
          </section>
        </div>

      </div>

      <footer className="text-center text-slate-600 text-[10px] uppercase tracking-widest font-bold pb-4">
        Telegram Media Downloader © 2026 • Premium Analytics Edition
      </footer>
    </div>
  );
};

export default Dashboard;
