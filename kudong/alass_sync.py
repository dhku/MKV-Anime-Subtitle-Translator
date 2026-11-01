"""alass를 Python에서 쓰기 위한 래퍼.

    from alass_sync import sync

    result = sync("reference.ass", "target.ass", "output.ass")
    for seg in result.segments:
        print(seg)

동작 방식
1. alass-cli(또는 alass)를 subprocess로 실행해 output에 바로 저장한다.
   (alass는 시간만 바꾸고 헤더, 스타일, 태그, BOM, 줄바꿈은 원본 그대로 둔다.)
2. 원본과 결과의 시간을 비교해 구간(segment)별 오프셋과
   0초 아래로 밀려 시작이 0초로 고정된 줄 수를 알려준다.

맞출 자막(target)과 output은 같은 형식이어야 한다 (.ass → .ass, .srt → .srt).
alass는 형식 변환을 하지 않는다. 기준 자막(reference)은 형식이 달라도 된다.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path


# ---------------------------------------------------------------- 시간 읽기
_SRT_TIME = re.compile(r"(\d+):(\d+):(\d+)[,.](\d+)\s*-->\s*(\d+):(\d+):(\d+)[,.](\d+)")


def _ass_t(t: str) -> float:
    h, m, s = t.strip().split(":")
    return int(h) * 3600 + int(m) * 60 + float(s)


def _times(path: str | os.PathLike) -> list[tuple[float, float]]:
    """자막 파일의 (시작, 끝) 목록. ASS/SSA는 Dialogue 줄, 그 외는 SRT로 읽는다."""
    text = Path(path).read_bytes().decode("utf-8-sig", errors="replace")
    if Path(path).suffix.lower() in (".ass", ".ssa"):
        out = []
        for ln in text.splitlines():
            if ln.startswith("Dialogue:"):
                p = ln.split(",", 9)
                out.append((_ass_t(p[1]), _ass_t(p[2])))
        return out
    out = []
    for g in _SRT_TIME.findall(text):
        a = list(map(int, g))
        ms = lambda h, m, s, f, n: h * 3600 + m * 60 + s + f / (10 ** n)
        out.append((ms(*a[0:4], len(g[3])), ms(*a[4:8], len(g[7]))))
    return out


def _fmt(x: float) -> str:
    cs = int(round(max(0.0, x) * 100))
    h, cs = divmod(cs, 360000)
    m, cs = divmod(cs, 6000)
    s, cs = divmod(cs, 100)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


# ---------------------------------------------------------------- 결과 타입
@dataclass
class Segment:
    start: float        # 원본 기준, 이 구간 첫 줄의 시작 시간(초)
    end: float          # 원본 기준, 이 구간 마지막 줄의 시작 시간(초)
    lines: int
    offset: float       # 초 단위, 음수면 당겨짐

    def __str__(self) -> str:
        return f"{_fmt(self.start)} ~ {_fmt(self.end)}  {self.lines:4d}줄  {self.offset:+.2f}s"


@dataclass
class SyncResult:
    output: Path
    segments: list[Segment] = field(default_factory=list)
    clamped: int = 0            # 0초 아래로 밀려 시작이 0초로 고정된 줄 수 (표시 시간은 유지되지만 위치가 틀림)
    log: str = ""


# ---------------------------------------------------------------- alass 실행
def find_alass(explicit: str | None = None) -> str:
    """alass 실행 파일 경로를 찾는다. 못 찾으면 FileNotFoundError."""
    candidates = [explicit, os.environ.get("ALASS_PATH"),
                  "alass-cli", "alass", "alass-cli.exe", "alass.exe"]
    for c in candidates:
        if not c:
            continue
        if Path(c).is_file():
            return str(Path(c))
        found = shutil.which(c)
        if found:
            return found
    raise FileNotFoundError(
        "alass 실행 파일을 찾을 수 없습니다. PATH에 추가하거나 "
        "alass_path 인자 또는 ALASS_PATH 환경 변수로 경로를 지정하세요.")


def _segments(orig, new, tol: float = 0.015) -> list[Segment]:
    segs: list[Segment] = []
    for (os_, _), (ns, ne) in zip(orig, new):
        if ns == 0.0 and os_ > 0:     # 0초로 밀린 줄은 오프셋이 틀어져 보이므로 구간 계산에서 제외
            continue
        d = round(ns - os_, 3)
        if segs and abs(segs[-1].offset - d) < tol:
            segs[-1].end = os_
            segs[-1].lines += 1
        else:
            segs.append(Segment(os_, os_, 1, d))
    return segs


def sync(reference: str | os.PathLike,
         target: str | os.PathLike,
         output: str | os.PathLike,
         *,
         split: bool = True,
         split_penalty: float | None = None,
         fix_framerate: bool = False,
         exact: bool = True,
         alass_path: str | None = None,
         timeout: float = 300) -> SyncResult:
    """target 자막을 reference(자막 또는 영상)에 맞춰 output에 저장한다.

    split          False면 전체에 오프셋 하나만 적용 (alass -l)
    split_penalty  분할 패널티 (기본 7, 클수록 덜 쪼갬)
    fix_framerate  True면 프레임레이트 추정/보정 허용 (기본은 끔, alass -g)
    exact          True면 속도 최적화를 끈다 (alass -O 0). 기본 켜짐.
                   alass 기본값(-O 1)은 근사 계산이라 시간 정밀도가 조금만 달라도
                   (예: ASS 센티초 vs SRT 밀리초) 결과가 0.1초 정도 달라질 수 있다.
    """
    exe = find_alass(alass_path)
    target, output = Path(target), Path(output)
    if target.suffix.lower() != output.suffix.lower():
        raise ValueError(f"target({target.suffix})과 output({output.suffix})의 형식이 같아야 합니다. "
                         "alass는 형식 변환을 하지 않습니다.")

    orig = _times(target)
    if not orig:
        raise ValueError(f"{target}에서 자막 줄을 찾지 못했습니다.")

    cmd = [exe]
    if not fix_framerate:
        cmd.append("-g")
    if not split:
        cmd.append("-l")
    if exact:
        cmd += ["-O", "0"]
    if split_penalty is not None:
        cmd += ["-p", str(split_penalty)]
    cmd += [str(reference), str(target), str(output)]

    proc = subprocess.run(cmd, capture_output=True, timeout=timeout)
    log = (proc.stdout + proc.stderr).decode("utf-8", errors="replace")
    if proc.returncode != 0 or not output.exists():
        err = next((ln for ln in log.splitlines() if ln.startswith("error")), log[-2000:])
        raise RuntimeError(f"alass 실행 실패 (code {proc.returncode}): {err}")

    new = _times(output)
    if len(new) != len(orig):
        raise RuntimeError(f"줄 수가 다릅니다: 원본 {len(orig)}, 결과 {len(new)}")

    clamped = sum(1 for (os_, _), (ns, _) in zip(orig, new) if ns == 0.0 and os_ > 0)
    return SyncResult(output=output, segments=_segments(orig, new),
                      clamped=clamped, log=log)


# ---------------------------------------------------------------- CLI
if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description="alass로 자막 싱크 맞추기")
    ap.add_argument("reference", help="기준 자막 또는 영상")
    ap.add_argument("target", help="맞출 자막 (.ass / .srt)")
    ap.add_argument("output", help="저장할 파일 (target과 같은 형식)")
    ap.add_argument("--no-split", action="store_true", help="오프셋 하나만 적용")
    ap.add_argument("-p", "--split-penalty", type=float)
    ap.add_argument("--fix-framerate", action="store_true")
    ap.add_argument("--fast", action="store_true",
                    help="alass 속도 최적화 사용 (-O 1, 근사 계산)")
    ap.add_argument("--alass", help="alass 실행 파일 경로")
    a = ap.parse_args()

    r = sync(a.reference, a.target, a.output, split=not a.no_split,
             split_penalty=a.split_penalty, fix_framerate=a.fix_framerate,
             exact=not a.fast, alass_path=a.alass)
    for s in r.segments:
        print(s)
    if r.clamped:
        print(f"주의: {r.clamped}줄이 0초 아래로 밀려 시작이 0초로 고정됐습니다.")
    print("저장:", r.output)
