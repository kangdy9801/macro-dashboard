# 매크로 한눈에 보기

금 선물(GCZ6), WTI 원유 선물, 미국 3년물·10년물 국채 금리, S&P 500, 나스닥을 하루(1D) 기준으로 한 페이지에서 보는 대시보드입니다.

## 구성
- `index.html` : 화면 (GitHub Pages)
- `scripts/fetch_data.py` : 시세 받기 (Yahoo Finance, FRED)
- `.github/workflows/update-data.yml` : 평일 매일 한국 시간 오전 7시 30분에 자동 실행
- `data/market.json` : 받아 온 데이터 (자동 생성)

## 직접 갱신하기
Actions 탭 → 시세 데이터 업데이트 → Run workflow
