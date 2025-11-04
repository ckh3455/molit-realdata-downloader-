# 국토부 부동산 실거래가 다운로더

국토교통부 실거래가 공개시스템에서 전체 부동산 데이터를 자동 다운로드합니다.

## 설치

```bash
pip install -r requirements.txt
```

## 로컬 실행

### 다운로드 경로

로컬 PC에서는 `download_realdata.py`의 `DOWNLOAD_DIR` 변수를 수정하세요:

```python
DOWNLOAD_DIR = Path(r"D:\OneDrive\office work\부동산 실거래 데이터")
```

### 실행

```bash
# 전체 다운로드 (2006-01부터 현재까지)
python download_realdata.py

# 업데이트 모드 (최근 1년만)
python download_realdata.py --update-mode

# 테스트 모드 (최근 2개월만)
python download_realdata.py --test-mode
```

## GitHub Actions에서 실행 (OneDrive 업로드)

### 1. rclone 설정

로컬 PC에서 rclone을 설치하고 OneDrive를 설정합니다:

```bash
# rclone 설치 (Windows)
# https://rclone.org/downloads/ 에서 다운로드

# OneDrive 설정
rclone config
```

설정 과정:
- `n` (새 remote)
- 이름: `onedrive`
- 타입: `onedrive`
- Microsoft 계정으로 로그인

### 2. GitHub Secrets 설정

1. GitHub 저장소 → Settings → Secrets and variables → Actions
2. `RCLONE_CONFIG` secret 추가

로컬에서 rclone 설정 파일 확인:
```bash
# Windows
type %USERPROFILE%\.config\rclone\rclone.conf

# Linux/Mac
cat ~/.config/rclone/rclone.conf
```

전체 내용을 복사하여 GitHub Secrets의 `RCLONE_CONFIG`에 붙여넣기

### 3. 워크플로우 실행

- 자동 실행: 매일 오전 2시 (KST) 자동 실행
- 수동 실행: GitHub Actions 탭에서 `Download Real Estate Data` 워크플로우 선택 후 "Run workflow" 클릭

### 4. OneDrive 저장 경로

GitHub Actions에서 다운로드한 파일은 다음 경로에 저장됩니다:
```
OneDrive/office work/부동산 실거래 데이터/
```

## 주요 기능

- ✅ 재시도 로직 (15초 대기, 최대 3회)
- ✅ 진행 상황 저장 및 재개
- ✅ 100회 제한 대응 (다음날 자동 재개)
- ✅ 업데이트 모드 (최근 1년만 갱신)
- ✅ OneDrive 자동 업로드 (GitHub Actions)

## 부동산 종목

- 아파트
- 연립다세대
- 단독다가구
- 오피스텔
- 토지
- 상업업무용
- 분양권
- 입주권

## 테스트 실행

### 1단계: 탭 선택 테스트

```bash
python test_tab_selection.py
```

각 부동산 종목 탭이 제대로 클릭되는지 확인합니다.
