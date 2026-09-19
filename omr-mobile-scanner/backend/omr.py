from __future__ import annotations

import base64
import io
import math
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

import cv2
import numpy as np
from PIL import Image, ImageOps


def _preprocess_gray(gray: np.ndarray) -> np.ndarray:
    """Adaptive histogram equalisation + light denoise.

    Mobile-camera photos suffer from uneven lighting, shadows from hands/desk,
    and sensor noise.  CLAHE normalises brightness across the sheet so that the
    same pencil-mark darkness produces comparable pixel values everywhere.
    A mild denoise pass removes high-frequency camera noise without blurring
    the pencil marks.
    """
    clahe = cv2.createCLAHE(clipLimit=2.5, tileGridSize=(8, 8))
    equalised = clahe.apply(gray)
    denoised = cv2.fastNlMeansDenoising(equalised, h=8, templateWindowSize=7, searchWindowSize=21)
    return denoised


def _pencil_gray(bgr: np.ndarray) -> np.ndarray:
    """Grayscale optimised for pencil-on-orange-print answer sheets.

    The answer sheet is printed entirely in orange/red ink.  Standard
    luminance conversion (0.299R + 0.587G + 0.114B) renders the orange
    circles as medium-gray (~154), making them hard to distinguish from
    light pencil marks (~100).

    By heavily weighting the red channel (0.65R + 0.30G + 0.05B), orange
    elements become near-white (~190) while pencil graphite (achromatic,
    R≈G≈B) stays dark (~60).  This nearly doubles the contrast between
    printed circles and pencil fills.

    Red teacher/grader marks are also suppressed (from ~95 to ~148),
    preventing them from being misread as filled bubbles.
    """
    b = bgr[:, :, 0].astype(np.float32)
    g = bgr[:, :, 1].astype(np.float32)
    r = bgr[:, :, 2].astype(np.float32)
    return np.clip(0.65 * r + 0.30 * g + 0.05 * b, 0, 255).astype(np.uint8)

CANON_W = 1600
CANON_H = 1200

# Calibrated against the supplied answer sheet after perspective warp to 1600x1200.
FRONT_X = [
    [678, 710, 743, 775],
    [871, 905, 937, 970],
    [1065, 1100, 1133, 1164],
    [1259, 1294, 1326, 1359],
    [1452, 1488, 1520, 1553],
]
FRONT_Y = [496, 572, 648, 723, 796, 874, 948, 1024, 1101, 1178]
BACK_X = [
    [58, 93, 126, 158],
    [254, 289, 322, 355],
    [451, 485, 518, 550],
    [643, 680, 712, 745],
    [840, 875, 908, 940],
]
BACK_Y = [482, 553, 622, 693, 762, 831, 900, 969, 1039, 1105]

# Candidate code, front side (10 digits, 0-9)
CAND_X = [66, 98, 130, 162, 194, 225, 257, 289, 320, 352]
CAND_Y = [303, 336, 370, 403, 436, 470, 503, 536, 569, 602]

# School code region is lower on the front side. Calibrated from the supplied form.
SCHOOL_X = [75, 107, 139, 171, 203, 235, 266, 298, 330, 362]
SCHOOL_Y = [798, 831, 864, 897, 930, 963, 996, 1029, 1063, 1097]

# Subject code: two category characters followed by five digits.
# The first two columns intentionally have different option counts on the form.
SUBJECT_X = [421, 453, 484, 514, 546, 578, 609]
SUBJECT_Y = [658, 692, 726, 760, 794, 828, 862, 896, 930, 964, 998, 1032, 1066, 1100]
SUBJECT_PREFIX_1 = ['ก', 'ค', 'ท', 'พ', 'ว', 'ส', 'อ', 'B', 'L', 'O', 'S', 'W']
SUBJECT_PREFIX_2 = ['ค', 'ช', 'ต', 'ท', 'ร', 'ว', 'ส', 'ฮ', 'D', 'F', 'M', 'P', 'S', 'T']

OPTIONS = ['A', 'B', 'C', 'D']


def _order_points(pts: np.ndarray) -> np.ndarray:
    rect = np.zeros((4, 2), dtype=np.float32)
    s = pts.sum(axis=1)
    d = np.diff(pts, axis=1).reshape(-1)
    rect[0] = pts[np.argmin(s)]   # TL
    rect[2] = pts[np.argmax(s)]   # BR
    rect[1] = pts[np.argmin(d)]   # TR
    rect[3] = pts[np.argmax(d)]   # BL
    return rect


def _largest_document_quad(image: np.ndarray) -> Optional[np.ndarray]:
    h, w = image.shape[:2]
    scale = min(1.0, 1400.0 / max(h, w))
    small = cv2.resize(image, None, fx=scale, fy=scale) if scale < 1 else image.copy()
    g = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)

    # 1) Edge based document detector — works on most backgrounds.
    blur = cv2.GaussianBlur(g, (5, 5), 0)
    edges = cv2.Canny(blur, 45, 145)
    edges = cv2.morphologyEx(edges, cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8), iterations=2)
    contours, _ = cv2.findContours(edges, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
    contours = sorted(contours, key=cv2.contourArea, reverse=True)[:25]
    img_area = small.shape[0] * small.shape[1]
    for c in contours:
        peri = cv2.arcLength(c, True)
        approx = cv2.approxPolyDP(c, 0.02 * peri, True)
        if len(approx) == 4 and cv2.contourArea(approx) > 0.55 * img_area:
            pts = approx.reshape(4, 2).astype(np.float32)
            if scale < 1:
                pts /= scale
            return pts

    # 2) Bright-paper fallback.  The supplied sheets are photographed over a
    # dark desk; taking the convex hull prevents handwriting/printing holes
    # from breaking the page contour.
    mask = (g > 70).astype(np.uint8) * 255
    k = max(9, int(round(min(small.shape[:2]) * 0.025)) | 1)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((k, k), np.uint8), iterations=1)
    cnts, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if cnts:
        c = max(cnts, key=cv2.contourArea)
        hull = cv2.convexHull(c)
        if cv2.contourArea(hull) > 0.70 * img_area:
            peri = cv2.arcLength(hull, True)
            quad = None
            for eps in (0.01, 0.015, 0.02, 0.025, 0.03, 0.04, 0.05, 0.06, 0.08):
                approx = cv2.approxPolyDP(hull, eps * peri, True)
                if len(approx) == 4:
                    quad = approx.reshape(4, 2).astype(np.float32)
                    break
            if quad is None:
                quad = cv2.boxPoints(cv2.minAreaRect(hull)).astype(np.float32)
            if scale < 1:
                quad /= scale
            return quad
    return None

def _timing_bar_score(gray: np.ndarray, where: str) -> float:
    h, w = gray.shape
    if where == 'bottom':
        roi = gray[int(h*0.925):int(h*0.995), int(w*0.02):int(w*0.98)]
    else:
        roi = gray[int(h*0.005):int(h*0.075), int(w*0.02):int(w*0.98)]
    if roi.size == 0:
        return 0.0
    # dark vertical timing marks produce a high share of very dark pixels.
    return float(np.mean(roi < 70))


def rectify_document(image: np.ndarray) -> Tuple[np.ndarray, Dict[str, float]]:
    quad = _largest_document_quad(image)
    meta = {
        'quad_found': 0.0,
        'rotated_180': 0.0,
        'document_area_ratio': 0.0,
        'geometry_confidence': 0.0,
    }

    if quad is not None:
        meta['quad_found'] = 1.0
        src = _order_points(quad)
        image_area = float(image.shape[0] * image.shape[1])
        meta['document_area_ratio'] = float(cv2.contourArea(src) / image_area)
        top = float(np.linalg.norm(src[1] - src[0]))
        bottom = float(np.linalg.norm(src[2] - src[3]))
        left = float(np.linalg.norm(src[3] - src[0]))
        right = float(np.linalg.norm(src[2] - src[1]))
        horizontal_balance = min(top, bottom) / max(top, bottom, 1.0)
        vertical_balance = min(left, right) / max(left, right, 1.0)
        area_score = max(0.0, min(1.0, (meta['document_area_ratio'] - 0.45) / 0.4))
        meta['geometry_confidence'] = float(min(horizontal_balance, vertical_balance, area_score))
        dst = np.array([[0, 0], [CANON_W-1, 0], [CANON_W-1, CANON_H-1], [0, CANON_H-1]], dtype=np.float32)
        M = cv2.getPerspectiveTransform(src, dst)
        warped = cv2.warpPerspective(image, M, (CANON_W, CANON_H), flags=cv2.INTER_LINEAR)
    else:
        # Fallback: crop to 4:3 centered region and resize.
        h, w = image.shape[:2]
        target_ratio = CANON_W / CANON_H
        ratio = w / h
        if ratio > target_ratio:
            nw = int(h * target_ratio)
            x0 = max(0, (w - nw)//2)
            crop = image[:, x0:x0+nw]
        else:
            nh = int(w / target_ratio)
            y0 = max(0, (h - nh)//2)
            crop = image[y0:y0+nh, :]
        warped = cv2.resize(crop, (CANON_W, CANON_H), interpolation=cv2.INTER_AREA)
        meta['document_area_ratio'] = 1.0

    gray = cv2.cvtColor(warped, cv2.COLOR_BGR2GRAY)
    if _timing_bar_score(gray, 'top') > _timing_bar_score(gray, 'bottom') * 1.15:
        warped = cv2.rotate(warped, cv2.ROTATE_180)
        meta['rotated_180'] = 1.0
    return warped, meta


def _disk_darkness(gray: np.ndarray, x: int, y: int, r: int = 11) -> float:
    """Gaussian-weighted darkness inside a circular disk.

    A larger radius (11 vs old 8) captures more of the pencil mark, and
    Gaussian weighting emphasises the centre where pencil marks are densest
    while gracefully fading at the edge where printed ring outlines live.
    """
    h, w = gray.shape
    x1, x2 = max(0, x - r - 1), min(w, x + r + 2)
    y1, y2 = max(0, y - r - 1), min(h, y + r + 2)
    patch = gray[y1:y2, x1:x2].astype(np.float32)
    yy, xx = np.ogrid[:patch.shape[0], :patch.shape[1]]
    cx = x - x1
    cy = y - y1
    d2 = (xx - cx) ** 2 + (yy - cy) ** 2
    mask = d2 <= r * r
    if not np.any(mask):
        return 0.0
    # Gaussian sigma = r/2 gives smooth fall-off within the disk.
    sigma2 = (r / 2.0) ** 2
    weights = np.exp(-d2.astype(np.float32) / (2.0 * sigma2))
    weights[~mask] = 0.0
    w_sum = float(weights.sum())
    if w_sum < 1e-6:
        return 0.0
    weighted_mean = float((patch * weights).sum() / w_sum)
    return float(255.0 - weighted_mean)



def _cluster_1d(values: np.ndarray, tol: float = 7.0) -> List[Tuple[float, int]]:
    if values.size == 0:
        return []
    vals = sorted(float(v) for v in values)
    groups: List[List[float]] = []
    for v in vals:
        if not groups or v - float(np.mean(groups[-1])) > tol:
            groups.append([v])
        else:
            groups[-1].append(v)
    return [(float(np.mean(g)), len(g)) for g in groups]


def _kmeans_centers(values: np.ndarray, k: int) -> Optional[List[int]]:
    if values.size < k:
        return None
    data = np.float32(values.reshape(-1, 1))
    criteria = (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 100, 0.1)
    _compact, _labels, centers = cv2.kmeans(data, k, None, criteria, 30, cv2.KMEANS_PP_CENTERS)
    return [int(round(x)) for x in sorted(float(x) for x in centers.ravel())]


def _detect_answer_grid(warped: np.ndarray, side: str) -> Optional[Tuple[List[List[int]], List[int]]]:
    gray = cv2.cvtColor(warped, cv2.COLOR_BGR2GRAY)
    if side == 'front':
        x1, y1, x2, y2 = int(CANON_W * 0.39), int(CANON_H * 0.35), CANON_W, CANON_H
    else:
        x1, y1, x2, y2 = 0, int(CANON_H * 0.34), int(CANON_W * 0.63), CANON_H

    roi = cv2.GaussianBlur(gray[y1:y2, x1:x2], (3, 3), 1)
    circles = cv2.HoughCircles(
        roi, cv2.HOUGH_GRADIENT, dp=1.2, minDist=17,
        param1=120, param2=20, minRadius=8, maxRadius=15
    )
    if circles is None:
        return None
    pts = np.round(circles[0]).astype(int)
    xs = pts[:, 0] + x1
    ys = pts[:, 1] + y1

    # True answer rows contain ~20 bubbles each.  This rejects subject-code
    # circles and other circular print that may sit inside the coarse ROI.
    y_clusters = _cluster_1d(ys.astype(float), tol=7.0)
    strong_y = [(c, n) for c, n in y_clusters if n >= 14]
    if len(strong_y) < 10:
        # Fall back to the ten densest row clusters.
        strong_y = sorted(y_clusters, key=lambda z: z[1], reverse=True)[:10]
    if len(strong_y) < 10:
        return None
    row_centers = sorted(int(round(c)) for c, _ in sorted(strong_y, key=lambda z: z[1], reverse=True)[:10])

    # Keep only circles that lie on one of the selected rows, then derive the
    # 20 answer columns.  K-means is stable here because each real column is
    # observed across ten rows.
    keep = np.array([min(abs(int(y) - ry) for ry in row_centers) <= 7 for y in ys], dtype=bool)
    x_on_rows = xs[keep]
    col_centers = _kmeans_centers(x_on_rows.astype(float), 20)
    if col_centers is None:
        return None

    # Reject obviously irregular solutions by checking within-group spacing.
    diffs = np.diff(np.array(col_centers, dtype=float))
    if len(diffs) != 19:
        return None
    # The form has four tight columns per block and larger gaps between blocks.
    # We do not require exact pixels; just prevent pathological fits.
    if np.median(diffs) < 18 or np.median(diffs) > 55:
        return None

    x_groups = [col_centers[i:i+4] for i in range(0, 20, 4)]
    return x_groups, row_centers


def _detect_digit_grid(warped: np.ndarray, roi_box: Tuple[int, int, int, int], is_school: bool = False) -> Optional[Tuple[List[int], List[int]]]:
    """Detect the 10×10 digit bubble grid using 2D column-intersection scoring.

    Candidate bubble rows have detected circles across 7-10 columns.
    Handwriting and header boxes only have 1-4 circles and are completely rejected.
    """
    gray = cv2.cvtColor(warped, cv2.COLOR_BGR2GRAY)
    x1, y1, x2, y2 = roi_box

    param_sets = [
        (1.2, 14, 110, 18, 8, 15),
        (1.2, 16, 120, 20, 8, 15),
        (1.3, 15, 95, 16, 7, 16),
    ]
    target_cols = 8 if is_school else 10

    for dp, minDist, p1, p2, minR, maxR in param_sets:
        roi = cv2.GaussianBlur(gray[y1:y2, x1:x2], (3, 3), 1)
        circles = cv2.HoughCircles(
            roi, cv2.HOUGH_GRADIENT, dp=dp, minDist=minDist,
            param1=p1, param2=p2, minRadius=minR, maxRadius=maxR,
        )
        if circles is None:
            continue
        pts = np.round(circles[0]).astype(int)
        xs = pts[:, 0] + x1
        ys = pts[:, 1] + y1

        # 1. Detect column centers (each column has circles from multiple rows)
        x_clusters = _cluster_1d(xs.astype(float), 8.0)
        valid_cols = sorted([int(round(c)) for c, cnt in x_clusters if cnt >= 5])

        if len(valid_cols) > target_cols:
            best_col_slice = None
            best_col_cv = 999.0
            for i in range(len(valid_cols) - target_cols + 1):
                sub = valid_cols[i:i + target_cols]
                diffs = np.diff(sub)
                step = float(np.median(diffs))
                if 25 <= step <= 40:
                    cv = float(np.std(diffs) / (step + 1e-6))
                    if cv < best_col_cv:
                        best_col_cv = cv
                        best_col_slice = sub
            if best_col_slice is not None and best_col_cv < 0.20:
                valid_cols = best_col_slice

        if len(valid_cols) != target_cols:
            continue

        if is_school:
            step_x = float(np.median(np.diff(valid_cols)))
            col2_x = int(round(valid_cols[0] - step_x))
            col1_x = int(round(col2_x - step_x))
            all_cols = [col1_x, col2_x] + valid_cols
        else:
            all_cols = valid_cols

        # 2. Score candidate rows by 2D intersection with the verified columns
        y_clusters = _cluster_1d(ys.astype(float), 8.0)
        scored_rows = []
        for row_y, _ in y_clusters:
            matched = sum(1 for col_x in valid_cols if np.min(np.hypot(xs - col_x, ys - row_y)) <= 11.0)
            if matched >= (5 if is_school else 6):
                scored_rows.append((int(round(row_y)), matched))

        scored_rows.sort(key=lambda item: item[0])
        candidate_ys = [item[0] for item in scored_rows]

        # 3. Find 10 consecutive rows with regular vertical step and high matched score
        if len(candidate_ys) >= 10:
            best_row_window = None
            best_row_score = -999.0
            for i in range(len(candidate_ys) - 10 + 1):
                sub = candidate_ys[i:i + 10]
                diffs = np.diff(sub)
                step = float(np.median(diffs))
                if 26 <= step <= 39:
                    cv = float(np.std(diffs) / (step + 1e-6))
                    if cv < 0.12:
                        total_matched = sum(scored_rows[i + k][1] for k in range(10))
                        score = total_matched - cv * 100.0
                        if score > best_row_score:
                            best_row_score = score
                            best_row_window = sub
            if best_row_window is not None:
                return all_cols, best_row_window

    return None


def _bubble_metrics(gray: np.ndarray, x: int, y: int) -> Tuple[float, float]:
    """Return (fill_contrast, center_darkness)."""
    r = 18
    h, w = gray.shape
    if x - r < 0 or y - r < 0 or x + r >= w or y + r >= h:
        return 0.0, 0.0
    patch = gray[y - r:y + r + 1, x - r:x + r + 1].astype(np.float32)
    yy, xx = np.ogrid[-r:r + 1, -r:r + 1]
    d2 = xx * xx + yy * yy
    center = patch[d2 <= 8 * 8]
    annulus = patch[(d2 >= 12 * 12) & (d2 <= 17 * 17)]
    if center.size == 0 or annulus.size == 0:
        return 0.0, 0.0
    contrast = float(annulus.mean() - center.mean())
    darkness = float(255.0 - center.mean())
    return contrast, darkness


def _read_question(gray: np.ndarray, xs: List[int], y: int) -> Dict:
    contrasts = np.array([_bubble_metrics(gray, x, y)[0] for x in xs], dtype=float)
    darkness = np.array([_bubble_metrics(gray, x, y)[1] for x in xs], dtype=float)

    valid: List[int] = []
    for j in range(4):
        other_dark = max(float(darkness[k]) for k in range(4) if k != j)
        dark_margin = float(darkness[j] - other_dark)
        if contrasts[j] >= 25.0 or (contrasts[j] >= 13.0 and dark_margin >= 18.0):
            valid.append(j)

    if len(valid) == 1:
        top = valid[0]
        status = 'ok'
        choice = OPTIONS[top]
        second_contrast = max(float(contrasts[k]) for k in range(4) if k != top)
        evidence = max(float(contrasts[top] - second_contrast), float(contrasts[top]))
        confidence = max(0.0, min(1.0, (evidence - 8.0) / 42.0))
    elif len(valid) > 1:
        status = 'multiple'
        choice = None
        confidence = min(1.0, max(float(contrasts[j]) for j in valid) / 45.0)
    else:
        status = 'blank'
        choice = None
        strongest = float(np.max(contrasts))
        contrast_margin = float(np.max(contrasts) - np.partition(contrasts, -2)[-2])
        contrast_certainty = 1.0 - max(0.0, strongest - 10.0) / 15.0
        margin_certainty = 1.0 - max(0.0, contrast_margin - 3.0) / 12.0
        confidence = max(0.0, min(1.0, contrast_certainty, margin_certainty))

    if status == 'multiple':
        needs_review = True
        review_reason = 'multiple_marks'
    elif status == 'ok' and confidence < 0.55:
        needs_review = True
        review_reason = 'low_confidence'
    elif status == 'blank' and confidence < 0.55:
        needs_review = True
        review_reason = 'possible_faint_mark'
    else:
        needs_review = False
        review_reason = None

    return {
        'choice': choice,
        'status': status,
        'confidence': round(confidence, 3),
        'needs_review': needs_review,
        'review_reason': review_reason,
        'scores': {OPTIONS[i]: round(float(contrasts[i]), 2) for i in range(4)},
        'margin': round(float(np.max(contrasts) - np.partition(contrasts, -2)[-2]), 2),
    }


def read_answers(warped: np.ndarray, side: str) -> List[Dict]:
    gray = cv2.cvtColor(warped, cv2.COLOR_BGR2GRAY)

    dynamic = _detect_answer_grid(warped, side)
    if dynamic is not None:
        x_groups, ys = dynamic
    elif side == 'front':
        x_groups, ys = FRONT_X, FRONT_Y
    else:
        x_groups, ys = BACK_X, BACK_Y

    q0 = 1 if side == 'front' else 51
    out: List[Dict] = []
    for g, xs in enumerate(x_groups):
        for r, y in enumerate(ys):
            q = q0 + g * 10 + r
            row = _read_question(gray, xs, y)
            row['question'] = q
            out.append(row)
    out.sort(key=lambda x: x['question'])
    return out


def _detect_subject_grid(warped: np.ndarray) -> Optional[Tuple[List[int], List[int]]]:
    gray = cv2.cvtColor(warped, cv2.COLOR_BGR2GRAY)
    x1, y1, x2, y2 = 390, 620, 630, 1120
    roi = cv2.GaussianBlur(gray[y1:y2, x1:x2], (3, 3), 1)
    circles = cv2.HoughCircles(
        roi, cv2.HOUGH_GRADIENT, dp=1.2, minDist=15,
        param1=110, param2=18, minRadius=8, maxRadius=15,
    )
    if circles is None:
        return None
    pts = np.round(circles[0]).astype(int)
    xs = pts[:, 0] + x1
    ys = pts[:, 1] + y1
    x_clusters = [int(round(c)) for c, n in _cluster_1d(xs.astype(float), 7.0) if n >= 8]
    y_clusters = [int(round(c)) for c, n in _cluster_1d(ys.astype(float), 7.0) if n >= 6]
    if len(x_clusters) != 7 or len(y_clusters) < 8:
        return None
    first_ten = sorted(y_clusters)[:10]
    step = int(round(float(np.median(np.diff(first_ten)))))
    if step < 28 or step > 40:
        return None
    y_centers = [int(round(first_ten[0] + i * step)) for i in range(14)]
    return sorted(x_clusters), y_centers


def _read_choice_column(
    gray: np.ndarray,
    x: int,
    ys: List[int],
    labels: List[str],
    min_lift: float = 22.0,
) -> Tuple[Optional[str], float, Optional[int]]:
    darkness = np.array([_disk_darkness(gray, x, y, 8) for y in ys[:len(labels)]], dtype=float)
    contrasts = np.array([_bubble_metrics(gray, x, y)[0] for y in ys[:len(labels)]], dtype=float)
    darkness_lift = np.maximum(darkness - np.median(darkness), 0.0)
    vals = 0.65 * np.maximum(contrasts, 0.0) + 0.35 * darkness_lift
    order = np.argsort(vals)[::-1]
    best, second = int(order[0]), int(order[1])
    lift = float(vals[best])
    margin = float(vals[best] - vals[second])
    if lift < min_lift or margin < 7.0:
        return None, max(0.0, min(1.0, lift / min_lift)), None
    confidence = max(0.0, min(1.0, (lift + margin) / 45.0))
    return labels[best], confidence, best


def _read_subject_code(warped: np.ndarray, gray: np.ndarray) -> Dict:
    grid = _detect_subject_grid(warped)
    xs, ys = grid if grid is not None else (SUBJECT_X, SUBJECT_Y)
    labels_by_column = [SUBJECT_PREFIX_1, SUBJECT_PREFIX_2] + [list('0123456789')] * 5
    chars: List[Optional[str]] = []
    indices: List[Optional[int]] = []
    confidences: List[float] = []
    for x, labels in zip(xs, labels_by_column):
        char, confidence, index = _read_choice_column(gray, x, ys, labels)
        chars.append(char)
        indices.append(index)
        confidences.append(confidence)
    value = ''.join(char if char is not None else '?' for char in chars)
    return {
        'value': value,
        'characters': chars,
        'indices': indices,
        'complete': all(char is not None for char in chars),
        'confidence': round(float(np.mean(confidences)), 3),
    }


def _read_metadata_columns(
    warped: np.ndarray,
    gray: np.ndarray,
    xs: List[int],
    ys: List[int],
    is_school: bool = False,
) -> Dict:
    """Read digit columns using combined optical contrast and pencil desaturation scoring.

    2B pencil marks have both optical darkness and significant desaturation of the
    form's orange print ink (R - B drops sharply). Even under shiny graphite glare,
    the desaturation signal remains strong.
    """
    b = warped[:, :, 0].astype(np.float32)
    r = warped[:, :, 2].astype(np.float32)
    sat = np.maximum(r - b, 0.0)
    score_img = (255.0 - r) + 3.0 * np.maximum(28.0 - sat, 0.0)

    digits: List[Optional[int]] = []
    confs: List[float] = []
    alternatives: List[List[int]] = []

    for col_idx, x in enumerate(xs):
        # Province code 12 (Ayutthaya) is pre-printed in columns 1 and 2 of school code.
        if is_school and col_idx == 0:
            digits.append(1)
            confs.append(1.0)
            alternatives.append([1, 0])
            continue
        if is_school and col_idx == 1:
            digits.append(2)
            confs.append(1.0)
            alternatives.append([2, 0])
            continue

        bubble_scores = []
        for y in ys:
            c, dark = _bubble_metrics(gray, x, y)
            yy, xx = np.ogrid[-10:11, -10:11]
            mask = xx * xx + yy * yy <= 10 * 10
            h, w = score_img.shape
            if x - 10 < 0 or y - 10 < 0 or x + 11 > w or y + 11 > h:
                p_score = 0.0
            else:
                patch = score_img[y - 10:y + 11, x - 10:x + 11]
                p_score = float(np.mean(patch[mask]))
            combined = c + 0.35 * p_score
            bubble_scores.append(combined)

        order = np.argsort(bubble_scores)[::-1]
        best, second = int(order[0]), int(order[1])
        alternatives.append([best, second])
        s = sorted(bubble_scores)
        margin = s[-1] - s[-2]

        if is_school:
            # Blank school columns have low score and small margin.
            if s[-1] < 45 or margin < 6.0:
                digits.append(None)
                confs.append(0.0)
            else:
                digits.append(best)
                confs.append(min(1.0, margin / 30.0))
        else:
            if s[-1] < 40 or margin < 4.0:
                digits.append(None)
                confs.append(0.0)
            else:
                digits.append(best)
                confs.append(min(1.0, margin / 30.0))

    raw_val = ''.join('?' if d is None else str(d) for d in digits)
    val = raw_val.rstrip('?') if is_school else raw_val
    complete = (len(val) >= 2 and '?' not in val) if is_school else all(d is not None for d in digits)
    active_confs = [c for c in confs if c > 0]
    confidence = round(float(np.mean(active_confs)), 3) if active_confs else 0.0

    return {
        'value': val,
        'raw_value': raw_val,
        'digits': digits,
        'alternatives': alternatives,
        'complete': complete,
        'confidence': confidence,
    }


def read_metadata(warped: np.ndarray, side: str) -> Dict:
    """Read candidate ID, school code, and subject code from the front side."""
    if side != 'front':
        return {}
    gray = cv2.cvtColor(warped, cv2.COLOR_BGR2GRAY)

    cand_grid = _detect_digit_grid(warped, (0, 180, int(CANON_W * 0.25), 750), is_school=False)
    cand_x, cand_y = cand_grid if cand_grid is not None else (CAND_X, CAND_Y)
    candidate = _read_metadata_columns(warped, gray, cand_x, cand_y, is_school=False)

    school_grid = _detect_digit_grid(warped, (0, 700, int(CANON_W * 0.25), 1180), is_school=True)
    school_x, school_y = school_grid if school_grid is not None else (SCHOOL_X, SCHOOL_Y)
    school = _read_metadata_columns(warped, gray, school_x, school_y, is_school=True)

    subject = _read_subject_code(warped, gray)

    return {
        'candidate_id': candidate,
        'school_code': school,
        'subject_code': subject,
    }

def make_debug_overlay(warped: np.ndarray, side: str, answers: List[Dict]) -> np.ndarray:
    out = warped.copy()
    dynamic = _detect_answer_grid(warped, side)
    if dynamic is not None:
        x_groups, ys = dynamic
    elif side == 'front':
        x_groups, ys = FRONT_X, FRONT_Y
    else:
        x_groups, ys = BACK_X, BACK_Y
    q0 = 1 if side == 'front' else 51
    answer_by_q = {a['question']: a for a in answers}
    for g, xs in enumerate(x_groups):
        for r, y in enumerate(ys):
            q = q0 + g*10 + r
            a = answer_by_q[q]
            for i, x in enumerate(xs):
                if a['status'] == 'ok' and a['choice'] == OPTIONS[i]:
                    col = (30, 180, 30)
                    thick = 3
                elif a['status'] in ('blank', 'multiple'):
                    col = (0, 165, 255)
                    thick = 1
                else:
                    col = (140, 140, 140)
                    thick = 1
                cv2.circle(out, (int(x), int(y)), 15, col, thick)
    return out

def encode_jpeg_b64(img: np.ndarray, quality: int = 82) -> str:
    ok, buf = cv2.imencode('.jpg', img, [int(cv2.IMWRITE_JPEG_QUALITY), quality])
    if not ok:
        raise RuntimeError('Failed to encode image')
    return base64.b64encode(buf).decode('ascii')


def _decode_mobile_image(data: bytes) -> np.ndarray:
    """Decode JPEG/HEIF-converted uploads and apply EXIF orientation."""
    try:
        with Image.open(io.BytesIO(data)) as source:
            oriented = ImageOps.exif_transpose(source).convert('RGB')
            return cv2.cvtColor(np.asarray(oriented), cv2.COLOR_RGB2BGR)
    except Exception:
        arr = np.frombuffer(data, np.uint8)
        image = cv2.imdecode(arr, cv2.IMREAD_COLOR)
        if image is None:
            raise ValueError('Invalid image')
        return image


def _capture_quality(image: np.ndarray, warped: np.ndarray, geom: Dict[str, float], grid_found: bool) -> Dict:
    gray = cv2.cvtColor(warped, cv2.COLOR_BGR2GRAY)
    sharpness = float(cv2.Laplacian(gray, cv2.CV_64F).var())
    brightness = float(np.mean(gray))
    glare_ratio = float(np.mean(gray >= 248))
    issues: List[str] = []
    if image.shape[1] < 1400 or image.shape[0] < 900:
        issues.append('low_resolution')
    if not geom['quad_found']:
        issues.append('document_edges_missing')
    elif geom['geometry_confidence'] < 0.45:
        issues.append('excessive_perspective')
    if sharpness < 70.0:
        issues.append('image_blur')
    if brightness < 65.0:
        issues.append('too_dark')
    elif brightness > 225.0:
        issues.append('too_bright')
    if glare_ratio > 0.12:
        issues.append('glare')
    if not grid_found:
        issues.append('answer_grid_fallback')
    blocking = {'document_edges_missing', 'excessive_perspective', 'image_blur', 'too_dark', 'too_bright', 'glare'}
    return {
        'resolution_ok': bool(image.shape[1] >= 1400 and image.shape[0] >= 900),
        'capture_ok': not any(issue in blocking for issue in issues),
        'issues': issues,
        'sharpness': round(sharpness, 1),
        'brightness': round(brightness, 1),
        'glare_ratio': round(glare_ratio, 4),
        'document_area_ratio': round(float(geom['document_area_ratio']), 3),
        'geometry_confidence': round(float(geom['geometry_confidence']), 3),
        'answer_grid_detected': grid_found,
    }


def scan_image_bytes(data: bytes, side: str) -> Dict:
    img = _decode_mobile_image(data)
    warped, geom = rectify_document(img)
    grid_found = _detect_answer_grid(warped, side) is not None
    answers = read_answers(warped, side)
    meta = read_metadata(warped, side)
    debug = make_debug_overlay(warped, side, answers)
    answered = sum(1 for a in answers if a['status'] == 'ok')
    blank = sum(1 for a in answers if a['status'] == 'blank')
    multiple = sum(1 for a in answers if a['status'] == 'multiple')
    review = sum(1 for a in answers if a['needs_review'])
    average_confidence = round(float(np.mean([a['confidence'] for a in answers])), 3)
    quality = {
        **_capture_quality(img, warped, geom, grid_found),
        'quad_found': bool(geom['quad_found']),
        'answered': answered,
        'blank_answers': blank,
        'multiple_answers': multiple,
        'needs_review': review,
        # Kept for clients built against the original prototype API.
        'uncertain_answers': review,
        'average_confidence': average_confidence,
    }
    if average_confidence < 0.78:
        quality['issues'].append('low_read_confidence')
        quality['capture_ok'] = False
    if side == 'front':
        identity_reliable = (
            meta.get('candidate_id', {}).get('complete')
            and meta.get('candidate_id', {}).get('confidence', 0) >= 0.55
            and meta.get('subject_code', {}).get('complete')
            and meta.get('subject_code', {}).get('confidence', 0) >= 0.55
        )
        if not identity_reliable:
            quality['issues'].append('metadata_unreliable')
    return {
        'side': side,
        'answers': answers,
        'metadata': meta,
        'quality': quality,
        'debug_image_base64': encode_jpeg_b64(debug),
    }
