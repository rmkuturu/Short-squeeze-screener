@echo off
cd /d "C:\Users\15084\Downloads\Prop\Squeeze screener"
call venv\Scripts\streamlit.exe run dashboard.py --server.headless true
