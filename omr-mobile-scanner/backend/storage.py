from __future__ import annotations

import json
import os
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    import pymysql
    import pymysql.cursors
except ImportError:
    pymysql = None


_SCHEMA_SIGNATURE: tuple[str, str, str] | None = None


def mysql_configured() -> bool:
    return bool(pymysql is not None and os.getenv('OMR_MYSQL_DATABASE', '').strip())


def mysql_database_name() -> str:
    name = os.getenv('OMR_MYSQL_DATABASE', '').strip()
    if not re.fullmatch(r'[A-Za-z0-9_]+', name):
        raise ValueError('OMR_MYSQL_DATABASE must contain only letters, numbers, and underscores')
    return name


def mysql_connection(*, include_database: bool = True):
    if pymysql is None:
        raise RuntimeError('PyMySQL is not installed. Install it with: pip install pymysql')
    options: dict[str, Any] = {
        'host': os.getenv('OMR_MYSQL_HOST', os.getenv('SDL_MYSQL_HOST', '127.0.0.1')),
        'port': int(os.getenv('OMR_MYSQL_PORT', os.getenv('SDL_MYSQL_PORT', '3306'))),
        'user': os.getenv('OMR_MYSQL_USER', os.getenv('SDL_MYSQL_USER', 'root')),
        'password': os.getenv('OMR_MYSQL_PASSWORD', os.getenv('SDL_MYSQL_PASSWORD', '')),
        'charset': 'utf8mb4',
        'cursorclass': pymysql.cursors.DictCursor,
        'connect_timeout': 5,
        'read_timeout': 12,
        'write_timeout': 12,
        'autocommit': False,
    }
    if include_database:
        options['database'] = mysql_database_name()
    return pymysql.connect(**options)


def _schema_statements(base: Path) -> list[str]:
    schema = (base / 'database' / 'schema.sql').read_text(encoding='utf-8')
    statements: list[str] = []
    for raw_statement in schema.split(';'):
        statement = re.sub(r'^\s*--[^\n]*(?:\n|$)', '', raw_statement, flags=re.MULTILINE).strip()
        if not statement or re.match(r'^(?:CREATE\s+DATABASE|USE\s+)', statement, flags=re.IGNORECASE):
            continue
        statements.append(statement)
    return statements


def initialise_mysql(base: Path, *, create_database: bool = False) -> None:
    if create_database:
        database = mysql_database_name()
        connection = mysql_connection(include_database=False)
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    f'CREATE DATABASE IF NOT EXISTS `{database}` '
                    'CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci'
                )
            connection.commit()
        finally:
            connection.close()

    connection = mysql_connection()
    try:
        with connection.cursor() as cursor:
            for statement in _schema_statements(base):
                cursor.execute(statement)
        connection.commit()
    finally:
        connection.close()


def ensure_mysql(base: Path) -> None:
    global _SCHEMA_SIGNATURE
    signature = (
        os.getenv('OMR_MYSQL_HOST', os.getenv('SDL_MYSQL_HOST', '127.0.0.1')),
        os.getenv('OMR_MYSQL_PORT', os.getenv('SDL_MYSQL_PORT', '3306')),
        mysql_database_name(),
    )
    if _SCHEMA_SIGNATURE == signature:
        return
    initialise_mysql(base)
    _SCHEMA_SIGNATURE = signature


def _sqlite_path(base: Path) -> Path:
    return base / '.run' / 'omr_scores.sqlite3'


def _ensure_sqlite(connection: sqlite3.Connection) -> None:
    connection.execute(
        '''CREATE TABLE IF NOT EXISTS scores (
            student_code TEXT NOT NULL, subject_code TEXT NOT NULL,
            class_group_id TEXT NOT NULL, term TEXT NOT NULL,
            score REAL NOT NULL, max_score REAL NOT NULL,
            answers_json TEXT NOT NULL, scan_quality_json TEXT,
            review_count INTEGER NOT NULL DEFAULT 0,
            corrected_count INTEGER NOT NULL DEFAULT 0,
            checked_at TEXT, updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (student_code, subject_code, class_group_id, term)
        )'''
    )
    connection.execute(
        '''CREATE TABLE IF NOT EXISTS answer_keys (
            subject_code TEXT NOT NULL, term TEXT NOT NULL,
            paper_subject_code TEXT NOT NULL, subject_name TEXT NOT NULL DEFAULT '',
            school_code TEXT NOT NULL, answer_count INTEGER NOT NULL,
            answers_json TEXT NOT NULL, version INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (subject_code, term)
        )'''
    )
    columns = {row[1] for row in connection.execute('PRAGMA table_info(scores)').fetchall()}
    additions = {
        'scan_quality_json': 'TEXT',
        'review_count': 'INTEGER NOT NULL DEFAULT 0',
        'corrected_count': 'INTEGER NOT NULL DEFAULT 0',
    }
    for name, definition in additions.items():
        if name not in columns:
            connection.execute(f'ALTER TABLE scores ADD COLUMN {name} {definition}')


def _decode_json(value: Any, fallback: Any) -> Any:
    if value is None:
        return fallback
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return fallback


def _normalise_checked_at(value: Any) -> str | None:
    if value in (None, ''):
        return None
    if isinstance(value, datetime):
        parsed = value
    else:
        parsed = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
    return parsed.strftime('%Y-%m-%d %H:%M:%S.%f')


def scores_for_subject(base: Path, subject_code: str, term: str) -> dict[tuple[str, str], dict[str, Any]]:
    if mysql_configured():
        ensure_mysql(base)
        connection = mysql_connection()
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    '''SELECT student_code, class_group_id, score, max_score,
                              checked_at, updated_at
                       FROM omr_scores WHERE subject_code = %s AND term = %s''',
                    (subject_code, term),
                )
                rows = cursor.fetchall()
        finally:
            connection.close()
    else:
        path = _sqlite_path(base)
        if not path.exists():
            return {}
        with sqlite3.connect(path) as connection:
            connection.row_factory = sqlite3.Row
            _ensure_sqlite(connection)
            rows = connection.execute(
                '''SELECT student_code, class_group_id, score, max_score,
                          checked_at, updated_at
                   FROM scores WHERE subject_code = ? AND term = ?''',
                (subject_code, term),
            ).fetchall()
    return {
        (str(row['student_code']), str(row['class_group_id']).strip()): dict(row)
        for row in rows
    }


def store_score(base: Path, payload: dict[str, Any]) -> None:
    answers_json = json.dumps(payload['answers'], ensure_ascii=False, separators=(',', ':'))
    quality_json = json.dumps(payload.get('scan_quality') or {}, ensure_ascii=False, separators=(',', ':'))
    values = (
        payload['student_code'], payload['subject_code'], payload['class_group_id'], payload['term'],
        payload['score'], payload['max_score'], answers_json, quality_json,
        payload.get('review_count', 0), payload.get('corrected_count', 0),
        _normalise_checked_at(payload.get('checked_at')),
    )
    if mysql_configured():
        ensure_mysql(base)
        connection = mysql_connection()
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    '''INSERT INTO omr_scores
                       (student_code, subject_code, class_group_id, term, score, max_score,
                        answers_json, scan_quality_json, review_count, corrected_count, checked_at)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                       ON DUPLICATE KEY UPDATE
                         score=VALUES(score), max_score=VALUES(max_score), answers_json=VALUES(answers_json),
                         scan_quality_json=VALUES(scan_quality_json), review_count=VALUES(review_count),
                         corrected_count=VALUES(corrected_count), checked_at=VALUES(checked_at)''',
                    values,
                )
            connection.commit()
        finally:
            connection.close()
        return

    path = _sqlite_path(base)
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as connection:
        _ensure_sqlite(connection)
        connection.execute(
            '''INSERT INTO scores
               (student_code, subject_code, class_group_id, term, score, max_score, answers_json,
                scan_quality_json, review_count, corrected_count, checked_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(student_code, subject_code, class_group_id, term) DO UPDATE SET
                 score=excluded.score, max_score=excluded.max_score, answers_json=excluded.answers_json,
                 scan_quality_json=excluded.scan_quality_json, review_count=excluded.review_count,
                 corrected_count=excluded.corrected_count, checked_at=excluded.checked_at,
                 updated_at=CURRENT_TIMESTAMP''',
            values,
        )


def delete_score(base: Path, student_code: str, candidate_code: str, subject_code: str, class_group_id: str, term: str) -> bool:
    if mysql_configured():
        ensure_mysql(base)
        connection = mysql_connection()
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    '''DELETE FROM omr_scores
                       WHERE subject_code = %s AND class_group_id = %s AND term = %s
                         AND (student_code = %s OR RIGHT(student_code, 10) = %s)''',
                    (subject_code, class_group_id, term, student_code, candidate_code),
                )
                deleted = cursor.rowcount > 0
            connection.commit()
            return deleted
        finally:
            connection.close()

    path = _sqlite_path(base)
    if not path.exists():
        return False
    with sqlite3.connect(path) as connection:
        _ensure_sqlite(connection)
        cursor = connection.execute(
            '''DELETE FROM scores
               WHERE subject_code = ? AND class_group_id = ? AND term = ?
                 AND (student_code = ? OR substr(student_code, -10) = ?)''',
            (subject_code, class_group_id, term, student_code, candidate_code),
        )
        return cursor.rowcount > 0


def list_answer_keys(base: Path, term: str) -> list[dict[str, Any]]:
    if mysql_configured():
        ensure_mysql(base)
        connection = mysql_connection()
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    '''SELECT subject_code, paper_subject_code, subject_name, term, school_code,
                              answer_count, answers_json, version, updated_at
                       FROM omr_answer_keys WHERE term = %s ORDER BY subject_code''',
                    (term,),
                )
                rows = cursor.fetchall()
        finally:
            connection.close()
    else:
        path = _sqlite_path(base)
        path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(path) as connection:
            connection.row_factory = sqlite3.Row
            _ensure_sqlite(connection)
            rows = connection.execute(
                '''SELECT subject_code, paper_subject_code, subject_name, term, school_code,
                          answer_count, answers_json, version, updated_at
                   FROM answer_keys WHERE term = ? ORDER BY subject_code''',
                (term,),
            ).fetchall()
    return [
        {
            'subject_code': row['subject_code'], 'paper_subject_code': row['paper_subject_code'],
            'subject_name': row['subject_name'], 'term': row['term'], 'school_code': row['school_code'],
            'answer_count': int(row['answer_count']), 'answers': _decode_json(row['answers_json'], {}),
            'version': int(row['version']), 'updated_at': str(row['updated_at']),
        }
        for row in rows
    ]


def get_answer_key(base: Path, subject_code: str, term: str) -> dict[str, Any] | None:
    return next((row for row in list_answer_keys(base, term) if row['subject_code'] == subject_code), None)


def store_answer_key(base: Path, payload: dict[str, Any]) -> dict[str, Any]:
    answers_json = json.dumps(payload['answers'], ensure_ascii=False, separators=(',', ':'))
    values = (
        payload['subject_code'], payload['paper_subject_code'], payload.get('subject_name', ''),
        payload['term'], payload['school_code'], len(payload['answers']), answers_json,
    )
    if mysql_configured():
        ensure_mysql(base)
        connection = mysql_connection()
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    '''INSERT INTO omr_answer_keys
                       (subject_code, paper_subject_code, subject_name, term, school_code, answer_count, answers_json)
                       VALUES (%s, %s, %s, %s, %s, %s, %s)
                       ON DUPLICATE KEY UPDATE
                         paper_subject_code=VALUES(paper_subject_code), subject_name=VALUES(subject_name),
                         school_code=VALUES(school_code), answer_count=VALUES(answer_count),
                         answers_json=VALUES(answers_json), version=version + 1''',
                    values,
                )
            connection.commit()
        finally:
            connection.close()
    else:
        path = _sqlite_path(base)
        path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(path) as connection:
            _ensure_sqlite(connection)
            connection.execute(
                '''INSERT INTO answer_keys
                   (subject_code, paper_subject_code, subject_name, term, school_code, answer_count, answers_json)
                   VALUES (?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(subject_code, term) DO UPDATE SET
                     paper_subject_code=excluded.paper_subject_code, subject_name=excluded.subject_name,
                     school_code=excluded.school_code, answer_count=excluded.answer_count,
                     answers_json=excluded.answers_json, version=answer_keys.version + 1,
                     updated_at=CURRENT_TIMESTAMP''',
                values,
            )
    stored = get_answer_key(base, payload['subject_code'], payload['term'])
    if stored is None:
        raise RuntimeError('Answer key was not saved')
    return stored


def delete_answer_key(base: Path, subject_code: str, term: str) -> bool:
    if mysql_configured():
        ensure_mysql(base)
        connection = mysql_connection()
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    'DELETE FROM omr_answer_keys WHERE subject_code = %s AND term = %s',
                    (subject_code, term),
                )
                deleted = cursor.rowcount > 0
            connection.commit()
            return deleted
        finally:
            connection.close()
    path = _sqlite_path(base)
    if not path.exists():
        return False
    with sqlite3.connect(path) as connection:
        _ensure_sqlite(connection)
        cursor = connection.execute(
            'DELETE FROM answer_keys WHERE subject_code = ? AND term = ?',
            (subject_code, term),
        )
        return cursor.rowcount > 0
