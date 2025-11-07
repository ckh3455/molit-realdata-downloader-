# -*- coding: utf-8 -*-
"""설정 파일"""
import os
from pathlib import Path

# GitHub Actions 여부
IS_CI = os.getenv("CI", "") == "1"

# 다운로드 경로
if IS_CI:
    DOWNLOAD_DIR = Path("./_downloads")
else:
    DOWNLOAD_DIR = Path(r"D:\OneDrive\office work\부동산 실거래 데이터")

# 임시 다운로드 폴더
TEMP_DOWNLOAD_DIR = Path("./_temp_downloads")

# 국토부 사이트 URL
MOLIT_URL = "https://rt.molit.go.kr/pt/xls/xls.do?mobileAt="

# 다운로드할 부동산 종목 (download_realdata.py와 일치)
PROPERTY_TYPES = [
    "아파트",
    "연립다세대",
    "단독다가구",
    "오피스텔",
    "토지",
    "상업업무용",
    "분양권",
    "입주권"
]

# 타임아웃 설정
DOWNLOAD_TIMEOUT = 30
CLICK_RETRY_MAX = 15
CLICK_RETRY_WAIT = 1.0
