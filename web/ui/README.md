# TMD Dashboard — React UI

Премиальный веб-интерфейс для Telegram Media Downloader с real-time мониторингом прогресса загрузок, аналитикой и визуализацией статистики.

## Технологии

- **React 19** — UI фреймворк
- **Vite** — сборщик и dev-сервер
- **Tailwind CSS v3** — utility-first CSS фреймворк
- **Framer Motion** — анимации
- **Recharts** — графики и визуализации
- **Lucide React** — иконки

## Разработка

### Установка зависимостей

```bash
cd web/ui
npm install
```

### Запуск dev-сервера

```bash
npm run dev
```

Dev-сервер запустится на `http://localhost:5173` с hot reload.

### Сборка для продакшена

```bash
npm run build
```

Собранные файлы появятся в `web/static/`.

## Структура проекта

```
web/ui/
├── src/
│   ├── App.jsx          # Главный компонент Dashboard
│   ├── App.css          # Специфичные стили
│   ├── index.css        # Глобальные стили + Tailwind directives
│   └── main.jsx         # Точка входа
├── public/              # Статические файлы
├── index.html           # HTML шаблон
├── vite.config.js       # Конфигурация Vite
├── tailwind.config.js   # Конфигурация Tailwind CSS
├── postcss.config.js    # Конфигурация PostCSS
└── package.json         # Зависимости и скрипты
```

## Tailwind CSS

### Установка (уже выполнена)

Tailwind CSS уже установлен и настроен. Если нужно переустановить:

```bash
npm install -D tailwindcss postcss autoprefixer
```

### Конфигурация

**tailwind.config.js:**
```javascript
export default {
  content: [
    "./index.html",
    "./src/**/*.{js,ts,jsx,tsx}",
  ],
  theme: {
    extend: {},
  },
  plugins: [],
}
```

**postcss.config.js:**
```javascript
export default {
  plugins: {
    tailwindcss: {},
    autoprefixer: {},
  },
}
```

**src/index.css:**
```css
@tailwind base;
@tailwind components;
@tailwind utilities;

/* Кастомные CSS переменные и стили */
```

### Использование

Tailwind utility классы используются напрямую в JSX:

```jsx
<div className="flex items-center gap-4 p-6 bg-slate-900 rounded-xl">
  <span className="text-white font-bold">Hello</span>
</div>
```

## Линтинг

```bash
npm run lint
```

## Превью продакшен-сборки

```bash
npm run preview
```

## API интеграция

Dashboard подключается к FastAPI бэкенду:

- **WebSocket:** `ws://localhost:8000/ws/progress` — real-time обновления прогресса
- **REST API:** `/api/stats` — статистика загрузок

## Стилевые особенности

- **Glassmorphism** эффекты с backdrop-filter
- **Gradient текст** для заголовков
- **Responsive дизайн** с breakpoints (md, lg)
- **Тёмная тема** по умолчанию
- **Анимации** через Framer Motion

## Troubleshooting

### Tailwind классы не применяются

1. Убедитесь, что в `src/index.css` есть директивы `@tailwind`
2. Проверьте `content` в `tailwind.config.js` — должны быть указаны все файлы с JSX
3. Пересоберите проект: `npm run build`

### Ошибка Recharts "width/height -1"

Это исправлено в текущей версии — график отображается только когда есть данные.

### Старые файлы в кэше браузера

Очистите кэш браузера (Ctrl+Shift+R) или откройте в приватном режиме.
