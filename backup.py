#!/usr/bin/env python3
import os
import subprocess
import shutil
import time
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.resolve()
BACKUP_DIR = PROJECT_ROOT / "backups"
EXCLUDE_PATTERNS = {
    "__pycache__",
    ".download_cache",
    "*.pyc",
    "*.pyo",
    ".git",
    ".pytest_cache",
    ".coverage",
    "*.egg-info",
    "ch_logs",
    "ch_data",
    "downloads.log*",
    "media_downloader.session*",
    "backups",
}
EXCLUDE_EXTENSIONS = {".pyc", ".pyo", ".so", ".egg"}


def get_backup_filename(prefix: str) -> str:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return f"{prefix}_{timestamp}.tar.gz"


def format_size(size_bytes: int) -> str:
    """Форматирует размер в байтах в читаемый формат."""
    for unit in ['B', 'KB', 'MB', 'GB']:
        if size_bytes < 1024.0:
            return f"{size_bytes:.2f} {unit}"
        size_bytes /= 1024.0
    return f"{size_bytes:.2f} TB"


def create_project_backup(backup_dir: Path) -> Path:
    backup_file = backup_dir / get_backup_filename("project")

    print(f"  Сканирование проекта: {PROJECT_ROOT}")
    files_to_backup = []
    total_size = 0
    excluded_count = 0

    for root, dirs, files in os.walk(PROJECT_ROOT):
        # Фильтруем исключенные директории
        excluded_dirs = [d for d in dirs if d in EXCLUDE_PATTERNS]
        if excluded_dirs:
            excluded_count += len(excluded_dirs)
        dirs[:] = [d for d in dirs if d not in EXCLUDE_PATTERNS]

        for f in files:
            file_path = Path(root) / f

            # Проверка на скрытые файлы
            if f.startswith(".") and f not in {".gitignore", ".pre-commit-config.yaml"}:
                excluded_count += 1
                continue

            # Проверка расширения
            if file_path.suffix in EXCLUDE_EXTENSIONS:
                excluded_count += 1
                continue

            rel_path = file_path.relative_to(PROJECT_ROOT)
            files_to_backup.append(rel_path)
            try:
                total_size += file_path.stat().st_size
            except (OSError, PermissionError):
                pass

    print(f"  Найдено файлов для бэкапа: {len(files_to_backup)}")
    print(f"  Исключено файлов/директорий: {excluded_count}")
    print(f"  Общий размер данных: {format_size(total_size)}")

    if not files_to_backup:
        print("  ⚠️  Нет файлов для бэкапа!")
        return backup_file

    print(f"  Создание архива: {backup_file.name}")
    start_time = time.time()

    cmd = ["tar", "-czf", str(backup_file), "-C", str(PROJECT_ROOT)]
    cmd.extend(str(f) for f in files_to_backup)

    result = subprocess.run(cmd, check=True, capture_output=True, text=True)

    elapsed_time = time.time() - start_time

    if backup_file.exists():
        archive_size = backup_file.stat().st_size
        compression_ratio = (1 - archive_size / total_size) * 100 if total_size > 0 else 0
        print(f"  ✓ Архив создан за {elapsed_time:.2f} сек")
        print(f"  Размер архива: {format_size(archive_size)}")
        print(f"  Сжатие: {compression_ratio:.1f}%")
    else:
        print("  ✗ Ошибка: архив не был создан")

    return backup_file


def create_database_backup(backup_dir: Path) -> Path:
    backup_filename = get_backup_filename("clickhouse")
    backup_file = backup_dir / backup_filename

    # Используем путь внутри /var/lib/clickhouse/backups/ (разрешенный путь для бэкапов)
    # Этот путь смонтирован через volume из ./ch_data на хосте
    container_backup_dir = "/var/lib/clickhouse/backups"
    container_backup_path = f"{container_backup_dir}/{backup_filename}"

    # Создаем директорию на хосте в ch_data/backups/ для доступа через volume
    host_backup_dir = PROJECT_ROOT / "ch_data" / "backups"

    print(f"  Контейнер: clickhouse-tgd-server")
    print(f"  База данных: telegram_downloader")
    print(f"  Файл бэкапа: {backup_filename}")
    print(f"  Путь в контейнере: {container_backup_path}")
    print(f"  Путь на хосте: {host_backup_dir}")

    # Создаем директорию на хосте (будет доступна в контейнере через volume)
    print("  Создание директории для бэкапов на хосте...")
    try:
        host_backup_dir.mkdir(parents=True, exist_ok=True)
        # Устанавливаем права доступа (101:101 - это обычно пользователь clickhouse в контейнере)
        os.chmod(host_backup_dir, 0o755)
        print(f"  ✓ Директория создана: {host_backup_dir}")
    except Exception as e:
        print(f"  ⚠️  Предупреждение при создании директории: {e}")

    # Также создаем директорию внутри контейнера на всякий случай
    print("  Создание директории для бэкапов в контейнере...")
    mkdir_result = subprocess.run(
        ["podman", "exec", "clickhouse-tgd-server", "mkdir", "-p", container_backup_dir],
        capture_output=True,
        text=True
    )
    if mkdir_result.returncode != 0:
        print(f"  ⚠️  Предупреждение: не удалось создать директорию в контейнере: {mkdir_result.stderr}")

    # Устанавливаем права на директорию внутри контейнера
    print("  Установка прав доступа...")
    chown_result = subprocess.run(
        ["podman", "exec", "clickhouse-tgd-server", "chown", "-R", "101:101", container_backup_dir],
        capture_output=True,
        text=True
    )
    if chown_result.returncode != 0:
        print(f"  ⚠️  Предупреждение: не удалось установить права: {chown_result.stderr}")

    print("  Выполнение команды BACKUP...")
    start_time = time.time()

    # Создаем бэкап внутри контейнера
    result = subprocess.run(
        [
            "podman", "exec", "clickhouse-tgd-server",
            "clickhouse-client", "--query",
            f"BACKUP DATABASE telegram_downloader TO File('{container_backup_path}')"
        ],
        capture_output=True,
        text=True
    )

    backup_time = time.time() - start_time

    if result.returncode != 0:
        print(f"  ✗ Ошибка выполнения команды (код: {result.returncode})")
        if result.stdout:
            print(f"  Вывод: {result.stdout}")
        raise RuntimeError(f"ClickHouse backup failed: {result.stderr}")

    if result.stdout:
        print(f"  Вывод ClickHouse: {result.stdout.strip()}")

    # Файл создан в /var/lib/clickhouse/backups/, который смонтирован из ./ch_data/backups/ на хосте
    # Проверяем наличие файла на хосте через volume
    host_backup_file = host_backup_dir / backup_filename

    print("  Ожидание появления файла на хосте...")
    max_wait = 10
    waited = 0
    while not host_backup_file.exists() and waited < max_wait:
        time.sleep(0.5)
        waited += 0.5

    if not host_backup_file.exists():
        # Если файл не появился через volume, пробуем скопировать через podman cp
        print("  Файл не найден через volume, копирование через podman cp...")
        copy_start_time = time.time()
        copy_result = subprocess.run(
            [
                "podman", "cp",
                f"clickhouse-tgd-server:{container_backup_path}",
                str(backup_file)
            ],
            capture_output=True,
            text=True
        )
        copy_time = time.time() - copy_start_time

        if copy_result.returncode != 0:
            print(f"  ✗ Ошибка копирования файла: {copy_result.stderr}")
            raise RuntimeError(f"Failed to copy backup from container: {copy_result.stderr}")

        if not backup_file.exists():
            raise RuntimeError("Backup file was not created")

        backup_size = backup_file.stat().st_size
        total_time = backup_time + copy_time
        print(f"  ✓ Бэкап создан за {backup_time:.2f} сек")
        print(f"  ✓ Файл скопирован за {copy_time:.2f} сек")
        print(f"  Общее время: {total_time:.2f} сек")
    else:
        # Файл найден через volume, но он создан от имени пользователя clickhouse
        # Используем podman cp для копирования (работает от root и обходит проблемы с правами)
        print("  Файл найден через volume, копирование в директорию бэкапов...")
        copy_start_time = time.time()
        copy_result = subprocess.run(
            [
                "podman", "cp",
                f"clickhouse-tgd-server:{container_backup_path}",
                str(backup_file)
            ],
            capture_output=True,
            text=True
        )
        copy_time = time.time() - copy_start_time

        if copy_result.returncode != 0:
            print(f"  ✗ Ошибка копирования файла: {copy_result.stderr}")
            raise RuntimeError(f"Failed to copy backup from container: {copy_result.stderr}")

        if not backup_file.exists():
            raise RuntimeError("Backup file was not copied")

        backup_size = backup_file.stat().st_size
        total_time = backup_time + copy_time
        print(f"  ✓ Бэкап создан за {backup_time:.2f} сек")
        print(f"  ✓ Файл скопирован за {copy_time:.2f} сек")
        print(f"  Общее время: {total_time:.2f} сек")

        # Удаляем файл из контейнера после успешного копирования
        print("  Очистка временного файла в контейнере...")
        subprocess.run(
            ["podman", "exec", "clickhouse-tgd-server", "rm", "-f", container_backup_path],
            capture_output=True
        )

    print(f"  Размер бэкапа: {format_size(backup_size)}")

    return backup_file


def main():
    print("=" * 60)
    print("Создание резервных копий проекта")
    print("=" * 60)
    print(f"Время начала: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"Директория бэкапов: {BACKUP_DIR}")

    if not BACKUP_DIR.exists():
        print(f"Создание директории бэкапов...")
        BACKUP_DIR.mkdir(parents=True)
        print(f"✓ Директория создана")
    else:
        print(f"✓ Директория бэкапов существует")

    print("\n" + "-" * 60)
    print("1. Создание бэкапа проекта")
    print("-" * 60)
    main_start_time = time.time()

    project_backup = create_project_backup(BACKUP_DIR)
    print(f"  Файл: {project_backup}")

    print("\n" + "-" * 60)
    print("2. Создание бэкапа базы данных ClickHouse")
    print("-" * 60)

    try:
        db_backup = create_database_backup(BACKUP_DIR)
        print(f"  Файл: {db_backup}")
    except RuntimeError as e:
        print(f"\n✗ КРИТИЧЕСКАЯ ОШИБКА: {e}")
        print("\n" + "=" * 60)
        print("Бэкап завершен с ошибками!")
        print("=" * 60)
        return 1

    total_time = time.time() - main_start_time

    print("\n" + "=" * 60)
    print("✓ Бэкап успешно завершен!")
    print("=" * 60)
    print(f"Общее время выполнения: {total_time:.2f} сек")
    print(f"Время окончания: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"\nСозданные файлы:")
    print(f"  • {project_backup.name}")
    print(f"  • {db_backup.name}")
    print("=" * 60)

    return 0


if __name__ == "__main__":
    exit(main())
