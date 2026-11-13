@echo off
chcp 65001 >nul
cd /d "%~dp0"
if not exist .venv ( py -3 -m venv .venv || python -m venv .venv )
call .venv\Scripts\activate.bat
pip install -q -r requirements.txt
if not defined PORT set PORT=8080
if not exist instance\esprel.db (
  for %%F in ("..\2.*.xlsx") do python scripts\seed_demo.py "%%F" >nul
)
start "" http://localhost:%PORT%
echo   test accounts: admin/admin1234  lead/lead1234  auditor1/audit1234
echo   labs (pw lab1234): labuser  ag3105@example.org  sc2201@example.org
echo   public application: http://localhost:%PORT%/apply
python app.py
