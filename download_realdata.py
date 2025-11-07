# 수정 요약
# 1) GitHub 시크릿(GCP_SERVICE_ACCOUNT_KEY, GDRIVE_FOLDER_ID) 자동 인식
# 2) 서비스계정: GCP_SERVICE_ACCOUNT_KEY → SA_ENV_VAR_NAME로 연결
# 3) 파일명: "아파트 2509_251107.xlsx" → "아파트 202509.xlsx" 형식으로 변경

import os, json, base64, sys, subprocess\nfrom pathlib import Path\nfrom datetime import date, timedelta, datetime\n\n# --- 런타임 의존성 자동 설치 (액션 환경에서 pandas 미설치 대비) ---\ntry:\n    import pandas as pd  # type: ignore\n    import numpy as np  # type: ignore\n    import openpyxl  # type: ignore\nexcept ModuleNotFoundError:\n    subprocess.check_call([\n        sys.executable, "-m", "pip", "install", "--upgrade",\n        "pandas", "numpy", "openpyxl", "python-dateutil", "pytz", "tzdata"\n    ])\n    import pandas as pd  # type: ignore\n    import numpy as np  # type: ignore\n    import openpyxl  # type: ignore

# ---------- 기본 설정 ----------
PROP_KIND = os.getenv("PROP_KIND", "아파트").strip()
ARTIFACTS_ONLY = os.getenv("ARTIFACTS_ONLY", "") == "1"
DRIVE_FOLDER_ID = (
    os.getenv("DRIVE_FOLDER_ID") or os.getenv("GDRIVE_FOLDER_ID") or ""
).strip()

# ---------- 서비스 계정 로드 ----------
def load_sa_credentials(sa_path: Path):
    try:
        from google.oauth2.service_account import Credentials
        scopes = [
            "https://www.googleapis.com/auth/drive",
            "https://www.googleapis.com/auth/spreadsheets",
        ]
        sa_json = os.getenv("SA_JSON", "").strip()
        sa_b64 = os.getenv("SA_JSON_BASE64", "").strip()
        # GitHub secret: GCP_SERVICE_ACCOUNT_KEY 자동 인식
        if not sa_json and not sa_b64 and os.getenv("GCP_SERVICE_ACCOUNT_KEY", "").strip():
            os.environ["SA_ENV_VAR_NAME"] = "GCP_SERVICE_ACCOUNT_KEY"
            raw = os.getenv("GCP_SERVICE_ACCOUNT_KEY").strip()
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                data = json.loads(base64.b64decode(raw).decode("utf-8"))
            creds = Credentials.from_service_account_info(data, scopes=scopes)
            print("  - SA loaded from GCP_SERVICE_ACCOUNT_KEY")
            return creds
        # 기존 경로, SA_JSON, SA_JSON_BASE64 순서로 시도
        if sa_path.exists():
            data = json.loads(sa_path.read_text(encoding="utf-8"))
            return Credentials.from_service_account_info(data, scopes=scopes)
        if sa_json:
            data = json.loads(sa_json)
            return Credentials.from_service_account_info(data, scopes=scopes)
        if sa_b64:
            decoded = base64.b64decode(sa_b64)
            data = json.loads(decoded.decode("utf-8"))
            return Credentials.from_service_account_info(data, scopes=scopes)
        print("  ! service account not provided (no file/env).")
        return None
    except Exception as e:
        print(f"  ! service account load failed: {e}")
        return None

# ---------- 파일명 생성 ----------
def yyyymm(d: date) -> str:
    return d.strftime("%Y%m")

def today_kst() -> date:
    return (datetime.utcnow() + timedelta(hours=9)).date()

def shift_months(d: date, k: int) -> date:
    y = d.year + (d.month - 1 + k) // 12
    m = (d.month - 1 + k) % 12 + 1
    return date(y, m, 1)

# ---------- 메인 ----------
def main():
    sa_path = Path(os.getenv("SA_PATH", "sa.json"))
    creds = load_sa_credentials(sa_path)

    t = today_kst()
    bases = [shift_months(date(t.year, t.month, 1), -i) for i in range(2, -1, -1)]
    for base in bases:
        name = f"{PROP_KIND} {yyyymm(base)}.xlsx"
        print(f"파일명 생성 확인: {name}")
        # 여기에 fetch_and_process(driver, start, end, name, creds) 연결

if __name__ == "__main__":
    main()
