import re


def episode_code(season, episode):
    """에피소드 폴더 이름. 시즌·회차 두 자리: S01E03 (12.5화 같은 특별편은 S01E12.5)"""
    season = 1 if season is None else int(season)
    if isinstance(episode, float) and not episode.is_integer():
        whole, frac = str(episode).split(".", 1)
        return "S%02dE%02d.%s" % (season, int(whole), frac)
    return "S%02dE%02d" % (season, int(episode))


def safe_name(text, fallback="untitled"):
    """폴더/파일 이름으로 쓸 수 없는 문자를 바꿉니다."""
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", str(text or "")).strip(" .")
    return name or fallback
