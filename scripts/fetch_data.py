"""매일 시세를 받아 data/market.json 으로 저장합니다. (GitHub Actions에서 실행)

- 금 선물(GCZ6), WTI 원유 선물, S&P 500, 나스닥: Yahoo Finance (yfinance)
- 미국 3년물·10년물 국채 금리: 미국 연준 FRED 공식 데이터 (DGS3, DGS10)
"""
import datetime as dt
import io
import json
import os

import pandas as pd
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
]
FRED = [
    ("us3y", "미국 3년물 국채 금리", "수익률 % · FRED DGS3", "DGS3"),
    ("us10y", "미국 10년물 국채 금리", "수익률 % · FRED DGS10", "DGS10"),
]


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


def from_fred(series_id):
    url = f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={series_id}&cosd={START}"
    try:
        r = requests.get(url, timeout=30, headers={"User-Agent": "Mozilla/5.0"})
        r.raise_for_status()
        df = pd.read_csv(io.StringIO(r.text))
        date_col, val_col = df.columns[0], df.columns[1]
        df[val_col] = pd.to_numeric(df[val_col], errors="coerce")
        df = df.dropna(subset=[val_col])
        rows = [{"time": str(d)[:10], "value": round(float(v), 3)} for d, v in zip(df[date_col], df[val_col])]
        print(f"[fred] {series_id}: {len(rows)}일")
        return rows
    except Exception as e:
        print(f"[fred] {series_id}: 실패 {e}")
        return []


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
        if rows:
            series.append({"id": sid, "title": title, "note": note, "source": f"Yahoo Finance {used}", "kind": "candle", "data": rows})
        elif sid in old:
            print(f"{sid}: 이번에 못 받아서 지난 데이터를 유지합니다")
            series.append(old[sid])

    for sid, title, note, fred_id in FRED:
        rows = from_fred(fred_id)
        if rows:
            series.append({"id": sid, "title": title, "note": note, "source": f"FRED {fred_id}", "kind": "line", "data": rows})
        elif sid in old:
            print(f"{sid}: 이번에 못 받아서 지난 데이터를 유지합니다")
            series.append(old[sid])

    if not series:
        raise SystemExit("모든 데이터 받기에 실패했습니다")

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump({"updated": dt.datetime.now(dt.timezone.utc).isoformat(timespec="minutes"), "series": series}, f, ensure_ascii=False)
    print("저장 완료:", OUT)


if __name__ == "__main__":
    main()
