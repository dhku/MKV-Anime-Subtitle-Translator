#!/usr/bin/env python3
"""
애니메이션 동영상 파일명에서 작품 이름(언어 무관)만 추출해 JSON으로 저장합니다.

사용법:
    python extract_anime_titles.py filenames.txt            # 한 줄에 파일명 하나인 텍스트 파일
    python extract_anime_titles.py /path/to/video/folder    # 폴더 안의 동영상 파일 스캔
    python extract_anime_titles.py filenames.txt -o out.json
    python extract_anime_titles.py filenames.txt -e          # 작품명 + 시즌 + 회차를 파일별로 저장

코드에서 직접 쓰기:
    from extract_anime_titles import extract_title, extract_season_episode, parse_filename
"""
import argparse
import json
import re
import sys
from pathlib import Path

VIDEO_EXTS = {".mkv", ".mp4", ".avi", ".m4v", ".mov", ".wmv", ".flv", ".webm", ".ts", ".m2ts"}

# 괄호류로 감싼 부분: [SubsPlease], (1080p), [F86EEED0], (らぶいーず), 【...】 등
BRACKETS = re.compile(r"\[[^\]]*\]|\([^)]*\)|【[^】]*】|（[^）]*）|「[^」]*」")

# 작품명이 끝나는 지점을 알려주는 표시들 (가장 먼저 나오는 위치에서 자름)
CUT_PATTERNS = [
    r"\bS\d{1,2}\s?E\d{1,4}\b",                    # S01E05
    r"\bS\d{1,2}\b",                               # S02 (시즌만 있는 경우)
    r"\bSeason\s?\d{1,2}\b",                       # Season 2
    r"\b\d{1,2}(?:st|nd|rd|th)\s?Season\b",        # 2nd Season
    r"\b\d{1,2}x\d{2,4}\b",                        # 1x05
    r"\b(?:Episode|Ep|E)\.?\s?\d{1,4}\b",          # Episode 5, Ep05, E05
    r"\s-\s*\d{1,4}(?:\.\d)?(?:v\d)?\b",           # " - 06", " - 01v2"
    # 참고: 숫자 없는 " - "는 자르지 않음 → "Title - Subtitle" 형태의 부제 유지
    r"\b(?:480|540|576|720|1080|2160)[pi]\b",      # 해상도
    r"\b\d{3,4}x\d{3,4}\b",                        # 1920x1080
    r"\b(?:WEB-?DL|WEB-?Rip|WEB|BluRay|BDRip|BD|HDTV|DVDRip|UNCENSORED)\b",
    r"\b(?:x26[45]|H\s?26[45]|HEVC|AVC|AAC\d?|FLAC|DDP?\d?|Opus|10bit)\b",
    r"\b(?:CR|NF|AMZN|DSNP|ADN|BILI|ABEMA|HIDIVE|Hulu|TVER)\b(?=\s|$)",  # 스트리밍 소스
]
CUT_RE = re.compile("|".join(f"(?:{p})" for p in CUT_PATTERNS), re.IGNORECASE)

# 맨 끝에 숫자만 남은 경우: "Title 05"
TRAILING_EP = re.compile(r"\s\d{1,4}(?:v\d)?$")


def _normalize(filename: str) -> str:
    """확장자·괄호 태그를 지우고 점/밑줄 구분자를 공백으로 바꾼 문자열을 돌려줍니다."""
    name = filename.strip()
    # 확장자 제거
    p = Path(name)
    if p.suffix.lower() in VIDEO_EXTS:
        name = p.stem

    # 괄호 태그 제거
    name = BRACKETS.sub(" ", name)

    # 점/밑줄 구분 방식(Scene 스타일) → 공백으로 (단, "12.5" 같은 소수점은 유지)
    name = name.replace("_", " ")
    if " " not in name.strip():
        name = re.sub(r"\.(?!\d)|(?<!\d)\.", " ", name)

    return re.sub(r"\s+", " ", name).strip()


# ---- 시즌 / 회차 패턴 ----
EP_NUM = r"(\d{1,4}(?:\.\d)?)(?:v\d)?"   # 12, 12.5, 01v2

# 시즌과 회차가 함께 있는 형식
SEASON_EP_PATTERNS = [
    re.compile(rf"\bS(\d{{1,2}})\s?E{EP_NUM}\b", re.IGNORECASE),        # S01E05
    re.compile(rf"\b(\d{{1,2}})x{EP_NUM}\b", re.IGNORECASE),            # 1x05
]
# 시즌만 있는 형식
SEASON_PATTERNS = [
    re.compile(r"\bS(\d{1,2})\b", re.IGNORECASE),                       # S2
    re.compile(r"\bSeason\s?(\d{1,2})\b", re.IGNORECASE),               # Season 2
    re.compile(r"\b(\d{1,2})(?:st|nd|rd|th)\s?Season\b", re.IGNORECASE), # 2nd Season
]
# 회차만 있는 형식
EP_PATTERNS = [
    re.compile(rf"\b(?:Episode|Ep|E)\.?\s?{EP_NUM}\b", re.IGNORECASE),  # Episode 5, Ep05, E05
    re.compile(rf"\s-\s*{EP_NUM}\b"),                                   # " - 06"
]


def _to_number(s: str):
    return float(s) if "." in s else int(s)


def extract_season_episode(filename: str) -> tuple[int | None, int | float | None]:
    """
    파일명에서 (시즌, 회차)를 추출합니다.
    - 표기가 없으면 None (예: "[SubsPlease] Title - 06" → (None, 6))
    - 12.5화 같은 특별편은 float로 반환
    """
    name = _normalize(filename)

    for pat in SEASON_EP_PATTERNS:
        m = pat.search(name)
        if m:
            return int(m.group(1)), _to_number(m.group(2))

    season = None
    for pat in SEASON_PATTERNS:
        m = pat.search(name)
        if m:
            season = int(m.group(1))
            break

    episode = None
    for pat in EP_PATTERNS:
        m = pat.search(name)
        if m:
            episode = _to_number(m.group(1))
            break

    return season, episode


def parse_filename(filename: str) -> dict:
    """작품명·시즌·회차를 한 번에 추출해 dict로 돌려줍니다."""
    season, episode = extract_season_episode(filename)
    return {
        "file": filename,
        "title": extract_title(filename),
        "season": season,
        "episode": episode,
    }


def extract_title(filename: str) -> str:
    name = _normalize(filename)

    # 회차/화질 표시가 처음 등장하는 곳에서 자르기
    m = CUT_RE.search(name)
    if m:
        # 맨 앞부터 회차/화질 표시라면 영어 작품명이 없는 파일 (예: 일본어 제목만 있는 경우)
        name = name[: m.start()]

    name = name.strip(" -_.:~")
    name = TRAILING_EP.sub("", name).strip(" -_.:~")
    return re.sub(r"\s+", " ", name)


def load_filenames(source: Path) -> list[str]:
    if source.is_dir():
        return sorted(f.name for f in source.rglob("*") if f.suffix.lower() in VIDEO_EXTS)
    return [line for line in source.read_text(encoding="utf-8").splitlines() if line.strip()]


def main():
    ap = argparse.ArgumentParser(description="파일명에서 애니메이션 작품명 추출")
    ap.add_argument("source", help="파일명 목록 텍스트 파일 또는 동영상 폴더")
    ap.add_argument("-o", "--output", default="anime_titles.json", help="저장할 JSON 경로")
    ap.add_argument("-e", "--episodes", action="store_true",
                    help="파일별 작품명·시즌·회차를 함께 저장")
    args = ap.parse_args()

    filenames = load_filenames(Path(args.source))

    if args.episodes:
        records = [parse_filename(fn) for fn in filenames]
        Path(args.output).write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"{len(records)}개 파일 정보를 {args.output}에 저장했습니다.")
        for r in records:
            print(f"  {r['title']!s:<45} season={r['season']!s:<5} episode={r['episode']}")
        return

    titles, seen, skipped = [], set(), []
    for fn in filenames:
        title = extract_title(fn)
        if not title:
            skipped.append(fn)
            continue
        key = title.casefold()
        if title and key not in seen:
            seen.add(key)
            titles.append(title)

    Path(args.output).write_text(json.dumps(titles, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"{len(titles)}개 작품명을 {args.output}에 저장했습니다.")
    print(json.dumps(titles, ensure_ascii=False, indent=2))
    if skipped:
        print(f"\n작품명을 찾지 못해 건너뛴 파일 {len(skipped)}개:")
        for fn in skipped:
            print(f"  - {fn}")


if __name__ == "__main__":
    sys.exit(main())
