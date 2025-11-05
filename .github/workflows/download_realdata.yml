name: Download Real Estate Data

on:
  schedule:
    - cron: '0 2 * * *'
  workflow_dispatch:

jobs:
  download:
    runs-on: ubuntu-latest
    
    steps:
    - uses: actions/checkout@v4
    
    - name: Set up Python
      uses: actions/setup-python@v5
      with:
        python-version: '3.11'
    
    - name: Install dependencies
      run: |
        python -m pip install --upgrade pip
        pip install -r requirements.txt
    
    - name: Install Chrome
      run: |
        sudo apt-get update
        sudo apt-get install -y google-chrome-stable
    
    - name: Download Real Estate Data
      env:
        CI: "1"
        GOOGLE_SERVICE_ACCOUNT_JSON: ${{ secrets.GCP_SERVICE_ACCOUNT_KEY }}
        GDRIVE_FOLDER_ID: ${{ secrets.GDRIVE_FOLDER_ID }}
      run: |
        python download_realdata.py --update-mode
      continue-on-error: true
    
    - name: Upload progress file
      if: always()
      uses: actions/upload-artifact@v4
      with:
        name: download-progress
        path: download_progress.json
        retention-days: 30
