"""매일 시세를 받아 data/market.json 으로 저장합니다. (GitHub Actions에서 실행)

- 금 선물(GCZ6), WTI 원유 선물, S&P 500, 나스닥: Yahoo Finance (yfinance)
- 미국 3년물·10년물 국채 금리: 미국 재무부 공식 금리표 → 실패 시 FRED → (10년물만) Yahoo ^TNX
- VIX: Yahoo ^VIX → 실패 시 CBOE 공식 CSV
- MOVE: Yahoo ^MOVE
- VKOSPI: 한국거래소(KRX) Open API 파생상품지수 시세 (환경변수 KRX_API_KEY 필요)
- 공포·탐욕 지수: CNN Fear & Greed 데이터
"""
import datetime as dt
import io
import json
import os

import pandas as pd
import time

import requests
import yfinance as yf

OUT = os.path.join(os.path.dirname(__file__), "..", "data", "market.json")
START = (dt.date.today() - dt.timedelta(days=365 * 3)).isoformat()

# (id, 제목, 설명, 후보 티커들 - 앞에서부터 시도)
YAHOO = [
    ("gold", "금 선물 (GCZ6)", "COMEX 2026년 12월물 · 달러/온스", ["GCZ26.CMX", "GC=F"]),
    ("wti", "WTI 원유 선물", "NYMEX 근월물 · 달러/배럴", ["CL=F"]),
    ("spx", "S&P 500", "미국 대형주 500개 지수", ["^GSPC"]),
    ("ndx", "나스닥 종합", "나스닥 상장 전 종목 지수", ["^IXIC"]),
    ("vix", "VIX", "S&P 500 옵션으로 본 미국 주식 30일 기대 변동성", ["^VIX"]),
    ("move", "MOVE", "미국 국채 옵션으로 본 채권 1개월 기대 변동성", ["^MOVE"]),
]
VIX_CBOE_CSV = "https://cdn.cboe.com/api/global/us_indices/daily_prices/VIX_History.csv"
KRX_DRV_URL = "https://data-dbg.krx.co.kr/svc/apis/idx/drvprod_dd_trd"
KRX_MAX_CALLS = 900  # 한 번 실행에 KRX에 묻는 최대 날짜 수 (하루 한도 10,000회)
CNN_FNG_URL = "https://production.dataviz.cnn.io/index/fearandgreed/graphdata"
BROWSER_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Origin": "https://edition.cnn.com",
    "Referer": "https://edition.cnn.com/markets/fear-and-greed",
}
# (id, 제목, 설명, 재무부 금리표 열 이름, FRED 코드, Yahoo 대체 티커)
RATES = [
    ("us3y", "미국 3년물 국채 금리", "수익률 %", "3 Yr", "DGS3", None),
    ("us10y", "미국 10년물 국채 금리", "수익률 %", "10 Yr", "DGS10", "^TNX"),
]
HEADERS = {"User-Agent": "Mozilla/5.0 (macro-dashboard; GitHub Actions)"}


def from_yahoo(tickers):
    for t in tickers:
        try:
            df = yf.download(t, start=START, interval="1d", auto_adjust=False, progress=False)
            if df is None or df.empty:
                print(f"[yahoo] {t}: 데이터 없음")
                continue
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = df.columns.get_level_values(0)
            df = df.dropna(subset=["Open", "High", "Low", "Close"])
            rows = [
                {
                    "time": idx.strftime("%Y-%m-%d"),
                    "open": round(float(r["Open"]), 4),
                    "high": round(float(r["High"]), 4),
                    "low": round(float(r["Low"]), 4),
                    "close": round(float(r["Close"]), 4),
                }
                for idx, r in df.iterrows()
            ]
            if rows:
                print(f"[yahoo] {t}: {len(rows)}일")
                return t, rows
        except Exception as e:  # 한 종목이 실패해도 나머지는 계속
            print(f"[yahoo] {t}: 실패 {e}")
    return None, []


def get_with_retry(url, tries=3, timeout=60):
    last = None
    for i in range(tries):
        try:
            r = requests.get(url, timeout=timeout, headers=HEADERS)
            r.raise_for_status()
            return r.text
        except Exception as e:
            last = e
            print(f"  재시도 {i + 1}/{tries}: {e}")
            time.sleep(5)
    raise last


def from_treasury(column):
    """미국 재무부 일별 국채 금리표 (연도별 CSV)를 최근 4년치 합칩니다."""
    frames = []
    this_year = dt.date.today().year
    for year in range(this_year - 3, this_year + 1):
        url = (
            "https://home.treasury.gov/resource-center/data-chart-center/interest-rates/"
            f"daily-treasury-rates.csv/{year}/all?type=daily_treasury_yield_curve"
            f"&field_tdr_date_value={year}&page&_format=csv"
        )
        try:
            df = pd.read_csv(io.StringIO(get_with_retry(url)))
            df.columns = [c.strip() for c in df.columns]
            if column not in df.columns or "Date" not in df.columns:
                print(f"[treasury] {year}: '{column}' 열 없음, 열 목록 {list(df.columns)}")
                continue
            frames.append(df[["Date", column]])
        except Exception as e:
            print(f"[treasury] {year}: 실패 {e}")
    if not frames:
        return []
    df = pd.concat(frames)
    df["Date"] = pd.to_datetime(df["Date"], errors="coerce")
    df[column] = pd.to_numeric(df[column], errors="coerce")
    df = df.dropna().sort_values("Date").drop_duplicates("Date")
    df = df[df["Date"] >= pd.Timestamp(START)]
    rows = [{"time": d.strftime("%Y-%m-%d"), "value": round(float(v), 3)} for d, v in zip(df["Date"], df[column])]
    print(f"[treasury] {column}: {len(rows)}일")
    return rows


def from_fred(series_id):
    url = f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={series_id}&cosd={START}"
    try:
        df = pd.read_csv(io.StringIO(get_with_retry(url)))
        date_col, val_col = df.columns[0], df.columns[1]
        df[val_col] = pd.to_numeric(df[val_col], errors="coerce")
        df = df.dropna(subset=[val_col])
        rows = [{"time": str(d)[:10], "value": round(float(v), 3)} for d, v in zip(df[date_col], df[val_col])]
        print(f"[fred] {series_id}: {len(rows)}일")
        return rows
    except Exception as e:
        print(f"[fred] {series_id}: 실패 {e}")
        return []


def from_yahoo_yield(ticker):
    used, rows = from_yahoo([ticker])
    return [{"time": r["time"], "value": round(r["close"], 3)} for r in rows]


def merge_rows(old_rows, new_rows):
    """날짜 기준으로 합칩니다. 같은 날짜는 새 값이 이깁니다."""
    by_date = {r["time"]: r for r in old_rows or []}
    for r in new_rows:
        by_date[r["time"]] = r
    return [by_date[d] for d in sorted(by_date) if d >= START]


def num(v):
    try:
        return float(str(v).replace(",", "").strip())
    except (TypeError, ValueError):
        return None


def vix_from_cboe():
    """CBOE 공식 VIX 일별 CSV (DATE, OPEN, HIGH, LOW, CLOSE)."""
    try:
        df = pd.read_csv(io.StringIO(get_with_retry(VIX_CBOE_CSV)))
        df.columns = [c.strip().upper() for c in df.columns]
        df["DATE"] = pd.to_datetime(df["DATE"], errors="coerce")
        df = df.dropna().sort_values("DATE")
        df = df[df["DATE"] >= pd.Timestamp(START)]
        rows = [
            {"time": d.strftime("%Y-%m-%d"), "open": round(float(o), 4), "high": round(float(h), 4),
             "low": round(float(l), 4), "close": round(float(c), 4)}
            for d, o, h, l, c in zip(df["DATE"], df["OPEN"], df["HIGH"], df["LOW"], df["CLOSE"])
        ]
        print(f"[cboe] VIX: {len(rows)}일")
        return rows
    except Exception as e:
        print(f"[cboe] VIX: 실패 {e}")
        return []


def is_vkospi(name):
    n = str(name).replace(" ", "")
    return "변동성지수" in n and "200" in n and "선물" not in n


def vkospi_from_krx(old):
    """KRX Open API 파생상품지수 시세정보에서 코스피 200 변동성지수(VKOSPI)를 날짜별로 받습니다.

    하루치씩만 주는 API라서, 이미 받은 날짜는 건너뛰고 빠진 날짜만 묻습니다.
    (처음 한 번은 3년치를 채우느라 몇 분 걸립니다.)
    반환: (rows, skip) — skip 은 휴장일처럼 데이터가 없던 날짜 목록
    """
    key = os.environ.get("KRX_API_KEY", "").strip()
    old_rows = (old or {}).get("data", [])
    skip = set((old or {}).get("skip", []))
    if not key:
        print("[krx] KRX_API_KEY 가 없어 VKOSPI를 건너뜁니다 (README의 'VKOSPI 켜기' 참고)")
        return old_rows, sorted(skip)

    have = {r["time"] for r in old_rows}
    today = dt.date.today()
    recent = today - dt.timedelta(days=7)  # 최근 1주일은 '데이터 없음'을 확정하지 않음
    todo = []
    d = today
    start = dt.date.fromisoformat(START)
    while d >= start:  # 최근 날짜부터 채웁니다
        iso = d.isoformat()
        if d.weekday() < 5 and iso not in have and iso not in skip:
            todo.append(d)
        d -= dt.timedelta(days=1)
    todo = todo[:KRX_MAX_CALLS]
    print(f"[krx] 새로 물어볼 날짜 {len(todo)}일")

    session = requests.Session()
    session.headers.update({"AUTH_KEY": key, "Content-Type": "application/json", **HEADERS})
    new_rows, names_seen = [], set()
    for i, day in enumerate(todo):
        try:
            r = session.get(KRX_DRV_URL, params={"basDd": day.strftime("%Y%m%d")}, timeout=20)
            if r.status_code in (401, 403):
                print(f"[krx] 인증 실패 ({r.status_code}) {r.text[:200]}")
                print("[krx] 인증키가 맞는지, '파생상품지수 시세정보' API 이용 신청이 승인됐는지 확인하세요.")
                break
            r.raise_for_status()
            block = r.json().get("OutBlock_1") or []
        except Exception as e:
            print(f"[krx] {day}: 실패 {e}")
            continue
        hit = None
        for row in block:
            names_seen.add(row.get("IDX_NM"))
            if is_vkospi(row.get("IDX_NM")):
                hit = row
                break
        close = num(hit.get("CLSPRC_IDX")) if hit else None
        if close is None:
            if day < recent:
                skip.add(day.isoformat())  # 휴장일 → 다음부터 묻지 않음
            continue
        o, h, l = (num(hit.get(k)) for k in ("OPNPRC_IDX", "HGPRC_IDX", "LWPRC_IDX"))
        o = o if o else close
        h = max(x for x in (h, o, close) if x)
        l = min(x for x in (l, o, close) if x)
        new_rows.append({"time": day.isoformat(), "open": round(o, 2), "high": round(h, 2), "low": round(l, 2), "close": round(close, 2)})
        if (i + 1) % 100 == 0:
            print(f"[krx] {i + 1}/{len(todo)} 진행")
        time.sleep(0.05)
    if todo and not new_rows and names_seen:
        print(f"[krx] VKOSPI 이름을 못 찾았어요. 받은 지수 이름 예: {sorted(n for n in names_seen if n)[:30]}")
    print(f"[krx] VKOSPI 새로 받은 날짜 {len(new_rows)}일")
    cutoff = START
    return merge_rows(old_rows, new_rows), sorted(x for x in skip if x >= cutoff)


FNG_KO = {
    "extreme fear": "극단적 공포",
    "fear": "공포",
    "neutral": "중립",
    "greed": "탐욕",
    "extreme greed": "극단적 탐욕",
}


def ny_day(ts):
    """미국 뉴욕 날짜로 바꿉니다. 주말이면 None (장이 없는 날 점을 만들지 않음)."""
    if ts.tzinfo is None:
        ts = ts.tz_localize("UTC")
    d = ts.tz_convert("America/New_York")
    return None if d.weekday() >= 5 else d.strftime("%Y-%m-%d")


# CNN 화면의 '세부 지표 7개' (API 키, 한국어 이름, 설명)
FNG_PARTS = [
    ("market_momentum_sp500", "시장 모멘텀", "S&P 500과 125일 이동평균 비교"),
    ("stock_price_strength", "주가 강도", "NYSE 52주 신고가 대 신저가 종목 수"),
    ("stock_price_breadth", "주가 폭", "상승 종목 대 하락 종목 거래량 (맥클렐런)"),
    ("put_call_options", "풋·콜 옵션", "5일 평균 풋/콜 비율"),
    ("market_volatility_vix", "시장 변동성", "VIX와 50일 이동평균 비교"),
    ("safe_haven_demand", "안전자산 수요", "주식과 국채의 최근 20일 수익률 차이"),
    ("junk_bond_demand", "정크본드 수요", "투기등급과 투자등급 채권 금리 차이"),
]
# CNN 화면의 '지난 값' 표 (API 키, 한국어 이름)
FNG_PAST = [
    ("previous_close", "전일 종가"),
    ("previous_1_week", "1주 전"),
    ("previous_1_month", "1달 전"),
    ("previous_1_year", "1년 전"),
]


def fng_label(v):
    """CNN 기준 구간: 0~24 극단적 공포, 25~44 공포, 45~55 중립, 56~75 탐욕, 76~100 극단적 탐욕"""
    v = round(v)
    return "극단적 공포" if v < 25 else "공포" if v < 45 else "중립" if v <= 55 else "탐욕" if v <= 75 else "극단적 탐욕"


def fng_ko(rating, value=None):
    ko = FNG_KO.get(str(rating or "").strip().lower(), "")
    return ko or (fng_label(value) if value is not None else "")


def fng_table(j):
    """CNN 화면 상단의 '지금 · 전일 종가 · 1주 전 · 1달 전 · 1년 전' 표와 세부 지표 7개."""
    now = j.get("fear_and_greed") or {}
    table = []
    if now.get("score") is not None:
        v = float(now["score"])
        table.append({"key": "now", "label": "지금", "value": round(v, 1), "rating": fng_ko(now.get("rating"), v)})
    for key, label in FNG_PAST:
        if now.get(key) is not None:
            v = float(now[key])
            table.append({"key": key, "label": label, "value": round(v, 1), "rating": fng_label(v)})
    parts = []
    for key, title, note in FNG_PARTS:
        p = j.get(key) or {}
        if p.get("score") is None:
            continue
        v = float(p["score"])
        parts.append({"key": key, "title": title, "note": note, "value": round(v, 1), "rating": fng_ko(p.get("rating"), v)})
    updated = None
    if now.get("timestamp"):
        try:
            ts = pd.Timestamp(now["timestamp"])
            ts = ts.tz_localize("UTC") if ts.tzinfo is None else ts
            updated = ts.tz_convert("America/New_York").strftime("%Y-%m-%d %H:%M")
        except Exception:
            pass
    return {"table": table, "parts": parts, "as_of": updated}


def fng_from_cnn():
    """CNN 공포·탐욕 지수. CNN은 최근 1년 남짓만 주므로 지난 데이터와 합쳐 쌓습니다.

    반환: (rows, info) — info 에는 CNN 화면과 같은 표(지금/전일 종가/1주/1달/1년 전)와 세부 지표 7개
    """
    for start in (START, (dt.date.today() - dt.timedelta(days=365)).isoformat(), None):
        url = f"{CNN_FNG_URL}/{start}" if start else CNN_FNG_URL
        try:
            r = requests.get(url, headers=BROWSER_HEADERS, timeout=30)
            r.raise_for_status()
            j = r.json()
        except Exception as e:
            print(f"[cnn] {url}: 실패 {e}")
            time.sleep(3)
            continue
        by_date = {}
        for p in (j.get("fear_and_greed_historical") or {}).get("data", []):
            if p.get("y") is None or p.get("x") is None:
                continue
            day = ny_day(pd.Timestamp(p["x"], unit="ms", tz="UTC"))
            if day:
                by_date[day] = {"time": day, "value": round(float(p["y"]), 1)}
        info = fng_table(j)
        rows = [by_date[d] for d in sorted(by_date)]
        if rows or info["table"]:
            now = info["table"][0] if info["table"] else {}
            print(f"[cnn] 공포·탐욕: {len(rows)}일, 지금 {now.get('value')} ({now.get('rating')}), 세부 지표 {len(info['parts'])}개")
            return rows, info
    return [], {}


def main():
    old = {}
    if os.path.exists(OUT):
        try:
            with open(OUT, encoding="utf-8") as f:
                old = {s["id"]: s for s in json.load(f).get("series", [])}
        except Exception:
            old = {}

    series = []
    for sid, title, note, tickers in YAHOO:
        used, rows = from_yahoo(tickers)
        if not rows and sid == "vix":
            rows, used = vix_from_cboe(), "CBOE"
        if rows:
            series.append({"id": sid, "title": title, "note": note, "source": used if used == "CBOE" else f"Yahoo Finance {used}", "kind": "candle", "data": rows})
        elif sid in old:
            print(f"{sid}: 이번에 못 받아서 지난 데이터를 유지합니다")
            series.append(old[sid])

    for sid, title, note, col, fred_id, yahoo_t in RATES:
        rows, source = from_treasury(col), f"미국 재무부 {col}"
        if not rows:
            rows, source = from_fred(fred_id), f"FRED {fred_id}"
        if not rows and yahoo_t:
            rows, source = from_yahoo_yield(yahoo_t), f"Yahoo Finance {yahoo_t}"
        if rows:
            series.append({"id": sid, "title": title, "note": note, "source": source, "kind": "line", "data": rows})
        elif sid in old:
            print(f"{sid}: 이번에 못 받아서 지난 데이터를 유지합니다")
            series.append(old[sid])

    # VKOSPI (KRX Open API, 빠진 날짜만 이어 받기)
    rows, skip = vkospi_from_krx(old.get("vkospi"))
    if rows:
        series.append({"id": "vkospi", "title": "VKOSPI", "note": "코스피 200 옵션으로 본 한국 주식 30일 기대 변동성",
                       "source": "한국거래소 KRX", "kind": "candle", "data": rows, "skip": skip})

    # 공포·탐욕 지수 (CNN, 지난 데이터와 합쳐 쌓기)
    rows, info = fng_from_cnn()
    prev = old.get("fng", {})
    if rows or info or prev.get("data"):
        if not rows and not info:
            print("fng: 이번에 못 받아서 지난 데이터를 유지합니다")
        table = info.get("table") or prev.get("table") or []
        series.append({"id": "fng", "title": "공포·탐욕 지수", "note": "0 극단적 공포 ~ 100 극단적 탐욕",
                       "source": "CNN Fear & Greed", "kind": "line",
                       "rating": table[0]["rating"] if table and table[0]["key"] == "now" else prev.get("rating", ""),
                       "table": table, "parts": info.get("parts") or prev.get("parts") or [],
                       "as_of": info.get("as_of") or prev.get("as_of"),
                       # CNN이 준 기간은 CNN 값만 쓰고, 그보다 오래된 날짜만 지난 데이터에서 가져옴
                       "data": merge_rows([r for r in prev.get("data") or [] if not rows or r["time"] < rows[0]["time"]], rows)})

    if not series:
        raise SystemExit("모든 데이터 받기에 실패했습니다")

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump({"updated": dt.datetime.now(dt.timezone.utc).isoformat(timespec="minutes"), "series": series}, f, ensure_ascii=False)
    print("저장 완료:", OUT)


if __name__ == "__main__":
    main()
