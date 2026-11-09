"""Validate calendar schema and create an actionable freshness report.
Schema violations fail the workflow; missing future schedules warn (not fabricated).
"""
import datetime as dt
import json
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
CAL = ROOT / 'data' / 'calendar.json'
REPORT = ROOT / 'data' / 'calendar_health.json'
KST = ZoneInfo('Asia/Seoul')


def validate(cal, now):
    errors, warnings = [], []
    seen = set()
    for event in cal.get('events', []):
        ident = event.get('id')
        if not ident or ident in seen:
            errors.append(f'duplicate or missing event id: {ident}')
        seen.add(ident)
        if event.get('series') not in cal.get('series', {}):
            errors.append(f'{ident}: unknown series')
        try:
            t = dt.datetime.fromisoformat(event['release_kst']).replace(tzinfo=KST)
        except (KeyError, TypeError, ValueError):
            errors.append(f'{ident}: invalid release_kst')
            continue
        vals = event.get('values')
        if not isinstance(vals, list) or len({v.get('key') for v in vals}) != len(vals):
            errors.append(f'{ident}: missing or duplicate metric keys')
            continue
        for val in vals:
            for field in ('actual', 'previous', 'forecast'):
                x = val.get(field)
                if x is not None and (not isinstance(x, (int, float)) or isinstance(x, bool) or abs(x) > 1e6):
                    errors.append(f'{ident}/{val.get("key")}: invalid {field}')
        if t < now - dt.timedelta(days=3) and all(v.get('actual') is None for v in vals):
            warnings.append(f'{ident}: released but no actual values; check source availability')
    for series in cal.get('series', {}):
        last = max((dt.datetime.fromisoformat(e['release_kst']).replace(tzinfo=KST)
                    for e in cal['events'] if e['series'] == series), default=None)
        if not last or last < now + dt.timedelta(days=35):
            warnings.append(f'{series}: no scheduled event at least 35 days ahead; source/manual review needed')
    return errors, warnings


def main():
    cal = json.loads(CAL.read_text(encoding='utf-8'))
    now = dt.datetime.now(KST)
    errors, warnings = validate(cal, now)
    result = {'checked_at': now.isoformat(timespec='minutes'), 'errors': errors,
              'warnings': warnings, 'event_count': len(cal.get('events', []))}
    REPORT.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    for message in warnings:
        print(f'::warning::{message}')
    for message in errors:
        print(f'::error::{message}')
    print(f'Calendar validation: {len(errors)} errors, {len(warnings)} warnings')
    if errors:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
