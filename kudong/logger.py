"""실행 로그: log/ 폴더에 실행 1회당 파일 1개를 만들고, 최대 100개까지 보관합니다."""
import logging
import sys
from datetime import datetime
from pathlib import Path

MAX_LOG_FILES = 100
FORMAT = "%(asctime)s [%(levelname)s] %(message)s"


def _prune(log_dir, keep):
    files = sorted(log_dir.glob("*.log"), key=lambda p: (p.stat().st_mtime, p.name))
    for old in files[: max(0, len(files) - keep)]:
        try:
            old.unlink()
        except OSError:
            pass


def setup_logging(log_dir):
    log_dir = Path(log_dir)
    log_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    path = log_dir / (stamp + ".log")
    counter = 1
    while path.exists():
        counter += 1
        path = log_dir / ("%s_%d.log" % (stamp, counter))

    # 새 파일을 만들기 전에 정리해 새 파일을 포함해 최대 MAX_LOG_FILES 개가 되게 합니다.
    _prune(log_dir, MAX_LOG_FILES - 1)

    formatter = logging.Formatter(FORMAT, "%Y-%m-%d %H:%M:%S")
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    for handler in list(root.handlers):
        root.removeHandler(handler)

    file_handler = logging.FileHandler(path, encoding="utf-8")
    file_handler.setFormatter(formatter)
    root.addHandler(file_handler)

    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except (OSError, ValueError):
            pass
    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(formatter)
    root.addHandler(console)

    # 라이브러리의 HTTP 요청 로그는 줄입니다.
    for name in ("httpx", "httpcore", "openai", "urllib3", "faster_whisper"):
        logging.getLogger(name).setLevel(logging.WARNING)
    return path
