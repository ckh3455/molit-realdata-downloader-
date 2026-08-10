# 국토부 실거래가 월별 자동 수집기

국토교통부 실거래가 공개시스템에서 전국 실거래 자료를 월별로 내려받아 전처리한 뒤, XLSX와 CSV 파일로 저장하고 Google Drive에 업로드하는 자동화 프로젝트입니다.

> 현재 버전은 기존 Google 스프레드시트에 행을 추가하지 않습니다. 월별 XLSX·CSV 파일을 Google Drive의 기존 유형별 폴더에 업로드하거나 같은 이름의 파일을 덮어씁니다.

## 현재 동작

- 매일 오전 6시 30분(KST) GitHub Actions 자동 실행
- Actions 화면에서 수동 실행 가능
- 실행일 기준 최근 5개월(4개월 전부터 당월까지) 재수집
- 전국 자료를 7개 부동산 유형별·월별로 다운로드
- 원본 자료를 전처리해 XLSX와 UTF-8 BOM CSV로 저장
- XLSX 내부 워크시트 이름은 `data`
- Google Drive의 기존 유형별 폴더에 업로드
- 동일한 파일명이 있으면 새 파일을 만들지 않고 기존 파일 덮어쓰기
- 실패 시 다음 Attempt로 넘어가 최대 10회 실행
- 실행별 `output/**`, `debug/**`를 GitHub Actions 아티팩트로 보관
- 당월 자료가 아직 생성되지 않았으면 오류 대신 해당 월을 건너뛸 수 있음

## 수집 대상

| 유형 | 국토부 화면 탭 | Google Drive 하위 폴더 |
|---|---|---|
| 아파트 | 아파트 | 아파트 |
| 연립다세대 | 연립/다세대 | 연립다세대 |
| 단독다가구 | 단독/다가구 | 단독다가구 |
| 오피스텔 | 오피스텔 | 오피스텔 |
| 상업업무용 | 상업/업무용 | 상업업무용 |
| 토지 | 토지 | 토지 |
| 공장창고등 | 공장/창고 등 | 공장창고등 |

한 번의 정상 실행에서 최대 `7개 유형 × 최근 5개월 × 2개 형식`으로 70개 파일을 생성할 수 있습니다. 당월 자료가 없으면 실제 생성 파일 수는 줄어듭니다.

## 파일명과 출력

파일명 형식:

```text
{유형} {YYYYMM}.xlsx
{유형} {YYYYMM}.csv
```

예:

```text
아파트 202608.xlsx
아파트 202608.csv
공장창고등 202607.xlsx
공장창고등 202607.csv
```

로컬 출력 경로:

```text
output/
debug/
_rt_downloads/
```

- `output/`: 전처리가 완료된 XLSX·CSV
- `debug/`: 실패 시 저장되는 HTML·화면 캡처
- `_rt_downloads/`: 국토부에서 받은 임시 원본 파일

## 자동 실행 흐름

```text
예약 또는 수동 실행
→ 국토부 접속 사전 점검(DNS·TCP·TLS·HTTP)
→ Chrome/Selenium 화면 점검
→ 최근 5개월 × 7개 유형 다운로드
→ 데이터 전처리
→ XLSX·CSV 생성
→ Google Drive 업로드 또는 덮어쓰기
→ GitHub Actions 아티팩트 저장
```

각 Attempt는 최대 180분 동안 실행됩니다. 선행 Attempt가 성공하면 뒤의 Attempt는 실행되지 않으며, 모두 실패한 경우에만 최종 워크플로가 실패로 끝납니다.

## Google Drive 저장 구조

`GDRIVE_FOLDER_ID`가 가리키는 루트 아래에서 `GDRIVE_BASE_PATH`를 찾습니다. 경로가 지정되지 않으면 `부동산 실거래자료` 폴더를 자동 탐색하고, 없으면 루트를 베이스로 사용합니다.

```text
Google Drive 루트
└── 부동산 실거래자료 또는 GDRIVE_BASE_PATH
    ├── 아파트
    ├── 연립다세대
    ├── 단독다가구
    ├── 오피스텔
    ├── 상업업무용
    ├── 토지
    └── 공장창고등
```

유형별 폴더는 코드가 새로 만들지 않습니다. 대상 폴더가 미리 존재하지 않으면 해당 유형의 Drive 업로드를 건너뜁니다.

## GitHub Secrets

Repository의 `Settings → Secrets and variables → Actions`에 다음 Secret이 필요합니다.

| Secret | 용도 |
|---|---|
| `GCP_SERVICE_ACCOUNT_KEY` | Google 서비스 계정 JSON 원문 또는 Base64 문자열 |
| `GDRIVE_FOLDER_ID` | 업로드 루트 Google Drive 폴더 ID |
| `GDRIVE_BASE_PATH` | 루트 아래의 선택적 베이스 경로 |

서비스 계정에는 대상 Google Drive 폴더의 파일 조회·생성·수정 권한이 필요합니다.

## GitHub Actions

워크플로 파일:

```text
.github/workflows/download-realdata.yml
```

자동 실행 시각:

```yaml
cron: "30 21 * * *"
```

GitHub Actions의 cron은 UTC이므로 한국시간으로 매일 오전 6시 30분입니다.

수동 실행:

1. 저장소의 `Actions` 탭으로 이동
2. `Download MOLIT realdata auto rerun` 선택
3. `Run workflow` 실행

## 로컬 실행

Python 3.11 환경을 권장합니다.

```bash
python download_realdata.py
```

필요한 패키지가 없으면 스크립트 시작 시 자동으로 설치합니다.

로컬에서 Google Drive 업로드까지 실행하려면 다음 환경변수를 설정해야 합니다.

```text
GCP_SERVICE_ACCOUNT_KEY
GDRIVE_FOLDER_ID
GDRIVE_BASE_PATH
```

`GDRIVE_FOLDER_ID`가 없거나 서비스 계정 로딩에 실패하면 다운로드와 로컬 파일 생성은 진행하고 Google Drive 업로드만 건너뜁니다.

## 주요 환경변수

| 변수 | 기본값 | 설명 |
|---|---:|---|
| `OUT_DIR` | `output` | 전처리 결과 저장 폴더 |
| `DEBUG_DIR` | `debug` | 실패 진단자료 저장 폴더 |
| `HEADLESS` | `1` | `0`이면 로컬 Chrome 창 표시 |
| `DOWNLOAD_TIMEOUT` | `30` | 파일 다운로드 대기시간(초), Actions에서는 60 |
| `CLICK_RETRY_MAX` | `15` | 다운로드 클릭 최대 재시도 |
| `NAV_RETRY_MAX` | `10` | 국토부 페이지 진입 최대 재시도 |
| `PAGELOAD_TIMEOUT` | `120` | 페이지 로딩 제한시간(초) |
| `MONTH_SLEEP` | `2` | 월별 요청 사이 기본 대기시간 |
| `CATEGORY_SLEEP` | `5` | 유형 변경 시 기본 대기시간 |
| `CURRENT_MONTH_DELAY_DAYS` | `0` | 당월 조회 종료일을 오늘보다 늦추는 일수 |
| `ALLOW_EMPTY_CURRENT_MONTH` | `1` | 당월 파일이 없을 때 오류 대신 건너뛰기 |
| `MOLIT_PROXY_URL` | 빈 값 | 국토부 접속 제한 시 사용할 선택적 프록시 URL |

## Google 스프레드시트와의 관계

현재 코드는 Google Drive API만 사용합니다.

```python
build("drive", "v3", ...)
```

Google Sheets API, `gspread`, `spreadsheets.values.append` 같은 호출은 없습니다. 따라서:

- 기존 Google 스프레드시트의 거래내역 탭에 행을 추가하지 않음
- XLSX·CSV를 Google 스프레드시트 형식으로 변환하지 않음
- Drive에 일반 XLSX·CSV 파일로 저장

시트 누적 기록이 필요하면 별도의 Google Sheets API 동기화 단계를 추가해야 합니다.

## 문제 해결

### `Process completed with exit code 1`

해당 Attempt가 실패했다는 뜻입니다. 다음 Attempt가 성공하면 최종 워크플로는 성공으로 표시될 수 있습니다.

### `No files were found with the provided path: debug/** output/**`

접속 사전 점검 단계에서 실패해 다운로더가 실행되지 않았거나, 결과·디버그 파일이 만들어지기 전에 종료된 경우입니다. 뒤 Attempt의 로그와 아티팩트를 확인합니다.

### Google Drive에 파일이 없음

- `GCP_SERVICE_ACCOUNT_KEY`가 올바른지 확인
- 서비스 계정에 대상 폴더 권한이 있는지 확인
- `GDRIVE_FOLDER_ID`와 `GDRIVE_BASE_PATH` 확인
- 7개 유형별 하위 폴더가 실제로 존재하는지 확인
- 로그의 `drive: skip`, `base path not found`, `category folder missing` 메시지 확인

### 당월 파일이 없음

국토부에서 당월 파일을 아직 생성하지 않은 경우 정상적으로 건너뛸 수 있습니다. `ALLOW_EMPTY_CURRENT_MONTH=1`이 기본값입니다.

## 주요 파일

| 파일 | 역할 |
|---|---|
| `download_realdata.py` | 수집·전처리·XLSX/CSV 생성·Drive 업로드 |
| `.github/workflows/download-realdata.yml` | 예약 실행·사전 점검·최대 10회 재시도·아티팩트 저장 |

## 참고

- [국토교통부 실거래가 공개시스템](https://rt.molit.go.kr/)
- [Google Drive API](https://developers.google.com/drive/api/guides/about-sdk)
