"""Jimaku(jimaku.cc) 에서 에피소드에 맞는 일본어 자막 1개를 골라 내려받습니다.

1. jimaku_keyword 로 작품을 검색합니다.
2. 파일 목록에서 시즌·회차가 일치하는 자막을 추립니다.
3. 파일명에 jimaku_priority 단어가 먼저 나오는(우선순위가 높은) 자막을 고릅니다.
   같은 순위면 ASS > SRT > 기타 순서, 그다음 최근 파일을 고릅니다.
4. 개별 파일이 없으면 ZIP 묶음 안에서 찾습니다.
"""
import io
import logging
import re
import unicodedata
import zipfile
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote

import requests

from .ai_client import decode_bytes
from .common import episode_code
from .extract_anime_titles import extract_season_episode
from .tools import convert_subtitle

log = logging.getLogger("translator.jimaku")

API = "https://jimaku.cc/api"
# Jimaku API 키입니다. (함수에 api_key 를 따로 넘기지 않으면 이 키를 씁니다)
JIMAKU_API_KEY = "AAAAAAAAQlMuAS4CB1Q4LDel6jey-jOuNfyV2grrSTpczHZL_mPn2iV6Ew"
SUB_EXTS = (".ass", ".ssa", ".srt", ".vtt", ".smi", ".sami")
EXT_RANK = {".ass": 0, ".srt": 1, ".ssa": 2, ".vtt": 3, ".smi": 4, ".sami": 4}
MAX_SUB_BYTES = 20 * 1024 * 1024
MAX_ZIP_BYTES = 200 * 1024 * 1024
# 파일명 끝의 언어 표시: Title.ja.srt, Title.JP.ass, Title.ja[cc].srt
_LANG_SUFFIX = re.compile(r"\.(?:ja|jp|jpn|japanese)(?:\[[^\]]*\])?$", re.IGNORECASE)
_EXTRA_EP_PATTERNS = [
    re.compile(r"第\s*(\d{1,4}(?:\.\d)?)\s*[話话回]"),
    re.compile(r"#\s*(\d{1,4}(?:\.\d)?)(?!\d)"),
    re.compile(r"\[\s*(\d{1,4}(?:\.\d)?)(?:v\d)?(?:\s*END)?\s*\]", re.IGNORECASE),
    re.compile(r"\b(?:EP|E)\.?\s?(\d{1,4}(?:\.\d)?)(?!\d)", re.IGNORECASE),
]
# 마지막 수단으로 숫자를 찾기 전에 지우는 표시들 (해상도, 코덱, 연도, CRC 등)
_NOISE = re.compile(
    r"\[[0-9A-F]{8}\]|\b\d{3,4}[pi]\b|\b\d{3,4}x\d{3,4}\b|\b(?:19|20)\d{2}\b|\b(?:x|h)\.?26[45]\b"
    r"|\b(?:10|8)[- ]?bit\b|\bMa10p\b|\b(?:AAC|DDP?|FLAC|Opus)\s?\d(?:\.\d)?\b|\bS\d{1,2}\b",
    re.IGNORECASE,
)


class JimakuError(Exception):
    pass


@dataclass
class JimakuSubtitle:
    path: Path              # 내려받아 UTF-8 로 정리한 자막 (SRT/ASS/SSA)
    file_name: str          # Jimaku 의 원래 파일명 (ZIP 이면 "묶음.zip / 항목")
    entry_id: int
    entry_name: str
    priority_word: str      # 일치한 우선순위 단어 (없으면 "")


# ---------------------------------------------------------------- API
def _headers(api_key):
    return {"Authorization": api_key or JIMAKU_API_KEY, "User-Agent": "MKV-Anime-Subtitle-Translator"}


def _get_json(url, api_key):
    try:
        response = requests.get(url, headers=_headers(api_key), timeout=20)
    except requests.RequestException as error:
        raise JimakuError("Jimaku 요청 실패: %s" % error) from error
    if response.status_code == 401:
        raise JimakuError("Jimaku API 키 인증 실패 (HTTP 401). kudong/jimaku.py 의 JIMAKU_API_KEY 를 확인하세요.")
    if response.status_code != 200:
        raise JimakuError("Jimaku 요청 실패 (HTTP %d): %s" % (response.status_code, url))
    try:
        return response.json()
    except ValueError as error:
        raise JimakuError("Jimaku 응답을 읽지 못했습니다.") from error


def _norm(text):
    return re.sub(r"[\s._\-]+", " ", unicodedata.normalize("NFKC", str(text or ""))).strip().casefold()


def _pick_entry(entries, keyword):
    key = _norm(keyword)
    for entry in entries:
        names = [_norm(entry.get(k)) for k in ("name", "english_name", "japanese_name")]
        if key and key in names:
            return entry
    for entry in entries:
        names = [_norm(entry.get(k)) for k in ("name", "english_name", "japanese_name")]
        if key and any(name and (key in name or name in key) for name in names):
            return entry
    return entries[0]


def search_entry(keyword, api_key=""):
    keyword = str(keyword or "").strip()
    if not keyword:
        raise JimakuError("jimaku_keyword 가 비어 있습니다.")
    entries = _get_json(API + "/entries/search?anime=true&query=" + quote(keyword), api_key)
    if not isinstance(entries, list) or not entries:
        raise JimakuError("Jimaku 검색 결과가 없습니다. (키워드: %s)" % keyword)
    entry = _pick_entry(entries, keyword)
    log.info("[+] Jimaku 작품: %s (id %s)", entry.get("name"), entry.get("id"))
    return entry


def list_files(entry_id, api_key=""):
    files = _get_json(API + "/entries/%s/files" % entry_id, api_key)
    if not isinstance(files, list):
        raise JimakuError("Jimaku 파일 목록을 읽지 못했습니다.")
    return files


def _download(url, api_key, limit):
    try:
        response = requests.get(url, headers=_headers(api_key), timeout=120, stream=True)
        if response.status_code != 200:
            raise JimakuError("다운로드 실패 (HTTP %d)" % response.status_code)
        data = bytearray()
        for chunk in response.iter_content(64 * 1024):
            data.extend(chunk)
            if len(data) > limit:
                raise JimakuError("파일이 너무 큽니다. (%d MB 초과)" % (limit // 1024 // 1024))
        return bytes(data)
    except requests.RequestException as error:
        raise JimakuError("다운로드 실패: %s" % error) from error


# ---------------------------------------------------------------- 시즌·회차 판별
def _strip_ext(name):
    stem = Path(name).name
    lower = stem.lower()
    for ext in SUB_EXTS + (".zip",):
        if lower.endswith(ext):
            stem = stem[: -len(ext)]
            break
    return _LANG_SUFFIX.sub("", stem)


def _num(value):
    return float(value) if "." in value else int(value)


def file_season_episode(name, entry_names=()):
    """Jimaku 파일명에서 (시즌, 회차)를 추출합니다. 못 찾으면 None"""
    stem = unicodedata.normalize("NFKC", _strip_ext(name))
    season, episode = extract_season_episode(stem)
    if episode is not None:
        return season, episode
    for pattern in _EXTRA_EP_PATTERNS:
        found = pattern.search(stem)
        if found:
            return season, _num(found.group(1))

    # 작품명(숫자가 들어간 제목 포함)과 잡음을 지운 뒤 남은 첫 숫자를 회차로 봅니다.
    rest = stem
    for entry_name in entry_names:
        if entry_name:
            rest = re.sub(re.escape(unicodedata.normalize("NFKC", entry_name)), " ", rest, flags=re.IGNORECASE)
    rest = _NOISE.sub(" ", rest)
    found = re.search(r"(?<![\d.])(\d{1,4}(?:\.5)?)(?:v\d)?(?![\d])", rest)
    if found:
        return season, _num(found.group(1))
    return season, None


def _matches(name, season, episode, entry_names):
    file_season, file_episode = file_season_episode(name, entry_names)
    if file_episode is None or float(file_episode) != float(episode):
        return False
    # 동영상에 시즌 표기가 있을 때만 시즌을 비교합니다. 파일에 시즌이 없으면 해당 엔트리의 시즌으로 봅니다.
    if season is not None and file_season is not None and file_season != season:
        return False
    return True


# ---------------------------------------------------------------- 우선순위
def priority_rank(name, priority):
    """(순위, 일치 단어). 우선순위 단어가 없으면 (len(priority), "")"""
    normalized = _norm(name)
    for index, word in enumerate(priority):
        if _norm(word) and _norm(word) in normalized:
            return index, str(word)
    return len(priority), ""


def _sorted(items, name_of, modified_of, priority):
    """우선순위 단어 순위 → 확장자(ASS > SRT > 기타) → 최근 파일 순서로 정렬합니다."""
    items = sorted(items, key=lambda i: str(modified_of(i) or ""), reverse=True)
    return sorted(items, key=lambda i: (priority_rank(name_of(i), priority)[0],
                                        EXT_RANK.get(Path(name_of(i)).suffix.lower(), 9)))


# ---------------------------------------------------------------- 저장
def _save_subtitle(data, file_name, out_dir, base_name):
    """내려받은 자막을 UTF-8 로 저장하고, 번역/싱크가 다루지 못하는 형식은 SRT 로 바꿉니다."""
    ext = Path(file_name).suffix.lower()
    text = decode_bytes(data)
    if not text.strip():
        raise JimakuError("내려받은 자막이 비어 있습니다: " + file_name)
    out_dir = Path(out_dir)
    raw = out_dir / ("%s.jimaku%s" % (base_name, ext))
    raw.write_text(text, encoding="utf-8", newline="")
    if ext in (".ass", ".ssa", ".srt"):
        return raw
    converted = convert_subtitle(raw, out_dir / ("%s.jimaku.srt" % base_name))
    raw.unlink(missing_ok=True)
    return converted


def _from_zips(archives, season, episode, priority, entry_names, api_key):
    """ZIP 묶음 안에서 회차에 맞는 자막을 찾습니다. 반환: (zip 파일명, 항목명, bytes) 또는 None"""
    found = []
    for item in archives:
        try:
            data = _download(item["url"], api_key, MAX_ZIP_BYTES)
            archive = zipfile.ZipFile(io.BytesIO(data))
        except (JimakuError, zipfile.BadZipFile) as error:
            log.warning("[=] ZIP 을 열지 못했습니다: %s (%s)", item.get("name"), error)
            continue
        for info in archive.infolist():
            member = info.filename.replace("\\", "/").split("/")[-1]
            if info.is_dir() or not member.lower().endswith(SUB_EXTS) or info.file_size > MAX_SUB_BYTES:
                continue
            if _matches(member, season, episode, entry_names):
                found.append((item["name"], member, archive.read(info), item.get("last_modified")))
    if not found:
        return None
    # 확장자 판단은 항목 이름 기준이므로 "zip 이름 항목 이름" 순서로 붙입니다.
    found = _sorted(found, lambda f: f[0] + " " + f[1], lambda f: f[3], priority)
    return found[0][:3]


def fetch_subtitle(keyword, season, episode, priority, out_dir, base_name, api_key=""):
    """에피소드에 맞는 Jimaku 자막 1개를 내려받아 JimakuSubtitle 로 돌려줍니다. 없으면 JimakuError"""
    if episode is None:
        raise JimakuError("회차 번호를 알 수 없습니다.")
    entry = search_entry(keyword, api_key)
    entry_id = entry.get("id")
    entry_names = [str(entry.get(k) or "") for k in ("name", "english_name", "japanese_name")] + [keyword]
    files = list_files(entry_id, api_key)
    log.info("> Jimaku 파일 %d개", len(files))

    candidates = [
        f for f in files
        if str(f.get("name", "")).lower().endswith(SUB_EXTS) and _matches(f.get("name", ""), season, episode, entry_names)
    ]
    candidates = _sorted(candidates, lambda f: f["name"], lambda f: f.get("last_modified"), priority)
    if candidates:
        log.info("> 회차가 일치하는 자막 %d개:", len(candidates))
        for f in candidates:
            log.info("    %s", f["name"])

    for f in candidates:
        try:
            data = _download(f["url"], api_key, MAX_SUB_BYTES)
            path = _save_subtitle(data, f["name"], out_dir, base_name)
        except Exception as error:
            log.warning("[=] 자막을 내려받지 못해 다음 후보를 시도합니다: %s (%s)", f["name"], error)
            continue
        _rank, word = priority_rank(f["name"], priority)
        log.info("[+] Jimaku 자막 선택: %s", f["name"])
        return JimakuSubtitle(path, f["name"], entry_id, str(entry.get("name") or ""), word)

    archives = [f for f in files if str(f.get("name", "")).lower().endswith(".zip")]
    if archives:
        archives = _sorted(archives, lambda f: f["name"], lambda f: f.get("last_modified"), priority)
        log.info("> 개별 파일이 없어 ZIP %d개에서 찾습니다.", len(archives))
        found = _from_zips(archives, season, episode, priority, entry_names, api_key)
        if found:
            zip_name, member, data = found
            path = _save_subtitle(data, member, out_dir, base_name)
            _rank, word = priority_rank(zip_name + " " + member, priority)
            log.info("[+] Jimaku 자막 선택: %s / %s", zip_name, member)
            return JimakuSubtitle(path, "%s / %s" % (zip_name, member), entry_id, str(entry.get("name") or ""), word)

    raise JimakuError("Jimaku 에서 회차(%s)가 일치하는 자막을 찾지 못했습니다. (작품: %s)"
                      % (episode_code(season, episode), entry.get("name")))
