#!/bin/sh
# PUID/PGID 사용자로 프로그램을 실행해 생성 파일의 소유자를 NAS 사용자와 맞춥니다.
set -e

PUID="${PUID:-1000}"
PGID="${PGID:-1000}"
umask "${UMASK:-022}"

mkdir -p /app/target /app/output /app/log

if [ "$(id -u)" = "0" ]; then
    if ! getent group "$PGID" >/dev/null 2>&1; then
        groupadd -o -g "$PGID" app
    fi
    if ! getent passwd "$PUID" >/dev/null 2>&1; then
        useradd -o -u "$PUID" -g "$PGID" -M -d /app -s /usr/sbin/nologin app
    fi
    # 프로그램이 쓰는 곳: 설정 폴더(anime.yml, prompt.yml 생성/추가), 출력, 로그
    chown "$PUID:$PGID" /app /app/output /app/log
    for f in /app/settings.yml /app/anime.yml /app/prompt.yml; do
        if [ -e "$f" ]; then chown "$PUID:$PGID" "$f"; fi
    done
    exec gosu "$PUID:$PGID" "$@"
fi

exec "$@"
