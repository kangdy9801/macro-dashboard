# 매크로 한눈에 보기

금 선물(GCZ6), WTI 원유 선물, 미국 3년물·10년물 국채 금리, S&P 500, 나스닥, 그리고 변동성·심리 지표(VIX, MOVE, VKOSPI, 공포·탐욕 지수)를 하루(1D) 기준으로 한 페이지에서 보는 대시보드입니다.

## 구성
- `index.html` : 화면 (GitHub Pages)
- `scripts/fetch_data.py` : 시세 받기 (Yahoo Finance, 미국 재무부/FRED, CBOE, 한국거래소 Open API, CNN)
- `.github/workflows/update-data.yml` : 평일 장 마감 뒤 자동 실행 (한국 시간 화~토 오전 7시 30분, 9시 30분)
- `data/market.json` : 받아 온 데이터 (자동 생성)

## 변동성·심리 지표 읽는 법
- **VIX** : 평상시 12~20, 30 이상이면 경계 표시
- **MOVE** : 미국 국채 변동성. VIX와 함께 보면
  - VIX 낮고 MOVE만 높다 = 주식은 낙관, 채권은 불안
  - VIX 높고 MOVE만 낮다 = 주식은 불안, 채권은 안정
  - 둘 다 높다 = 시장 전체 변동성 확대 (같이 출렁임)
  - 둘 다 낮다 = 시장 전체 안정 (같이 잠잠)
  - '높음' 기준은 VIX 20, MOVE 100 (`index.html`의 `MATRIX_TH`에서 바꿀 수 있음)
- **VKOSPI** : 평상시 20 안팎, 40 이상이면 경계 표시
- **공포·탐욕 지수** : [CNN Fear & Greed](https://edition.cnn.com/markets/fear-and-greed) 화면과 같은 값을 씁니다.
  - 구간: 0~24 극단적 공포, 25~44 공포, 45~55 중립, 56~75 탐욕, 76~100 극단적 탐욕
  - 지금 · 전일 종가 · 1주 전 · 1달 전 · 1년 전 값은 CNN이 발표한 숫자를 그대로 보여 줍니다.
  - '세부 지표 7개 보기'를 누르면 CNN이 지수를 만드는 7가지 지표(시장 모멘텀, 주가 강도, 주가 폭, 풋·콜 옵션, 시장 변동성, 안전자산 수요, 정크본드 수요)의 점수가 나옵니다.

## VKOSPI 켜기 (한 번만)
VKOSPI는 한국거래소 Open API에서만 안정적으로 받을 수 있어 인증키가 필요합니다. (무료)
1. https://openapi.krx.co.kr 회원가입 → 인증키 신청
2. 서비스 목록 → 지수 → **파생상품지수 시세정보** 'API 이용 신청' (승인까지 하루쯤 걸릴 수 있음)
3. GitHub 저장소 Settings → Secrets and variables → Actions → New repository secret
   - Name: `KRX_API_KEY` / Secret: 발급받은 인증키
4. Actions 탭 → 시세 데이터 업데이트 → Run workflow
   - 처음 한 번은 3년치를 하루씩 받느라 5~10분쯤 걸리고, 그 뒤로는 새 날짜만 받아 금방 끝납니다.

## 직접 갱신하기
Actions 탭 → 시세 데이터 업데이트 → Run workflow
