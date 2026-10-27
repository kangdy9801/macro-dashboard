"""경제 일정(data/calendar.json)의 '결과'와 '이전' 값을 자동으로 채웁니다. (GitHub Actions에서 실행)

- 일정(발표 날짜·시간)과 예상치는 data/calendar.json 에서 손으로 관리합니다.
- 이 스크립트는 발표 시간이 지난 일정 중 '결과'가 비어 있는 칸만 채웁니다.
  손으로 넣은 값은 절대 덮어쓰지 않습니다.
- 결과가 채워지면, 같은 지표의 다음 일정 '이전' 칸도 비어 있을 때 채웁니다.

데이터 출처
- 미국: FRED (세인트루이스 연은) — 인증키 없이 사용
    CPI  : CPIAUCNS(전체), CPILFENS(근원) → 전년 같은 달 대비 %
    PCE  : PCEPI(전체), PCEPILFE(근원)   → 전년 같은 달 대비 %
    고용 : PAYEMS(비농업 일자리, 전월 대비 증감), UNRATE(실업률)
    금리 : DFEDTARU(연방기금금리 목표 상단)
- 한국: 한국은행 ECOS Open API — 환경변수 ECOS_API_KEY 필요 (없으면 건너뜀)
    기준금리 : 722Y001 / 0101000
    CPI      : 901Y009 (총지수) · 901Y010 (식료품 및 에너지 제외) → 전년 같은 달 대비 %
    고용     : '경제활동인구' 통계표의 취업자(전년 같은 달 대비 증감)·실업률
- 미국 예상치: Forex Factory 이번 주 일정(JSON)에서 예상치가 비어 있는 칸만 채움 (실패해도 무시)
"""
import csv
import datetime as dt
import io
import json
import os
import re
import time

import requests

HERE = os.path.dirname(__file__)
CAL = os.path.join(HERE, "..", "data", "calendar.json")
KST = dt.timezone(dt.timedelta(hours=9))
HEADERS = {"User-Agent": "Mozilla/5.0 (macro-dashboard; GitHub Actions)"}
FRED_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv?id={sid}&cosd={start}"
ECOS_URL = "https://ecos.bok.or.kr/api"
FF_URL = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
WAIT_AFTER_RELEASE = dt.timedelta(minutes=30)  # 발표 직후 데이터가 올라올 시간


# ── 공통 ──────────────────────────────────────────────
def get(url, tries=3, timeout=40, **kw):
    last = None
    for i in range(tries):
        try:
            r = requests.get(url, timeout=timeout, headers=HEADERS, **kw)
            r.raise_for_status()
            return r
        except Exception as e:  # 네트워크가 잠깐 끊겨도 다시 시도
            last = e
            time.sleep(3 * (i + 1))
    raise last


def release_time(ev):
    return dt.datetime.fromisoformat(ev["release_kst"]).replace(tzinfo=KST)


def month_add(period, n):
    """'2026-09' 에 n달을 더합니다."""
    y, m = map(int, period.split("-"))
    m += n
    y += (m - 1) // 12
    m = (m - 1) % 12 + 1
    return f"{y:04d}-{m:02d}"


def yoy(series, period):
    """월별 지수 → 전년 같은 달 대비 % (소수 첫째 자리)."""
    now, before = series.get(period), series.get(month_add(period, -12))
    if now is None or not before:
        return None
    return round((now / before - 1) * 100, 1)


def first_on_or_after(daily, iso_date):
    """날짜별 값에서 iso_date 이후 첫 값."""
    for d in sorted(daily):
        if d >= iso_date:
            return daily[d]
    return None


# ── 미국: FRED ────────────────────────────────────────
_fred_cache = {}


def fred(sid, start="2024-01-01"):
    """FRED CSV → {날짜: 값}. 월별 지표는 '2026-09-01' 같은 날짜를 '2026-09'로 줄여서 돌려줍니다."""
    if sid in _fred_cache:
        return _fred_cache[sid]
    out = {}
    try:
        text = get(FRED_URL.format(sid=sid, start=start)).text
        rows = list(csv.reader(io.StringIO(text)))
        for row in rows[1:]:
            if len(row) < 2:
                continue
            try:
                out[row[0]] = float(row[1])
            except ValueError:
                pass  # '.' 같은 빈 값
        print(f"[fred] {sid}: {len(out)}개")
    except Exception as e:
        print(f"[fred] {sid}: 실패 {e}")
    _fred_cache[sid] = out
    return out


def monthly(daily):
    return {d[:7]: v for d, v in daily.items()}


def us_values(series_id, ev):
    """미국 지표 결과값 {metric_key: 값}. 아직 없으면 빈 dict."""
    p = ev.get("period")
    if series_id == "us_cpi":
        return {"yoy": yoy(monthly(fred("CPIAUCNS")), p), "core_yoy": yoy(monthly(fred("CPILFENS")), p)}
    if series_id == "us_pce":
        return {"yoy": yoy(monthly(fred("PCEPI")), p), "core_yoy": yoy(monthly(fred("PCEPILFE")), p)}
    if series_id == "us_jobs":
        pay, ur = monthly(fred("PAYEMS")), monthly(fred("UNRATE"))
        prev = pay.get(month_add(p, -1))
        nfp = round((pay[p] - prev) / 10, 1) if p in pay and prev is not None else None  # 천 명 → 만 명
        return {"nfp": nfp, "ur": ur.get(p)}
    if series_id == "us_rate":
        # 한국 시간 발표일 = 미국 결정일 다음 날 = 새 금리가 적용되는 날
        effective = release_time(ev).date().isoformat()
        return {"rate": first_on_or_after(fred("DFEDTARU", start="2025-01-01"), effective)}
    return {}


# ── 한국: ECOS ────────────────────────────────────────
def ecos_key():
    return os.environ.get("ECOS_API_KEY", "").strip()


_ecos_cache = {}


def ecos_rows(path):
    """ECOS API 한 번 부르기. path 예: 'StatisticSearch/{key}/json/kr/1/100/722Y001/D/20260101/20261231/0101000'"""
    if path in _ecos_cache:
        return _ecos_cache[path]
    rows = []
    try:
        j = get(f"{ECOS_URL}/{path}").json()
        block = next((v for v in j.values() if isinstance(v, dict) and "row" in v), None)
        if block:
            rows = block["row"]
        else:
            res = j.get("RESULT") or {}
            print(f"[ecos] {path.split('/')[0]} 응답 없음: {res.get('MESSAGE', j)}")
    except Exception as e:
        print(f"[ecos] 실패 {e}")
    _ecos_cache[path] = rows
    return rows


def ecos_series(stat, cycle, item, start, end):
    """{기간: 값}. 월별은 '2026-09', 일별은 '2026-09-01'."""
    key = ecos_key()
    rows = ecos_rows(f"StatisticSearch/{key}/json/kr/1/1000/{stat}/{cycle}/{start}/{end}/{item}")
    out = {}
    for r in rows:
        t, v = str(r.get("TIME", "")), r.get("DATA_VALUE")
        try:
            v = float(str(v).replace(",", ""))
        except (TypeError, ValueError):
            continue
        if cycle == "M" and len(t) == 6:
            out[f"{t[:4]}-{t[4:]}"] = v
        elif cycle == "D" and len(t) == 8:
            out[f"{t[:4]}-{t[4:6]}-{t[6:]}"] = v
    return out


def norm(s):
    return re.sub(r"[\s·ㆍ()]", "", str(s or ""))


def ecos_find_item(stat, want, exact=False):
    """통계표 안에서 이름으로 항목 코드를 찾습니다. 찾은 이름은 로그에 남겨 확인할 수 있게 합니다."""
    rows = ecos_rows(f"StatisticItemList/{ecos_key()}/json/kr/1/500/{stat}")
    w = norm(want)
    for r in rows:
        name = norm(r.get("ITEM_NAME"))
        if (name == w) if exact else (w in name):
            if r.get("CYCLE") in (None, "", "M"):
                print(f"[ecos] {stat} '{want}' → {r.get('ITEM_CODE')} ({r.get('ITEM_NAME')})")
                return r.get("ITEM_CODE")
    return None


def ecos_find_table(want):
    rows = ecos_rows(f"StatisticTableList/{ecos_key()}/json/kr/1/2000")
    w = norm(want)
    for r in rows:
        if w in norm(r.get("STAT_NAME")) and r.get("CYCLE") == "M":
            print(f"[ecos] 통계표 '{want}' → {r.get('STAT_CODE')} ({r.get('STAT_NAME')})")
            return r.get("STAT_CODE")
    return None


_kr_jobs_codes = None


def kr_jobs_codes():
    """경제활동인구 통계표에서 취업자·실업률 항목 코드를 찾습니다. (처음 한 번만)"""
    global _kr_jobs_codes
    if _kr_jobs_codes is None:
        stat = "901Y027"
        emp = ecos_find_item(stat, "취업자", exact=True)
        if not emp:
            stat = ecos_find_table("경제활동인구") or stat
            emp = ecos_find_item(stat, "취업자", exact=True)
        ur = ecos_find_item(stat, "실업률", exact=True)
        _kr_jobs_codes = (stat, emp, ur)
    return _kr_jobs_codes


def kr_values(series_id, ev):
    if not ecos_key():
        return {}
    p = ev.get("period")
    if series_id == "kr_rate":
        day = release_time(ev).date()
        daily = ecos_series("722Y001", "D", "0101000", (day - dt.timedelta(days=40)).strftime("%Y%m%d"),
                            (day + dt.timedelta(days=10)).strftime("%Y%m%d"))
        return {"rate": first_on_or_after(daily, day.isoformat())}
    start, end = month_add(p, -13).replace("-", ""), p.replace("-", "")
    if series_id == "kr_cpi":
        total = ecos_series("901Y009", "M", "0", start, end)
        core_code = ecos_find_item("901Y010", "식료품 및 에너지제외")
        core = ecos_series("901Y010", "M", core_code, start, end) if core_code else {}
        return {"yoy": yoy(total, p), "core_yoy": yoy(core, p)}
    if series_id == "kr_jobs":
        stat, emp_code, ur_code = kr_jobs_codes()
        out = {}
        if emp_code:
            emp = ecos_series(stat, "M", emp_code, start, end)  # 천 명
            if p in emp and month_add(p, -12) in emp:
                out["emp"] = round((emp[p] - emp[month_add(p, -12)]) / 10, 1)  # 만 명
        if ur_code:
            out["ur"] = ecos_series(stat, "M", ur_code, start, end).get(p)
        return out
    return {}


# ── 미국 예상치: Forex Factory 이번 주 일정 ─────────────
FF_MAP = [  # (제목 정규식, series, metric)
    (r"^CPI y/y$", "us_cpi", "yoy"),
    (r"^Core CPI y/y$", "us_cpi", "core_yoy"),
    (r"^PCE Price Index y/y$", "us_pce", "yoy"),
    (r"^Core PCE Price Index y/y$", "us_pce", "core_yoy"),
    (r"^Non-Farm Employment Change$", "us_jobs", "nfp"),
    (r"^Unemployment Rate$", "us_jobs", "ur"),
    (r"^Federal Funds Rate$", "us_rate", "rate"),
]


def ff_number(text, metric):
    s = str(text or "").strip().replace(",", "")
    m = re.match(r"^(-?\d+(?:\.\d+)?)\s*([KkMm%]?)", s)
    if not m:
        return None
    v, unit = float(m.group(1)), m.group(2).upper()
    if metric == "nfp":  # '56K' → 5.6만 명
        v = v * 1000 if unit == "M" else v
        return round(v / 10, 1)
    return v


def fill_ff_forecasts(events):
    try:
        items = get(FF_URL, tries=2).json()
    except Exception as e:
        print(f"[ff] 이번 주 예상치를 못 받았어요: {e}")
        return 0
    n = 0
    for it in items:
        if it.get("country") != "USD" or not it.get("forecast"):
            continue
        for pat, sid, key in FF_MAP:
            if not re.match(pat, str(it.get("title", "")).strip()):
                continue
            try:
                when = dt.datetime.fromisoformat(it["date"]).astimezone(KST)
            except Exception:
                continue
            for ev in events:
                if ev["series"] != sid or abs(release_time(ev) - when) > dt.timedelta(hours=3):
                    continue
                for v in ev["values"]:
                    if v["key"] == key and v.get("forecast") is None:
                        val = ff_number(it["forecast"], key)
                        if val is not None:
                            v["forecast"] = val
                            n += 1
                            print(f"[ff] {ev['id']} {key} 예상치 {val}")
    return n


# ── 실행 ──────────────────────────────────────────────
def main():
    with open(CAL, encoding="utf-8") as f:
        cal = json.load(f)
    events = cal["events"]
    now = dt.datetime.now(KST)
    changed = 0

    for ev in events:
        if release_time(ev) + WAIT_AFTER_RELEASE > now:
            continue
        missing = [v for v in ev["values"] if v.get("actual") is None]
        if not missing:
            continue
        sid = ev["series"]
        try:
            got = us_values(sid, ev) if sid.startswith("us_") else kr_values(sid, ev)
        except Exception as e:  # 한 지표가 실패해도 나머지는 계속
            print(f"{ev['id']}: 실패 {e}")
            got = {}
        for v in missing:
            val = got.get(v["key"])
            if val is not None:
                v["actual"] = val
                changed += 1
                print(f"{ev['id']} {v['key']} 결과 {val}")

    # 결과가 생긴 일정 → 같은 지표 다음 일정의 '이전' 칸 (비어 있을 때만)
    by_series = {}
    for ev in sorted(events, key=release_time):
        by_series.setdefault(ev["series"], []).append(ev)
    for evs in by_series.values():
        for a, b in zip(evs, evs[1:]):
            for va in a["values"]:
                vb = next((x for x in b["values"] if x["key"] == va["key"]), None)
                if vb is not None and vb.get("previous") is None and va.get("actual") is not None:
                    vb["previous"] = va["actual"]
                    changed += 1

    changed += fill_ff_forecasts(events)

    if changed:
        cal["updated"] = now.isoformat(timespec="minutes")
        with open(CAL, "w", encoding="utf-8") as f:
            json.dump(cal, f, ensure_ascii=False, indent=1)
            f.write("\n")
    print(f"경제 일정: {changed}칸 채움" + ("" if ecos_key() else " (ECOS_API_KEY 가 없어 한국 결과는 건너뜀)"))


if __name__ == "__main__":
    main()
