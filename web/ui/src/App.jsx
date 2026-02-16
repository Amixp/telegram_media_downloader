import { AnimatePresence, motion } from "framer-motion";
import {
  Activity,
  ChevronLeft,
  ChevronRight,
  Database,
  Download,
  FileText,
  FolderOpen,
  History,
  Image,
  Music,
  Search,
  Zap,
} from "lucide-react";
import { Component, useCallback, useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { Virtuoso } from "react-virtuoso";
import {
  Area,
  AreaChart,
  CartesianGrid,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

// Error Boundary: ловит падения в дочерних компонентах, чтобы не очищать всю страницу
class ErrorBoundary extends Component {
  state = { hasError: false, error: null };

  static getDerivedStateFromError(error) {
    return { hasError: true, error };
  }

  componentDidCatch(error, info) {
    console.error("ErrorBoundary:", error, info);
  }

  render() {
    if (this.state.hasError) {
      return (
        this.props.fallback ?? (
          <div className="p-4 rounded-lg bg-rose-900/30 border border-rose-500/50 text-rose-200 text-sm">
            Ошибка отображения
          </div>
        )
      );
    }
    return this.props.children;
  }
}

// Глобальные стили для графиков
const chartColors = ["#3b82f6", "#8b5cf6", "#10b981", "#f59e0b", "#ef4444"];

// Скользящее среднее для скорости и ETA (окно последних N значений)
const SMOOTH_WINDOW = 12;

const PAGE_SIZE = 100;
const MAX_IN_MEMORY = 500;
const LOGS_PAGE_SIZE = 100;
const FILES_PAGE_SIZE = 50;
const LOGS_DEBOUNCE_MS = 400;

// Индикатор загрузки — «ромашка» с переменными лепестками (пульсирующие точки по кругу)
const FlowerLoader = ({ size = 28, className = "", color = "amber" }) => {
  const colorClass = color === "emerald" ? "bg-emerald-400" : "bg-amber-400";
  return (
    <div
      className={`relative inline-flex items-center justify-center ${className}`}
      style={{ width: size, height: size }}
    >
      {[...Array(8)].map((_, i) => (
        <motion.span
          key={i}
          className={`absolute rounded-full ${colorClass}`}
          style={{
            width: size * 0.2,
            height: size * 0.2,
            top: "50%",
            left: "50%",
            transformOrigin: "center center",
            transform: `rotate(${i * 45}deg) translateY(-${size * 0.45}px)`,
          }}
          animate={{ opacity: [0.2, 1, 0.2], scale: [0.8, 1.1, 0.8] }}
          transition={{
            duration: 0.9,
            repeat: Infinity,
            delay: i * 0.1,
          }}
        />
      ))}
    </div>
  );
};

// Парсит текст сообщения на сегменты: текст и ссылки t.me (c/ID, username).
// Возвращает массив { type: 'text'|'link', value?, href?, matchedChat?, postId? }.
function parseTelegramLinks(text, chats) {
  if (!text || typeof text !== "string")
    return [{ type: "text", value: text || "" }];
  const segments = [];
  const re =
    /(https?:\/\/)?(?:www\.)?t\.me\/(c\/(\d+)(?:\/(\d+))?|([a-zA-Z0-9_]+)(?:\/(\d+))?)/gi;
  let lastIndex = 0;
  let m;
  while ((m = re.exec(text)) !== null) {
    if (m.index > lastIndex)
      segments.push({ type: "text", value: text.slice(lastIndex, m.index) });
    const raw = m[0];
    const href = /^https?:/i.test(raw) ? raw : `https://t.me/${m[2]}`;
    const channelId = m[3];
    const postId = m[4] || m[6] || null;
    const username = m[5];
    let matchedChat = null;
    if (channelId && Array.isArray(chats)) {
      const idNorm = String(channelId).replace(/^-100/, "");
      matchedChat = chats.find(
        (c) => String(c.chat_id).replace(/^-100/, "") === idNorm,
      );
    }
    segments.push({
      type: "link",
      value: raw,
      href,
      matchedChat,
      postId: postId ? parseInt(postId, 10) : null,
      username: username || null,
    });
    lastIndex = re.lastIndex;
  }
  if (lastIndex < text.length)
    segments.push({ type: "text", value: text.slice(lastIndex) });
  return segments.length ? segments : [{ type: "text", value: text }];
}

const ChatViewer = ({
  chatId,
  title,
  onClose,
  initialMessageId,
  chats = [],
  onOpenChat,
}) => {
  const [items, setItems] = useState([]);
  const [startOffset, setStartOffset] = useState(0);
  const [firstItemIndex, setFirstItemIndex] = useState(0);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(false);
  const [loadingTop, setLoadingTop] = useState(false);
  const [loadingBottom, setLoadingBottom] = useState(false);
  const loadingRef = useRef(false);
  const [openFileManager, setOpenFileManager] = useState(false);
  const [pathModal, setPathModal] = useState(null);
  const virtuosoRef = useRef(null);
  const scrolledToInitialRef = useRef(false);
  const [searchQuery, setSearchQuery] = useState("");

  const fetchPage = useCallback(
    async (offset, limit = PAGE_SIZE) => {
      const res = await fetch(
        `/api/chat/${chatId}/messages?offset=${offset}&limit=${limit}`,
      );
      const data = await res.json();
      return data;
    },
    [chatId],
  );

  const loadInitial = useCallback(async () => {
    if (loadingRef.current) return;
    loadingRef.current = true;
    setLoading(true);
    try {
      const { messages, total: t } = await fetchPage(0);
      setItems(messages || []);
      setStartOffset(0);
      setFirstItemIndex(0);
      setTotal(t || 0);
    } finally {
      setLoading(false);
      loadingRef.current = false;
    }
  }, [fetchPage]);

  useEffect(() => {
    scrolledToInitialRef.current = false;
  }, [chatId]);

  useEffect(() => {
    loadInitial();
  }, [chatId, loadInitial]);

  useEffect(() => {
    if (
      scrolledToInitialRef.current ||
      searchQuery.trim() ||
      initialMessageId == null ||
      items.length === 0
    )
      return;
    const idx = items.findIndex((m) => m.id === initialMessageId);
    if (idx >= 0) {
      scrolledToInitialRef.current = true;
      setTimeout(
        () =>
          virtuosoRef.current?.scrollToIndex({
            index: idx,
            behavior: "smooth",
          }),
        100,
      );
    }
  }, [items, initialMessageId, searchQuery]);

  useEffect(() => {
    fetch("/api/settings")
      .then((r) => r.json())
      .then((d) => setOpenFileManager(!!d.open_file_manager))
      .catch(() => setOpenFileManager(false));
  }, []);

  const handleOpenFolder = useCallback(
    async (e, messageId) => {
      e.preventDefault();
      e.stopPropagation();
      try {
        const res = await fetch(
          `/api/chat/${chatId}/message/${messageId}/path`,
        );
        const data = await res.json();
        if (data.dir)
          setPathModal({ messageId, dir: data.dir, file: data.file || "" });
      } catch (err) {
        console.error(err);
      }
    },
    [chatId],
  );

  const handleCopyPath = useCallback((dir) => {
    navigator.clipboard.writeText(dir).catch(() => {});
    setPathModal(null);
  }, []);

  const handleOpenInExplorer = useCallback(
    async (messageId) => {
      try {
        const res = await fetch(
          `/api/chat/${chatId}/message/${messageId}/open_folder`,
          { method: "POST" },
        );
        if (!res.ok) {
          const d = await res.json().catch(() => ({}));
          alert(d.detail || "Ошибка");
          return;
        }
        setPathModal(null);
      } catch (err) {
        alert(err.message || "Ошибка");
      }
    },
    [chatId],
  );

  const loadMoreTop = useCallback(async () => {
    if (startOffset <= 0 || loadingRef.current) return;
    loadingRef.current = true;
    setLoadingTop(true);
    try {
      const off = Math.max(0, startOffset - PAGE_SIZE);
      const { messages } = await fetchPage(off);
      if (!messages?.length) return;
      const newItems = [...messages, ...items];
      setItems(
        newItems.length > MAX_IN_MEMORY
          ? newItems.slice(0, MAX_IN_MEMORY)
          : newItems,
      );
      setStartOffset(off);
      if (newItems.length > MAX_IN_MEMORY) {
        setFirstItemIndex((i) => i + (newItems.length - MAX_IN_MEMORY));
      } else {
        setFirstItemIndex((i) => i + messages.length);
      }
    } finally {
      setLoadingTop(false);
      loadingRef.current = false;
    }
  }, [fetchPage, items, startOffset]);

  const loadMoreBottom = useCallback(async () => {
    if (loadingRef.current) return;
    const endOffset = startOffset + items.length;
    if (total > 0 && endOffset >= total) return;
    loadingRef.current = true;
    setLoadingBottom(true);
    try {
      const { messages } = await fetchPage(endOffset);
      if (!messages?.length) return;
      let newItems = [...items, ...messages];
      let newStart = startOffset;
      let newFirst = firstItemIndex;
      if (newItems.length > MAX_IN_MEMORY) {
        const drop = newItems.length - MAX_IN_MEMORY;
        newItems = newItems.slice(drop);
        newStart += drop;
        newFirst += drop;
      }
      setItems(newItems);
      setStartOffset(newStart);
      setFirstItemIndex(newFirst);
    } finally {
      setLoadingBottom(false);
      loadingRef.current = false;
    }
  }, [fetchPage, items, startOffset, firstItemIndex, total]);

  const formatDate = (d) => {
    if (!d) return "";
    try {
      const dt = new Date(d);
      return dt.toLocaleString();
    } catch {
      return String(d);
    }
  };

  const searchQueryLower = searchQuery.trim().toLowerCase();
  const filteredItems = searchQueryLower
    ? items.filter((msg) => {
        const text = (msg.text || "").toLowerCase();
        const media = (msg.media_type || "").toLowerCase();
        return (
          text.includes(searchQueryLower) || media.includes(searchQueryLower)
        );
      })
    : items;

  const handleBackdropClick = (e) => {
    if (e.target === e.currentTarget) onClose();
  };

  if (loading && items.length === 0) {
    return (
      <div
        className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 backdrop-blur-sm"
        onClick={handleBackdropClick}
      >
        <div
          className="glass-card p-8 max-w-2xl w-full mx-4 max-h-[90vh] flex flex-col"
          onClick={(e) => e.stopPropagation()}
        >
          <div className="flex items-center justify-between mb-4">
            <h2 className="text-xl font-bold text-white">{title}</h2>
            <button
              onClick={onClose}
              className="p-2 text-slate-400 hover:text-white rounded"
            >
              <ChevronLeft size={20} />
            </button>
          </div>
          <p className="text-slate-400 text-center py-12">
            Загрузка сообщений…
          </p>
        </div>
      </div>
    );
  }

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 backdrop-blur-sm p-4"
      onClick={handleBackdropClick}
    >
      <div
        className="glass-card flex flex-col max-w-2xl w-full max-h-[90vh]"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between p-4 border-b border-slate-700/50 shrink-0">
          <h2 className="text-xl font-bold text-white truncate pr-4">
            {title}
          </h2>
          <button
            onClick={onClose}
            className="p-2 text-slate-400 hover:text-white rounded hover:bg-slate-700/50 transition-colors"
          >
            <ChevronLeft size={20} />
          </button>
        </div>
        <div className="px-4 py-2 border-b border-slate-700/30 shrink-0 flex items-center gap-2">
          <Search size={14} className="text-slate-500 shrink-0" />
          <input
            type="text"
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            placeholder="Поиск по сообщениям…"
            className="flex-1 bg-slate-800/50 border border-slate-600/50 rounded px-3 py-1.5 text-sm text-white placeholder-slate-500 focus:outline-none focus:border-emerald-500/50"
          />
        </div>
        <p className="text-xs text-slate-500 px-4 py-1 shrink-0">
          {searchQuery.trim()
            ? `${filteredItems.length} из ${items.length}`
            : items.length}{" "}
          из {total} сообщений (подгрузка при прокрутке)
        </p>
        <div className="flex-1 min-h-0 relative">
          <Virtuoso
            ref={virtuosoRef}
            style={{ height: "100%", minHeight: 360 }}
            data={filteredItems}
            firstItemIndex={searchQuery.trim() ? 0 : firstItemIndex}
            startReached={loadMoreTop}
            endReached={loadMoreBottom}
            overscan={200}
            itemContent={(idx, msg) => {
              if (!msg) return null;
              const fileUrl = msg.downloaded_file
                ? `/api/chat/${chatId}/message/${msg.id}/file`
                : null;
              const fileName = msg.downloaded_file
                ? msg.downloaded_file.replace(/^.*[/\\]/, "")
                : "";
              const mt = (msg.media_type || "").toLowerCase();
              return (
                <div className="px-4 py-2 border-b border-slate-700/30 hover:bg-slate-800/30">
                  <div className="flex flex-col gap-1">
                    <p className="text-[10px] text-slate-500 font-medium">
                      {formatDate(msg.date)}
                    </p>
                    {msg.text ? (
                      <p className="text-sm text-slate-200 break-words whitespace-pre-wrap">
                        {parseTelegramLinks(msg.text, chats).map((seg, i) =>
                          seg.type === "text" ? (
                            <span key={i}>{seg.value}</span>
                          ) : seg.matchedChat ? (
                            <a
                              key={i}
                              href="#"
                              onClick={(e) => {
                                e.preventDefault();
                                onOpenChat?.({
                                  chat_id: seg.matchedChat.chat_id,
                                  title: seg.matchedChat.title,
                                  initialMessageId: seg.postId ?? undefined,
                                });
                              }}
                              className="text-emerald-400 hover:underline"
                            >
                              {seg.value}
                            </a>
                          ) : (
                            <a
                              key={i}
                              href={seg.href}
                              target="_blank"
                              rel="noopener noreferrer"
                              className="text-sky-400 hover:underline"
                            >
                              {seg.value}
                            </a>
                          ),
                        )}
                      </p>
                    ) : null}
                    {msg.has_media && (
                      <div
                        className="mt-1.5 rounded-lg overflow-hidden bg-slate-800/50 border border-slate-600/30 max-w-full"
                        onMouseDown={(e) => {
                          if (e.ctrlKey && fileUrl) {
                            e.preventDefault();
                            handleOpenFolder(e, msg.id);
                          }
                        }}
                      >
                        {fileUrl ? (
                          <>
                            {mt === "photo" && (
                              <a
                                href={fileUrl}
                                target="_blank"
                                rel="noopener noreferrer"
                                className="block"
                              >
                                <img
                                  src={fileUrl}
                                  alt=""
                                  className="max-h-[200px] w-auto object-contain"
                                  loading="lazy"
                                />
                              </a>
                            )}
                            {(mt === "video" || mt === "video_note") && (
                              <div className="relative">
                                <video
                                  src={fileUrl}
                                  controls
                                  preload="metadata"
                                  className="max-h-[240px] w-full"
                                />
                              </div>
                            )}
                            {(mt === "voice" || mt === "audio") && (
                              <div className="p-2 flex items-center gap-2">
                                <Music
                                  size={18}
                                  className="text-slate-400 shrink-0"
                                />
                                <audio
                                  src={fileUrl}
                                  controls
                                  className="flex-1 max-w-full"
                                />
                              </div>
                            )}
                            {mt !== "photo" &&
                              mt !== "video" &&
                              mt !== "video_note" &&
                              mt !== "voice" &&
                              mt !== "audio" && (
                                <a
                                  href={fileUrl}
                                  download={fileName}
                                  target="_blank"
                                  rel="noopener noreferrer"
                                  className="flex items-center gap-2 p-2 text-slate-200 hover:bg-slate-700/50"
                                >
                                  <FileText
                                    size={18}
                                    className="text-slate-400 shrink-0"
                                  />
                                  <span className="truncate flex-1 min-w-0">
                                    {fileName || msg.media_type || "File"}
                                  </span>
                                  {msg.file_size > 0 && (
                                    <span className="text-xs text-slate-500 shrink-0">
                                      {(msg.file_size / 1024).toFixed(1)} KB
                                    </span>
                                  )}
                                  <Download
                                    size={14}
                                    className="shrink-0 text-emerald-400"
                                  />
                                </a>
                              )}
                            <div className="px-2 pb-1.5 flex justify-end">
                              <button
                                type="button"
                                onClick={(ev) => handleOpenFolder(ev, msg.id)}
                                className="flex items-center gap-1 text-xs text-slate-500 hover:text-emerald-400 transition-colors"
                                title="Папка с файлом (Ctrl+клик)"
                              >
                                <FolderOpen size={14} />
                                Папка
                              </button>
                            </div>
                          </>
                        ) : (
                          <div className="flex items-center gap-2 p-2 text-slate-500 text-sm">
                            <Image size={16} className="shrink-0" />
                            <span>
                              {msg.media_type || "Media"}
                              {msg.file_size > 0 &&
                                ` (${(msg.file_size / 1024).toFixed(1)} KB)`}
                            </span>
                            <span className="text-xs">— не скачано</span>
                          </div>
                        )}
                      </div>
                    )}
                  </div>
                </div>
              );
            }}
            components={{
              Header: () =>
                loadingTop ? (
                  <div className="px-4 py-2 text-center text-slate-500 text-sm">
                    Загрузка…
                  </div>
                ) : null,
              Footer: () =>
                loadingBottom ? (
                  <div className="px-4 py-2 text-center text-slate-500 text-sm">
                    Загрузка…
                  </div>
                ) : null,
            }}
          />
        </div>
        {pathModal && (
          <div
            className="absolute inset-0 z-10 flex items-center justify-center bg-black/60 rounded-lg p-4"
            onClick={() => setPathModal(null)}
          >
            <div
              className="bg-slate-800 border border-slate-600 rounded-lg p-4 max-w-md w-full shadow-xl"
              onClick={(e) => e.stopPropagation()}
            >
              <p className="text-xs text-slate-500 mb-1">Папка с файлом:</p>
              <p className="text-sm text-slate-200 break-all font-mono mb-3">
                {pathModal.dir}
              </p>
              <div className="flex flex-wrap gap-2">
                <button
                  type="button"
                  onClick={() => handleCopyPath(pathModal.dir)}
                  className="px-3 py-1.5 rounded bg-slate-700 text-slate-200 text-sm hover:bg-slate-600"
                >
                  Скопировать путь
                </button>
                {openFileManager && (
                  <button
                    type="button"
                    onClick={() => handleOpenInExplorer(pathModal.messageId)}
                    className="px-3 py-1.5 rounded bg-emerald-700/50 text-emerald-200 text-sm hover:bg-emerald-600/50"
                  >
                    Открыть в проводнике
                  </button>
                )}
                <button
                  type="button"
                  onClick={() => setPathModal(null)}
                  className="px-3 py-1.5 rounded bg-slate-700 text-slate-400 text-sm hover:bg-slate-600"
                >
                  Закрыть
                </button>
              </div>
            </div>
          </div>
        )}
      </div>
    </div>
  );
};

// Debounce для поиска/фильтра — уменьшает число запросов при вводе
const useDebounce = (value, delay) => {
  const [debouncedValue, setDebouncedValue] = useState(value);
  useEffect(() => {
    const t = setTimeout(() => setDebouncedValue(value), delay);
    return () => clearTimeout(t);
  }, [value, delay]);
  return debouncedValue;
};

const LogsView = ({ enabled }) => {
  const [items, setItems] = useState([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(false);
  const [query, setQuery] = useState("");
  const [level, setLevel] = useState("");
  const [offset, setOffset] = useState(0);
  const debouncedQuery = useDebounce(query, LOGS_DEBOUNCE_MS);
  const debouncedLevel = useDebounce(level, LOGS_DEBOUNCE_MS);
  const abortRef = useRef(null);

  const fetchLogs = useCallback(
    async (off = 0, opts = {}) => {
      if (!enabled) return;
      abortRef.current?.abort();
      abortRef.current = new AbortController();
      const signal = abortRef.current.signal;
      const q = opts.q ?? debouncedQuery;
      const lvl = opts.level ?? debouncedLevel;

      setLoading(true);
      try {
        const params = new URLSearchParams({
          limit: String(LOGS_PAGE_SIZE),
          offset: String(off),
        });
        if (String(q || "").trim()) params.set("q", String(q).trim());
        if (lvl) params.set("level", lvl);
        const res = await fetch(`/api/logs?${params}`, { signal });
        const data = await res.json();
        if (signal.aborted) return;
        setItems(data.items || []);
        setTotal(data.total || 0);
        setOffset(off);
      } catch (err) {
        if (err.name === "AbortError") return;
        console.error("Logs fetch error:", err);
      } finally {
        if (!signal.aborted) setLoading(false);
      }
    },
    [enabled, debouncedQuery, debouncedLevel],
  );

  // Автообновление при смене debounced поиска/фильтра
  useEffect(() => {
    if (!enabled) return;
    fetchLogs(0);
  }, [enabled, debouncedQuery, debouncedLevel, fetchLogs]);

  const handleRefresh = useCallback(() => {
    fetchLogs(0, { q: query, level });
  }, [fetchLogs, query, level]);

  const formatTs = (ts) => {
    if (!ts) return "";
    try {
      return new Date(ts).toLocaleString();
    } catch {
      return String(ts);
    }
  };

  if (!enabled) {
    return (
      <section className="glass-card p-6">
        <h3 className="card-title text-white">
          <FileText size={18} className="text-amber-400" /> Логи
        </h3>
        <p className="text-slate-400 mt-4">
          Включите ClickHouse в config.yaml для сохранения и просмотра логов.
        </p>
      </section>
    );
  }

  return (
    <section className="glass-card p-6">
      <h3 className="card-title text-white">
        <FileText size={18} className="text-amber-400" /> Логи
      </h3>
      <div className="mt-4 flex flex-wrap gap-2 items-center">
        <Search size={14} className="text-slate-500 shrink-0" />
        <input
          type="text"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Поиск по тексту лога…"
          disabled={loading}
          className="bg-slate-800/50 border border-slate-600/50 rounded px-3 py-1.5 text-sm text-white placeholder-slate-500 focus:outline-none focus:border-amber-500/50 w-48 md:w-64 disabled:opacity-60 disabled:cursor-not-allowed"
        />
        <select
          value={level}
          onChange={(e) => setLevel(e.target.value)}
          disabled={loading}
          className="bg-slate-800/50 border border-slate-600/50 rounded px-3 py-1.5 text-sm text-white focus:outline-none focus:border-amber-500/50 disabled:opacity-60 disabled:cursor-not-allowed"
        >
          <option value="">Все уровни</option>
          <option value="DEBUG">DEBUG</option>
          <option value="INFO">INFO</option>
          <option value="WARNING">WARNING</option>
          <option value="ERROR">ERROR</option>
        </select>
        <button
          type="button"
          onClick={handleRefresh}
          disabled={loading}
          className="flex items-center gap-2 px-3 py-1.5 rounded bg-amber-500/20 text-amber-300 border border-amber-500/30 hover:bg-amber-500/30 disabled:opacity-60 disabled:cursor-not-allowed text-sm"
        >
          {loading ? <FlowerLoader size={18} /> : null}
          Обновить
        </button>
      </div>
      <p className="text-xs text-slate-500 mt-2 flex items-center gap-2">
        {loading ? <FlowerLoader size={14} /> : null}
        {total} записей
      </p>
      <div className="mt-4 overflow-x-auto max-h-[60vh] overflow-y-auto border border-slate-700/50 rounded-lg relative">
        {loading && items.length === 0 ? (
          <div className="p-8 flex flex-col items-center justify-center gap-3 text-slate-400">
            <FlowerLoader size={40} />
            <span>Загрузка логов…</span>
          </div>
        ) : (
          <table className="w-full text-sm">
            <thead className="bg-slate-800/80 sticky top-0">
              <tr>
                <th className="text-left p-2 text-slate-400 font-medium">
                  Время
                </th>
                <th className="text-left p-2 text-slate-400 font-medium w-20">
                  Уровень
                </th>
                <th className="text-left p-2 text-slate-400 font-medium">
                  Логгер
                </th>
                <th className="text-left p-2 text-slate-400 font-medium">
                  Сообщение
                </th>
              </tr>
            </thead>
            <tbody>
              {items.map((row, i) => (
                <tr
                  key={i}
                  className="border-t border-slate-700/30 hover:bg-slate-800/40"
                >
                  <td className="p-2 text-slate-500 whitespace-nowrap">
                    {formatTs(row.ts)}
                  </td>
                  <td className="p-2">
                    <span
                      className={`font-mono text-xs ${
                        row.level === "ERROR"
                          ? "text-rose-400"
                          : row.level === "WARNING"
                            ? "text-amber-400"
                            : "text-slate-300"
                      }`}
                    >
                      {row.level}
                    </span>
                  </td>
                  <td className="p-2 text-slate-500 truncate max-w-[120px]">
                    {row.logger_name}
                  </td>
                  <td className="p-2 text-slate-200 break-words">
                    {row.message}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
      {total > LOGS_PAGE_SIZE && (
        <div className="mt-2 flex gap-2">
          <button
            type="button"
            disabled={offset <= 0 || loading}
            onClick={() => fetchLogs(Math.max(0, offset - LOGS_PAGE_SIZE))}
            className="px-2 py-1 rounded bg-slate-700/50 text-slate-300 disabled:opacity-50 text-sm"
          >
            Назад
          </button>
          <span className="text-slate-500 text-sm py-1">
            {offset + 1}–{Math.min(offset + LOGS_PAGE_SIZE, total)} из {total}
          </span>
          <button
            type="button"
            disabled={offset + LOGS_PAGE_SIZE >= total || loading}
            onClick={() => fetchLogs(offset + LOGS_PAGE_SIZE)}
            className="px-2 py-1 rounded bg-slate-700/50 text-slate-300 disabled:opacity-50 text-sm"
          >
            Вперёд
          </button>
        </div>
      )}
    </section>
  );
};

const FILES_DEBOUNCE_MS = 400;

const FilesView = ({ enabled, chatList }) => {
  const [items, setItems] = useState([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(false);
  const [query, setQuery] = useState("");
  const [status, setStatus] = useState("");
  const [chatId, setChatId] = useState("");
  const [offset, setOffset] = useState(0);
  const debouncedQuery = useDebounce(query, FILES_DEBOUNCE_MS);
  const debouncedStatus = useDebounce(status, 150);
  const debouncedChatId = useDebounce(chatId, 150);
  const abortRef = useRef(null);

  const fetchFiles = useCallback(
    async (off = 0, opts = {}) => {
      if (!enabled) return;
      abortRef.current?.abort();
      abortRef.current = new AbortController();
      const signal = abortRef.current.signal;
      const q = opts.q ?? debouncedQuery;
      const st = opts.status ?? debouncedStatus;
      const cid = opts.chatId ?? debouncedChatId;

      setLoading(true);
      try {
        const params = new URLSearchParams({
          limit: String(FILES_PAGE_SIZE),
          offset: String(off),
        });
        if (String(q || "").trim()) params.set("q", String(q).trim());
        if (st) params.set("status", st);
        if (cid) params.set("chat_id", cid);
        const res = await fetch(`/api/files?${params}`, { signal });
        const data = await res.json();
        if (signal.aborted) return;
        setItems(data.items || []);
        setTotal(data.total || 0);
        setOffset(off);
      } catch (err) {
        if (err.name === "AbortError") return;
        console.error("Files fetch error:", err);
      } finally {
        if (!signal.aborted) setLoading(false);
      }
    },
    [enabled, debouncedQuery, debouncedStatus, debouncedChatId],
  );

  useEffect(() => {
    if (!enabled) return;
    fetchFiles(0);
  }, [enabled, debouncedQuery, debouncedStatus, debouncedChatId, fetchFiles]);

  const handleRefresh = useCallback(() => {
    fetchFiles(0, { q: query, status, chatId });
  }, [fetchFiles, query, status, chatId]);

  const formatDate = (d) => {
    if (!d) return "";
    try {
      return new Date(d).toLocaleString();
    } catch {
      return String(d);
    }
  };
  const formatSize = (n) => {
    if (n == null || n === 0) return "—";
    if (n >= 1024 * 1024 * 1024)
      return `${(n / (1024 * 1024 * 1024)).toFixed(2)} GB`;
    if (n >= 1024 * 1024) return `${(n / (1024 * 1024)).toFixed(2)} MB`;
    if (n >= 1024) return `${(n / 1024).toFixed(1)} KB`;
    return `${n} B`;
  };

  if (!enabled) {
    return (
      <section className="glass-card p-6">
        <h3 className="card-title text-white">
          <FolderOpen size={18} className="text-emerald-400" /> Файлы
        </h3>
        <p className="text-slate-400 mt-4">
          Включите ClickHouse в config.yaml для просмотра файлов загрузки.
        </p>
      </section>
    );
  }

  return (
    <section className="glass-card p-6">
      <h3 className="card-title text-white">
        <FolderOpen size={18} className="text-emerald-400" /> Файлы (путь и
        статус)
      </h3>
      <div className="mt-4 flex flex-wrap gap-2 items-center">
        <Search size={14} className="text-slate-500 shrink-0" />
        <input
          type="text"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Поиск по имени или пути…"
          disabled={loading}
          className="bg-slate-800/50 border border-slate-600/50 rounded px-3 py-1.5 text-sm text-white placeholder-slate-500 focus:outline-none focus:border-emerald-500/50 w-48 md:w-64 disabled:opacity-60 disabled:cursor-not-allowed"
        />
        <select
          value={status}
          onChange={(e) => setStatus(e.target.value)}
          disabled={loading}
          className="bg-slate-800/50 border border-slate-600/50 rounded px-3 py-1.5 text-sm text-white focus:outline-none focus:border-emerald-500/50 disabled:opacity-60 disabled:cursor-not-allowed"
        >
          <option value="">Все статусы</option>
          <option value="downloaded">downloaded</option>
          <option value="failed">failed</option>
          <option value="skipped">skipped</option>
        </select>
        <select
          value={chatId}
          onChange={(e) => setChatId(e.target.value)}
          disabled={loading}
          className="bg-slate-800/50 border border-slate-600/50 rounded px-3 py-1.5 text-sm text-white focus:outline-none focus:border-emerald-500/50 max-w-[200px] disabled:opacity-60 disabled:cursor-not-allowed"
        >
          <option value="">Все чаты</option>
          {(chatList || []).map((c) => (
            <option key={c.chat_id} value={String(c.chat_id)}>
              {c.title || c.chat_id}
            </option>
          ))}
        </select>
        <button
          type="button"
          onClick={handleRefresh}
          disabled={loading}
          className="flex items-center gap-2 px-3 py-1.5 rounded bg-emerald-500/20 text-emerald-300 border border-emerald-500/30 hover:bg-emerald-500/30 disabled:opacity-60 disabled:cursor-not-allowed text-sm"
        >
          {loading ? <FlowerLoader size={18} color="emerald" /> : null}
          Обновить
        </button>
      </div>
      <p className="text-xs text-slate-500 mt-2 flex items-center gap-2">
        {loading ? <FlowerLoader size={14} color="emerald" /> : null}
        {total} файлов
      </p>
      <div className="mt-4 overflow-x-auto max-h-[60vh] overflow-y-auto border border-slate-700/50 rounded-lg">
        {loading && items.length === 0 ? (
          <div className="p-8 flex flex-col items-center justify-center gap-3 text-slate-400">
            <FlowerLoader size={40} color="emerald" />
            <span>Загрузка файлов…</span>
          </div>
        ) : (
          <table className="w-full text-sm">
            <thead className="bg-slate-800/80 sticky top-0">
              <tr>
                <th className="text-left p-2 text-slate-400 font-medium">
                  Чат
                </th>
                <th className="text-left p-2 text-slate-400 font-medium">
                  Имя файла
                </th>
                <th className="text-left p-2 text-slate-400 font-medium">
                  Путь
                </th>
                <th className="text-left p-2 text-slate-400 font-medium w-24">
                  Статус
                </th>
                <th className="text-left p-2 text-slate-400 font-medium w-20">
                  Размер
                </th>
                <th className="text-left p-2 text-slate-400 font-medium">
                  Ошибка
                </th>
                <th className="text-left p-2 text-slate-400 font-medium">
                  Дата
                </th>
              </tr>
            </thead>
            <tbody>
              {items.map((row, i) => (
                <tr
                  key={i}
                  className="border-t border-slate-700/30 hover:bg-slate-800/40"
                >
                  <td
                    className="p-2 text-slate-400 truncate max-w-[140px]"
                    title={row.chat_title}
                  >
                    {row.chat_title || row.chat_id}
                  </td>
                  <td
                    className="p-2 text-slate-200 truncate max-w-[180px]"
                    title={row.file_name}
                  >
                    {row.file_name || "—"}
                  </td>
                  <td
                    className="p-2 text-slate-400 truncate max-w-[220px] font-mono text-xs"
                    title={row.file_path}
                  >
                    {row.file_path || "—"}
                  </td>
                  <td className="p-2">
                    <span
                      className={`font-mono text-xs ${
                        row.status === "downloaded"
                          ? "text-emerald-400"
                          : row.status === "failed"
                            ? "text-rose-400"
                            : "text-slate-400"
                      }`}
                    >
                      {row.status}
                    </span>
                  </td>
                  <td className="p-2 text-slate-500">
                    {formatSize(row.file_size)}
                  </td>
                  <td
                    className="p-2 text-rose-300/90 text-xs truncate max-w-[160px]"
                    title={row.error_message}
                  >
                    {row.error_message || "—"}
                  </td>
                  <td className="p-2 text-slate-500 whitespace-nowrap">
                    {formatDate(row.created_at)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
      {total > FILES_PAGE_SIZE && (
        <div className="mt-2 flex gap-2">
          <button
            type="button"
            disabled={offset <= 0 || loading}
            onClick={() => fetchFiles(Math.max(0, offset - FILES_PAGE_SIZE))}
            className="px-2 py-1 rounded bg-slate-700/50 text-slate-300 disabled:opacity-50 text-sm"
          >
            Назад
          </button>
          <span className="text-slate-500 text-sm py-1">
            {offset + 1}–{Math.min(offset + FILES_PAGE_SIZE, total)} из {total}
          </span>
          <button
            type="button"
            disabled={offset + FILES_PAGE_SIZE >= total || loading}
            onClick={() => fetchFiles(offset + FILES_PAGE_SIZE)}
            className="px-2 py-1 rounded bg-slate-700/50 text-slate-300 disabled:opacity-50 text-sm"
          >
            Вперёд
          </button>
        </div>
      )}
    </section>
  );
};

const TABS = [
  { id: "progress", label: "Прогресс", icon: Zap },
  { id: "logs", label: "Логи", icon: FileText },
  { id: "files", label: "Файлы", icon: FolderOpen },
];

const Dashboard = () => {
  const [activeTab, setActiveTab] = useState("progress");
  const [progress, setProgress] = useState({
    overall: { total: 0, completed: 0, status: "Idle", speed: 0 },
    chats: {},
    active_downloads: {},
  });
  const [stats, setStats] = useState({
    enabled: false,
    chats: [],
    history: [],
  });
  const [statsUpdatedAt, setStatsUpdatedAt] = useState(null);
  const [connected, setConnected] = useState(false);
  const [selectedChat, setSelectedChat] = useState(null);
  const ws = useRef(null);
  const speedBuffer = useRef([]);
  const etaBuffer = useRef([]);
  const chartContainerRef = useRef(null);
  const [chartSize, setChartSize] = useState({ width: 0, height: 256 });
  const showProgressContent = activeTab === "progress" && !selectedChat;

  const chartObserverRef = useRef(null);

  useEffect(() => {
    if (!showProgressContent) {
      setChartSize((s) => (s.width > 0 ? { width: 0, height: 256 } : s));
      if (chartObserverRef.current) {
        chartObserverRef.current.disconnect();
        chartObserverRef.current = null;
      }
      return;
    }
    const raf = requestAnimationFrame(() => {
      const el = chartContainerRef.current;
      if (!el) return;
      const ro = new ResizeObserver((entries) => {
        const { width, height } = entries[0].contentRect;
        if (width > 0 && height > 0)
          setChartSize({
            width: Math.round(width),
            height: Math.min(Math.round(height), 256),
          });
      });
      chartObserverRef.current = ro;
      ro.observe(el);
    });
    return () => {
      cancelAnimationFrame(raf);
      if (chartObserverRef.current) {
        chartObserverRef.current.disconnect();
        chartObserverRef.current = null;
      }
    };
  }, [showProgressContent]);

  useEffect(() => {
    connectWS();
    fetchStats();
    const interval = setInterval(fetchStats, 10_000);
    return () => {
      clearInterval(interval);
      ws.current?.close();
    };
  }, []);

  const connectWS = () => {
    const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
    const host =
      window.location.hostname === "localhost"
        ? "localhost:8000"
        : window.location.host;
    ws.current = new WebSocket(`${protocol}//${host}/ws/progress`);

    ws.current.onopen = () => setConnected(true);
    ws.current.onclose = () => {
      setConnected(false);
      setTimeout(connectWS, 3000);
    };
    ws.current.onmessage = (event) => {
      const data = JSON.parse(event.data);
      const rawSpeed = data?.overall?.speed;
      const rawEta = data?.overall?.eta_seconds;

      if (typeof rawSpeed === "number" && rawSpeed >= 0) {
        speedBuffer.current = [...speedBuffer.current, rawSpeed].slice(
          -SMOOTH_WINDOW,
        );
      }
      if (typeof rawEta === "number" && rawEta >= 0) {
        etaBuffer.current = [...etaBuffer.current, rawEta].slice(
          -SMOOTH_WINDOW,
        );
      }
      if (Object.keys(data?.active_downloads || {}).length === 0) {
        speedBuffer.current = [];
        etaBuffer.current = [];
      }

      const avgSpeed =
        speedBuffer.current.length > 0
          ? speedBuffer.current.reduce((a, b) => a + b, 0) /
            speedBuffer.current.length
          : rawSpeed;
      const avgEta =
        etaBuffer.current.length > 0
          ? Math.round(
              etaBuffer.current.reduce((a, b) => a + b, 0) /
                etaBuffer.current.length,
            )
          : rawEta;

      setProgress({
        ...data,
        overall: {
          ...data.overall,
          speed:
            typeof avgSpeed === "number"
              ? Math.round(avgSpeed * 100) / 100
              : data.overall?.speed,
          eta_seconds:
            typeof avgEta === "number" ? avgEta : data.overall?.eta_seconds,
        },
      });
    };
  };

  const fetchStats = async () => {
    try {
      const res = await fetch("/api/stats");
      const data = await res.json();
      setStats(data);
      setStatsUpdatedAt(new Date());
    } catch (e) {
      console.error("Failed to fetch stats", e);
      setStats({
        enabled: true,
        connected: false,
        error: String(e),
        chats: [],
        history: [],
      });
      setStatsUpdatedAt(new Date());
    }
  };

  const overallPercentage =
    progress.overall.total > 0
      ? Math.round((progress.overall.completed / progress.overall.total) * 100)
      : 0;

  const etaSeconds = progress?.overall?.eta_seconds ?? null;
  const formatEta = (seconds) => {
    if (seconds == null || !Number.isFinite(seconds) || seconds <= 0)
      return null;
    const s = Math.floor(seconds);
    const hh = Math.floor(s / 3600);
    const mm = Math.floor((s % 3600) / 60);
    const ss = s % 60;
    return `${hh.toString().padStart(2, "0")}:${mm.toString().padStart(2, "0")}:${ss.toString().padStart(2, "0")}`;
  };
  const etaText = formatEta(etaSeconds);

  const clickhouseState = (() => {
    if (stats?.enabled === false)
      return { kind: "off", label: "CLICKHOUSE OFF", detail: stats?.error };
    if (stats?.connected === false || stats?.error)
      return { kind: "err", label: "CLICKHOUSE ERROR", detail: stats?.error };
    return { kind: "ok", label: "CLICKHOUSE OK", detail: null };
  })();

  const activeEntries = Object.entries(progress.active_downloads || {}).filter(
    ([, dl]) => {
      // страховка: если бэк не успел удалить завершённое — не показываем 100%
      if (!dl) return false;
      const t = Number(dl.total || 0);
      const c = Number(dl.completed || 0);
      if (t > 0 && c >= t) return false;
      return true;
    },
  );

  return (
    <div className="app-container p-4 md:p-8 max-w-7xl mx-auto space-y-8 relative">
      {selectedChat &&
        createPortal(
          <ErrorBoundary
            fallback={
              <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 p-4">
                <div className="glass-card p-6 max-w-md">
                  <p className="text-rose-300 mb-4">Ошибка при открытии чата</p>
                  <button
                    type="button"
                    onClick={() => setSelectedChat(null)}
                    className="px-4 py-2 rounded bg-slate-600 text-white hover:bg-slate-500"
                  >
                    Закрыть
                  </button>
                </div>
              </div>
            }
          >
            <AnimatePresence>
              <ChatViewer
                key={selectedChat.chat_id}
                chatId={selectedChat.chat_id}
                title={selectedChat.title}
                onClose={() => setSelectedChat(null)}
                initialMessageId={selectedChat.initialMessageId}
                chats={stats?.chats || []}
                onOpenChat={(payload) =>
                  setSelectedChat({
                    chat_id: payload.chat_id,
                    title: payload.title ?? `Chat ${payload.chat_id}`,
                    initialMessageId: payload.initialMessageId,
                  })
                }
              />
            </AnimatePresence>
          </ErrorBoundary>,
          document.body,
        )}
      {/* Header */}
      <header className="flex flex-col md:flex-row justify-between items-start md:items-center gap-4">
        <div>
          <h1 className="text-3xl font-extrabold premium-gradient-text tracking-tight">
            TMD Dashboard
          </h1>
          <p className="text-sm text-slate-400 mt-1 flex items-center gap-2">
            <Database size={14} /> Web Dashboard
          </p>
        </div>
        <div className="flex items-center gap-2">
          <div
            className={`flex items-center gap-2 px-3 py-1.5 rounded-full border text-xs font-medium ${
              clickhouseState.kind === "ok"
                ? "bg-emerald-500/10 border-emerald-500/20 text-emerald-400"
                : clickhouseState.kind === "off"
                  ? "bg-slate-500/10 border-slate-500/20 text-slate-300"
                  : "bg-rose-500/10 border-rose-500/20 text-rose-400"
            }`}
            title={clickhouseState.detail || ""}
          >
            <div
              className={`w-2 h-2 rounded-full ${
                clickhouseState.kind === "ok"
                  ? "bg-emerald-400"
                  : clickhouseState.kind === "off"
                    ? "bg-slate-400"
                    : "bg-rose-400"
              }`}
            />
            {clickhouseState.label}
          </div>

          <div
            className={`flex items-center gap-2 px-3 py-1.5 rounded-full border text-xs font-medium ${
              connected
                ? "bg-emerald-500/10 border-emerald-500/20 text-emerald-400"
                : "bg-rose-500/10 border-rose-500/20 text-rose-400"
            }`}
          >
            <div
              className={`w-2 h-2 rounded-full ${connected ? "bg-emerald-400 animate-pulse" : "bg-rose-400"}`}
            />
            {connected ? "WS CONNECTED" : "WS DISCONNECTED"}
          </div>
        </div>
      </header>

      {/* Tabs */}
      <nav className="flex gap-1 p-1 rounded-xl bg-slate-800/50 border border-slate-700/50 w-fit">
        {TABS.map(({ id, label, icon: Icon }) => (
          <button
            key={id}
            type="button"
            onClick={() => setActiveTab(id)}
            className={`flex items-center gap-2 px-4 py-2 rounded-lg text-sm font-medium transition-colors ${
              activeTab === id
                ? "bg-slate-700 text-white shadow"
                : "text-slate-400 hover:text-slate-200 hover:bg-slate-700/50"
            }`}
          >
            <Icon size={16} />
            {label}
          </button>
        ))}
      </nav>

      {activeTab === "logs" && (
        <LogsView enabled={stats?.enabled && stats?.connected} />
      )}
      {activeTab === "files" && (
        <FilesView
          enabled={stats?.enabled && stats?.connected}
          chatList={stats?.chats || []}
        />
      )}

      {showProgressContent && (
        <>
          {/* Hero Progress Section */}
          <motion.section
            initial={{ opacity: 0, y: 20 }}
            animate={{ opacity: 1, y: 0 }}
            className="glass-card p-6 md:p-8 relative overflow-hidden"
          >
            <div className="relative z-10 grid grid-cols-1 md:grid-cols-2 gap-8 items-center">
              <div className="space-y-4">
                <h2 className="text-xl font-bold flex items-center gap-2 text-white">
                  <Zap size={20} className="text-blue-400" /> Overall Progress
                </h2>
                <div className="flex items-end gap-3">
                  <span className="text-5xl font-black text-white">
                    {overallPercentage}%
                  </span>
                  <span className="text-blue-300 font-medium mb-1">
                    {progress.overall.status}
                  </span>
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
                  <div className="flex justify-between text-sm text-slate-200 font-semibold">
                    <span>{progress.overall.completed} CHATS</span>
                    <span>{progress.overall.total} TOTAL</span>
                  </div>
                </div>
              </div>

              <div className="grid grid-cols-2 gap-4">
                <div className="bg-slate-900/50 p-4 rounded-xl border border-slate-600/30">
                  <p className="text-xs text-slate-300 uppercase tracking-wider font-bold mb-2">
                    Speed
                  </p>
                  <p className="text-2xl font-bold text-white">
                    {progress.overall.speed || "---"} MB/s
                  </p>
                </div>
                <div className="bg-slate-900/50 p-4 rounded-xl border border-slate-600/30">
                  <p className="text-xs text-slate-300 uppercase tracking-wider font-bold mb-2">
                    Time Left
                  </p>
                  <p className="text-2xl font-bold text-white">
                    {etaText || "---"}
                  </p>
                </div>
              </div>
            </div>
          </motion.section>

          {/* Main Grid */}
          <div className="grid grid-cols-1 lg:grid-cols-3 gap-8">
            {/* Left Column: Active Downloads */}
            <div className="lg:col-span-2 space-y-8">
              <section className="glass-card p-6">
                <h3 className="card-title text-white">
                  <Activity size={18} className="text-blue-400" /> Active
                  Threads
                </h3>
                <div className="space-y-4 mt-4">
                  <AnimatePresence>
                    {activeEntries.length === 0 ? (
                      <motion.div
                        initial={{ opacity: 0 }}
                        animate={{ opacity: 1 }}
                        className="text-center py-10 text-slate-400 bg-slate-900/20 rounded-xl border border-dashed border-slate-700"
                      >
                        <p>No active media downloads</p>
                      </motion.div>
                    ) : (
                      activeEntries.map(([id, dl]) => {
                        const pct =
                          dl.total > 0
                            ? Math.round((dl.completed / dl.total) * 100)
                            : 0;
                        return (
                          <motion.div
                            key={id}
                            initial={{ opacity: 0, x: -20 }}
                            animate={{ opacity: 1, x: 0 }}
                            exit={{ opacity: 0, x: 20 }}
                            className="bg-slate-900/40 p-4 rounded-xl border border-slate-600/30 hover:border-blue-500/30 transition-all group"
                          >
                            <div className="flex justify-between items-center mb-2">
                              <span className="text-sm font-semibold text-white truncate pr-4">
                                {dl.description}
                              </span>
                              <span className="text-xs font-mono text-blue-300">
                                {pct}%
                              </span>
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

              {/* Charts Section — рендер только при положительных размерах, без ResponsiveContainer */}
              {stats.enabled && stats.history && stats.history.length > 0 && (
                <ErrorBoundary
                  fallback={
                    <section className="glass-card p-6">
                      <h3 className="card-title text-white">
                        <History size={18} className="text-indigo-400" />{" "}
                        Download History
                      </h3>
                      <p className="mt-4 text-slate-400 text-sm">
                        Ошибка отображения графика
                      </p>
                    </section>
                  }
                >
                  <section className="glass-card p-6">
                    <h3 className="card-title text-white">
                      <History size={18} className="text-indigo-400" /> Download
                      History
                    </h3>
                    <div
                      ref={chartContainerRef}
                      className="mt-6"
                      style={{
                        width: "100%",
                        minWidth: 300,
                        height: 256,
                        minHeight: 200,
                      }}
                    >
                      {chartSize.width > 0 && (
                        <AreaChart
                          width={chartSize.width}
                          height={chartSize.height}
                          data={stats.history}
                        >
                          <defs>
                            <linearGradient
                              id="colorCount"
                              x1="0"
                              y1="0"
                              x2="0"
                              y2="1"
                            >
                              <stop
                                offset="5%"
                                stopColor="#3b82f6"
                                stopOpacity={0.3}
                              />
                              <stop
                                offset="95%"
                                stopColor="#3b82f6"
                                stopOpacity={0}
                              />
                            </linearGradient>
                          </defs>
                          <CartesianGrid
                            strokeDasharray="3 3"
                            stroke="#475569"
                            vertical={false}
                          />
                          <XAxis
                            dataKey="date"
                            stroke="#94a3b8"
                            fontSize={11}
                            tickLine={false}
                            axisLine={false}
                          />
                          <YAxis
                            stroke="#94a3b8"
                            fontSize={11}
                            tickLine={false}
                            axisLine={false}
                          />
                          <Tooltip
                            contentStyle={{
                              backgroundColor: "#1e293b",
                              border: "1px solid #475569",
                              borderRadius: "8px",
                            }}
                            itemStyle={{ color: "#fff" }}
                          />
                          <Area
                            type="monotone"
                            dataKey="count"
                            stroke="#3b82f6"
                            fillOpacity={1}
                            fill="url(#colorCount)"
                            strokeWidth={2}
                          />
                        </AreaChart>
                      )}
                    </div>
                  </section>
                </ErrorBoundary>
              )}
            </div>

            {/* Right Column: Chat Queue & Stats */}
            <div className="space-y-8">
              <section className="glass-card p-6 h-full flex flex-col">
                <h3 className="card-title text-white">
                  <Search size={18} className="text-emerald-400" /> Chat
                  Statistics
                </h3>
                <div className="flex-1 overflow-y-auto mt-4 space-y-3 pr-2 scroll-list">
                  {stats?.enabled === false && (
                    <div className="text-center py-8 text-slate-300">
                      <p className="font-semibold">ClickHouse выключен</p>
                      <p className="text-xs text-slate-400 mt-2">
                        {stats?.error ||
                          "Включи clickhouse.enabled в config.yaml"}
                      </p>
                    </div>
                  )}

                  {stats?.enabled !== false &&
                    (stats?.connected === false || stats?.error) && (
                      <div className="text-center py-8 text-rose-300">
                        <p className="font-semibold">ClickHouse недоступен</p>
                        <p className="text-xs text-rose-200/80 mt-2 break-words">
                          {stats?.error || "Ошибка подключения/запроса"}
                        </p>
                        {stats?.error_type === "schema_missing" && (
                          <p className="text-xs text-slate-300 mt-3">
                            Похоже, нет таблиц/схемы (или миграции/инициализация
                            не запускались).
                          </p>
                        )}
                      </div>
                    )}

                  {stats?.enabled !== false &&
                    stats?.connected !== false &&
                    !stats?.error &&
                    (stats?.chats?.length || 0) > 0 &&
                    stats.chats.map((chat) => (
                      <div
                        key={chat.chat_id ?? chat.title}
                        onClick={() =>
                          chat.chat_id != null &&
                          setSelectedChat({
                            chat_id: chat.chat_id,
                            title: chat.title || `Chat ${chat.chat_id}`,
                          })
                        }
                        className="bg-slate-900/40 p-3 rounded-lg border border-slate-600/30 flex items-center justify-between hover:bg-slate-800/50 hover:border-emerald-500/30 transition-colors cursor-pointer"
                      >
                        <div>
                          <p className="text-sm font-bold text-white">
                            {chat.title}
                          </p>
                          <p className="text-[10px] text-slate-400 font-medium">
                            {(chat.size / (1024 * 1024 * 1024)).toFixed(2)} GB •{" "}
                            {chat.count} msgs
                          </p>
                        </div>
                        <ChevronRight size={14} className="text-slate-500" />
                      </div>
                    ))}
                  {stats?.enabled !== false &&
                    stats?.connected !== false &&
                    !stats?.error &&
                    (stats?.chats?.length || 0) === 0 && (
                      <div className="text-center py-8 text-slate-300 italic">
                        <p>Данных пока нет</p>
                        <p className="text-xs text-slate-400 mt-2">
                          Таблицы пустые или данные ещё не успели записаться в
                          ClickHouse.
                        </p>
                      </div>
                    )}
                  {statsUpdatedAt && (
                    <p className="text-center text-[10px] text-slate-500 mt-4">
                      Обновлено: {statsUpdatedAt.toLocaleTimeString()}
                    </p>
                  )}
                </div>
              </section>
            </div>
          </div>
        </>
      )}

      <footer className="text-center text-slate-500 text-[10px] uppercase tracking-widest font-bold pb-4">
        Telegram Media Downloader © 2026 • Premium Analytics Edition
      </footer>
    </div>
  );
};

export default Dashboard;
