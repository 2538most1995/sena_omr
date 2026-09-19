CREATE DATABASE IF NOT EXISTS `sena_omr`
    CHARACTER SET utf8mb4
    COLLATE utf8mb4_unicode_ci;

USE `sena_omr`;

CREATE TABLE IF NOT EXISTS omr_answer_keys (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    subject_code VARCHAR(50) NOT NULL,
    paper_subject_code VARCHAR(50) NOT NULL,
    subject_name VARCHAR(255) NOT NULL DEFAULT '',
    term VARCHAR(20) NOT NULL,
    school_code VARCHAR(50) NOT NULL,
    answer_count SMALLINT UNSIGNED NOT NULL,
    answers_json LONGTEXT NOT NULL,
    version INT UNSIGNED NOT NULL DEFAULT 1,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    UNIQUE KEY uq_omr_answer_key (subject_code, term),
    KEY idx_omr_answer_key_term (term)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS omr_scores (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    student_code VARCHAR(50) NOT NULL,
    subject_code VARCHAR(50) NOT NULL,
    class_group_id VARCHAR(100) NOT NULL,
    term VARCHAR(20) NOT NULL,
    score DECIMAL(7,2) NOT NULL,
    max_score DECIMAL(7,2) NOT NULL,
    answers_json LONGTEXT NOT NULL,
    scan_quality_json LONGTEXT NULL,
    review_count SMALLINT UNSIGNED NOT NULL DEFAULT 0,
    corrected_count SMALLINT UNSIGNED NOT NULL DEFAULT 0,
    checked_at DATETIME NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    UNIQUE KEY uq_omr_score (student_code, subject_code, class_group_id, term),
    KEY idx_omr_scores_subject_term (subject_code, term),
    KEY idx_omr_scores_group (class_group_id),
    KEY idx_omr_scores_checked_at (checked_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
