#!/usr/bin/env python3
"""
MP3 Tag Converter: Windows-1251 → UTF-8  (v6)
Рекурсивно обходит каталоги, исправляет кодировку тегов ID3
и опционально переименовывает файлы с кракозябрами.

Установка зависимостей:
    pip install mutagen chardet tqdm

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
ВАРИАНТЫ ЗАПУСКА
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

1. ТОЛЬКО ТЕГИ — проверка без изменений (рекомендуется запустить первым):
   python convert_tags.py "D:\Музыка" --dry-run

2. ТОЛЬКО ТЕГИ — применить исправления:
   python convert_tags.py "D:\Музыка"

3. ТОЛЬКО ИМЕНА ФАЙЛОВ — проверка без изменений:
   python convert_tags.py "D:\Музыка" --rename --no-tags --dry-run

4. ТОЛЬКО ИМЕНА ФАЙЛОВ — применить исправления:
   python convert_tags.py "D:\Музыка" --rename --no-tags

5. ТЕГИ + ИМЕНА ФАЙЛОВ — проверка без изменений:
   python convert_tags.py "D:\Музыка" --rename --dry-run

6. ТЕГИ + ИМЕНА ФАЙЛОВ — применить исправления:
   python convert_tags.py "D:\Музыка" --rename

7. С СОХРАНЕНИЕМ ЛОГА В ФАЙЛ:
   python convert_tags.py "D:\Музыка" --rename --dry-run --log

8. С ПОДРОБНЫМ ВЫВОДОМ (все файлы, включая без изменений):
   python convert_tags.py "D:\Музыка" --rename --dry-run --log --verbose

9. БЫСТРАЯ ПРОВЕРКА большой коллекции (многопоточность):
   python convert_tags.py "D:\Музыка" --rename --dry-run --workers 8

10. БОЕВОЙ ПРОГОН большой коллекции (с логом в файл):
    python convert_tags.py "D:\Музыка" --rename --log --workers 8

11. ПОВЫШЕННАЯ СТРОГОСТЬ ДЛЯ ИМЁН (меньше риск ошибки, больше пропусков):
    python convert_tags.py "D:\Музыка" --rename --rename-confidence 0.95

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
ОБОЗНАЧЕНИЯ В ВЫВОДЕ
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  🏷️  — исправлены теги ID3
  📁  — файл переименован
  ⚠️  — предупреждение (конфликт имён и т.п.)
  ❌  — ошибка

  В режиме --dry-run перед иконкой добавляется префикс [DRY-RUN]:
  [DRY-RUN] 🏷️  ...
  [DRY-RUN] 📁  ...

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
ФЛАГИ
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  --dry-run             Только показать что изменится, не сохранять
  --rename              Включить переименование файлов с кракозябрами
  --no-tags             Не трогать теги (только имена файлов, если --rename)
  --rename-confidence   Порог уверенности chardet для имён файлов (0.0–1.0)
                        По умолчанию: 0.80  (для тегов: 0.50)
  --log                 Сохранить лог в файл рядом с каталогом
  --verbose             Подробный вывод (все файлы, включая без изменений)
                        ⚠️  При --workers > 1 автоматически подавляется в консоли,
                        но сохраняется в лог-файл (если указан --log)
  --workers N           Число потоков для параллельной обработки (по умолч. 1)

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
РЕКОМЕНДУЕМЫЙ ПОРЯДОК РАБОТЫ
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  Шаг 1: python convert_tags.py "D:\Музыка" --rename --dry-run --log --workers 8
          Смотрим лог, проверяем что скрипт правильно определяет кракозябры.

  Шаг 2: python convert_tags.py "D:\Музыка" --rename --log --workers 8
          Применяем исправления.
"""

from __future__ import annotations

import sys
import os
import shutil
import argparse
import logging
from pathlib import Path
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import Lock

try:
    from mutagen.id3 import ID3, ID3NoHeaderError
    import chardet
except ImportError:
    print("❌ Установите зависимости: pip install mutagen chardet tqdm")
    sys.exit(1)

# tqdm опционален — без него скрипт тоже работает
try:
    from tqdm import tqdm
    HAS_TQDM = True
except ImportError:
    HAS_TQDM = False


# ─── Префиксы для вывода ─────────────────────────────────────────────────────
#
# Каждый тип события имеет свой визуальный префикс:
#   🏷️  — операция с тегами ID3
#   📁  — операция с именем файла (переименование)
#   ⚠️  — предупреждение (не фатально, файл пропущен)
#   ❌  — ошибка (файл не обработан)
#
# В режиме dry-run перед иконкой добавляется "[DRY-RUN]" для явного обозначения
# что изменения не применялись.

def _prefix_tags(dry_run: bool) -> str:
    return "[DRY-RUN] 🏷️ " if dry_run else "🏷️ "

def _prefix_rename(dry_run: bool) -> str:
    return "[DRY-RUN] 📁" if dry_run else "📁"


# ─── Текстовые фреймы (COMM — отдельно) ──────────────────────────────────────
TEXT_FRAMES = [
    "TIT2",  # Название трека
    "TPE1",  # Исполнитель
    "TPE2",  # Исполнитель альбома
    "TALB",  # Альбом
    "TCOM",  # Композитор
    "TCON",  # Жанр
    "TYER",  # Год (ID3v2.3)
    "TDRC",  # Год (ID3v2.4)
    "TRCK",  # Номер трека
    "TPOS",  # Номер диска
    "TPUB",  # Издатель
    "TCOP",  # Авторские права
]


# ─── TqdmLoggingHandler ───────────────────────────────────────────────────────

class TqdmLoggingHandler(logging.Handler):
    """
    Кастомный logging.Handler, который пишет в консоль через tqdm.write().

    Проблема без него:
        Обычный logging пишет в stderr напрямую через sys.stderr.write().
        tqdm держит полосу прогресса в нижней строке терминала и ожидает,
        что никто другой не пишет туда же. Когда logging пишет напрямую —
        курсор прыгает, полоса "разрывается" и наезжает на лог-строки.

    Решение:
        tqdm.write() специально создан для вывода текста "над" прогресс-баром.
        Он временно стирает полосу, выводит текст, восстанавливает полосу.
        Результат — чистый вывод без наложений.

    Используется ТОЛЬКО для консольного вывода при workers > 1.
    FileHandler пишет в файл напрямую — там полоса прогресса не отображается.
    """

    def emit(self, record: logging.LogRecord) -> None:
        try:
            msg = self.format(record)
            tqdm.write(msg)
        except Exception:
            self.handleError(record)


# ─── Logging ──────────────────────────────────────────────────────────────────

def setup_logging(
    log_to_file: bool,
    music_dir: Path,
    use_tqdm_handler: bool = False,
) -> logging.Logger:
    """
    Настройка логирования.

    use_tqdm_handler=True — при многопоточности с tqdm:
        консольный вывод идёт через TqdmLoggingHandler → tqdm.write()
        Гарантирует что строки логов не разрывают полосу прогресса.

    use_tqdm_handler=False — однопоточный режим или без tqdm:
        обычный StreamHandler → sys.stderr напрямую.
    """
    logger = logging.getLogger("mp3converter")
    logger.setLevel(logging.DEBUG)

    # Защита от дублирования handlers при повторном вызове
    if logger.handlers:
        logger.handlers.clear()

    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", "%H:%M:%S")

    # ── Консольный handler ────────────────────────────────────────────────────
    if use_tqdm_handler and HAS_TQDM:
        ch = TqdmLoggingHandler()
    else:
        ch = logging.StreamHandler()

    ch.setLevel(logging.INFO)
    ch.setFormatter(fmt)
    logger.addHandler(ch)

    # ── Файловый handler ──────────────────────────────────────────────────────
    if log_to_file:
        log_name = f"mp3_convert_{datetime.now():%Y%m%d_%H%M%S}.log"
        log_path = music_dir / log_name
        fh = logging.FileHandler(log_path, encoding="utf-8")
        fh.setLevel(logging.DEBUG)
        fh.setFormatter(fmt)
        logger.addHandler(fh)
        logger.info(f"Лог сохраняется в: {log_path}")

    return logger


# ─── Кодировка ────────────────────────────────────────────────────────────────

def is_cp1251_bytes(raw: bytes, min_confidence: float = 0.5) -> bool:
    """
    Определяет, похожи ли байты на cp1251.

    min_confidence — минимальный порог уверенности chardet:
      - 0.50 — для тегов (допустимо ошибиться, теги легко перезаписать)
      - 0.80 — для имён файлов (переименование опаснее, нужна большая уверенность)
      - 0.95 — максимальная осторожность

    Строки < 4 байт пропускаем — chardet ненадёжен на коротких данных.
    """
    if len(raw) < 4:
        return False
    detected = chardet.detect(raw)
    encoding = (detected.get("encoding") or "").lower()
    confidence = detected.get("confidence", 0)
    return ("1251" in encoding or "cp1251" in encoding) and confidence >= min_confidence


def decode_cp1251_text(text: str, min_confidence: float = 0.5) -> str | None:
    """
    Пытается интерпретировать строку как cp1251-байты, считанные как latin-1.
    Возвращает исправленную строку или None.

    Тройная защита от ложных срабатываний:
      1. Строка должна кодироваться в latin-1 (иначе это уже нормальный UTF-8)
      2. chardet должен определить cp1251 с достаточной уверенностью
      3. После перекодировки должна появиться кириллица (U+0400..U+04FF)
    """
    if text.isascii():
        return None
    try:
        raw = text.encode("latin-1")
    except (UnicodeEncodeError, UnicodeDecodeError):
        # Строка содержит символы вне latin-1 — значит это уже нормальный Unicode.
        # Обычная кириллица в UTF-8 сюда не пройдёт — она в безопасности.
        return None

    if not is_cp1251_bytes(raw, min_confidence):
        return None

    try:
        fixed = raw.decode("cp1251")
        # Финальная проверка: в результате должна быть именно кириллица
        if any("\u0400" <= ch <= "\u04FF" for ch in fixed):
            return fixed
    except (UnicodeDecodeError, Exception):
        pass

    return None


def _fix_text_list(texts: list, min_confidence: float = 0.5) -> tuple[list, bool]:
    """Исправляет список текстовых значений фрейма. Возвращает (новый список, изменён_ли)."""
    new_texts = []
    changed = False
    for text in texts:
        fixed = decode_cp1251_text(str(text), min_confidence)
        if fixed:
            new_texts.append(fixed)
            changed = True
        else:
            new_texts.append(str(text))
    return new_texts, changed


# ─── Переименование файлов ────────────────────────────────────────────────────

def fix_filename(
    filepath: Path,
    dry_run: bool,
    logger: logging.Logger,
    min_confidence: float = 0.80,
) -> tuple[Path, bool]:
    """
    Проверяет имя файла на кракозябры и при необходимости переименовывает.

    Возвращает (актуальный_путь, был_ли_переименован).

    Что НЕ будет переименовано:
      - Файлы с нормальной кириллицей в UTF-8 (не пройдут latin-1 encode)
      - Короткие имена < 4 символов (chardet ненадёжен)
      - Имена где chardet неуверен (confidence < min_confidence)
      - Файлы, у которых после исправления имя уже занято другим файлом
    """
    stem = filepath.stem
    suffix = filepath.suffix

    fixed_stem = decode_cp1251_text(stem, min_confidence)
    if not fixed_stem:
        return filepath, False

    new_name = fixed_stem + suffix
    new_path = filepath.parent / new_name

    if new_path == filepath:
        return filepath, False

    if new_path.exists():
        # Конфликт: файл с таким именем уже существует — пропускаем
        logger.warning(
            f"  ⚠️  Конфликт имён, пропускаем: «{filepath.name}» → «{new_name}» уже существует"
        )
        return filepath, False

    # 📁 — визуальный маркер операции с именем файла
    logger.info(f"  {_prefix_rename(dry_run)} «{filepath.name}» → «{new_name}»")

    if not dry_run:
        try:
            filepath.rename(new_path)
            return new_path, True
        except Exception as e:
            logger.error(f"  ❌ Ошибка переименования [{filepath.name}]: {e}")
            return filepath, False

    return filepath, True


# ─── Обработка тегов ──────────────────────────────────────────────────────────

def process_tags(filepath: Path, dry_run: bool, logger: logging.Logger) -> dict:
    """
    Обрабатывает теги одного MP3-файла.

    Возвращает:
        modified       (bool)    — теги изменены
        changed_fields (int)     — количество исправленных полей
        skipped        (bool)    — нет ID3-тегов
        error          (str|None)
    """
    result = {"modified": False, "changed_fields": 0, "skipped": False, "error": None}

    try:
        tags = ID3(str(filepath))
    except ID3NoHeaderError:
        logger.debug(f"  Нет ID3-тегов: {filepath.name}")
        result["skipped"] = True
        return result
    except Exception as e:
        result["error"] = str(e)
        return result

    modified = False
    changes: list[str] = []

    # ── Стандартные текстовые фреймы ─────────────────────────────────────────
    for frame_id in TEXT_FRAMES:
        for frame in tags.getall(frame_id):
            texts = getattr(frame, "text", None)
            if not texts:
                continue
            new_texts, frame_changed = _fix_text_list(texts, min_confidence=0.5)
            if frame_changed:
                frame.text = new_texts
                # encoding=3 — UTF-8; при v2_version=3 Mutagen сконвертирует в UTF-16
                # для совместимости с Windows и старыми автомагнитолами
                frame.encoding = 3
                modified = True
                for old, new in zip(texts, new_texts):
                    if str(old) != new:
                        changes.append(f"      {frame_id}: «{str(old)[:40]}» → «{new[:40]}»")

    # ── COMM: текст + описание ────────────────────────────────────────────────
    for frame in tags.getall("COMM"):
        frame_changed = False

        texts = getattr(frame, "text", None)
        if texts:
            new_texts, txt_changed = _fix_text_list(texts, min_confidence=0.5)
            if txt_changed:
                frame.text = new_texts
                frame.encoding = 3
                frame_changed = True
                for old, new in zip(texts, new_texts):
                    if str(old) != new:
                        changes.append(f"      COMM.text: «{str(old)[:40]}» → «{new[:40]}»")

        desc = str(getattr(frame, "desc", "") or "")
        fixed_desc = decode_cp1251_text(desc, min_confidence=0.5)
        if fixed_desc:
            frame.desc = fixed_desc
            frame.encoding = 3
            frame_changed = True
            changes.append(f"      COMM.desc: «{desc[:40]}» → «{fixed_desc[:40]}»")

        if frame_changed:
            modified = True

    # ── Сохранение / отчёт ───────────────────────────────────────────────────
    if modified:
        result["modified"] = True
        result["changed_fields"] = len(changes)

        # 🏷️  — визуальный маркер операции с тегами
        logger.info(f"  {_prefix_tags(dry_run)} {filepath.name} — {len(changes)} тег(ов):")
        for ch in changes:
            logger.info(ch)

        if not dry_run:
            try:
                # Keep an exact original before the first write. Never overwrite it.
                backup = filepath.with_name(filepath.name + ".tag-backup")
                if backup.exists():
                    raise FileExistsError(f"Backup already exists: {backup}")
                with backup.open("xb") as target, filepath.open("rb") as source:
                    shutil.copyfileobj(source, target)
                    target.flush()
                    os.fsync(target.fileno())
                shutil.copystat(filepath, backup)
                tags.save(str(filepath), v2_version=3)
            except Exception as e:
                result["error"] = f"Ошибка сохранения тегов: {e}"
                result["modified"] = False
                logger.error(f"  ❌ {result['error']}")
    else:
        logger.debug(f"  Теги без изменений: {filepath.name}")

    return result


# ─── Обработка одного файла (теги + имя) ─────────────────────────────────────

def process_file(
    filepath: Path,
    dry_run: bool,
    logger: logging.Logger,
    do_tags: bool = True,
    do_rename: bool = False,
    rename_confidence: float = 0.80,
) -> dict:
    """
    Обрабатывает один MP3-файл: теги и/или имя файла.

    Порядок важен: сначала теги, потом переименование.
    Если переименовать сначала — путь к файлу изменится и теги не найдём.
    """
    result = {
        "tags_modified": False,
        "tags_changed_fields": 0,
        "renamed": False,
        "skipped": False,
        "error": None,
    }

    current_path = filepath

    # ── Шаг 1: исправление тегов ─────────────────────────────────────────────
    if do_tags:
        tag_result = process_tags(current_path, dry_run, logger)
        result["tags_modified"] = tag_result["modified"]
        result["tags_changed_fields"] = tag_result["changed_fields"]
        result["skipped"] = tag_result["skipped"]
        if tag_result["error"]:
            result["error"] = tag_result["error"]

    # ── Шаг 2: переименование файла ──────────────────────────────────────────
    if do_rename:
        new_path, was_renamed = fix_filename(
            current_path, dry_run, logger, min_confidence=rename_confidence
        )
        result["renamed"] = was_renamed

    return result


# ─── Сканирование ─────────────────────────────────────────────────────────────

def scan_directory(
    root: Path,
    dry_run: bool,
    logger: logging.Logger,
    do_tags: bool = True,
    do_rename: bool = False,
    rename_confidence: float = 0.80,
    workers: int = 1,
) -> dict:
    """
    Рекурсивно обходит каталог и обрабатывает все MP3-файлы.

    workers > 1 — многопоточная обработка (ускоряет IO-bound задачи на HDD/SSD).
    При workers > 1 консольный вывод идёт через TqdmLoggingHandler —
    полоса прогресса не ломается строками логов.
    """
    logger.info("🔍 Сканирование каталога...")
    mp3_files = [p for p in root.rglob("*")
                 if p.is_file() and not p.is_symlink()
                 and p.suffix.lower() == ".mp3"
                 and not any(part.startswith(("@", ".")) for part in p.relative_to(root).parts[:-1])]
    total = len(mp3_files)

    logger.info(f"📁 Найдено MP3-файлов: {total}")
    if workers > 1:
        logger.info(f"⚡ Параллельная обработка: {workers} потоков")
    logger.info("─" * 60)

    stats = {
        "total": total,
        "tags_fixed_files": 0,
        "tags_fixed_fields": 0,
        "renamed_files": 0,
        "skipped": 0,
        "errors": 0,
    }
    lock = Lock()

    def _handle(filepath: Path) -> dict:
        return process_file(
            filepath, dry_run, logger,
            do_tags=do_tags,
            do_rename=do_rename,
            rename_confidence=rename_confidence,
        )

    def _update_stats(result: dict) -> None:
        """Обновляет общую статистику (thread-safe через Lock)."""
        with lock:
            if result["error"]:
                stats["errors"] += 1
            if result["skipped"]:
                stats["skipped"] += 1
            if result["tags_modified"]:
                stats["tags_fixed_files"] += 1
                stats["tags_fixed_fields"] += result["tags_changed_fields"]
            if result["renamed"]:
                stats["renamed_files"] += 1

    def _make_progress(iterable, total: int):
        """Возвращает итерируемое с прогресс-баром или без него."""
        if HAS_TQDM:
            return tqdm(iterable, total=total, unit="файл",
                        desc="Обработка", dynamic_ncols=True)
        return iterable

    if workers > 1:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(_handle, fp): fp for fp in mp3_files}
            for future in _make_progress(as_completed(futures), total=total):
                fp = futures[future]
                try:
                    result = future.result()
                    _update_stats(result)
                except Exception as e:
                    logger.error(f"❌ Необработанное исключение [{fp.name}]: {e}")
                    with lock:
                        stats["errors"] += 1
    else:
        for filepath in _make_progress(mp3_files, total=total):
            result = _handle(filepath)
            _update_stats(result)

    return stats


# ─── CLI ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Конвертер тегов MP3: Windows-1251 → UTF-8",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Примеры:
  python convert_tags.py "D:\Музыка" --dry-run
  python convert_tags.py "D:\Музыка"
  python convert_tags.py "D:\Музыка" --rename --dry-run --log
  python convert_tags.py "D:\Музыка" --rename --log
  python convert_tags.py "D:\Музыка" --rename --no-tags --dry-run
  python convert_tags.py "D:\Музыка" --rename --rename-confidence 0.95 --log
  python convert_tags.py "D:\Музыка" --rename --dry-run --workers 8
  python convert_tags.py "D:\Музыка" --rename --log --workers 8
        """,
    )
    parser.add_argument(
        "directory",
        nargs="?",
        default=".",
        help="Путь к каталогу с музыкой (по умолчанию: текущий)",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Только показать что изменится, не сохранять и не переименовывать",
    )
    parser.add_argument(
        "--rename", action="store_true",
        help="Включить исправление кракозябр в именах файлов",
    )
    parser.add_argument(
        "--no-tags", action="store_true",
        help="Не трогать теги (использовать вместе с --rename для обработки только имён)",
    )
    parser.add_argument(
        "--rename-confidence", type=float, default=0.80, metavar="0.0-1.0",
        help=(
            "Порог уверенности chardet для переименования (по умолч. 0.80). "
            "Рекомендуемый диапазон: 0.75–0.95"
        ),
    )
    parser.add_argument(
        "--log", action="store_true",
        help="Сохранить подробный лог в файл рядом с каталогом",
    )
    parser.add_argument(
        "--verbose", action="store_true",
        help=(
            "Подробный вывод в консоль (все файлы, включая без изменений). "
            "При --workers > 1 подавляется в консоли автоматически "
            "(во избежание наложений на прогресс-бар), но сохраняется в --log"
        ),
    )
    parser.add_argument(
        "--workers", type=int, default=1, metavar="N",
        help="Число потоков для параллельной обработки (по умолч. 1)",
    )
    args = parser.parse_args()

    # ── Валидация аргументов ─────────────────────────────────────────────────
    if args.workers < 1:
        print("❌ --workers должен быть >= 1")
        sys.exit(1)

    if not 0.0 <= args.rename_confidence <= 1.0:
        print("❌ --rename-confidence должен быть от 0.0 до 1.0")
        sys.exit(1)

    if args.no_tags and not args.rename:
        print("❌ --no-tags имеет смысл только вместе с --rename")
        sys.exit(1)

    root = Path(args.directory).resolve()
    if not root.is_dir():
        print(f"❌ Каталог не найден: {root}")
        sys.exit(1)

    # ── Определяем нужен ли TqdmLoggingHandler ───────────────────────────────
    multithreaded = args.workers > 1 and HAS_TQDM
    use_tqdm_handler = multithreaded

    if multithreaded and args.verbose:
        print(
            "⚠️  --verbose при --workers > 1 подавлен в консоли "
            "(во избежание наложений на прогресс-бар).\n"
            "   Подробный лог доступен в файле при использовании --log."
        )

    # ── Настройка логирования ─────────────────────────────────────────────────
    logger = setup_logging(
        log_to_file=args.log,
        music_dir=root,
        use_tqdm_handler=use_tqdm_handler,
    )

    # verbose в консоли — только при однопоточном режиме
    if args.verbose and not multithreaded:
        for h in logger.handlers:
            if not isinstance(h, logging.FileHandler):
                h.setLevel(logging.DEBUG)

    do_tags = not args.no_tags
    do_rename = args.rename

    # ── Шапка ─────────────────────────────────────────────────────────────────
    mode = "🔍 DRY-RUN (без изменений)" if args.dry_run else "✏️  КОНВЕРТАЦИЯ"
    logger.info(f"\n{'='*60}")
    logger.info(f"  MP3 Tag Converter: cp1251 → UTF-8  (v6)")
    logger.info(f"  Режим        : {mode}")
    logger.info(f"  Каталог      : {root}")
    logger.info(f"  Теги         : {'да' if do_tags else 'нет (--no-tags)'}")
    logger.info(
        f"  Имена файлов : "
        f"{'да (confidence >= ' + str(args.rename_confidence) + ')' if do_rename else 'нет (добавьте --rename)'}"
    )
    logger.info(f"  Потоков      : {args.workers}")
    logger.info(f"  tqdm         : {'да' if HAS_TQDM else 'нет (pip install tqdm)'}")
    logger.info(
        f"  Вывод лога   : "
        f"{'tqdm.write (без наложений)' if use_tqdm_handler else 'StreamHandler'}"
    )
    logger.info(f"{'='*60}")
    logger.info(f"  Обозначения: 🏷️  — теги   📁 — переименование   ⚠️  — предупреждение   ❌ — ошибка")
    logger.info(f"{'='*60}")

    # ── Запуск ────────────────────────────────────────────────────────────────
    start = datetime.now()
    stats = scan_directory(
        root,
        dry_run=args.dry_run,
        logger=logger,
        do_tags=do_tags,
        do_rename=do_rename,
        rename_confidence=args.rename_confidence,
        workers=args.workers,
    )
    elapsed = (datetime.now() - start).total_seconds()

    unchanged = max(
        0,
        stats["total"]
        - stats["tags_fixed_files"]
        - stats["renamed_files"]
        - stats["skipped"]
        - stats["errors"],
    )

    # ── Итоги ─────────────────────────────────────────────────────────────────
    logger.info(f"\n{'='*60}")
    logger.info(f"  📊 ИТОГИ")
    logger.info(f"  Всего файлов         : {stats['total']}")
    if do_tags:
        logger.info(
            f"  🏷️  Исправлено тегов  : {stats['tags_fixed_files']} файлов  "
            f"({stats['tags_fixed_fields']} полей)"
        )
    if do_rename:
        logger.info(f"  📁 Переименовано     : {stats['renamed_files']} файлов")
    logger.info(f"  Без изменений        : {unchanged}")
    logger.info(f"  Без тегов (пропущено): {stats['skipped']}")
    logger.info(f"  ❌ Ошибок            : {stats['errors']}")
    logger.info(f"  Время                : {elapsed:.1f} сек")
    logger.info(f"{'='*60}")

    if stats["errors"]:
        sys.exit(1)

    if args.dry_run and (stats["tags_fixed_files"] > 0 or stats["renamed_files"] > 0):
        logger.info("\n💡 Запустите без --dry-run для применения изменений")


if __name__ == "__main__":
    main()
