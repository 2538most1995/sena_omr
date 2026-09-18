from __future__ import annotations

import base64
import math
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

import cv2
import numpy as np

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
CAND_X = [18, 51, 83, 117, 148, 182, 216, 249, 281, 314]
CAND_Y = [263, 303, 341, 379, 418, 456, 494, 532, 570, 608]

# School code region is lower on the front side. Calibrated from the supplied form.
SCHOOL_X = [22, 56, 88, 121, 154, 186, 220, 253, 285, 319]
SCHOOL_Y = [834, 873, 909, 949, 988, 1026, 1064, 1102, 1141, 1178]

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
    meta = {'quad_found': 0.0, 'rotated_180': 0.0}

    if quad is not None:
        meta['quad_found'] = 1.0
        src = _order_points(quad)
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

    gray = cv2.cvtColor(warped, cv2.COLOR_BGR2GRAY)
    if _timing_bar_score(gray, 'top') > _timing_bar_score(gray, 'bottom') * 1.15:
        warped = cv2.rotate(warped, cv2.ROTATE_180)
        meta['rotated_180'] = 1.0
    return warped, meta


def _disk_darkness(gray: np.ndarray, x: int, y: int, r: int = 8) -> float:
    h, w = gray.shape
    x1, x2 = max(0, x-r-1), min(w, x+r+2)
    y1, y2 = max(0, y-r-1), min(h, y+r+2)
    patch = gray[y1:y2, x1:x2]
    yy, xx = np.ogrid[:patch.shape[0], :patch.shape[1]]
    cx = x - x1
    cy = y - y1
    mask = (xx-cx)**2 + (yy-cy)**2 <= r*r
    vals = patch[mask]
    return float(255.0 - vals.mean()) if vals.size else 0.0



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


def _detect_digit_grid(warped: np.ndarray, roi_box: Tuple[int, int, int, int]) -> Optional[Tuple[List[int], List[int]]]:
    gray = cv2.cvtColor(warped, cv2.COLOR_BGR2GRAY)
    x1, y1, x2, y2 = roi_box
    roi = cv2.GaussianBlur(gray[y1:y2, x1:x2], (3, 3), 1)
    circles = cv2.HoughCircles(
        roi, cv2.HOUGH_GRADIENT, dp=1.2, minDist=15,
        param1=110, param2=18, minRadius=8, maxRadius=15
    )
    if circles is None:
        return None
    pts = np.round(circles[0]).astype(int)
    xs = pts[:, 0] + x1
    ys = pts[:, 1] + y1
    x_centers = _kmeans_centers(xs.astype(float), 10)
    y_centers = _kmeans_centers(ys.astype(float), 10)
    if x_centers is None or y_centers is None:
        return None
    return x_centers, y_centers

def _bubble_metrics(gray: np.ndarray, x: int, y: int) -> Tuple[float, float]:
    """Return (fill_contrast, center_darkness).

    fill_contrast compares the bubble center with a surrounding annulus, so
    it is resistant to shadows / uneven lighting across the sheet.
    """
    r = 18
    h, w = gray.shape
    if x-r < 0 or y-r < 0 or x+r >= w or y+r >= h:
        return 0.0, 0.0
    patch = gray[y-r:y+r+1, x-r:x+r+1].astype(np.float32)
    yy, xx = np.ogrid[-r:r+1, -r:r+1]
    d2 = xx*xx + yy*yy
    center = patch[d2 <= 8*8]
    annulus = patch[(d2 >= 12*12) & (d2 <= 17*17)]
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
        # Strong fill, or a faint fill that is clearly darker than all peers.
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
        # Printed rings naturally have some local contrast.  A clean blank is
        # therefore judged by how far its strongest circle and peer margin
        # remain below a plausible pencil mark, rather than expecting zero.
        contrast_certainty = 1.0 - max(0.0, strongest - 10.0) / 15.0
        margin_certainty = 1.0 - max(0.0, contrast_margin - 3.0) / 12.0
        confidence = max(0.0, min(1.0, contrast_certainty, margin_certainty))

    # A blank answer is a valid reading, not automatically an error.  Only
    # competing marks, a weak selected mark, or unusually dark residue on an
    # otherwise blank row needs a human review.
    strongest = float(np.max(contrasts))
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
            q = q0 + g*10 + r
            row = _read_question(gray, xs, y)
            row['question'] = q
            out.append(row)
    out.sort(key=lambda x: x['question'])
    return out

def _read_digit_columns(
    gray: np.ndarray,
    xs: List[int],
    ys: List[int],
    min_lift: float = 22.0,
    trim_trailing_blanks: bool = False,
) -> Dict:
    digits: List[Optional[int]] = []
    confidences: List[float] = []
    for x in xs:
        vals = np.array([_disk_darkness(gray, x, y, 8) for y in ys], dtype=float)
        order = np.argsort(vals)[::-1]
        best, second = int(order[0]), int(order[1])
        base = float(np.median(vals))
        lift = float(vals[best] - base)
        margin = float(vals[best] - vals[second])
        if lift < min_lift:
            digits.append(None)
            confidences.append(max(0.0, min(1.0, lift / min_lift)))
        else:
            digits.append(best)
            confidences.append(max(0.0, min(1.0, (lift + margin) / 70.0)))
    raw_text = ''.join('?' if d is None else str(d) for d in digits)
    text = raw_text.rstrip('?') if trim_trailing_blanks else raw_text
    return {
        'value': text,
        'raw_value': raw_text,
        'digits': digits,
        'complete': all(d is not None for d in digits),
        'confidence': round(float(np.mean(confidences)), 3),
    }


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
    vals = np.array([_disk_darkness(gray, x, y, 8) for y in ys[:len(labels)]], dtype=float)
    order = np.argsort(vals)[::-1]
    best, second = int(order[0]), int(order[1])
    lift = float(vals[best] - np.median(vals))
    margin = float(vals[best] - vals[second])
    if lift < min_lift or margin < 10.0:
        return None, max(0.0, min(1.0, lift / min_lift)), None
    confidence = max(0.0, min(1.0, (lift + margin) / 70.0))
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


def read_metadata(warped: np.ndarray, side: str) -> Dict:
    if side != 'front':
        return {}
    gray = cv2.cvtColor(warped, cv2.COLOR_BGR2GRAY)

    cand_grid = _detect_digit_grid(warped, (0, int(CANON_H*0.20), int(CANON_W*0.23), int(CANON_H*0.53)))
    school_grid = _detect_digit_grid(warped, (0, int(CANON_H*0.63), int(CANON_W*0.23), CANON_H))

    if cand_grid is None:
        cand_x, cand_y = CAND_X, CAND_Y
    else:
        cand_x, cand_y = cand_grid
    if school_grid is None:
        school_x, school_y = SCHOOL_X, SCHOOL_Y
    else:
        school_x, school_y = school_grid

    return {
        'candidate_id': _read_digit_columns(gray, cand_x, cand_y, min_lift=20),
        'school_code': _read_digit_columns(
            gray, school_x, school_y, min_lift=24, trim_trailing_blanks=True,
        ),
        'subject_code': _read_subject_code(warped, gray),
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


def scan_image_bytes(data: bytes, side: str) -> Dict:
    arr = np.frombuffer(data, np.uint8)
    img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    if img is None:
        raise ValueError('Invalid image')
    warped, geom = rectify_document(img)
    answers = read_answers(warped, side)
    meta = read_metadata(warped, side)
    debug = make_debug_overlay(warped, side, answers)
    answered = sum(1 for a in answers if a['status'] == 'ok')
    blank = sum(1 for a in answers if a['status'] == 'blank')
    multiple = sum(1 for a in answers if a['status'] == 'multiple')
    review = sum(1 for a in answers if a['needs_review'])
    quality = {
        'resolution_ok': bool(img.shape[1] >= 1400 and img.shape[0] >= 900),
        'quad_found': bool(geom['quad_found']),
        'answered': answered,
        'blank_answers': blank,
        'multiple_answers': multiple,
        'needs_review': review,
        # Kept for clients built against the original prototype API.
        'uncertain_answers': review,
        'average_confidence': round(float(np.mean([a['confidence'] for a in answers])), 3),
    }
    return {
        'side': side,
        'answers': answers,
        'metadata': meta,
        'quality': quality,
        'debug_image_base64': encode_jpeg_b64(debug),
    }
