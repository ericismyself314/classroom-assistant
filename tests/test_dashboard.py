"""The new homepage only shows actionable data from the active semester."""
import json
import threading
import urllib.request

from backend import api as api_mod, db


def test_dashboard_scope_order_and_completion():
    active = db.create_semester('本学期', '2026-09-01', '2027-01-31', is_active=1)
    old = db.create_semester('旧学期', '2026-02-01', '2026-07-01')
    course = db.create_course(active, '设计心理学')
    archived = db.create_course(active, '已归档课程')
    db.update_course(archived, is_archived=1)
    other = db.create_course(old, '旧课程')
    for cid in (other, archived):
        sid = db.get_or_create_session(cid, '2026-10-30')
        db.replace_items(sid, [{'course_id': cid, 'category': 'homework', 'title': '不应显示', 'action_required': True}])
    for day in range(15, 20):
        db.get_or_create_session(course, f'2026-10-{day}')
    sid = db.get_or_create_session(course, '2026-10-19')
    db.replace_items(sid, [
        {'course_id': course, 'category': 'homework', 'title': title, 'action_required': action,
         'normalized_date': due, 'is_completed': completed, 'is_confirmed': confirmed, 'verification_status': status}
        for title, due, action, completed, confirmed, status in [
            ('未定日期', None, True, False, False, 'pending'),
            ('已完成', '2026-10-20', True, True, True, 'verified'),
            ('已确认待完成', '2026-10-23', True, False, True, 'verified'),
            ('先到期', '2026-10-22', True, False, False, 'pending'),
            ('被拒绝', '2026-10-20', True, False, False, 'rejected'),
            ('非待办', '2026-10-20', False, False, False, 'verified'),
        ]
    ])
    result = db.dashboard_overview(active)
    assert [s['session_date'] for s in result['recent_sessions']] == ['2026-10-19', '2026-10-18', '2026-10-17']
    assert all(s['course_name'] == '设计心理学' for s in result['recent_sessions'])
    assert [item['title'] for item in result['items']] == ['先到期', '已确认待完成', '未定日期']
    db.update_item(result['items'][0]['id'], is_completed=1)
    assert '先到期' not in [item['title'] for item in db.dashboard_overview(active)['items']]


def test_dashboard_endpoint_needs_no_model_probe(monkeypatch):
    def no_probe(*args, **kwargs):
        raise AssertionError('Dashboard must not start/probe AI models')
    monkeypatch.setattr(api_mod.whisper_worker, 'gpu_info', no_probe)
    server = api_mod.make_server('127.0.0.1', 0)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        with urllib.request.urlopen(f'http://127.0.0.1:{server.server_address[1]}/api/dashboard') as response:
            assert json.load(response) == {'recent_sessions': [], 'items': []}
    finally:
        server.shutdown()
        server.server_close()
