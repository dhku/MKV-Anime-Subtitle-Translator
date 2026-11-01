# MKV-Anime-Subtitle-Translator



> 폴더에 애니를 넣어두기만 하세요. 한국어 자막은 알아서 생깁니다. 

**자막 수집 → 싱크 보정 → AI 번역 → 정리까지, 애니메이션 한국어 자막 자동화 파이프라인**

Jimaku 일본어 자막과 MKV 내장 자막을 찾아 타이밍을 맞추고, AI로 번역해 에피소드별로 정리합니다. 

NAS에 올려두고 스케줄러만 걸어두면, 새 에피소드가 들어올 때마다 자막이 준비됩니다.



## 기능

* Jimaku에서 시즌·회차에 맞는 일본어 자막을 자동 선택 

* 내장 자막 또는 Whisper 음성 인식으로 alass 싱크 보정 

- OpenAI / Gemini 번역 + 작품별 용어집으로 일관된 고유명사 
- `output/작품명/S01E03/` 구조로 깔끔하게 정리, 처리한 회차는 건너뜀

## 동작 순서

1. `settings.yml` 을 읽습니다. 없으면 기본값으로 만듭니다.
2. 전역 `target_path` 의 동영상 파일명에서 작품 제목을 뽑아, `anime.yml` 에 없는 작품을 추가합니다. (기존 항목은 수정하지 않음)
3. 파일명의 제목이 `anime.yml` 의 `title` 과 일치하는 동영상만 처리합니다.
4. `finish.txt` 가 있는 에피소드는 건너뛰고, `finish.txt` 없이 남은 폴더는 지우고 다시 처리합니다.
5. 작품의 `translate_mode` 로 자막을 만들고, 성공하면 결과 파일과 `finish.txt` 를 저장합니다.

## 번역 모드

| 모드 | 동작 |
| --- | --- |
| `jimaku` | Jimaku 에서 시즌·회차가 일치하는 일본어 자막 1개를 `jimaku_priority` 순서로 골라 번역 (일본어 → 한국어). 같은 순위면 ASS > SRT > SSA > 기타 확장자, 그다음 최근 파일 |
| `embed` | 동영상의 첫 번째 내장 자막을 추출해 번역 (영어 → 한국어) |
| `auto` | `jimaku` 를 먼저 하고, 실패하면 `embed` |

`sync_jimaku_to_embed: true` 이면 Jimaku 자막을 내장 자막 기준으로 alass 싱크 보정합니다.
내장 자막이 없으면 Whisper(`tiny`, VAD 필터)로 음성에서 레퍼런스 자막을 만듭니다. (`use_whisper: false` 면 싱크 보정을 건너뜀)
싱크 보정이 실패하면 Jimaku 원래 타이밍으로 저장하고 `finish.txt` 에 사유를 남깁니다.

## 설정 파일

### settings.yml (전역)

| 항목 | 설명 |
| --- | --- |
| `global.target_path` | 동영상 폴더 (하위 폴더 포함) |
| `global.output_path` | 자막 저장 폴더 |
| `global.translate_mode` | `jimaku` / `embed` / `auto` |
| `global.sync_jimaku_to_embed` | Jimaku 자막 싱크 보정 여부 |
| `global.use_whisper` | 내장 자막이 없을 때 Whisper 사용 여부 |
| `jimaku_priority` | Jimaku 자막 선택 우선순위 (파일명에 포함된 단어 기준, 같은 순위면 ASS > SRT > SSA) |
| `ai-translate.active` | `openai` / `gemini` / `none`. `none` 이면 번역하지 않고 원문 자막만 저장 |
| `ai-translate.providers.*` | 제공자별 `api_key`, `model`, `base_url` |

> `ai-translate.active: none` 으로 처리한 에피소드도 `finish.txt` 가 생기므로, 나중에 번역을 켜고 다시 만들려면 해당 에피소드 폴더를 지우세요.

### anime.yml (작품별)

프로그램 실행시, 자동으로 작품마다 `title`,  `translate_mode` ,`jimaku_keyword` 가 생성됩니다.

해당 항목은 수정하면 `settings.yml` 전역값 대신 사용합니다.
(`translate_mode`, `jimaku_keyword`, `sync_jimaku_to_embed`, `use_whisper`, `target_path`, `output_path`)

```yaml
mkv_anime_list: '[
  {
    "title" : "Reincarnated as a Sword",
    "translate_mode" : "jimaku",
    "jimaku_keyword" : "Reincarnated as a Sword"
  }
  ]'
```

### prompt.yml

AI 번역 프롬프트입니다. `translate` / `glossary` 는 일본어 원문, `translate_en` / `glossary_en` 은 영어 원문용입니다. 파일이 없으면 기본값으로 만듭니다.

## 결과 파일

| 파일 | 내용 |
| --- | --- |
| `{동영상 이름}.ko.ass` / `.ko.srt` | 한국어 번역 자막 (AI 번역을 켠 경우) |
| `{동영상 이름}.ja.*` | Jimaku 원문 자막 (싱크 보정 결과 반영) |
| `{동영상 이름}.en.*` | 내장 영어 자막 (embed 모드) |
| `finish.txt` | 처리 기록 (사용한 모드, 자막 출처, 싱크 결과, 번역 모델) |
| `output/{작품명}/용어집-ja.yml`, `용어집-en.yml` | 작품별 AI 번역 용어집 (직접 고칠 수 있음) |

실행 로그는 `log/` 에 실행 1회당 1개씩 남고, 최대 100개까지 보관합니다.

## 로컬 실행 (Windows)

```
pip install -r requirements.txt
python main.py
```

- ffmpeg / ffprobe / alass 는 환경 변수(`FFMPEG_PATH`, `FFPROBE_PATH`, `ALASS_PATH`) → PATH → `lib/windows/` 순서로 찾습니다.
- Whisper 를 쓰려면 `faster-whisper` 가 설치되어 있어야 합니다. (`requirements.txt` 에 포함)

## Docker (NAS)

1. 해당 프로젝트를 시놀로지 docker 공유 폴더 안에 넣습니다.  (리눅스의 경우 아무 경로에 넣으세요)

   ```
   /volume1/docker/MKV-Anime-Subtitle-Translator
   ```

2. 애니메이션 동영상 폴더에 읽기와 폴더 진입 권한을 부여합니다. (SSH 이용 or 시놀로지 웹에서 폴더 속성 - 권한)

   ```
   sudo chmod u+rwx /volume1/video/anime # 애니메이션 동영상 폴더
   ```

3. 도커 이미지를 빌드합니다.

   ```
   cd /volume1/docker/MKV-Anime-Subtitle-Translator
   docker-compose build
   ```

4. docker-compose.yml을 수정합니다.

   ```
       volumes:
         - <프로젝트 경로>:/app			# settings.yml, anime.yml, prompt.yml
         - <애니메이션 동영상 경로>:/anime		# 애니메이션 동영상 폴더
         - <프로젝트 경로>/output:/app/output
         - <프로젝트 경로>/log:/app/log
         
    ~~<예시>~~     
       volumes:
         - /volume1/docker/MKV-Anime-Subtitle-Translator:/app	# settings.yml, anime.yml, prompt.yml
         - /volume1/video/anime:/anime				# 애니메이션 동영상 폴더
         - /volume1/docker/MKV-Anime-Subtitle-Translator/output:/app/output
         - /volume1/docker/MKV-Anime-Subtitle-Translator/log:/app/log
   ```

5. settings.yml의 target_path를 `/anime` 로 수정합니다.

   ```
   global:
     target_path: /anime
   ```

6. 이미지를 실행합니다.

   ```
   cd /volume1/docker/MKV-Anime-Subtitle-Translator
   docker-compose run --rm subtitle-translator
   ```

- 1회 실행 후 종료합니다. 주기 실행은 NAS 작업 스케줄러에서 위 명령을 등록하세요.
- 이미지에 ffmpeg, alass, Whisper `tiny` 모델이 포함되어 있습니다. (CPU 전용, 기본 `linux/amd64`, 다른 아키텍처는 alass 를 소스 빌드)

| 컨테이너 경로 | 용도 |
| --- | --- |
| `/app` | `settings.yml`, `anime.yml`, `prompt.yml` (없으면 자동 생성) |
| `/anime` | 동영상 폴더 |
| `/app/output` | 생성된 자막 |
| `/app/log` | 실행 로그 |

| 환경 변수 | 설명 |
| --- | --- |
| `PUID` / `PGID` | 생성 파일 소유자 (NAS 사용자·그룹 id) |
| `TZ` | 로그 시간대 (기본 `Asia/Seoul`) |

볼륨 경로 예시는 `docker-compose.yml` 을 참고하세요.
