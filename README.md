# 국토부 부동산 실거래가 다운로더

국토교통부 실거래가 공개시스템에서 전국 부동산 데이터를 자동 다운로드하고 Google Drive에 저장합니다.

## 📋 기능

- **8개 부동산 종목** 자동 다운로드
  - 아파트, 연립다세대, 단독다가구, 오피스텔
  - 토지, 상업업무용, 분양권, 입주권

- **스마트 다운로드**
  - 2006년 1월부터 현재까지 전체 데이터 수집
  - 최근 1년 자동 업데이트 모드
  - 진행 상황 자동 저장 및 재개
  - 100회 제한 감지 및 다음날 자동 재개

- **Google Drive 자동 업로드**
  - 다운로드된 파일을 자동으로 Google Drive에 저장
  - 폴더별 자동 분류 (종목별 폴더 생성)
  - 기존 파일 자동 업데이트 (중복 방지)

## 🚀 GitHub Actions 설정

### 1. Google Cloud 서비스 계정 생성

1. [Google Cloud Console](https://console.cloud.google.com) 접속
2. 프로젝트 생성 또는 선택
3. **API 및 서비스** → **사용자 인증 정보**
4. **+ 사용자 인증 정보 만들기** → **서비스 계정**
5. 이름 입력 후 생성
6. 생성된 서비스 계정 클릭 → **키** 탭 → **키 추가** → **JSON** 다운로드

### 2. Google Drive API 활성화

1. **API 및 서비스** → **라이브러리**
2. "Google Drive API" 검색 → **사용 설정**

### 3. Google Drive 폴더 공유

1. Google Drive에서 저장할 폴더 열기
2. 우클릭 → **공유**
3. JSON 파일의 `client_email` 주소 추가 (편집자 권한)
4. 폴더 URL에서 **폴더 ID** 복사
   ```
   https://drive.google.com/drive/folders/[여기가_폴더_ID]
   ```

### 4. GitHub Secrets 설정

Repository → Settings → Secrets and variables → Actions:

- **`GCP_SERVICE_ACCOUNT_KEY`**: JSON 파일 전체 내용
- **`GDRIVE_FOLDER_ID`**: Google Drive 폴더 ID

### 5. 워크플로우 파일 추가

`.github/workflows/download.yml` 파일을 레포지토리에 추가

## 📂 파일 구조

```
├── .github/
│   └── workflows/
│       └── download.yml          # GitHub Actions 워크플로우
├── download_realdata.py          # 메인 다운로드 스크립트
├── upload_to_gdrive.py           # Google Drive 업로드 스크립트
├── requirements.txt              # Python 패키지 의존성
└── README.md                     # 프로젝트 설명서
```

## 🕐 실행 스케줄

- **자동 실행**: 매일 오전 10시 (한국시간)
- **수동 실행**: GitHub Actions 탭에서 "Run workflow" 클릭

## 💻 로컬 실행

```bash
# 의존성 설치
pip install -r requirements.txt

# 다운로드 실행
python download_realdata.py --update-mode

# Google Drive 업로드 (선택사항)
export SERVICE_ACCOUNT_JSON=service-account.json
export GDRIVE_FOLDER_ID=your_folder_id
python upload_to_gdrive.py
```

## 📊 다운로드 모드

### 전체 다운로드 (최초 실행)
```bash
python download_realdata.py
```
- 2006년 1월부터 현재까지 모든 데이터 다운로드

### 업데이트 모드
```bash
python download_realdata.py --update-mode
```
- 최근 1년치 데이터만 갱신

### 테스트 모드
```bash
python download_realdata.py --test-mode --max-months 2
```
- 최근 2개월치만 다운로드 (테스트용)

## 🔄 진행 상황 관리

- 진행 상황은 `download_progress.json`에 자동 저장
- 중단되어도 다음 실행 시 이어서 진행
- 100회 제한 도달 시 자동으로 다음날 재개

## 📝 출력 형식

다운로드된 파일은 다음과 같이 정리됩니다:

```
output/
├── 아파트/
│   ├── 아파트 200601.xlsx
│   ├── 아파트 200602.xlsx
│   └── ...
├── 연립다세대/
│   └── ...
└── ...
```

Google Drive에도 동일한 구조로 저장됩니다.

## ⚠️ 주의사항

- 국토부 사이트는 일일 다운로드 **100건 제한**이 있습니다
- 제한 도달 시 자동으로 저장되며 다음날 재실행하면 이어서 진행됩니다
- 최초 전체 다운로드는 여러 날에 걸쳐 완료됩니다

## 📄 라이선스

MIT License
