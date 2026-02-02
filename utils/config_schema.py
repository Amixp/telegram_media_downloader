from typing import Dict, List, Literal, Optional, Union
from pydantic import BaseModel, Field, field_validator

class DownloadSettings(BaseModel):
    max_parallel_downloads: Optional[int] = 5
    pagination_limit: int = 100
    base_directory: str = ""
    skip_duplicates: bool = True
    download_message_history: bool = False
    history_format: Literal["json", "txt", "html", "jsonl"] = "json"
    history_directory: str = "history"
    history_rebuild_if_missing: bool = False
    validate_downloads: bool = True
    validate_archives: bool = True
    auto_add_chats_from_links: bool = False
    resumable_downloads: bool = True
    cache_directory: str = ".download_cache"

class SenderFilter(BaseModel):
    enabled: bool = False
    user_ids: List[int] = []
    usernames: List[str] = []

class LoggingConfig(BaseModel):
    enabled: bool = False
    file_path: str = "downloads.log"
    level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    max_bytes: int = 10485760
    backup_count: int = 5

class ProxyConfig(BaseModel):
    scheme: Literal["socks4", "socks5", "http"]
    hostname: str
    port: int
    username: Optional[str] = None
    password: Optional[str] = None

class TuiColors(BaseModel):
    screen_fg: str = "default"
    screen_bg: str = "default"
    header_fg: str = "cyan"
    header_bg: str = "default"
    footer_fg: str = "white"
    footer_bg: str = "default"
    separator_fg: str = "blue"
    separator_bg: str = "default"
    list_fg: str = "default"
    list_bg: str = "default"
    selected_fg: str = "default"
    selected_bg: str = "default"

class TuiKeys(BaseModel):
    quit: List[str] = ["q", "esc"]
    confirm: List[str] = ["enter"]
    toggle: List[str] = ["space"]
    filter: List[str] = ["f"]
    search: List[str] = ["/"]
    clear: List[str] = ["c"]
    show_selected: List[str] = ["v"]
    up: List[str] = ["up", "k"]
    down: List[str] = ["down", "j"]
    page_up: List[str] = ["pageup"]
    page_down: List[str] = ["pagedown"]
    home: List[str] = ["home"]
    end: List[str] = ["end"]
    move_up: List[str] = ["K"]
    move_down: List[str] = ["J"]

class TuiLayout(BaseModel):
    list_min_width: int = 30
    list_width_ratio: float = 0.5
    preview_min_width: int = 10

class TuiPreview(BaseModel):
    messages_count: int = 1
    fetch_mode: Literal["auto", "on_demand", "off"] = "auto"
    debounce_ms: int = 200
    cache_size: int = 128
    cache_ttl_s: int = 300
    wrap: bool = True
    max_lines: int = 12
    show_loading: bool = True
    loading_text: str = "Загружаю…"
    error_text: str = "Ошибка загрузки превью"
    label_single: str = "Последнее сообщение:"
    label_multi: str = "Последние сообщения:"
    include_media_placeholder: bool = True
    poll_interval_ms: int = 33

class TuiText(BaseModel):
    header: str = "Выбор чатов: ↑/↓ PgUp/PgDn Home/End | Space=выбрать | Enter=OK | f=фильтр | /=поиск по имени | c=очистить | v=только выбранные | J/K=порядок очереди | q/Esc=выход"
    no_chats: str = "Нет доступных чатов"
    no_selected: str = "Нет выбранных чатов"
    search_prompt: str = "Поиск по имени: "

class TuiDisplay(BaseModel):
    show_chat_id: bool = True

class TuiConfig(BaseModel):
    display: TuiDisplay = Field(default_factory=TuiDisplay)
    preview: TuiPreview = Field(default_factory=TuiPreview)
    colors: TuiColors = Field(default_factory=TuiColors)
    layout: TuiLayout = Field(default_factory=TuiLayout)
    text: TuiText = Field(default_factory=TuiText)
    keys: TuiKeys = Field(default_factory=TuiKeys)

class ChatConfig(BaseModel):
    chat_id: int
    title: Optional[str] = None
    last_read_message_id: int = 0
    ids_to_retry: List[int] = []
    enabled: bool = True
    order: Optional[int] = None

class ClickHouseConfig(BaseModel):
    enabled: bool = False
    host: str = "localhost"
    port: int = 9000
    user: str = "default"
    password: str = ""
    database: str = "telegram_downloader"
    batch_size: int = 1000

class AppConfig(BaseModel):
    api_id: Optional[Union[int, str]] = None
    api_hash: Optional[str] = None
    language: str = "ru"
    download_settings: DownloadSettings = Field(default_factory=DownloadSettings)
    media_types: List[str] = ["all"]
    file_formats: Dict[str, List[str]] = {
        "audio": ["all"],
        "document": ["all"],
        "video": ["all"]
    }
    sender_filter: SenderFilter = Field(default_factory=SenderFilter)
    logging: Dict[str, LoggingConfig] = {"file_logging": LoggingConfig()}
    proxy: Optional[ProxyConfig] = None
    interactive_chat_selection: bool = True
    chat_selection_ui: Literal["classic", "tui"] = "tui"
    tui: TuiConfig = Field(default_factory=TuiConfig)
    chats: List[ChatConfig] = []
    clickhouse: ClickHouseConfig = Field(default_factory=ClickHouseConfig)

    # Legacy fields
    chat_id: Optional[int] = None
    last_read_message_id: Optional[int] = None
    ids_to_retry: Optional[List[int]] = None

    @field_validator("api_id")
    @classmethod
    def validate_api_id(cls, v):
        if v == "your_api_id":
             return None
        return v

    @field_validator("api_hash")
    @classmethod
    def validate_api_hash(cls, v):
        if v == "your_api_hash":
             return None
        return v
