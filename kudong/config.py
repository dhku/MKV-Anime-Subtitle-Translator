"""settings.yml(전역 설정)과 anime.yml(작품별 설정)을 읽고 씁니다."""
import json
import logging
import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

import yaml


log = logging.getLogger("translator.config")

TRANSLATE_MODES = ("jimaku", "embed", "auto")
DEFAULT_PRIORITY = ["ABEMA", "Netflix", "Nekomoe kissaten", "NanakoRaws", "shincaps"]
LIST_KEY = "mkv_anime_list"


class ConfigError(Exception):
    pass


def _bool(value, default):
    if value is None or value == "":
        return default
    if isinstance(value, str):
        return value.strip().lower() not in ("false", "off", "no", "none", "0")
    return bool(value)


def _mode(value, where):
    mode = str(value or "").strip().lower()
    if mode not in TRANSLATE_MODES:
        raise ConfigError("%s 의 translate_mode '%s' 는 지원하지 않습니다. (%s 중 하나)"
                          % (where, value, ", ".join(TRANSLATE_MODES)))
    return mode


def _priority(value):
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        return None
    words = [str(v).strip() for v in value if v is not None and str(v).strip()]
    return words


def title_key(title):
    """작품 제목 비교용 키: 대소문자·공백 차이를 무시합니다."""
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", str(title or ""))).strip().casefold()


# ---------------------------------------------------------------- settings.yml
@dataclass
class Settings:
    base_dir: Path
    settings_path: Path
    anime_path: Path
    prompt_path: Path
    log_dir: Path
    target_path: Path
    output_path: Path
    translate_mode: str = "jimaku"
    sync_jimaku_to_embed: bool = True
    use_whisper: bool = True
    jimaku_priority: list = field(default_factory=lambda: list(DEFAULT_PRIORITY))

    def resolve(self, path):
        path = Path(str(path))
        return path if path.is_absolute() else (self.base_dir / path)


DEFAULT_SETTINGS_TEXT = """# 기본 설정
global:
  target_path: ./target # mkv
  output_path: ./output # ass srt
  translate_mode: "jimaku" # jimaku, embed, auto(jimaku 실패 시 embed) 중 하나 선택
  sync_jimaku_to_embed: true # jimaku 자막을 내장 자막 기준으로 싱크를 맞춘다 (기본값 true)
  use_whisper: true # 내장 자막이 없을 때 Whisper로 레퍼런스 자막 생성 여부

# Jimaku 자막 선택 우선순위. 파일명에 앞쪽 단어가 들어간 자막을 먼저 고릅니다.
jimaku_priority:
  - ABEMA
  - Netflix
  - Nekomoe kissaten
  - NanakoRaws
  - shincaps

# AI 자막 번역. active 만 바꾸면 제공자가 바뀝니다. 기본값 none 은 번역을 사용하지 않습니다.
# API 키는 사용할 제공자의 api_key 에 적습니다.
ai-translate:
  active: none # openai 또는 gemini 또는 타 제공자 (Claude, DeepSeek, etc.)
  concurrency: 3 # 동시에 보낼 번역 요청 수 (기본값: 3, 1이면 순서대로). 429 오류가 나면 줄이세요
  glossary: true # true면 작품별 용어집을 쓰고 매 화 새 항목을 덧붙임 (없으면 새로 만듦), false면 용어집을 사용하지 않음
  item_batch: 200 # SRT, ASS, SSA 를 한 요청에 넣을 대사 수 (기본값: 200, 1~4000). 응답에서 줄을 빠뜨리면 줄이세요
  providers:
    openai:
      api_key: ""
      model: gpt-5.5
      base_url: "https://api.openai.com/v1"
    gemini:
      api_key: ""
      model: gemini-3.1-pro-preview
      base_url: "https://generativelanguage.googleapis.com/v1beta/openai/"
"""


def load_settings(base_dir):
    base_dir = Path(base_dir)
    path = base_dir / "settings.yml"
    if not path.is_file():
        # 처음 실행(예: 빈 Docker 볼륨)이면 기본 설정 파일을 만들어 둡니다.
        base_dir.mkdir(parents=True, exist_ok=True)
        path.write_text(DEFAULT_SETTINGS_TEXT, encoding="utf-8", newline="")
        log.warning("[=] settings.yml 이 없어 기본값으로 만들었습니다: %s", path)
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8-sig")) or {}
    except FileNotFoundError as error:
        raise ConfigError("settings.yml 을 찾지 못했습니다: %s" % path) from error
    except yaml.YAMLError as error:
        raise ConfigError("settings.yml 형식이 올바르지 않습니다.\n%s" % error) from error
    if not isinstance(data, dict):
        raise ConfigError("settings.yml 형식이 올바르지 않습니다.")

    # 전역 설정은 global: 아래에 둡니다. (최상위에 적어도 읽습니다)
    section = data.get("global") if isinstance(data.get("global"), dict) else {}
    get = lambda key, default=None: section.get(key, data.get(key, default))

    settings = Settings(
        base_dir=base_dir,
        settings_path=path,
        anime_path=base_dir / "anime.yml",
        prompt_path=base_dir / "prompt.yml",
        log_dir=base_dir / "log",
        target_path=base_dir,
        output_path=base_dir,
    )
    settings.target_path = settings.resolve(get("target_path") or "./target")
    settings.output_path = settings.resolve(get("output_path") or "./output")
    settings.translate_mode = _mode(get("translate_mode") or "jimaku", "settings.yml")
    settings.sync_jimaku_to_embed = _bool(get("sync_jimaku_to_embed"), True)
    settings.use_whisper = _bool(get("use_whisper"), True)
    priority = _priority(get("jimaku_priority"))
    settings.jimaku_priority = priority if priority is not None else list(DEFAULT_PRIORITY)
    return settings


# ---------------------------------------------------------------- anime.yml
@dataclass
class Anime:
    title: str
    translate_mode: str
    jimaku_keyword: str
    sync_jimaku_to_embed: bool
    use_whisper: bool
    target_path: Path
    output_path: Path
    jimaku_priority: list


def _read_anime_items(path):
    """anime.yml 의 작품 목록(dict 리스트)과 저장 형식("string"/"list")을 돌려줍니다."""
    if not path.is_file():
        return [], "missing"
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8-sig")) or {}
    except yaml.YAMLError as error:
        raise ConfigError("anime.yml 형식이 올바르지 않습니다.\n%s" % error) from error
    if not isinstance(data, dict):
        raise ConfigError("anime.yml 형식이 올바르지 않습니다.")
    raw = data.get(LIST_KEY)
    if raw is None:
        return [], "missing"
    if isinstance(raw, str):
        # mkv_anime_list: '[ {...}, {...} ]' 처럼 JSON 문자열로 적는 형식
        try:
            items = json.loads(raw) if raw.strip() else []
        except json.JSONDecodeError as error:
            raise ConfigError("anime.yml 의 %s JSON 형식이 올바르지 않습니다: %s" % (LIST_KEY, error)) from error
        kind = "string"
    elif isinstance(raw, list):
        items, kind = raw, "list"
    else:
        raise ConfigError("anime.yml 의 %s 는 목록이어야 합니다." % LIST_KEY)
    if not isinstance(items, list) or not all(isinstance(i, dict) for i in items):
        raise ConfigError("anime.yml 의 %s 항목은 { } 객체여야 합니다." % LIST_KEY)
    return items, kind


def _entry_text(title, mode):
    def q(value):
        # JSON 문자열을 YAML 작은따옴표 문자열 안에 넣으므로 ' 는 '' 로 씁니다.
        return json.dumps(value, ensure_ascii=False).replace("'", "''")

    return (
        "  { \n"
        '    "title" : %s,\n'
        '    "translate_mode" : %s,\n'
        '    "jimaku_keyword" : %s\n'
        "  }" % (q(title), q(mode), q(title))
    )


def add_new_titles(settings, titles):
    """anime.yml 에 없는 작품을 추가합니다. 기존 항목은 수정하지 않습니다. 반환: 추가한 제목 목록"""
    path = settings.anime_path
    items, kind = _read_anime_items(path)
    known = {title_key(item.get("title")) for item in items}
    new_titles = []
    for title in titles:
        key = title_key(title)
        if key and key not in known:
            known.add(key)
            new_titles.append(title)
    if not new_titles:
        return []

    mode = settings.translate_mode
    if kind == "string":
        text = path.read_text(encoding="utf-8-sig")
        # JSON 배열을 닫는 ] (바로 뒤가 YAML 문자열 끝 ') 앞에 새 항목을 끼워 넣어 기존 내용과 서식을 그대로 둡니다.
        close = None
        for match in re.finditer(r"\][ \t\r\n]*'", text):
            close = match
        if close is None:
            raise ConfigError("anime.yml 에서 %s 목록의 끝(]')을 찾지 못했습니다." % LIST_KEY)
        head = text[: close.start()].rstrip()
        joined = ",\n".join(_entry_text(t, mode) for t in new_titles)
        separator = "\n" if head.endswith("[") else ",\n"
        updated = head + separator + joined + "\n\n  " + text[close.start():]
    else:
        # 새 파일이거나 YAML 목록 형식이면 같은 형식으로 다시 씁니다.
        new_items = [{"title": t, "translate_mode": mode, "jimaku_keyword": t} for t in new_titles]
        if kind == "list":
            data = yaml.safe_load(path.read_text(encoding="utf-8-sig")) or {}
            data[LIST_KEY] = items + new_items
            updated = yaml.safe_dump(data, allow_unicode=True, sort_keys=False)
        else:
            joined = ",\n".join(_entry_text(t, mode) for t in new_titles)
            updated = "%s: '[\n\n%s\n\n  ]'\n" % (LIST_KEY, joined)

    # 쓰기 전에 다시 읽어 보고, 기존 항목이 그대로이고 새 항목이 들어갔을 때만 저장합니다.
    try:
        data = yaml.safe_load(updated) or {}
        raw = data.get(LIST_KEY)
        check = json.loads(raw) if isinstance(raw, str) else raw
    except (yaml.YAMLError, json.JSONDecodeError) as error:
        raise ConfigError("anime.yml 에 새 작품을 추가하지 못했습니다: %s" % error) from error
    if check[: len(items)] != items or [i.get("title") for i in check[len(items):]] != new_titles:
        raise ConfigError("anime.yml 에 새 작품을 추가하지 못했습니다. (검증 실패)")

    path.write_text(updated, encoding="utf-8", newline="")
    return new_titles


def load_anime_list(settings):
    items, _kind = _read_anime_items(settings.anime_path)
    animes = []
    seen = set()
    for index, item in enumerate(items, start=1):
        title = str(item.get("title") or "").strip()
        if not title:
            log.warning("[=] anime.yml %d번째 항목에 title 이 없어 건너뜁니다.", index)
            continue
        key = title_key(title)
        if key in seen:
            log.warning("[=] anime.yml 에 '%s' 가 중복되어 첫 항목만 사용합니다.", title)
            continue
        seen.add(key)

        def pick(name, default):
            value = item.get(name)
            return default if value is None or (isinstance(value, str) and value.strip() == "") else value

        try:
            mode = _mode(pick("translate_mode", settings.translate_mode), "anime.yml '%s'" % title)
        except ConfigError as error:
            log.error("[-] %s → 이 작품은 건너뜁니다.", error)
            continue
        priority = _priority(item.get("jimaku_priority"))
        animes.append(Anime(
            title=title,
            translate_mode=mode,
            jimaku_keyword=str(pick("jimaku_keyword", title)).strip(),
            sync_jimaku_to_embed=_bool(item.get("sync_jimaku_to_embed"), settings.sync_jimaku_to_embed),
            use_whisper=_bool(item.get("use_whisper"), settings.use_whisper),
            target_path=settings.resolve(pick("target_path", settings.target_path)),
            output_path=settings.resolve(pick("output_path", settings.output_path)),
            jimaku_priority=priority if priority is not None else settings.jimaku_priority,
        ))
    return animes
