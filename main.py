import logging
import os
import sys
import time
from pathlib import Path

from kudong import pipeline
from kudong.config import ConfigError, load_settings
from kudong.logger import setup_logging

# =================================================
# Title: MKV-Anime-Subtitle-Translator
# Author: KUDONG
# Version: 1.0
# Url: https://github.com/dhku/MKV-Anime-Subtitle-Translator
# =================================================

log = logging.getLogger("translator")


def base_dir():
    """settings.yml, anime.yml 이 있는 폴더. APP_HOME 환경 변수 > 현재 폴더 > main.py 폴더"""
    if os.environ.get("APP_HOME"):
        return Path(os.environ["APP_HOME"]).resolve()
    cwd = Path.cwd()
    if (cwd / "settings.yml").is_file():
        return cwd
    return Path(__file__).resolve().parent


def run():
    base = base_dir()
    log_path = setup_logging(base / "log")
    started = time.monotonic()
    log.info("MKV-Anime-Subtitle-Translator 시작")
    log.info("> 설정 폴더: %s", base)
    log.info("> 로그: %s", log_path)

    try:
        # 1. 설정 로드
        settings = load_settings(base)
        log.info("> target_path: %s", settings.target_path)
        log.info("> output_path: %s", settings.output_path)
        log.info("> translate_mode: %s / sync_jimaku_to_embed: %s / use_whisper: %s",
                 settings.translate_mode, settings.sync_jimaku_to_embed, settings.use_whisper)
        log.info("> jimaku_priority: %s", " > ".join(settings.jimaku_priority) or "(없음)")
        stats = pipeline.run(settings)
    except ConfigError as error:
        log.error("[-] 설정 오류: %s", error)
        return 2
    except Exception:
        log.exception("[-] 예상하지 못한 오류로 종료합니다.")
        return 1

    log.info("================================================================")
    log.info("완료 %d개, 건너뜀 %d개, 실패 %d개, anime.yml 에 없는 파일 %d개 (%d초)",
             stats["done"], stats["skip"], stats["fail"], stats["unmatched"], int(time.monotonic() - started))
    return 1 if stats["fail"] else 0


if __name__ == "__main__":
    sys.exit(run())
