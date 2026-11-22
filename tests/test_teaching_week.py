import json
import threading
from datetime import date
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from backend import api, db, schedule


@pytest.fixture
def request_api():
    server = api.make_server('127.0.0.1', 0)
    threading.Thread(target=server.serve_forever, daemon=True).start()

    def call(path, body=None):
        request = Request(f'http://127.0.0.1:{server.server_port}/api/{path}',
                          data=json.dumps(body).encode() if body is not None else None,
                          method='PUT' if body is not None else 'GET',
                          headers={'Content-Type': 'application/json'})
        with urlopen(request, timeout=5) as response:
            return json.load(response)

    try:
        yield call
    finally:
        server.shutdown()
        server.server_close()


def test_calibration_changes_labels_not_calendar_dates(request_api):
    sem = db.create_semester('秋', '2026-12-02', '2027-02-28', is_active=1)
    other = db.create_semester('春', '2027-03-01', '2027-06-15')
    cid = db.create_course(sem, '金融')
    db.create_slot(cid, 0, '09:00', '10:30', recurrence_start='2027-01-04')
    sid = db.get_or_create_session(cid, '2027-01-04', week_number=6)
    assert request_api('week?center=2026-12-31')['teaching']['number'] == 5
    old = request_api('week?center=2027-01-04')['week']
    assert request_api(f'semesters/{sem}/teaching-week', {'reference_date': '2027-01-03', 'week_number': 2}) == {
        'week_one_monday': '2026-12-21'}
    assert request_api('week?center=2027-01-03')['teaching']['number'] == 2
    new = request_api('week?center=2027-01-04')
    assert new['teaching']['number'] == 3
    assert new['week'][0]['slots'][0]['week_number'] == 3
    assert [(d['date'], len(d['slots'])) for d in new['week']] == [(d['date'], len(d['slots'])) for d in old]
    assert request_api(f'sessions/{sid}')['session']['week_number'] == 3
    assert request_api(f'courses/{cid}/sessions')['sessions'][0]['week_number'] == 3
    assert db.get_session(sid)['week_number'] == 6
    assert db.get_semester(sem)['start_date'] == '2026-12-02'
    assert db.get_semester(other)['week_one_monday'] is None
    assert not any(d['slots'] for d in request_api('week?center=2026-12-28')['week'])
    db.close_all_connections()
    db.init_db()
    assert schedule.week_anchor(db.get_semester(sem)) == '2026-12-21'


@pytest.mark.parametrize('day,n', [('2026-02-30', 1), ('2026-11-22', 0), ('2026-11-22', 1.5),
                                     ('2026-11-22', True), ('2026-11-22', 999999999)])
def test_invalid_calibration(day, n):
    with pytest.raises(ValueError):
        schedule.calibrated_monday(day, n)


def test_course_and_timetable_save_together(request_api):
    sem = db.create_semester('秋', '2026-09-01', '2027-01-15', is_active=1)
    cid = db.create_course(sem, '课程')
    db.create_slot(cid, 1, '10:00', '12:00', recurrence_end='2026-12-15')
    old = db.list_slots(cid)
    with pytest.raises(HTTPError) as failure:
        request_api(f'courses/{cid}', {'name': '误改', 'slots': [{**old[0], 'end_time': '09:00'}]})
    assert failure.value.code == 400
    assert db.get_course(cid)['name'] == '课程' and db.list_slots(cid) == old
    request_api(f'courses/{cid}', {'name': '新课程', 'slots': [{**old[0], 'weekday': 4, 'location': '302'}]})
    slot = db.list_slots(cid)[0]
    assert db.get_course(cid)['name'] == '新课程'
    assert (slot['weekday'], slot['location'], slot['recurrence_end']) == (4, '302', '2026-12-15')
    assert schedule.build_week_plan(db.get_semester(sem), [db.get_course(cid)], date(2026, 11, 20))[4]['slots']
