# 국토부 부동산 실거래가 다운로더

국토교통부 실거래가 공개시스템에서 전체 부동산 데이터를 자동 다운로드합니다.

## 설치
```bash
pip install -r requirements.txt
```

## 테스트 실행

### 1단계: 탭 선택 테스트
```bash
python test_tab_selection.py
```

각 부동산 종목 탭이 제대로 클릭되는지 확인합니다.

## 다운로드 경로

`config.py`에서 다운로드 경로 설정:
```python
DOWNLOAD_DIR = Path(r"C:\Users\USER\OneDrive\office work\부동산 실거래 데이터")
```

## 부동산 종목

- 아파트
- 연립/다세대
- 단독/다가구
- 오피스텔
- 토지
- 분양/입주권
- 상업/업무용
- 공장/창고 등
