"""외부 도구(ffmpeg, ffprobe, alass, Whisper) 호출을 모아 둔 모듈입니다.

실행 파일은 다음 순서로 찾습니다.
1. 환경 변수 (FFMPEG_PATH, FFPROBE_PATH, ALASS_PATH)
2. PATH
3. 저장소에 포함된 lib/ 폴더 (Windows: lib/windows, Linux: lib/linux)
"""
import json
import logging
import os
import platform
import shutil
import subprocess
from pathlib import Path

from . import alass_sync

log = logging.getLogger("translator.tools")

_ROOT = Path(__file__).resolve().parent.parent
_IS_WINDOWS = platform.system() == "Windows"

# ffmpeg 이 텍스트로 꺼낼 수 있는 자막 코덱입니다. (PGS, VobSub 같은 그림 자막은 제외)
ASS_CODECS = {"ass", "ssa"}
TEXT_CODECS = {"subrip", "srt", "mov_text", "webvtt", "text", "sami", "microdvd", "realtext", "subviewer", "jacosub"}


class ToolError(Exception):
    pass


def _find(env_name, names, bundled):
    explicit = os.environ.get(env_name)
    if explicit and Path(explicit).is_file():
        return explicit
    for name in names:
        found = shutil.which(name)
        if found:
            return found
    for path in bundled:
        if path.is_file():
            return str(path)
    raise ToolError("%s 실행 파일을 찾지 못했습니다. PATH에 추가하거나 %s 환경 변수로 경로를 지정하세요."
                    % (names[0], env_name))


def ffmpeg_path():
    return _find("FFMPEG_PATH", ["ffmpeg"], [_ROOT / "lib/windows/ffmpeg/bin/ffmpeg.exe"] if _IS_WINDOWS else [])


def ffprobe_path():
    return _find("FFPROBE_PATH", ["ffprobe"], [_ROOT / "lib/windows/ffmpeg/bin/ffprobe.exe"] if _IS_WINDOWS else [])


def alass_path():
    bundled = [_ROOT / "lib/windows/bin/alass-cli.exe"] if _IS_WINDOWS else [_ROOT / "lib/linux/alass-linux64"]
    return _find("ALASS_PATH", ["alass", "alass-cli"], bundled)


def _run(cmd, timeout):
    proc = subprocess.run(cmd, capture_output=True, timeout=timeout)
    out = (proc.stdout + proc.stderr).decode("utf-8", errors="replace")
    return proc.returncode, out


# ---------------------------------------------------------------- 내장 자막
def first_subtitle_stream(video):
    """동영상의 첫 번째 자막 트랙 정보 (없으면 None)"""
    cmd = [ffprobe_path(), "-v", "error", "-select_streams", "s:0",
           "-show_entries", "stream=index,codec_name:stream_tags=language,title", "-of", "json", str(video)]
    code, out = _run(cmd, 120)
    if code != 0:
        raise ToolError("ffprobe 실행 실패: " + out.strip()[-500:])
    try:
        streams = json.loads(out[out.find("{"):]).get("streams") or []
    except (ValueError, AttributeError):
        streams = []
    return streams[0] if streams else None


def extract_first_subtitle(video, out_dir, base_name):
    """첫 번째 자막 트랙을 텍스트 자막으로 추출합니다.

    반환: (저장 경로, 트랙 정보 dict) / 내장 자막이 없거나 그림 자막이면 (None, 이유)
    """
    stream = first_subtitle_stream(video)
    if stream is None:
        return None, "내장 자막 트랙이 없습니다."
    codec = str(stream.get("codec_name") or "").lower()
    if codec in ASS_CODECS:
        out = Path(out_dir) / (base_name + ".ass")
        codec_args = ["-c:s", "copy"] if codec == "ass" else ["-c:s", "ass"]
    elif codec in TEXT_CODECS:
        out = Path(out_dir) / (base_name + ".srt")
        codec_args = ["-c:s", "srt"]
    else:
        return None, "첫 번째 자막 트랙이 텍스트 자막이 아닙니다. (코덱: %s)" % (codec or "알 수 없음")

    cmd = [ffmpeg_path(), "-y", "-v", "error", "-i", str(video), "-map", "0:s:0", *codec_args, str(out)]
    code, msg = _run(cmd, 600)
    if code != 0 or not out.is_file() or out.stat().st_size == 0:
        raise ToolError("ffmpeg 자막 추출 실패: " + msg.strip()[-500:])
    tags = stream.get("tags") or {}
    info = {"codec": codec, "language": tags.get("language", ""), "title": tags.get("title", ""),
            "index": stream.get("index")}
    log.info("[+] 내장 자막 추출: 트랙 #%s (%s, %s) → %s",
             info["index"], codec, info["language"] or "언어 정보 없음", out.name)
    return out, info


def convert_subtitle(src, dst):
    """ffmpeg 으로 자막 형식을 바꿉니다. (VTT/SMI → SRT 등)"""
    cmd = [ffmpeg_path(), "-y", "-v", "error", "-i", str(src), str(dst)]
    code, msg = _run(cmd, 120)
    if code != 0 or not Path(dst).is_file():
        raise ToolError("자막 형식 변환 실패: " + msg.strip()[-500:])
    return Path(dst)


# ---------------------------------------------------------------- Whisper
WHISPER_MODEL = "tiny"


def _srt_time(seconds):
    ms = int(round(max(0.0, seconds) * 1000))
    h, ms = divmod(ms, 3600000)
    m, ms = divmod(ms, 60000)
    s, ms = divmod(ms, 1000)
    return "%02d:%02d:%02d,%03d" % (h, m, s, ms)


def whisper_reference(video, out_path, language="ja"):
    """Whisper(tiny, CPU, VAD 필터)로 음성에서 싱크 비교용 SRT 를 만듭니다."""
    try:
        from faster_whisper import WhisperModel
    except ImportError as error:
        raise ToolError("faster-whisper 패키지가 없습니다. pip install faster-whisper 로 설치하세요.") from error

    log.info("[+] Whisper(%s) 로 레퍼런스 자막을 생성합니다. (CPU, VAD 필터)", WHISPER_MODEL)
    model = WhisperModel(
        WHISPER_MODEL,
        device="cpu",
        compute_type="int8",
        download_root=os.environ.get("WHISPER_MODEL_DIR") or None,
    )
    segments, _info = model.transcribe(
        str(video),
        language=language or None,
        vad_filter=True,
        beam_size=1,
        condition_on_previous_text=False,
    )
    blocks = []
    for segment in segments:
        text = segment.text.strip()
        if not text:
            continue
        blocks.append("%d\n%s --> %s\n%s" % (len(blocks) + 1, _srt_time(segment.start), _srt_time(segment.end), text))
    if not blocks:
        raise ToolError("Whisper 가 음성에서 대사를 찾지 못했습니다.")
    Path(out_path).write_text("\n\n".join(blocks) + "\n", encoding="utf-8")
    log.info("[+] Whisper 레퍼런스 자막: %d줄", len(blocks))
    return Path(out_path)


# ---------------------------------------------------------------- alass
def alass_sync_file(reference, target, output):
    """alass 로 target 자막을 reference 자막에 맞춰 output 에 저장합니다."""
    result = alass_sync.sync(reference, target, output, alass_path=alass_path())
    for segment in result.segments:
        log.info("    %s", segment)
    if result.clamped:
        log.warning("[=] %d줄이 0초 아래로 밀려 시작이 0초로 고정됐습니다.", result.clamped)
    return result
