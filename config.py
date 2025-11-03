# -*- coding: utf-8 -*-
"""설정 파일"""
from pathlib import Path

# 다운로드 경로 (Windows 경로)
DOWNLOAD_DIR = Path(r"C:\Users\USER\OneDrive\office work\부동산 실거래 데이터")

# 임시 다운로드 폴더
TEMP_DOWNLOAD_DIR = Path("./_rt_downloads")

# 국토부 사이트 URL
MOLIT_URL = "https://rt.molit.go.kr/pt/xls/xls.do?mobileAt="

# 다운로드할 부동산 종목
PROPERTY_TYPES = [
    "아파트",
    "연립/다세대",
    "단독/다가구",
    "오피스텔",
    "토지",
    "분양/입주권",
    "상업/업무용",
    "공장/창고 등"
]

# 타임아웃 설정
DOWNLOAD_TIMEOUT = 30  # 초
CLICK_RETRY_MAX = 15
CLICK_RETRY_WAIT = 1.0  # 초
