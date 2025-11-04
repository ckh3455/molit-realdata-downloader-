# -*- coding: utf-8 -*-
"""
국토부 실거래가 데이터 월별 대량 다운로드
- 재시도 로직 (15초 대기, 최대 3회)
- 진행 상황 저장 및 재개
- 100회 제한 대응 (다음날 자동 재개)
- 업데이트 모드 (최근 1년만 갱신)
- Google Drive 기존 파일 체크 및 최신 파일 이후부터 다운로드

파일명: download_realdata.py
"""
import os
import re
import sys
import json
import time
import argparse
from pathlib import Path
from datetime import date, datetime, timedelta
from typing import Optional, Tuple, List, Dict, Set

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.common.alert import Alert
from selenium.common.exceptions import UnexpectedAlertPresentException

# ==================== 설정 ====================
IS_CI = os.getenv("CI", "") == "1"

# 저장 폴더 (환경에 따라 자동 전환)
if IS_CI:
    # GitHub Actions: 테스트용 output 폴더
    DOWNLOAD_DIR = Path("output")
else:
    # 로컬 PC: OneDrive 경로
    DOWNLOAD_DIR = Path(r"D:\OneDrive\office work\부동산 실거래 데이터")

# 임시 다운로드 폴더
TEMP_DOWNLOAD_DIR = Path("_temp_downloads")

# 국토부 URL (엑셀 다운로드 페이지)
MOLIT_URL = "https://rt.molit.go.kr/pt/xls/xls.do?mobileAt="

# 부동산 종목 (8개)
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

# 진행 상황 파일
PROGRESS_FILE = Path("download_progress.json")

# Google Drive 기존 파일 목록
EXISTING_FILES_JSON = Path("existing_files.json")

# 임시 다운로드 폴더 생성
TEMP_DOWNLOAD_DIR.mkdir(exist_ok=True)

# 저장 폴더 생성
DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)

# Google Drive 기존 파일 캐시
GDRIVE_EXISTING_FILES: Dict[str, Set[str]] = {}

// ... existing code ...
