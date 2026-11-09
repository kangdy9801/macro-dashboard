import datetime as dt
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

from scripts import update_schedule as sc
from scripts import update_calendar as uc
from scripts.validate_calendar import validate

KST = ZoneInfo('Asia/Seoul')


class AutomationTests(unittest.TestCase):
    def setUp(self):
        self.calendar = json.loads((Path(__file__).resolve().parents[1] / 'data/calendar.json').read_text())
        self.now = dt.datetime(2026, 11, 9, 14, 0, tzinfo=KST)

    def test_bls_ics_dst(self):
        ics = '''BEGIN:VCALENDAR
BEGIN:VEVENT
SUMMARY:Consumer Price Index for January 2027
DTSTART;TZID=America/New_York:20270210T083000
END:VEVENT
BEGIN:VEVENT
SUMMARY:Employment Situation for February 2027
DTSTART:20270305T133000Z
END:VEVENT
END:VCALENDAR'''
        candidates = sc.get_ics_candidates('BLS', ics)
        self.assertEqual(candidates[0]['id'], 'us_cpi-2027-01')
        self.assertEqual(candidates[0]['release_kst'], '2027-02-10T22:30')
        self.assertEqual(candidates[1]['release_kst'], '2027-03-05T22:30')

    def test_bea_gdp_and_pce(self):
        ics = '''BEGIN:VCALENDAR
BEGIN:VEVENT
SUMMARY:Gross Domestic Product\\, 4th Quarter 2026 (Advance Estimate)
DTSTART:20270128T133000Z
END:VEVENT
BEGIN:VEVENT
SUMMARY:Personal Income and Outlays\\, December 2026
DTSTART:20270129T133000Z
END:VEVENT
END:VCALENDAR'''
        rows = sc.get_ics_candidates('BEA', ics)
        self.assertEqual([r['id'] for r in rows], ['us_gdp-2026-Q4a', 'us_pce-2026-12'])

    def test_create_is_idempotent_and_preserves_manual(self):
        item = sc.candidate('us_cpi', '2027-01', None, dt.datetime(2027, 2, 10, 22, 30, tzinfo=KST), 'BLS', 'CPI')
        first = sc.reconcile(self.calendar, [item], self.now)
        second = sc.reconcile(self.calendar, [item], self.now)
        self.assertEqual(len(first), 1)
        self.assertEqual(second, [])
        self.assertEqual(self.calendar['events'][-1]['values'][0]['forecast'], None)
        original = next(e for e in self.calendar['events'] if e['id'] == 'us_cpi-2026-10')
        original['values'][0]['forecast'] = 9.5
        modified = sc.candidate('us_cpi', '2026-10', None, dt.datetime(2026, 11, 11, 22, 30, tzinfo=KST), 'BLS', 'CPI')
        sc.reconcile(self.calendar, [modified], self.now)
        self.assertEqual(original['values'][0]['forecast'], 9.5)
        self.assertEqual(original['release_kst'], '2026-11-11T22:30')

    def test_forecast_preserves_manual_and_records_change(self):
        future = next(e for e in self.calendar['events'] if e['id'] == 'us_cpi-2026-11')
        future['values'][0]['forecast'] = 2.5  # manual; do not overwrite
        future['values'][1]['forecast'] = 0.3
        future['values'][1]['forecast_source'] = 'Forex Factory'
        ff = [
            {'country': 'USD', 'title': 'CPI y/y', 'date': '2026-12-10T13:30:00+00:00', 'forecast': '8.8%'},
            {'country': 'USD', 'title': 'CPI m/m', 'date': '2026-12-10T13:30:00+00:00', 'forecast': '0.4%'}
        ]
        with tempfile.TemporaryDirectory() as d, patch.object(uc, 'get') as getter:
            getter.return_value.json.return_value = ff
            file = str(Path(d) / 'history.json')
            count = uc.fill_ff_forecasts(self.calendar['events'], self.now, file)
            self.assertEqual(count, 1)
            self.assertEqual(future['values'][0]['forecast'], 2.5)
            self.assertEqual(future['values'][1]['forecast'], 0.4)
            self.assertEqual(json.loads(Path(file).read_text())[0]['previous_forecast'], 0.3)
            self.assertEqual(uc.fill_ff_forecasts(self.calendar['events'], self.now, file), 0)

    def test_validate_existing_data_schema(self):
        errors, warnings = validate(self.calendar, self.now)
        self.assertEqual(errors, [])
        self.assertIsInstance(warnings, list)

    def test_fomc_calendar(self):
        html = '<h2>2026 FOMC Meetings</h2><p>December 8-9*</p><h2>2027 FOMC Meetings</h2><p>January 26-27 March 16-17*</p>'
        rows = sc.get_fomc_candidates(html)
        self.assertTrue(any(r['release_kst'].startswith('2027-01-28T04:00') for r in rows))


if __name__ == '__main__':
    unittest.main()
