import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx
from fastapi.testclient import TestClient


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))

import main  # noqa: E402
from main import (  # noqa: E402
    _normalise_groups,
    _normalise_students,
    _normalise_subject_roster,
    _normalise_subjects,
    _paper_student_code,
    _paper_subject_code,
)


class SubjectNormalisationTests(unittest.TestCase):
    def test_normalises_data_envelope(self):
        payload = {
            'data': [
                {'subject_code': 'TH101', 'subject_name': 'ภาษาไทย', 'school_code': '12'},
                {'course_code': 'MA201', 'course_name': 'คณิตศาสตร์'},
            ]
        }
        self.assertEqual(_normalise_subjects(payload), [
            {'code': 'TH101', 'paper_code': 'TH101', 'name': 'ภาษาไทย', 'school_code': '12'},
            {'code': 'MA201', 'paper_code': 'MA201', 'name': 'คณิตศาสตร์', 'school_code': None},
        ])

    def test_supports_nested_subject_and_removes_duplicates(self):
        payload = {'data': {'items': [
            {'subject': {'code': 'EN101', 'name': 'English'}},
            {'code': 'EN101', 'name': 'Duplicate'},
            {'name': 'Missing code'},
        ]}}
        self.assertEqual(_normalise_subjects(payload), [
            {'code': 'EN101', 'paper_code': 'EN101', 'name': 'English', 'school_code': None},
        ])

    def test_maps_long_sakan_subject_codes_to_the_seven_mark_paper_code(self):
        self.assertEqual(_paper_subject_code('สค0200035'), 'สค02035')
        self.assertEqual(_paper_subject_code('สค0200036'), 'สค02036')
        self.assertEqual(_paper_subject_code('สค0200037'), 'สค02037')
        self.assertEqual(_paper_subject_code('สค0200038'), 'สค02038')
        self.assertEqual(_paper_subject_code('พว21001'), 'พว21001')

    def test_maps_sdl_full_student_code_to_the_ten_digit_paper_code(self):
        self.assertEqual(_paper_student_code('12141200006800000001'), '6800000001')
        self.assertEqual(_paper_student_code('6800000001'), '6800000001')
        self.assertEqual(_paper_student_code('STD-001'), 'STD-001')

    def test_normalises_groups_and_students_from_nested_envelopes(self):
        self.assertEqual(_normalise_groups({'data': {'class_groups': [
            {'class_group_id': 7, 'class_group_code': 'ม.1/1', 'class_group_name': 'ห้อง 1'},
        ]}}), [{'id': '7', 'code': 'ม.1/1', 'name': 'ห้อง 1', 'student_count': None}])
        self.assertEqual(_normalise_students({'data': {'members': [
            {'student': {'student_code': '68001', 'first_name': 'สมชาย', 'last_name': 'ใจดี'}},
        ]}}), [{'code': '68001', 'name': 'สมชาย ใจดี'}])
        self.assertEqual(_normalise_subject_roster({'data': [
            {
                'code': '12141200006800000001', 'full_name': 'เด็กหญิงหนึ่ง',
                'group_id': 'g1', 'group_code': '1/1', 'group_name': 'ห้อง 1',
            },
        ]}), [{
            'student_code': '6800000001', 'student_name': 'เด็กหญิงหนึ่ง',
            'group_id': 'g1', 'group_code': '1/1', 'group_name': 'ห้อง 1',
        }])


class SubjectEndpointTests(unittest.TestCase):
    def test_automatic_group_lookup_and_report_use_sdl_api(self):
        async def fetch(path, params):
            if path.endswith('/subjects/TH101/students'):
                return {'data': [
                    {
                        'code': '12141200006800000001', 'full_name': 'เด็กหญิงหนึ่ง',
                        'group_id': 'g1', 'group_code': '1/1', 'group_name': 'ห้อง 1',
                    },
                    {
                        'code': '6800000002', 'full_name': 'เด็กชายสอง',
                        'group_id': 'g1', 'group_code': '1/1', 'group_name': 'ห้อง 1',
                    },
                    {
                        'code': '6800000003', 'full_name': 'เด็กหญิงสาม',
                        'group_id': 'g2', 'group_code': '1/2', 'group_name': 'ห้อง 2',
                    },
                ]}
            raise AssertionError(path)

        scores = {
            ('6800000001', 'g1'): {
                'score': 42.0, 'max_score': 50.0,
                'checked_at': '2026-09-17T10:00:00', 'updated_at': None,
            },
        }
        client = TestClient(main.app)
        with patch.object(main, '_sdl_api_configured', return_value=True), \
                patch.object(main, '_fetch_sdl_json', side_effect=fetch) as api, \
                patch.object(main, '_local_scores', return_value=scores):
            lookup = client.get(
                '/api/subjects/TH101/students/6800000001/class-group',
                params={'term': '1/2569'},
            )
            report = client.get(
                '/api/reports/students',
                params={'subject_code': 'TH101', 'term': '1/2569'},
            )

        self.assertEqual(lookup.status_code, 200)
        self.assertEqual(lookup.json()['source'], 'sdl_api')
        self.assertEqual(lookup.json()['group']['id'], 'g1')
        self.assertEqual(lookup.json()['student']['code'], '6800000001')
        self.assertEqual(report.status_code, 200)
        self.assertEqual(report.json()['source'], 'sdl_api')
        self.assertEqual(report.json()['summary'], {'total': 3, 'checked': 1, 'pending': 2, 'groups': 2})
        self.assertEqual(api.call_count, 2)

    def test_automatic_group_lookup_and_report_summary(self):
        rows = [
            {
                'student_code': '68001', 'student_name': 'เด็กหญิงหนึ่ง',
                'subject_code': 'TH101', 'group_id': 'g1', 'group_name': 'ห้อง 1',
                'checked': True, 'score': 42.0, 'max_score': 50.0,
                'checked_at': '2026-09-17T10:00:00',
            },
            {
                'student_code': '68002', 'student_name': 'เด็กชายสอง',
                'subject_code': 'TH101', 'group_id': 'g1', 'group_name': 'ห้อง 1',
                'checked': False, 'score': None, 'max_score': None, 'checked_at': None,
            },
        ]
        client = TestClient(main.app)
        with patch.object(main, '_sdl_mysql_configured', return_value=True), \
                patch.object(main, '_mysql_subject_roster', return_value=rows) as roster:
            lookup = client.get('/api/subjects/TH101/students/68001/class-group', params={'term': '1/2569'})
            report = client.get('/api/reports/students', params={'subject_code': 'TH101', 'term': '1/2569'})

        self.assertEqual(lookup.status_code, 200)
        self.assertEqual(lookup.json()['group']['id'], 'g1')
        self.assertEqual(lookup.json()['student']['code'], '68001')
        self.assertEqual(report.status_code, 200)
        self.assertEqual(report.json()['summary'], {'total': 2, 'checked': 1, 'pending': 1, 'groups': 1})
        self.assertEqual(roster.call_count, 2)

    def test_score_is_stored_locally_when_writable_api_is_not_configured(self):
        client = TestClient(main.app)
        with patch.object(main, '_store_local_score') as store, \
                patch.object(main, '_sdl_api_configured', return_value=False):
            response = client.post('/api/scores', json={
                'student_code': '68001', 'subject_code': 'TH101',
                'class_group_id': 'g1', 'term': '1/2569',
                'score': 42, 'max_score': 50, 'answers': {'1': 'A'},
            })

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['delivery'], 'stored')
        store.assert_called_once()

    def test_cancel_score_removes_saved_result_for_rechecking(self):
        client = TestClient(main.app)
        with patch.object(main, '_delete_local_score', return_value=True) as delete:
            response = client.delete(
                '/api/scores/TH101/6800000001',
                params={'term': '1/2569', 'class_group_id': 'g1'},
            )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()['deleted'])
        delete.assert_called_once_with('6800000001', 'TH101', 'g1', '1/2569')

    def test_local_score_delete_removes_the_persisted_row(self):
        payload = {
            'student_code': '6800000001', 'subject_code': 'TH101',
            'class_group_id': 'g1', 'term': '1/2569',
            'score': 18, 'max_score': 20, 'answers': {'1': 'A'},
            'checked_at': '2026-09-17T20:35:00',
        }
        with tempfile.TemporaryDirectory() as directory, patch.object(main, 'BASE', Path(directory)):
            main._store_local_score(payload)
            self.assertTrue(main._local_scores('TH101', '1/2569'))
            self.assertTrue(main._delete_local_score('6800000001', 'TH101', 'g1', '1/2569'))
            self.assertEqual(main._local_scores('TH101', '1/2569'), {})

    def test_proxy_sends_auth_and_query_then_returns_stable_shape(self):
        calls = []

        class FakeClient:
            def __init__(self, **kwargs):
                self.options = kwargs

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                return None

            async def request(self, method, url, **kwargs):
                calls.append((method, url, kwargs))
                return httpx.Response(200, json={
                    'data': [{'subject_code': 'SC101', 'subject_name': 'วิทยาศาสตร์'}],
                })

        env = {
            'SDL_SCHOOL_BASE_URL': 'https://sdl.example.test',
            'SDL_SCHOOL_TOKEN': 'sdl_student_secret',
        }
        with patch.dict(os.environ, env), patch.object(main.httpx, 'AsyncClient', FakeClient):
            response = TestClient(main.app).get(
                '/api/students/6800000001/subjects',
                params={'term': '1/2569', 'page': 1, 'per_page': 100},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['subjects'], [
            {'code': 'SC101', 'paper_code': 'SC101', 'name': 'วิทยาศาสตร์', 'school_code': None},
        ])
        self.assertEqual(
            calls[0][1],
            'https://sdl.example.test/api/v1/integrations/student-data/students/6800000001/subjects',
        )
        self.assertEqual(calls[0][2]['params']['term'], '1/2569')
        self.assertEqual(calls[0][2]['headers']['Authorization'], 'Bearer sdl_student_secret')
        self.assertEqual(calls[0][2]['headers']['X-Student-Data-Token'], 'sdl_student_secret')

    def test_group_roster_and_score_proxy_use_configurable_api_paths(self):
        calls = []

        class FakeClient:
            def __init__(self, **kwargs):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                return None

            async def request(self, method, url, **kwargs):
                calls.append((method, url, kwargs))
                if url.endswith('/classes/TH101'):
                    return httpx.Response(200, json={'groups': [{'id': 'g1', 'code': '1/1', 'name': 'ห้อง 1'}]})
                if url.endswith('/classes/g1/members'):
                    return httpx.Response(200, json={'students': [{'student_code': '68001', 'name': 'เด็กหญิงหนึ่ง'}]})
                return httpx.Response(201, json={'id': 'score-1'})

        env = {
            'SDL_SCHOOL_BASE_URL': 'https://sdl.example.test',
            'SDL_SCHOOL_TOKEN': 'secret',
            'SDL_SCHOOL_GROUPS_PATH': '/classes/{subject_code}',
            'SDL_SCHOOL_GROUP_STUDENTS_PATH': '/classes/{group_id}/members',
            'SDL_SCHOOL_SCORES_PATH': '/results/{subject_code}/{group_id}',
        }
        client = TestClient(main.app)
        with patch.dict(os.environ, env), patch.object(main.httpx, 'AsyncClient', FakeClient):
            groups = client.get('/api/subjects/TH101/class-groups', params={'term': '1/2569'})
            students = client.get('/api/class-groups/g1/students', params={
                'term': '1/2569', 'subject_code': 'TH101',
            })
            score = client.post('/api/scores', json={
                'student_code': '68001', 'subject_code': 'TH101', 'class_group_id': 'g1',
                'term': '1/2569', 'score': 42, 'max_score': 50, 'answers': {'1': 'A'},
            })

        self.assertEqual(groups.json()['groups'][0]['id'], 'g1')
        self.assertEqual(students.json()['students'][0]['code'], '68001')
        self.assertTrue(score.json()['ok'])
        self.assertEqual(calls[-1][0], 'POST')
        self.assertEqual(calls[-1][1], 'https://sdl.example.test/results/TH101/g1')
        self.assertEqual(calls[-1][2]['json']['score'], 42.0)


if __name__ == '__main__':
    unittest.main()
