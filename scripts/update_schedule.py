"""Sync independently verified US release schedules into data/calendar.json.

Sources: BLS and BEA public calendar ICS; Federal Reserve FOMC calendar page.
Unsupported schedules (KR, US retail) are preserved for manual curation.
No date is extrapolated. Existing actuals, manually entered forecasts and notes remain intact.
"""
import datetime as dt
import json
import os
import re
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

ROOT = Path(__file__).resolve().parents[1]
CAL = ROOT / 'data' / 'calendar.json'
HISTORY = ROOT / 'data' / 'schedule_history.json'
KST = ZoneInfo('Asia/Seoul')
ET = ZoneInfo('America/New_York')
UTC = dt.timezone.utc
SOURCES = {
    'BLS': 'https://www.bls.gov/schedule/news_release/bls.ics',
    'BEA': 'https://www.bea.gov/news/schedule/ics/online-calendar-subscription.ics',
    'FOMC': 'https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm',
}
HEADERS = {'User-Agent': 'MacroDashboard/1.0 (public economic calendar; GitHub Pages)', 'Accept': 'text/calendar,text/html,*/*'}


def unfold_ics(text):
    lines = []
    for line in text.replace('\r\n', '\n').split('\n'):
        if line.startswith((' ', '\t')) and lines:
            lines[-1] += line[1:]
        else:
            lines.append(line)
    return lines


def parse_ics(text):
    result, entry = [], None
    for line in unfold_ics(text):
        if line == 'BEGIN:VEVENT':
            entry = {}
        elif line == 'END:VEVENT':
            if entry:
                result.append(entry)
            entry = None
        elif entry is not None and ':' in line:
            key, value = line.split(':', 1)
            entry[key] = value.replace('\\,', ',').replace('\\n', '\n')
    return result


def ics_datetime(entry):
    for key, raw in entry.items():
        if not key.startswith('DTSTART'):
            continue
        if 'VALUE=DATE' in key and 'DATE-TIME' not in key:
            return None  # no exact release hour: do not fabricate one
        value = raw.strip()
        if not re.match(r'^\d{8}T\d{6}Z?$', value):
            return None
        moment = dt.datetime.strptime(value[:15], '%Y%m%dT%H%M%S')
        zone = UTC if value.endswith('Z') else (ZoneInfo(re.search(r'TZID=([^;:]+)', key).group(1)) if 'TZID=' in key else ET)
        return moment.replace(tzinfo=zone).astimezone(KST)
    return None


def previous_month(y, m):
    return f'{y-1}-12' if m == 1 else f'{y}-{m-1:02d}'


def month_from_title(summary):
    months = '|'.join('January February March April May June July August September October November December'.split())
    match = re.search(r'\b(' + months + r')\s+(20\d{2})\b', summary, re.I)
    if match:
        number = list('january february march april may june july august september october november december'.split()).index(match.group(1).lower()) + 1
        return f'{match.group(2)}-{number:02d}'
    return None


def identify(source, title):
    title = ' '.join(title.split())
    if source == 'BLS':
        kind = ('us_cpi' if re.search(r'Consumer Price Index', title, re.I) and not re.search('Producer|Import|Export', title, re.I)
                else 'us_jobs' if re.search(r'Employment Situation', title, re.I) else None)
        return (kind, month_from_title(title), None) if kind else None
    if source == 'BEA':
        if re.match(r'Personal Income and Outlays', title, re.I):
            return ('us_pce', month_from_title(title), None)
        if re.match(r'Gross Domestic Product[, ]', title, re.I):
            q = re.search(r'\b([1-4])(?:st|nd|rd|th)\s+Quarter\b(?:\s+and\s+Year)?\s+(20\d{2})', title, re.I)
            if not q:
                return None
            estimate = ('속보치' if re.search(r'Advance Estimate', title, re.I)
                        else '잠정치' if re.search(r'Second Estimate', title, re.I)
                        else '확정치' if re.search(r'Third Estimate', title, re.I) else None)
            return ('us_gdp', f'{q.group(2)}-Q{q.group(1)}', estimate) if estimate else None
    return None


def candidate(series, period, estimate, moment, source, title):
    if series != 'us_rate' and not period:
        return None
    if series == 'us_rate':
        identifier = 'us_rate-' + moment.date().isoformat()
    elif series == 'us_gdp':
        identifier = f'us_gdp-{period}' + {'속보치': 'a', '잠정치': 's', '확정치': 't'}[estimate]
    else:
        identifier = f'{series}-{period}'
    result = {'id': identifier, 'series': series, 'release_kst': moment.strftime('%Y-%m-%dT%H:%M'), 'period': period,
              'source_url': SOURCES[source], 'schedule_source': source}
    if estimate:
        result['estimate'] = estimate
    if source == 'FOMC':
        result['tentative'] = True
    return result


def get_ics_candidates(source, text):
    output = []
    for entry in parse_ics(text):
        title = entry.get('SUMMARY', '')
        classification = identify(source, title)
        when = ics_datetime(entry)
        if not classification or not when:
            continue
        item = candidate(*classification, when, source, title)
        if item:
            output.append(item)
    return output


def get_fomc_candidates(html):
    """Read official Fed year sections (no meeting-date estimation)."""
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, 'html.parser')
    text = soup.get_text(' ', strip=True)
    output = []
    months = dict(zip('January February March April May June July August September October November December'.split(), range(1, 13)))
    for year in range(dt.datetime.now(KST).year, dt.datetime.now(KST).year + 3):
        # Find the year heading and stop at the next year's heading.
        match = re.search(rf'\b{year}\s+FOMC\s+Meetings\b(.*?)(?=\b{year+1}\s+FOMC\s+Meetings\b|\b{year-1}\s+FOMC\s+Meetings\b|\*\s*Meeting\s+associated|\Z)', text, re.I | re.S)
        if not match:
            continue
        section = match.group(1)
        for name, month in months.items():
            m = re.search(rf'\b{name}\s+(\d{{1,2}})\s*[-–]\s*(\d{{1,2}})\*?', section)
            if not m:
                continue
            day = int(m.group(2))
            try:
                # Fed statement is at 14:00 New York time on final meeting day.
                moment = dt.datetime(year, month, day, 14, tzinfo=ET).astimezone(KST)
            except ValueError:
                continue
            output.append(candidate('us_rate', None, None, moment, 'FOMC', f'{name} {day}'))
    return output


def event_match(existing, incoming):
    if existing['series'] != incoming['series']:
        return False
    if incoming['series'] == 'us_rate':
        # For rate meetings, match within 48 hours, including time-zone and schedule corrections.
        a = dt.datetime.fromisoformat(existing['release_kst'])
        b = dt.datetime.fromisoformat(incoming['release_kst'])
        return abs(a - b) <= dt.timedelta(hours=48)
    return existing.get('period') == incoming.get('period') and existing.get('estimate') == incoming.get('estimate')


def reconcile(cal, incoming, now):
    history = []
    events = cal['events']
    min_date = (now - dt.timedelta(days=2)).date()
    max_date = (now + dt.timedelta(days=550)).date()
    for item in sorted(incoming, key=lambda e: e['release_kst']):
        date = dt.date.fromisoformat(item['release_kst'][:10])
        if not min_date <= date <= max_date or item['series'] not in cal['series']:
            continue
        existing = next((e for e in events if event_match(e, item)), None)
        if existing:
            # Don't silently edit already published releases, even if source calendar changes retroactively.
            if dt.datetime.fromisoformat(existing['release_kst']).replace(tzinfo=KST) < now - dt.timedelta(hours=1):
                continue
            if existing['release_kst'] != item['release_kst']:
                history.append({'at': now.isoformat(timespec='seconds'), 'event_id': existing['id'], 'field': 'release_kst',
                                'from': existing['release_kst'], 'to': item['release_kst'], 'source': item['source_url']})
                existing['release_kst'] = item['release_kst']
            if existing.get('tentative') is True and item['schedule_source'] != 'FOMC':
                existing.pop('tentative')
            for k in ('source_url', 'schedule_source'):
                # Keep older manual information unless updating a schedule with official information.
                if existing.get(k) != item[k]:
                    existing[k] = item[k]
                    history.append({'at': now.isoformat(timespec='seconds'), 'event_id': existing['id'],
                                    'field': k, 'to': item[k], 'source': item['source_url']})
        else:
            new = dict(item)
            new['values'] = [{'key': m['key'], 'forecast': None, 'actual': None, 'previous': None}
                             for m in cal['series'][item['series']]['metrics']]
            events.append(new)
            history.append({'at': now.isoformat(timespec='seconds'), 'event_id': new['id'], 'field': 'created',
                            'to': new['release_kst'], 'source': new['source_url']})
    events.sort(key=lambda e: (e['release_kst'], e['id']))
    return history


def main():
    now = dt.datetime.now(KST)
    cal = json.loads(CAL.read_text(encoding='utf-8'))
    all_items, successes = [], []
    for name, url in SOURCES.items():
        try:
            response = requests.get(url, timeout=35, headers=HEADERS)
            response.raise_for_status()
            records = get_fomc_candidates(response.text) if name == 'FOMC' else get_ics_candidates(name, response.text)
            if not records:
                raise ValueError('no matching calendar records (source format might have changed)')
            all_items.extend(records)
            successes.append(name)
            print(f'[schedule] {name}: {len(records)} recognized')
        except Exception as exc:
            print(f'::warning::[schedule] {name}: {exc}')
    if not successes:
        print('::warning::All official schedule sources failed; existing dates preserved and other data jobs will continue')
        return
    changes = reconcile(cal, all_items, now)
    if changes:
        existing_history = json.loads(HISTORY.read_text(encoding='utf-8')) if HISTORY.exists() else []
        HISTORY.write_text(json.dumps(existing_history + changes, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        cal['updated'] = now.isoformat(timespec='minutes')
        CAL.write_text(json.dumps(cal, ensure_ascii=False, indent=1) + '\n', encoding='utf-8')
    print(f'[schedule] sources={successes}, changes={len(changes)}')


if __name__ == '__main__':
    main()
