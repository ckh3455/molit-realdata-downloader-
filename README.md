# 국토부 실거래가 데이터 다운로드

국토부 실거래가 데이터를 월별로 다운로드하고 Google Shared Drive에 자동 업로드하는 스크립트입니다.

## 📋 기능

- ✅ 월별 데이터 자동 다운로드 (2006년 1월 ~ 현재)
- ✅ 재시도 로직 (15초 대기, 최대 3회)
- ✅ 진행 상황 저장 및 재개
- ✅ 일일 100건 제한 대응 (다음날 자동 재개)
- ✅ 업데이트 모드 (최근 1년만 갱신)
- ✅ Google Shared Drive 자동 업로드
- ✅ 폴더 자동 생성/찾기

## 🚀 설치

### 1. 패키지 설치

```bash
pip install -r requirements.txt
```

### 2. Google 서비스 계정 설정

#### 로컬 실행 시
1. 서비스 계정 JSON 파일을 다운로드
2. 파일 경로를 `drive_uploader.py`의 `SERVICE_ACCOUNT_FILE`에 설정

#### GitHub Actions 실행 시
1. 서비스 계정 JSON 내용을 GitHub Secrets에 `GOOGLE_SERVICE_ACCOUNT_JSON`로 추가
2. Shared Drive ID를 `GOOGLE_SHARED_DRIVE_ID`로 추가

### 3. Shared Drive 설정

1. Google Drive에서 "부동산자료" Shared Drive 생성
2. "부동산 실거래자료" 폴더 생성
3. 서비스 계정을 Shared Drive 멤버로 추가 (권한: 콘텐츠 관리자)

## 📁 폴더 구조

```
부동산자료 (Shared Drive)
└── 부동산 실거래자료
    ├── 아파트
    │   ├── 아파트 200601.xlsx
    │   ├── 아파트 200602.xlsx
    │   └── ...
    ├── 연립다세대
    ├── 단독다가구
    ├── 오피스텔
    ├── 토지
    ├── 상업업무용
    ├── 분양권
    └── 입주권
```

## 🎯 사용법

### 기본 실행 (전체 다운로드)

```bash
python download_realdata.py
```

### 업데이트 모드 (최근 1년만)

```bash
python download_realdata.py --update-mode
```

### 테스트 모드

```bash
python download_realdata.py --test-mode --max-months 2
```

### Google Drive 업로드 건너뛰기

```bash
python download_realdata.py --skip-drive-upload
```

## 🧪 테스트

### 폴더 생성 테스트

```bash
python test_create_folders.py
```

이 스크립트는 "부동산 실거래자료" 폴더에 8개 섹션별 폴더를 생성합니다.

## 📊 진행 상황

진행 상황은 `download_progress.json` 파일에 저장됩니다:

```json
{
  "아파트": {
    "last_month": "202401",
    "last_update": "2024-01-15T10:30:00"
  },
  "연립다세대": {
    "last_month": "202312",
    "last_update": "2024-01-15T10:35:00"
  }
}
```

## ⚙️ 환경 변수

### 로컬 실행
- `GOOGLE_SERVICE_ACCOUNT_FILE`: 서비스 계정 파일 경로
- `GOOGLE_SHARED_DRIVE_ID`: Shared Drive ID (기본값: `0APa-MWwUseXzUk9PVA`)

### GitHub Actions
- `GOOGLE_SERVICE_ACCOUNT_JSON`: 서비스 계정 JSON 문자열
- `GOOGLE_SHARED_DRIVE_ID`: Shared Drive ID

## 🔧 문제 해결

### 파일을 찾을 수 없음
- 서비스 계정이 Shared Drive 멤버로 추가되었는지 확인
- 권한이 "편집자" 이상인지 확인

### 권한 오류
- `supportsAllDrives: true` 옵션이 포함되어 있는지 확인
- 서비스 계정 권한을 "콘텐츠 관리자"로 설정

### 다운로드 실패
- 네트워크 연결 확인
- 국토부 사이트 접근 가능 여부 확인
- 100건 일일 제한 도달 시 다음날 재실행

## 📝 파일명 형식

다운로드된 파일은 다음 형식으로 저장됩니다:
- `{섹션명} {YYYYMM}.xlsx`
- 예: `아파트 200601.xlsx`, `연립다세대 202401.xlsx`

## 🔗 참고 자료

- [Google Drive API 문서](https://developers.google.com/drive/api)
- [공유드라이브 연결 과정](./공유드라이브_연결_과정.md)

