import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Literal
from urllib.parse import quote

import httpx
try:
    import pymysql
    import pymysql.cursors
    MySQLError = pymysql.MySQLError
except ImportError:
    pymysql = None
    MySQLError = Exception
from fastapi import FastAPI, File, HTTPException, Path as ApiPath, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

try:
    from .omr import scan_image_bytes
    from . import storage
except ImportError:  # Allows `uvicorn main:app` when launched inside backend/.
    from omr import scan_image_bytes
    import storage

BASE = Path(__file__).resolve().parent.parent
FRONTEND = BASE / 'frontend'


def _read_env_file(path: Path) -> dict[str, str]:
    """Read simple KEY=VALUE files without adding a runtime dependency."""
    values: dict[str, str] = {}
    try:
        lines = path.read_text(encoding='utf-8').splitlines()
    except (OSError, UnicodeError):
        return values

    for raw_line in lines:
        line = raw_line.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        key, value = line.split('=', 1)
        key = key.strip()
        value = value.strip()
        if not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', key):
            continue
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ('\"', "'"):
            value = value[1:-1]
        elif ' #' in value:
            value = value.split(' #', 1)[0].rstrip()
        values[key] = value
    return values


def _bootstrap_environment() -> None:
    """Load app settings and optionally reuse SDL_school's server DB settings.

    Production credentials remain in SDL_school's existing Laravel .env and are
    never copied to the browser or committed with this application.
    """
    for key, value in _read_env_file(BASE / '.env').items():
        os.environ.setdefault(key, value)

    if os.getenv('OMR_USE_LARAVEL_DB_ENV', '').strip().lower() not in ('1', 'true', 'yes', 'on'):
        return

    configured_path = os.getenv('SDL_LARAVEL_ENV', '').strip()
    candidates = ([Path(configured_path).expanduser()] if configured_path else []) + [
        BASE.parent.parent / 'SDL_school' / 'laravel-app' / '.env',
        BASE.parent.parent / 'SDL_school' / '.env',
    ]
    laravel_values: dict[str, str] = {}
    for candidate in candidates:
        laravel_values = _read_env_file(candidate)
        if laravel_values:
            break

    mapping = {
        'DB_HOST': 'SDL_MYSQL_HOST',
        'DB_PORT': 'SDL_MYSQL_PORT',
        'DB_DATABASE': 'SDL_MYSQL_DATABASE',
        'DB_USERNAME': 'SDL_MYSQL_USER',
        'DB_PASSWORD': 'SDL_MYSQL_PASSWORD',
    }
    for source, destination in mapping.items():
        if source in laravel_values:
            os.environ.setdefault(destination, laravel_values[source])


_bootstrap_environment()

PAPER_SUBJECT_CODE_OVERRIDES = {
    'สค0200035': 'สค02035',
    'สค0200036': 'สค02036',
    'สค0200037': 'สค02037',
    'สค0200038': 'สค02038',
}

app = FastAPI(title='Mobile OMR Scanner', version='0.5.0')

# The normal MAMP/production setup is same-origin and does not need CORS.
# Explicitly opt in only when a separate frontend origin is required.
allowed_origins = [
    origin.strip()
    for origin in os.getenv('OMR_ALLOWED_ORIGINS', '').split(',')
    if origin.strip()
]
if allowed_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=allowed_origins,
        allow_credentials=True,
        allow_methods=['GET', 'POST', 'DELETE'],
        allow_headers=['*'],
    )

@app.get('/api/health')
def health():
    source = 'sdl_api' if _sdl_api_configured() else 'sdl_mysql' if _sdl_mysql_configured() else 'unconfigured'
    if storage.mysql_configured():
        try:
            connection = storage.mysql_connection()
            try:
                with connection.cursor() as cursor:
                    cursor.execute('SELECT 1 AS ready')
                    cursor.fetchone()
            finally:
                connection.close()
        except (OSError, ValueError, MySQLError) as exc:
            raise HTTPException(status_code=503, detail='OMR MySQL database is unavailable') from exc
    return {
        'ok': True,
        'data_source': source,
        'storage': 'mysql' if storage.mysql_configured() else 'sqlite_fallback',
    }


def _first_value(item: dict[str, Any], keys: tuple[str, ...]) -> Any:
    for key in keys:
        value = item.get(key)
        if value is not None and str(value).strip():
            return value
    return None


def _paper_subject_code(code: Any) -> str:
    value = str(code or '').strip()
    return PAPER_SUBJECT_CODE_OVERRIDES.get(value, value)


def _paper_student_code(code: Any) -> str:
    """Return the 10-digit candidate code printed on the OMR sheet.

    SDL import tables store a 20-digit value made from the school prefix and
    the student's 10-digit candidate code.  The paper and student profile URL
    use only the final 10 digits.
    """
    value = str(code or '').strip()
    return value[-10:] if value.isdigit() and len(value) > 10 else value


def _resolve_observed_student(observed_code: str, rows: list[dict[str, Any]]) -> tuple[dict[str, Any], str]:
    """Resolve an imperfect OMR code only when the roster has one safe winner."""
    observed = str(observed_code or '').strip().upper()
    if not re.fullmatch(r'[A-Z0-9?._-]{5,50}', observed):
        raise HTTPException(status_code=422, detail='Observed student code is invalid')

    exact = [row for row in rows if _paper_student_code(row.get('student_code')) == observed]
    if len(exact) == 1:
        return exact[0], 'exact'

    known = sum(character != '?' for character in observed)
    if len(observed) != 10 or known < 5:
        raise HTTPException(status_code=404, detail='Student code is too incomplete to resolve safely')

    ranked: list[tuple[int, int, dict[str, Any]]] = []
    for row in rows:
        candidate = _paper_student_code(row.get('student_code')).upper()
        if len(candidate) != len(observed):
            continue
        mismatches = sum(
            1 for actual, expected in zip(candidate, observed)
            if expected != '?' and actual != expected
        )
        if mismatches <= 1:
            unknowns = observed.count('?')
            ranked.append((mismatches * 10 + unknowns, mismatches, row))

    ranked.sort(key=lambda item: item[0])
    if not ranked:
        raise HTTPException(status_code=404, detail='Student is not registered for this subject')
    if len(ranked) > 1 and ranked[0][0] == ranked[1][0]:
        raise HTTPException(status_code=409, detail='Student code matches more than one roster entry')
    return ranked[0][2], 'recovered'


def _normalise_subjects(payload: Any) -> list[dict[str, Any]]:
    """Accept common SDL response envelopes and expose a stable UI shape."""
    rows = payload
    if isinstance(payload, dict):
        for key in ('data', 'subjects', 'items', 'results'):
            candidate = payload.get(key)
            if isinstance(candidate, list):
                rows = candidate
                break
            if isinstance(candidate, dict):
                for nested_key in ('data', 'subjects', 'items', 'results'):
                    nested = candidate.get(nested_key)
                    if isinstance(nested, list):
                        rows = nested
                        break
                if isinstance(rows, list):
                    break

    if not isinstance(rows, list):
        return []

    subjects = []
    seen = set()
    for row in rows:
        if not isinstance(row, dict):
            continue
        code = _first_value(row, (
            'subject_code', 'course_code', 'code', 'subjectCode', 'courseCode',
        ))
        name = _first_value(row, (
            'subject_name', 'course_name', 'name', 'subjectName', 'courseName',
            'name_th', 'title',
        ))
        school_code = _first_value(row, (
            'school_code', 'institution_code', 'schoolCode', 'institutionCode',
        ))
        if code is None and isinstance(row.get('subject'), dict):
            code = _first_value(row['subject'], ('code', 'subject_code', 'subjectCode'))
            name = name or _first_value(row['subject'], ('name', 'subject_name', 'subjectName', 'name_th'))
        if school_code is None and isinstance(row.get('school'), dict):
            school_code = _first_value(row['school'], ('code', 'school_code', 'schoolCode'))
        if code is None:
            continue
        code = str(code).strip()
        name = str(name).strip() if name is not None else code
        if code in seen:
            continue
        seen.add(code)
        subjects.append({
            'code': code,
            'paper_code': _paper_subject_code(code),
            'name': name,
            'school_code': str(school_code).strip() if school_code is not None else None,
        })
    return subjects


def _payload_rows(payload: Any, keys: tuple[str, ...]) -> list[Any]:
    """Find a list in common API envelopes without coupling the UI to SDL's shape."""
    if isinstance(payload, list):
        return payload
    if not isinstance(payload, dict):
        return []
    for key in keys:
        candidate = payload.get(key)
        if isinstance(candidate, list):
            return candidate
    for envelope in ('data', 'result', 'payload'):
        candidate = payload.get(envelope)
        if isinstance(candidate, list):
            return candidate
        if isinstance(candidate, dict):
            rows = _payload_rows(candidate, keys)
            if rows:
                return rows
    return []


def _normalise_groups(payload: Any) -> list[dict[str, Any]]:
    rows = _payload_rows(payload, ('groups', 'class_groups', 'sections', 'items', 'results'))
    groups: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in rows:
        if not isinstance(row, dict):
            continue
        group_id = _first_value(row, (
            'id', 'group_id', 'class_group_id', 'section_id', 'groupId',
            'classGroupId', 'sectionId', 'code', 'group_code', 'section_code',
        ))
        code = _first_value(row, (
            'code', 'group_code', 'class_group_code', 'section_code',
            'groupCode', 'classGroupCode', 'sectionCode', 'name',
        ))
        name = _first_value(row, (
            'name', 'group_name', 'class_group_name', 'section_name',
            'groupName', 'classGroupName', 'sectionName', 'title',
        ))
        if group_id is None and code is None:
            continue
        group_id = str(group_id if group_id is not None else code).strip()
        if group_id in seen:
            continue
        seen.add(group_id)
        groups.append({
            'id': group_id,
            'code': str(code if code is not None else group_id).strip(),
            'name': str(name if name is not None else code or group_id).strip(),
            'student_count': row.get('student_count'),
        })
    return groups


def _normalise_students(payload: Any) -> list[dict[str, Any]]:
    rows = _payload_rows(payload, ('students', 'members', 'enrollments', 'items', 'results'))
    students: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in rows:
        if not isinstance(row, dict):
            continue
        source = row.get('student') if isinstance(row.get('student'), dict) else row
        code = _first_value(source, (
            'student_code', 'candidate_id', 'student_id', 'code', 'username',
            'studentCode', 'candidateId', 'studentId',
        ))
        if code is None:
            continue
        code = _paper_student_code(code)
        if code in seen:
            continue
        seen.add(code)
        full_name = _first_value(source, (
            'full_name', 'student_name', 'display_name', 'name',
            'fullName', 'studentName', 'displayName',
        ))
        if full_name is None:
            first_name = _first_value(source, ('first_name', 'firstname', 'firstName', 'name_th'))
            last_name = _first_value(source, ('last_name', 'lastname', 'lastName', 'surname'))
            full_name = ' '.join(str(value).strip() for value in (first_name, last_name) if value)
        students.append({'code': code, 'name': str(full_name or code).strip()})
    return students


def _normalise_subject_roster(payload: Any) -> list[dict[str, Any]]:
    """Normalise a subject-wide roster that includes each student's group."""
    rows = _payload_rows(payload, ('students', 'members', 'enrollments', 'items', 'results'))
    roster: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for row in rows:
        if not isinstance(row, dict):
            continue
        student = row.get('student') if isinstance(row.get('student'), dict) else row
        group = row.get('group') if isinstance(row.get('group'), dict) else row
        code = _first_value(student, (
            'student_code', 'candidate_id', 'student_id', 'code', 'username',
            'studentCode', 'candidateId', 'studentId',
        ))
        group_id = _first_value(group, (
            'group_id', 'class_group_id', 'section_id', 'groupId',
            'classGroupId', 'sectionId', 'id', 'group_code', 'class_group_code',
        ))
        if code is None or group_id is None:
            continue
        code = _paper_student_code(code)
        group_id = str(group_id).strip()
        if not code or not group_id or (code, group_id) in seen:
            continue
        seen.add((code, group_id))
        full_name = _first_value(student, (
            'full_name', 'student_name', 'display_name', 'name',
            'fullName', 'studentName', 'displayName',
        ))
        if full_name is None:
            first_name = _first_value(student, ('first_name', 'firstname', 'firstName', 'name_th'))
            last_name = _first_value(student, ('last_name', 'lastname', 'lastName', 'surname'))
            full_name = ' '.join(str(value).strip() for value in (first_name, last_name) if value)
        group_code = _first_value(group, (
            'group_code', 'class_group_code', 'section_code', 'groupCode',
            'classGroupCode', 'sectionCode', 'code',
        ))
        group_name = _first_value(group, (
            'group_name', 'class_group_name', 'section_name', 'groupName',
            'classGroupName', 'sectionName', 'name', 'title',
        ))
        roster.append({
            'student_code': code,
            'student_name': str(full_name or code).strip(),
            'group_id': group_id,
            'group_code': str(group_code or group_id).strip(),
            'group_name': str(group_name or group_code or group_id).strip(),
        })
    return roster


def _sdl_api_configured() -> bool:
    return bool(os.getenv('SDL_SCHOOL_BASE_URL', '').strip() and os.getenv('SDL_SCHOOL_TOKEN', '').strip())


def _sdl_mysql_configured() -> bool:
    return bool(os.getenv('SDL_MYSQL_DATABASE', '').strip())


def _mysql_connection():
    if not _sdl_mysql_configured() or pymysql is None:
        raise HTTPException(status_code=503, detail='SDL_school data source is not configured')
    try:
        return pymysql.connect(
            host=os.getenv('SDL_MYSQL_HOST', '127.0.0.1'),
            port=int(os.getenv('SDL_MYSQL_PORT', '3306')),
            user=os.getenv('SDL_MYSQL_USER', 'root'),
            password=os.getenv('SDL_MYSQL_PASSWORD', ''),
            database=os.environ['SDL_MYSQL_DATABASE'].strip(),
            charset='utf8mb4',
            cursorclass=pymysql.cursors.DictCursor,
            connect_timeout=5,
            read_timeout=12,
            write_timeout=12,
        )
    except (MySQLError, ValueError) as exc:
        raise HTTPException(status_code=503, detail='Cannot connect to SDL_school database') from exc


def _safe_table(name: str) -> str:
    if not re.fullmatch(r'[A-Za-z0-9_]+', name):
        raise HTTPException(status_code=500, detail='Invalid SDL_school import table')
    return f'`{name}`'


def _mysql_batch_key(connection) -> str:
    district_id = int(os.getenv('SDL_DISTRICT_ID', '1'))
    with connection.cursor() as cursor:
        cursor.execute(
            """SELECT batch_key FROM import_history
               WHERE status = 'success' AND district_id = %s AND batch_key IS NOT NULL
               ORDER BY id DESC LIMIT 1""",
            (district_id,),
        )
        row = cursor.fetchone()
    if not row or not row.get('batch_key'):
        raise HTTPException(status_code=503, detail='SDL_school has no successful import batch')
    return str(row['batch_key'])


def _mysql_tables(connection, batch_key: str, suffix: str) -> list[str]:
    pattern = f'db_{batch_key}_%_{suffix}'
    with connection.cursor() as cursor:
        cursor.execute(
            """SELECT table_name AS import_table FROM information_schema.tables
               WHERE table_schema = DATABASE() AND table_name LIKE %s
               ORDER BY table_name""",
            (pattern,),
        )
        return [str(row['import_table']) for row in cursor.fetchall()]


def _term_variants(term: str) -> tuple[str, ...]:
    semester, year = term.split('/', 1)
    short_year = year[-2:]
    return term, f'{short_year}/{semester}', f'{semester}/{short_year}'


def _mysql_subjects(term: str) -> list[dict[str, Any]]:
    connection = _mysql_connection()
    try:
        batch_key = _mysql_batch_key(connection)
        subject_tables = _mysql_tables(connection, batch_key, 'subject')
        grade_tables = _mysql_tables(connection, batch_key, 'grade')
        variants = _term_variants(term)
        registered_codes: set[str] = set()
        with connection.cursor() as cursor:
            placeholders = ','.join(['%s'] * len(variants))
            for table in grade_tables:
                cursor.execute(
                    f'SELECT DISTINCT TRIM(sub_code) AS code FROM {_safe_table(table)} '
                    f'WHERE semestry IN ({placeholders}) AND TRIM(sub_code) <> %s',
                    (*variants, ''),
                )
                registered_codes.update(str(row['code']).strip() for row in cursor.fetchall() if row.get('code'))

            subjects: dict[str, dict[str, Any]] = {}
            for table in subject_tables:
                cursor.execute(
                    f'SELECT TRIM(sub_code) AS code, TRIM(sub_name) AS name FROM {_safe_table(table)} '
                    'WHERE TRIM(sub_code) <> %s ORDER BY sub_code',
                    ('',),
                )
                for row in cursor.fetchall():
                    code = str(row.get('code') or '').strip()
                    if code and (not registered_codes or code in registered_codes):
                        subjects.setdefault(code, {
                            'code': code,
                            'paper_code': _paper_subject_code(code),
                            'name': str(row.get('name') or code).strip(),
                            'school_code': os.getenv('SDL_SCHOOL_CODE', '').strip() or None,
                        })
        return list(subjects.values())
    except MySQLError as exc:
        raise HTTPException(status_code=502, detail='Cannot read subjects from SDL_school') from exc
    finally:
        connection.close()


def _mysql_groups(subject_code: str, term: str) -> list[dict[str, Any]]:
    connection = _mysql_connection()
    try:
        batch_key = _mysql_batch_key(connection)
        grade_tables = _mysql_tables(connection, batch_key, 'grade')
        group_tables = _mysql_tables(connection, batch_key, 'group')
        variants = _term_variants(term)
        group_names: dict[str, str] = {}
        groups: set[str] = set()
        with connection.cursor() as cursor:
            for table in group_tables:
                cursor.execute(
                    f'SELECT TRIM(grp_code) AS code, TRIM(grp_name) AS name FROM {_safe_table(table)}'
                )
                for row in cursor.fetchall():
                    code = str(row.get('code') or '').strip()
                    if code:
                        group_names[code] = str(row.get('name') or code).strip()
            placeholders = ','.join(['%s'] * len(variants))
            for table in grade_tables:
                cursor.execute(
                    f'SELECT DISTINCT TRIM(grp_code) AS code FROM {_safe_table(table)} '
                    f'WHERE TRIM(sub_code) = %s AND semestry IN ({placeholders}) AND TRIM(grp_code) <> %s',
                    (subject_code, *variants, ''),
                )
                groups.update(str(row['code']).strip() for row in cursor.fetchall() if row.get('code'))
        return [{'id': code, 'code': code, 'name': group_names.get(code, code)} for code in sorted(groups)]
    except MySQLError as exc:
        raise HTTPException(status_code=502, detail='Cannot read class groups from SDL_school') from exc
    finally:
        connection.close()


def _mysql_group_students(group_id: str, subject_code: str, term: str) -> list[dict[str, Any]]:
    connection = _mysql_connection()
    try:
        batch_key = _mysql_batch_key(connection)
        grade_tables = _mysql_tables(connection, batch_key, 'grade')
        student_tables = {table.rsplit('_', 1)[0]: table for table in _mysql_tables(connection, batch_key, 'student')}
        variants = _term_variants(term)
        students: dict[str, dict[str, str]] = {}
        with connection.cursor() as cursor:
            placeholders = ','.join(['%s'] * len(variants))
            for grade_table in grade_tables:
                prefix = grade_table.rsplit('_', 1)[0]
                student_table = student_tables.get(prefix)
                if not student_table:
                    continue
                cursor.execute(
                    f'''SELECT DISTINCT TRIM(g.std_code) AS code,
                               TRIM(CONCAT_WS(' ', s.prename, s.name, s.surname)) AS name
                        FROM {_safe_table(grade_table)} g
                        JOIN {_safe_table(student_table)} s
                          ON TRIM(s.std_code) = TRIM(g.std_code)
                        WHERE TRIM(g.grp_code) = %s AND TRIM(g.sub_code) = %s
                          AND g.semestry IN ({placeholders})''',
                    (group_id, subject_code, *variants),
                )
                for row in cursor.fetchall():
                    code = _paper_student_code(row.get('code'))
                    if code:
                        students[code] = {'code': code, 'name': str(row.get('name') or code).strip()}
        return sorted(students.values(), key=lambda item: item['code'])
    except MySQLError as exc:
        raise HTTPException(status_code=502, detail='Cannot read group roster from SDL_school') from exc
    finally:
        connection.close()


def _local_scores(subject_code: str, term: str) -> dict[tuple[str, str], dict[str, Any]]:
    try:
        rows = storage.scores_for_subject(BASE, subject_code, term)
        return {(_paper_student_code(key[0]), key[1]): value for key, value in rows.items()}
    except (OSError, ValueError, MySQLError) as exc:
        if storage.mysql_configured():
            raise HTTPException(status_code=503, detail='Cannot read the OMR MySQL database') from exc
        return {}


def _mysql_subject_roster(
    subject_code: str,
    term: str,
    *,
    group_id: str | None = None,
    student_code: str | None = None,
) -> list[dict[str, Any]]:
    """Return the registered roster for one subject, enriched with saved OMR scores."""
    connection = _mysql_connection()
    try:
        batch_key = _mysql_batch_key(connection)
        grade_tables = _mysql_tables(connection, batch_key, 'grade')
        student_tables = {table.rsplit('_', 1)[0]: table for table in _mysql_tables(connection, batch_key, 'student')}
        group_tables = _mysql_tables(connection, batch_key, 'group')
        variants = _term_variants(term)
        group_names: dict[str, str] = {}
        roster: dict[tuple[str, str], dict[str, Any]] = {}
        with connection.cursor() as cursor:
            for table in group_tables:
                cursor.execute(
                    f'SELECT TRIM(grp_code) AS code, TRIM(grp_name) AS name FROM {_safe_table(table)}'
                )
                for row in cursor.fetchall():
                    code = str(row.get('code') or '').strip()
                    if code:
                        group_names[code] = str(row.get('name') or code).strip()

            placeholders = ','.join(['%s'] * len(variants))
            for grade_table in grade_tables:
                student_table = student_tables.get(grade_table.rsplit('_', 1)[0])
                if not student_table:
                    continue
                conditions = [
                    'TRIM(g.sub_code) = %s',
                    f'g.semestry IN ({placeholders})',
                    "TRIM(g.grp_code) <> ''",
                ]
                params: list[Any] = [subject_code, *variants]
                if group_id:
                    conditions.append('TRIM(g.grp_code) = %s')
                    params.append(group_id)
                if student_code:
                    candidate_code = _paper_student_code(student_code)
                    if candidate_code.isdigit() and len(candidate_code) == 10:
                        conditions.append('(TRIM(g.std_code) = %s OR RIGHT(TRIM(g.std_code), 10) = %s)')
                        params.extend([student_code, candidate_code])
                    else:
                        conditions.append('TRIM(g.std_code) = %s')
                        params.append(student_code)
                cursor.execute(
                    f'''SELECT DISTINCT TRIM(g.std_code) AS student_code,
                               TRIM(CONCAT_WS(' ', s.prename, s.name, s.surname)) AS student_name,
                               TRIM(g.grp_code) AS group_id
                        FROM {_safe_table(grade_table)} g
                        JOIN {_safe_table(student_table)} s
                          ON TRIM(s.std_code) = TRIM(g.std_code)
                        WHERE {' AND '.join(conditions)}''',
                    tuple(params),
                )
                for row in cursor.fetchall():
                    code = _paper_student_code(row.get('student_code'))
                    current_group = str(row.get('group_id') or '').strip()
                    if not code or not current_group:
                        continue
                    roster[(code, current_group)] = {
                        'student_code': code,
                        'student_name': str(row.get('student_name') or code).strip(),
                        'subject_code': subject_code,
                        'group_id': current_group,
                        'group_name': group_names.get(current_group, current_group),
                    }
    except MySQLError as exc:
        raise HTTPException(status_code=502, detail='Cannot read subject roster from SDL_school') from exc
    finally:
        connection.close()

    scores = _local_scores(subject_code, term)
    result = []
    for key, row in roster.items():
        score = scores.get(key)
        result.append({
            **row,
            'checked': score is not None,
            'score': score.get('score') if score else None,
            'max_score': score.get('max_score') if score else None,
            'checked_at': (score.get('checked_at') or score.get('updated_at')) if score else None,
        })
    return sorted(result, key=lambda item: (item['group_name'], item['student_code']))


async def _api_subject_roster(
    subject_code: str,
    term: str,
    *,
    student_code: str | None = None,
) -> list[dict[str, Any]]:
    """Build a subject roster from the read-only SDL integration API."""
    roster_path = _configured_path(
        'SDL_SCHOOL_SUBJECT_STUDENTS_PATH',
        '/api/v1/integrations/student-data/subjects/{subject_code}/students',
        subject_code=subject_code,
    )
    payload = await _fetch_sdl_json(roster_path, {'term': term})
    api_rows = _normalise_subject_roster(payload)
    target_code = _paper_student_code(student_code) if student_code else None
    scores = _local_scores(subject_code, term)
    roster: dict[tuple[str, str], dict[str, Any]] = {}
    for row in api_rows:
        code = row['student_code']
        group_id = row['group_id']
        if target_code and code != target_code:
            continue
        key = (code, group_id)
        score = scores.get(key)
        roster[key] = {
            **row,
            'subject_code': subject_code,
            'checked': score is not None,
            'score': score.get('score') if score else None,
            'max_score': score.get('max_score') if score else None,
            'checked_at': (score.get('checked_at') or score.get('updated_at')) if score else None,
        }
    return sorted(roster.values(), key=lambda item: (item['group_name'], item['student_code']))


def _store_local_score(payload: dict[str, Any]) -> None:
    try:
        storage.store_score(BASE, payload)
    except (OSError, ValueError, MySQLError) as exc:
        raise HTTPException(status_code=503, detail='Cannot save to the OMR database') from exc


def _delete_local_score(student_code: str, subject_code: str, class_group_id: str, term: str) -> bool:
    candidate_code = _paper_student_code(student_code)
    try:
        return storage.delete_score(
            BASE, student_code, candidate_code, subject_code, class_group_id, term,
        )
    except (OSError, ValueError, MySQLError) as exc:
        if storage.mysql_configured():
            raise HTTPException(status_code=503, detail='Cannot update the OMR MySQL database') from exc
        return False


async def _request_sdl_json(
    method: Literal['GET', 'POST'],
    path: str,
    *,
    params: dict[str, Any] | None = None,
    json: dict[str, Any] | None = None,
) -> Any:
    base_url = os.getenv('SDL_SCHOOL_BASE_URL', '').strip().rstrip('/')
    token = os.getenv('SDL_SCHOOL_TOKEN', '').strip()
    if not base_url or not token:
        raise HTTPException(
            status_code=503,
            detail='SDL_school is not configured (SDL_SCHOOL_BASE_URL / SDL_SCHOOL_TOKEN)',
        )

    if token.lower().startswith('bearer '):
        token = token[7:].strip()
    url = f'{base_url}/{path.lstrip("/")}'
    try:
        async with httpx.AsyncClient(timeout=12.0, follow_redirects=False) as client:
            response = await client.request(
                method,
                url,
                params=params or {},
                json=json,
                headers={
                    'Accept': 'application/json',
                    'Authorization': f'Bearer {token}',
                    # Plesk/shared-hosting proxies can strip Authorization before
                    # PHP-FPM. SDL_school accepts this backend-only fallback.
                    'X-Student-Data-Token': token,
                },
            )
    except httpx.TimeoutException as exc:
        raise HTTPException(status_code=504, detail='SDL_school request timed out') from exc
    except httpx.RequestError as exc:
        raise HTTPException(status_code=502, detail='Cannot connect to SDL_school') from exc

    if response.status_code >= 400:
        status_code = response.status_code if response.status_code in (400, 404, 409, 422, 429) else 502
        raise HTTPException(status_code=status_code, detail=f'SDL_school returned HTTP {response.status_code}')
    if response.status_code == 204 or not response.content:
        return {}
    try:
        return response.json()
    except ValueError as exc:
        raise HTTPException(status_code=502, detail='SDL_school returned invalid JSON') from exc


async def _fetch_sdl_json(path: str, params: dict[str, Any]) -> Any:
    return await _request_sdl_json('GET', path, params=params)


def _configured_path(env_name: str, default: str, **values: str) -> str:
    template = os.getenv(env_name, default).strip()
    try:
        return template.format(**{key: quote(str(value), safe='') for key, value in values.items()})
    except KeyError as exc:
        raise HTTPException(status_code=500, detail=f'Invalid {env_name} path template') from exc


class ScoreResult(BaseModel):
    student_code: str = Field(min_length=1, max_length=50, pattern=r'^[A-Za-z0-9._-]+$')
    subject_code: str = Field(min_length=1, max_length=50)
    class_group_id: str = Field(min_length=1, max_length=100)
    term: str = Field(pattern=r'^\d{1,2}/\d{4}$')
    score: float = Field(ge=0)
    max_score: float = Field(gt=0)
    answers: dict[str, Literal['A', 'B', 'C', 'D'] | None] = Field(default_factory=dict)
    scan_quality: dict[str, Any] = Field(default_factory=dict)
    review_count: int = Field(default=0, ge=0, le=100)
    corrected_count: int = Field(default=0, ge=0, le=100)
    checked_at: datetime | None = None


class AnswerKeyPayload(BaseModel):
    subject_code: str = Field(min_length=1, max_length=50)
    paper_subject_code: str = Field(min_length=1, max_length=50)
    subject_name: str = Field(default='', max_length=255)
    term: str = Field(pattern=r'^\d{1,2}/\d{4}$')
    school_code: str = Field(min_length=1, max_length=50)
    answers: dict[str, str]


def _validated_answer_key(payload: AnswerKeyPayload) -> dict[str, Any]:
    answers: dict[str, str] = {}
    for question, choice in payload.answers.items():
        try:
            number = int(question)
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=422, detail='Answer-key question numbers must be integers') from exc
        value = str(choice).strip().upper()
        if number < 1 or number > 100 or value not in ('A', 'B', 'C', 'D'):
            raise HTTPException(status_code=422, detail='Answer key must contain questions 1-100 with A, B, C or D')
        answers[str(number)] = value
    if not answers:
        raise HTTPException(status_code=422, detail='Answer key cannot be empty')
    highest = max(int(question) for question in answers)
    missing = [number for number in range(1, highest + 1) if str(number) not in answers]
    if missing:
        raise HTTPException(status_code=422, detail=f'Answer key is missing question {missing[0]}')
    data = payload.model_dump()
    data['answers'] = answers
    return data


def _storage_error(exc: Exception) -> HTTPException:
    return HTTPException(status_code=503, detail='Cannot connect to the OMR results database')


@app.get('/api/answer-keys')
def answer_keys(term: str = Query('1/2569', pattern=r'^\d{1,2}/\d{4}$')):
    try:
        rows = storage.list_answer_keys(BASE, term)
    except (OSError, ValueError, MySQLError) as exc:
        raise _storage_error(exc) from exc
    return {
        'term': term,
        'answer_keys': rows,
        'total': len(rows),
        'storage': 'mysql' if storage.mysql_configured() else 'sqlite_fallback',
    }


@app.get('/api/answer-keys/{subject_code}')
def answer_key(
    subject_code: str = ApiPath(..., min_length=1, max_length=50),
    term: str = Query('1/2569', pattern=r'^\d{1,2}/\d{4}$'),
):
    try:
        row = storage.get_answer_key(BASE, subject_code, term)
    except (OSError, ValueError, MySQLError) as exc:
        raise _storage_error(exc) from exc
    if row is None:
        raise HTTPException(status_code=404, detail='Answer key was not found')
    return row


@app.put('/api/answer-keys/{subject_code}')
def save_answer_key(
    payload: AnswerKeyPayload,
    subject_code: str = ApiPath(..., min_length=1, max_length=50),
):
    if payload.subject_code.strip() != subject_code.strip():
        raise HTTPException(status_code=422, detail='Subject code in path and body must match')
    data = _validated_answer_key(payload)
    try:
        stored = storage.store_answer_key(BASE, data)
    except (OSError, ValueError, MySQLError) as exc:
        raise _storage_error(exc) from exc
    return {
        'ok': True,
        'answer_key': stored,
        'storage': 'mysql' if storage.mysql_configured() else 'sqlite_fallback',
    }


@app.delete('/api/answer-keys/{subject_code}')
def remove_answer_key(
    subject_code: str = ApiPath(..., min_length=1, max_length=50),
    term: str = Query('1/2569', pattern=r'^\d{1,2}/\d{4}$'),
):
    try:
        deleted = storage.delete_answer_key(BASE, subject_code, term)
    except (OSError, ValueError, MySQLError) as exc:
        raise _storage_error(exc) from exc
    return {'ok': True, 'deleted': deleted, 'subject_code': subject_code, 'term': term}


@app.get('/api/subjects')
async def all_subjects(
    term: str | None = Query(None, pattern=r'^\d{1,2}/\d{4}$'),
    per_page: int = Query(100, ge=1, le=100),
):
    """Return every subject for the current term without requiring a student code."""
    current_term = term or os.getenv('SDL_CURRENT_TERM', '1/2569').strip()
    if _sdl_mysql_configured() and not _sdl_api_configured():
        subjects = _mysql_subjects(current_term)
        return {'term': current_term, 'subjects': subjects, 'total': len(subjects), 'source': 'sdl_mysql'}
    path = os.getenv(
        'SDL_SCHOOL_SUBJECTS_PATH',
        '/api/v1/integrations/student-data/subjects',
    ).strip()
    collected: list[dict[str, Any]] = []
    seen: set[str] = set()
    for page in range(1, 51):
        payload = await _fetch_sdl_json(
            path,
            {'term': current_term, 'page': page, 'per_page': per_page},
        )
        page_subjects = _normalise_subjects(payload)
        for subject in page_subjects:
            if subject['code'] not in seen:
                seen.add(subject['code'])
                collected.append(subject)
        if len(page_subjects) < per_page:
            break
    return {'term': current_term, 'subjects': collected, 'total': len(collected)}


@app.get('/api/students/{student_code}/subjects')
async def student_subjects(
    student_code: str = ApiPath(..., min_length=1, max_length=50, pattern=r'^[A-Za-z0-9._-]+$'),
    term: str = Query('1/2569', pattern=r'^\d{1,2}/\d{4}$'),
    page: int = Query(1, ge=1),
    per_page: int = Query(100, ge=1, le=100),
):
    """Backward-compatible per-student subject lookup."""
    payload = await _fetch_sdl_json(
        f'/api/v1/integrations/student-data/students/{quote(student_code, safe="")}/subjects',
        {'term': term, 'page': page, 'per_page': per_page},
    )

    return {
        'student_code': student_code,
        'term': term,
        'subjects': _normalise_subjects(payload),
    }


@app.get('/api/subjects/{subject_code}/class-groups')
async def subject_class_groups(
    subject_code: str = ApiPath(..., min_length=1, max_length=50),
    term: str = Query('1/2569', pattern=r'^\d{1,2}/\d{4}$'),
):
    if _sdl_mysql_configured() and not _sdl_api_configured():
        groups = _mysql_groups(subject_code, term)
        return {'subject_code': subject_code, 'term': term, 'groups': groups, 'total': len(groups), 'source': 'sdl_mysql'}
    path = _configured_path(
        'SDL_SCHOOL_GROUPS_PATH',
        '/api/v1/integrations/student-data/subjects/{subject_code}/class-groups',
        subject_code=subject_code,
    )
    payload = await _fetch_sdl_json(path, {'term': term})
    groups = _normalise_groups(payload)
    return {'subject_code': subject_code, 'term': term, 'groups': groups, 'total': len(groups)}


@app.get('/api/class-groups/{group_id}/students')
async def class_group_students(
    group_id: str = ApiPath(..., min_length=1, max_length=100),
    term: str = Query('1/2569', pattern=r'^\d{1,2}/\d{4}$'),
    subject_code: str = Query(..., min_length=1, max_length=50),
):
    if _sdl_mysql_configured() and not _sdl_api_configured():
        students = _mysql_group_students(group_id, subject_code, term)
        return {
            'group_id': group_id,
            'subject_code': subject_code,
            'term': term,
            'students': students,
            'total': len(students),
            'source': 'sdl_mysql',
        }
    path = _configured_path(
        'SDL_SCHOOL_GROUP_STUDENTS_PATH',
        '/api/v1/integrations/student-data/class-groups/{group_id}/students',
        group_id=group_id,
        subject_code=subject_code,
    )
    payload = await _fetch_sdl_json(path, {'term': term, 'subject_code': subject_code})
    students = _normalise_students(payload)
    return {
        'group_id': group_id,
        'subject_code': subject_code,
        'term': term,
        'students': students,
        'total': len(students),
    }


@app.get('/api/subjects/{subject_code}/resolve-student')
async def resolve_subject_student(
    subject_code: str = ApiPath(..., min_length=1, max_length=50),
    observed_code: str = Query(..., min_length=5, max_length=50),
    term: str = Query('1/2569', pattern=r'^\d{1,2}/\d{4}$'),
):
    """Resolve an exact or partly unreadable OMR code against one subject roster."""
    if _sdl_api_configured():
        rows = await _api_subject_roster(subject_code, term)
        source = 'sdl_api'
    elif _sdl_mysql_configured():
        rows = _mysql_subject_roster(subject_code, term)
        source = 'sdl_mysql'
    else:
        raise HTTPException(status_code=503, detail='SDL_school data source is not configured')
    row, resolution = _resolve_observed_student(observed_code, rows)
    return {
        'student': {'code': row['student_code'], 'name': row['student_name']},
        'group': {'id': row['group_id'], 'code': row['group_id'], 'name': row['group_name']},
        'observed_code': observed_code,
        'resolution': resolution,
        'subject_code': subject_code,
        'term': term,
        'source': source,
    }


@app.get('/api/subjects/{subject_code}/students/{student_code}/class-group')
async def subject_student_class_group(
    subject_code: str = ApiPath(..., min_length=1, max_length=50),
    student_code: str = ApiPath(..., min_length=1, max_length=50, pattern=r'^[A-Za-z0-9._-]+$'),
    term: str = Query('1/2569', pattern=r'^\d{1,2}/\d{4}$'),
):
    """Resolve a student's class group from the selected subject and current term."""
    if _sdl_api_configured():
        rows = await _api_subject_roster(subject_code, term, student_code=student_code)
        source = 'sdl_api'
    elif _sdl_mysql_configured():
        rows = _mysql_subject_roster(subject_code, term, student_code=student_code)
        source = 'sdl_mysql'
    else:
        raise HTTPException(status_code=503, detail='SDL_school data source is not configured')
    if not rows:
        raise HTTPException(status_code=404, detail='Student is not registered for this subject')
    row = rows[0]
    return {
        'student': {'code': row['student_code'], 'name': row['student_name']},
        'group': {'id': row['group_id'], 'code': row['group_id'], 'name': row['group_name']},
        'subject_code': subject_code,
        'term': term,
        'matches': len(rows),
        'source': source,
    }


@app.get('/api/reports/students')
async def student_check_report(
    subject_code: str = Query(..., min_length=1, max_length=50),
    term: str = Query('1/2569', pattern=r'^\d{1,2}/\d{4}$'),
):
    """Return the full subject roster with checked/pending status for the dashboard."""
    if _sdl_api_configured():
        rows = await _api_subject_roster(subject_code, term)
        source = 'sdl_api'
    elif _sdl_mysql_configured():
        rows = _mysql_subject_roster(subject_code, term)
        source = 'sdl_mysql'
    else:
        raise HTTPException(status_code=503, detail='SDL_school data source is not configured')
    checked = sum(1 for row in rows if row['checked'])
    return {
        'subject_code': subject_code,
        'term': term,
        'rows': rows,
        'summary': {
            'total': len(rows),
            'checked': checked,
            'pending': len(rows) - checked,
            'groups': len({row['group_id'] for row in rows}),
        },
        'source': source,
    }


@app.post('/api/scores')
async def submit_score(result: ScoreResult):
    """Persist a checked result locally and optionally forward it to a writable SDL endpoint."""
    if result.score > result.max_score:
        raise HTTPException(status_code=422, detail='score cannot be greater than max_score')
    payload = result.model_dump(mode='json')
    _store_local_score(payload)
    if not _sdl_api_configured() or not os.getenv('SDL_SCHOOL_SCORES_PATH', '').strip():
        return {'ok': True, 'delivery': 'stored', 'submitted': payload}
    path = _configured_path(
        'SDL_SCHOOL_SCORES_PATH',
        os.environ['SDL_SCHOOL_SCORES_PATH'],
        subject_code=result.subject_code,
        group_id=result.class_group_id,
    )
    response = await _request_sdl_json('POST', path, json=payload)
    return {'ok': True, 'delivery': 'synced', 'submitted': payload, 'upstream': response}


@app.delete('/api/scores/{subject_code}/{student_code}')
async def cancel_score(
    subject_code: str = ApiPath(..., min_length=1, max_length=50),
    student_code: str = ApiPath(..., min_length=1, max_length=50, pattern=r'^[A-Za-z0-9._-]+$'),
    class_group_id: str = Query(..., min_length=1, max_length=100),
    term: str = Query('1/2569', pattern=r'^\d{1,2}/\d{4}$'),
):
    """Remove a saved local result so the student can be checked again."""
    deleted = _delete_local_score(student_code, subject_code, class_group_id, term)
    return {
        'ok': True,
        'deleted': deleted,
        'student_code': _paper_student_code(student_code),
        'subject_code': subject_code,
        'class_group_id': class_group_id,
        'term': term,
    }

@app.post('/api/scan')
async def scan(
    side: Literal['front', 'back'] = Query(...),
    image: UploadFile = File(...),
):
    try:
        data = await image.read()
        if not data:
            raise HTTPException(status_code=400, detail='Empty image')
        if len(data) > 20 * 1024 * 1024:
            raise HTTPException(status_code=413, detail='Image too large')
        try:
            (BASE / 'output' / 'last_scan.jpg').write_bytes(data)
        except Exception:
            pass
        return scan_image_bytes(data, side)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

app.mount('/assets', StaticFiles(directory=FRONTEND), name='assets')

@app.get('/sw.js', include_in_schema=False)
def service_worker():
    return FileResponse(FRONTEND / 'sw.js', media_type='application/javascript')

@app.get('/')
def root():
    return FileResponse(FRONTEND / 'index.html')

@app.get('/{path:path}')
def spa(path: str):
    f = FRONTEND / path
    if f.is_file():
        return FileResponse(f)
    return FileResponse(FRONTEND / 'index.html')
