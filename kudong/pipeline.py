"""target 폴더의 동영상마다 한국어 자막을 만들어 output/{작품명}/{S01E03}/ 에 저장합니다."""
import logging
import shutil
import tempfile
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from . import ai_client, jimaku, tools
from .common import episode_code, safe_name
from .config import Anime, Settings, add_new_titles, load_anime_list, title_key
from .extract_anime_titles import VIDEO_EXTS, extract_season_episode, extract_title

log = logging.getLogger("translator")

FINISH_FILE = "finish.txt"
LANG_TAG = {"ja": "ja", "en": "en"}


class EpisodeError(Exception):
    pass


@dataclass
class Episode:
    video: Path
    title: str
    season: object   # 파일명에 시즌 표기가 없으면 None (폴더 이름은 S01)
    episode: object

    @property
    def code(self):
        return episode_code(self.season, self.episode)


@dataclass
class Job:
    settings: Settings
    anime: Anime
    ep: Episode
    work: Path                       # 임시 작업 폴더
    out_files: dict = field(default_factory=dict)   # 결과 파일 이름 → 작업 폴더의 경로
    report: list = field(default_factory=list)      # finish.txt 에 남길 줄

    def note(self, key, value):
        self.report.append("%s: %s" % (key, value))

    @property
    def base(self):
        return self.ep.video.stem


# ---------------------------------------------------------------- 동영상 찾기
def scan_videos(folder):
    folder = Path(folder)
    if not folder.is_dir():
        log.warning("[=] 동영상 폴더가 없습니다: %s", folder)
        return []
    videos = []
    for path in folder.rglob("*"):
        # Synology 의 @eaDir 같은 시스템 폴더와 숨김 파일은 건너뜁니다.
        if any(part.startswith(("@", ".")) for part in path.relative_to(folder).parts):
            continue
        if path.is_file() and path.suffix.lower() in VIDEO_EXTS:
            videos.append(path)
    return sorted(videos)


def parse_episode(video):
    season, episode = extract_season_episode(video.name)
    return Episode(video=video, title=extract_title(video.name), season=season, episode=episode)


def update_anime_list(settings):
    """2단계: 전역 target_path 의 파일명에서 작품 제목을 뽑아 anime.yml 에 없는 작품을 추가합니다."""
    titles = []
    for video in scan_videos(settings.target_path):
        title = extract_title(video.name)
        if title:
            titles.append(title)
        else:
            log.warning("[=] 파일명에서 작품 제목을 찾지 못했습니다: %s", video.name)
    added = add_new_titles(settings, titles)
    for title in added:
        log.info("[+] anime.yml 에 새 작품을 추가했습니다: %s", title)
    return added


# ---------------------------------------------------------------- 번역 / 싱크
def _translate(job, src, lang):
    """src 자막을 전처리·번역해 결과 목록에 넣고 번역 자막 경로를 돌려줍니다.
    AI 번역이 꺼져 있으면 원문 자막만 남기고 None 을 돌려줍니다."""
    settings = job.settings
    if not ai_client.is_enabled(settings.settings_path):
        log.warning("[=] AI 번역이 꺼져 있어(ai-translate.active: none) 원문 자막만 저장합니다.")
        job.note("번역", "사용 안 함 (ai-translate.active: none) — 원문 자막만 저장")
        return None
    out_name = "%s.ko%s" % (job.base, ai_client.output_extension(src))
    out_path = job.work / out_name
    title_dir = job.anime.output_path / safe_name(job.anime.title)
    glossary = title_dir / ("용어집-%s.yml" % lang)
    info = ai_client.translate_subtitle(
        src, out_path, lang, settings.settings_path, settings.prompt_path,
        glossary_path=str(glossary), title=job.anime.title,
    )
    job.out_files[out_name] = out_path
    job.note("번역", "%s → 한국어 (%s / %s)" % (ai_client.LANG_NAMES[lang], info["provider"], info["model"]))
    return out_path


def _reference(job):
    """싱크 기준 자막: 첫 번째 내장 자막, 없으면 Whisper. 반환 (경로, 설명) / 만들 수 없으면 (None, 이유)"""
    ref, info = tools.extract_first_subtitle(job.ep.video, job.work, "reference.embed")
    if ref is not None:
        desc = "내장 자막 (트랙 #%s, %s, %s)" % (info["index"], info["codec"], info["language"] or "언어 정보 없음")
        return ref, desc
    log.info("> %s", info)
    if not job.anime.use_whisper:
        return None, "%s use_whisper: false 라 Whisper 를 사용하지 않습니다." % info
    path = tools.whisper_reference(job.ep.video, job.work / "reference.whisper.srt", language="ja")
    return path, "Whisper %s (VAD 필터, 내장 자막 없음: %s)" % (tools.WHISPER_MODEL, info)


def _sync(job, targets):
    """alass 싱크 보정. targets: [(설명, 자막 경로)] 를 같은 레퍼런스에 맞춥니다.

    반환: {자막 경로: 결과 경로}. 실패해도 에피소드는 실패로 보지 않고, 원래 타이밍 자막을 사용합니다.
    """
    synced = {src: src for _label, src in targets}
    log.info("[+] 싱크 보정을 시작합니다.")
    try:
        ref, desc = _reference(job)
    except Exception as error:
        log.warning("[=] 레퍼런스 자막을 만들지 못해 Jimaku 원래 타이밍을 사용합니다: %s", error)
        job.note("싱크 보정", "실패 — 원래 타이밍 사용 (%s)" % " ".join(str(error).split()))
        return synced
    if ref is None:
        log.warning("[=] 싱크 보정을 건너뜁니다: %s", desc)
        job.note("싱크 보정", "건너뜀 — " + desc)
        return synced
    log.info("> 레퍼런스: %s", desc)
    job.note("싱크 레퍼런스", desc)

    for label, src in targets:
        log.info("> alass 싱크: %s (%s)", label, src.name)
        out = job.work / ("%s.synced%s" % (src.stem, src.suffix))
        try:
            result = tools.alass_sync_file(ref, src, out)
        except Exception as error:
            log.warning("[=] %s 싱크 보정에 실패해 원래 타이밍을 사용합니다: %s", label, error)
            job.note("싱크 보정 (%s)" % label, "실패 — 원래 타이밍 사용 (%s)" % " ".join(str(error).split()))
            continue
        offsets = ", ".join("%+.2fs(%d줄)" % (s.offset, s.lines) for s in result.segments[:8])
        if len(result.segments) > 8:
            offsets += ", ..."
        job.note("싱크 보정 (%s)" % label, "alass 적용 — 구간 %d개: %s%s" % (
            len(result.segments), offsets,
            " / 0초 고정 %d줄" % result.clamped if result.clamped else ""))
        synced[src] = out
    return synced


# ---------------------------------------------------------------- 번역 모드
def run_jimaku(job):
    anime, ep = job.anime, job.ep
    sub = jimaku.fetch_subtitle(
        anime.jimaku_keyword, ep.season, ep.episode, anime.jimaku_priority,
        job.work, job.base,
    )
    job.note("자막 출처", "Jimaku — %s (entry %s) / %s" % (sub.entry_name, sub.entry_id, sub.file_name))
    job.note("우선순위 일치", sub.priority_word or "없음 (우선순위 단어가 없는 파일)")

    src = sub.path
    ja_name = "%s.ja%s" % (job.base, src.suffix)
    job.out_files[ja_name] = src

    # 1) 전처리(같은 시간 대사 병합, 효과음·가사·라벨 줄 정리)와 번역을 먼저 합니다.
    ko_path = _translate(job, src, "ja")

    # 2) 정리된 번역 자막을 alass 로 동영상에 맞춥니다. 원문 자막도 같은 레퍼런스로 따로 맞춥니다.
    if not anime.sync_jimaku_to_embed:
        job.note("싱크 보정", "사용 안 함 (sync_jimaku_to_embed: false)")
        return
    targets = [("한국어 번역", ko_path)] if ko_path is not None else []
    targets.append(("일본어 원문", src))
    synced = _sync(job, targets)
    job.out_files[ja_name] = synced[src]
    if ko_path is not None:
        job.out_files[ko_path.name] = synced[ko_path]


def run_embed(job):
    src, info = tools.extract_first_subtitle(job.ep.video, job.work, job.base + ".embed")
    if src is None:
        raise EpisodeError("embed 모드 실패: " + info)
    job.note("자막 출처", "내장 자막 (트랙 #%s, %s, %s%s)" % (
        info["index"], info["codec"], info["language"] or "언어 정보 없음",
        ", " + info["title"] if info["title"] else ""))
    job.out_files["%s.en%s" % (job.base, src.suffix)] = src
    _translate(job, src, "en")


def run_mode(job):
    mode = job.anime.translate_mode
    if mode == "jimaku":
        run_jimaku(job)
        return "jimaku"
    if mode == "embed":
        run_embed(job)
        return "embed"

    # auto: jimaku 를 먼저 하고, 실패하면 embed
    try:
        run_jimaku(job)
        return "jimaku"
    except Exception as error:
        log.warning("[=] jimaku 모드 실패 → embed 모드로 전환합니다: %s", error)
        job.report = ["jimaku 실패 사유: %s" % " ".join(str(error).split())]
        job.out_files.clear()
        run_embed(job)
        return "embed"


# ---------------------------------------------------------------- 에피소드 처리
def _write_finish(path, job, used_mode, started):
    elapsed = int((datetime.now() - started).total_seconds())
    lines = [
        "처리 완료: %s (%d초)" % (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), elapsed),
        "동영상: %s" % job.ep.video,
        "작품: %s" % job.anime.title,
        "에피소드: %s" % job.ep.code,
        "번역 모드: %s (설정값: %s)" % (used_mode, job.anime.translate_mode),
    ]
    lines += job.report
    lines.append("결과 파일:")
    lines += ["  - %s" % name for name in job.out_files]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def process_episode(settings, anime, ep):
    """반환: "done" / "skip" / "fail" """
    ep_dir = anime.output_path / safe_name(anime.title) / ep.code
    finish = ep_dir / FINISH_FILE
    if finish.is_file():
        log.info("[=] 이미 처리됨, 건너뜁니다: %s %s", anime.title, ep.code)
        return "skip"

    log.info("================================================================")
    log.info("[+] %s %s (%s 모드)", anime.title, ep.code, anime.translate_mode)
    log.info("> 파일: %s", ep.video)
    if ep_dir.exists():
        log.info("[=] 이전 실행에서 끝나지 않은 폴더를 지우고 다시 처리합니다: %s", ep_dir)
        shutil.rmtree(ep_dir)

    started = datetime.now()
    try:
        with tempfile.TemporaryDirectory(prefix="subtrans-") as work:
            job = Job(settings=settings, anime=anime, ep=ep, work=Path(work))
            used_mode = run_mode(job)
            # 모든 단계가 끝난 뒤에만 결과를 옮기고 finish.txt 를 만듭니다.
            ep_dir.mkdir(parents=True, exist_ok=True)
            for name, path in job.out_files.items():
                shutil.copyfile(path, ep_dir / name)
            _write_finish(finish, job, used_mode, started)
    except Exception as error:
        log.error("[-] 실패: %s %s — %s", anime.title, ep.code, error)
        if not isinstance(error, (EpisodeError, jimaku.JimakuError, ai_client.AiClientError, tools.ToolError)):
            log.exception("예상하지 못한 오류")
        return "fail"
    log.info("[+] 완료: %s → %s", ep.code, ep_dir)
    return "done"


def run(settings):
    """전체 프로세스. 반환: {"done": n, "skip": n, "fail": n, "unmatched": n}"""
    # 2. 작품 목록 갱신
    update_anime_list(settings)

    # 3. 작품 설정 로드
    animes = load_anime_list(settings)
    log.info("> anime.yml 작품 %d개", len(animes))
    by_folder = {}
    for anime in animes:
        by_folder.setdefault(anime.target_path.resolve(), {})[title_key(anime.title)] = anime

    if not ai_client.is_enabled(settings.settings_path):
        log.warning("[=] settings.yml 의 ai-translate.active 가 none 입니다. 번역 없이 원문 자막만 저장합니다.")

    stats = {"done": 0, "skip": 0, "fail": 0, "unmatched": 0}
    seen = set()
    for folder, titles in by_folder.items():
        for video in scan_videos(folder):
            # 4. 에피소드 파싱
            ep = parse_episode(video)
            # 5. 작품 매칭
            anime = titles.get(title_key(ep.title))
            if anime is None:
                stats["unmatched"] += 1
                continue
            if ep.episode is None:
                log.warning("[=] 회차 번호를 찾지 못해 건너뜁니다: %s", video.name)
                stats["fail"] += 1
                continue
            key = (str(anime.output_path.resolve()), title_key(anime.title), ep.code)
            if key in seen:
                log.info("[=] 같은 에피소드의 다른 파일이라 건너뜁니다: %s", video.name)
                continue
            seen.add(key)
            # 6~8. 중복 확인, 자막 생성, 결과 저장
            stats[process_episode(settings, anime, ep)] += 1
    return stats
