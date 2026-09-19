from __future__ import annotations

import base64
import io
import math
import os
import time
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

import cv2
import numpy as np
try:
    from PIL import Image, ImageOps
except ImportError:
    Image = None
    ImageOps = None



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
PIPELINE_VERSION = 'professional-omr-1.0'


@dataclass
class OMRChannels:
    """Illumination-normalised channels used for mark measurement.

    ``gray`` preserves edges, ``pencil`` suppresses the orange form printing,
    and ``binary`` is an adaptive local foreground mask.  Keeping the three
    signals separate is important: OMR measures graphite coverage; it does not
    try to recognise a character with OCR.
    """

    gray: np.ndarray
    pencil: np.ndarray
    normalized: np.ndarray
    binary: np.ndarray


def _build_omr_channels(warped: np.ndarray) -> OMRChannels:
    gray = cv2.cvtColor(warped, cv2.COLOR_BGR2GRAY)
    pencil = _pencil_gray(warped)

    # Retinex-like local illumination correction.  Division removes broad
    # phone/hand shadows while retaining pencil strokes and printed rings.
    background = cv2.GaussianBlur(pencil, (0, 0), sigmaX=31, sigmaY=31)
    background = np.maximum(background, 24).astype(np.uint8)
    normalized = cv2.divide(pencil, background, scale=220)
    normalized = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(12, 9)).apply(normalized)
    binary = cv2.adaptiveThreshold(
        normalized,
        255,
        cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
        cv2.THRESH_BINARY_INV,
        31,
        8,
    )
    binary = cv2.morphologyEx(binary, cv2.MORPH_OPEN, np.ones((2, 2), np.uint8))
    return OMRChannels(gray=gray, pencil=pencil, normalized=normalized, binary=binary)


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


def _correct_lens_distortion(image: np.ndarray) -> Tuple[np.ndarray, bool]:
    """Apply an optional calibrated phone-camera lens profile.

    Modern phones normally correct the exported JPEG themselves.  Dedicated
    kiosk cameras can provide OpenCV coefficients through
    ``OMR_LENS_COEFFICIENTS=k1,k2,p1,p2,k3`` and an optional
    ``OMR_CAMERA_FOCAL_RATIO``.  We never invent coefficients because a wrong
    profile reduces OMR accuracy more than leaving a corrected JPEG untouched.
    """
    raw = os.getenv('OMR_LENS_COEFFICIENTS', '').strip()
    if not raw:
        return image, False
    try:
        coefficients = [float(value.strip()) for value in raw.split(',')]
        if len(coefficients) not in (4, 5):
            return image, False
        if len(coefficients) == 4:
            coefficients.append(0.0)
        focal_ratio = float(os.getenv('OMR_CAMERA_FOCAL_RATIO', '1.0'))
    except ValueError:
        return image, False
    h, w = image.shape[:2]
    focal = max(h, w) * max(0.5, min(focal_ratio, 3.0))
    camera = np.array([[focal, 0.0, w / 2.0], [0.0, focal, h / 2.0], [0.0, 0.0, 1.0]])
    return cv2.undistort(image, camera, np.asarray(coefficients, dtype=np.float64)), True

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


def _detect_fiducials(warped: np.ndarray) -> Dict:
    """Locate timing bars and edge registration squares after coarse warp."""
    channels = _build_omr_channels(warped)
    binary = channels.binary
    h, w = binary.shape

    y0 = int(h * 0.88)
    strip = binary[y0:int(h * 0.998), :]
    count, _labels, stats, centroids = cv2.connectedComponentsWithStats(strip, 8)
    timing_points: List[Tuple[float, float]] = []
    for idx in range(1, count):
        x, y, width, height, area = (int(v) for v in stats[idx])
        # The printed timing bars are narrow, solid and share one baseline.
        if 6 <= width <= 15 and 25 <= height <= 44 and y >= int(strip.shape[0] * 0.42) and area >= 190:
            timing_points.append((float(centroids[idx][0]), float(centroids[idx][1] + y0)))
    timing_points.sort(key=lambda point: point[0])
    timing_x = [point[0] for point in timing_points]
    timing_x.sort()
    diffs = np.diff(timing_x) if len(timing_x) > 1 else np.array([], dtype=float)
    spacing_cv = float(np.std(diffs) / max(np.mean(diffs), 1.0)) if diffs.size else 1.0
    count_score = max(0.0, 1.0 - abs(len(timing_x) - 50) / 25.0)
    timing_confidence = float(np.clip(count_score * (1.0 - min(spacing_cv, 0.6)), 0.0, 1.0))
    if len(timing_points) >= 15:
        slope, _intercept = np.polyfit(
            np.asarray([point[0] for point in timing_points]),
            np.asarray([point[1] for point in timing_points]),
            1,
        )
        timing_angle = math.degrees(math.atan(float(slope)))
    else:
        timing_angle = 0.0

    # Registration marks are compact dark squares near one vertical page edge.
    full_count, _full_labels, full_stats, full_centroids = cv2.connectedComponentsWithStats(binary, 8)
    marks: List[Tuple[float, float, float]] = []
    for idx in range(1, full_count):
        x, y, width, height, area = (int(v) for v in full_stats[idx])
        if not (14 <= width <= 42 and 14 <= height <= 42 and area >= 180):
            continue
        aspect = min(width, height) / max(width, height)
        fill = area / float(width * height)
        cx, cy = (float(v) for v in full_centroids[idx])
        near_edge = cx < w * 0.09 or cx > w * 0.91
        if near_edge and cy > h * 0.45 and aspect >= 0.68 and fill >= 0.38:
            marks.append((round(cx, 1), round(cy, 1), round(fill, 3)))

    left_marks = [mark for mark in marks if mark[0] < w / 2]
    right_marks = [mark for mark in marks if mark[0] >= w / 2]
    side_hint = 'front' if len(left_marks) > len(right_marks) else 'back' if len(right_marks) > len(left_marks) else 'unknown'
    registration_confidence = float(min(1.0, max(len(left_marks), len(right_marks)) / 2.0))
    return {
        'timing_bar_count': len(timing_x),
        'timing_confidence': round(timing_confidence, 3),
        'timing_spacing_cv': round(spacing_cv, 3),
        'timing_angle_degrees': round(timing_angle, 3),
        'timing_span': [round(timing_x[0], 1), round(timing_x[-1], 1)] if timing_x else [],
        'registration_marks': marks[:8],
        'registration_confidence': round(registration_confidence, 3),
        'detected_side': side_hint,
    }


def _fine_align_with_timing_marks(warped: np.ndarray) -> Tuple[np.ndarray, Dict]:
    """Remove the small residual roll left after the global homography."""
    fiducials = _detect_fiducials(warped)
    angle = float(fiducials.get('timing_angle_degrees', 0.0))
    if fiducials['timing_confidence'] >= 0.65 and 0.06 <= abs(angle) <= 1.5:
        matrix = cv2.getRotationMatrix2D((CANON_W / 2.0, CANON_H / 2.0), angle, 1.0)
        warped = cv2.warpAffine(
            warped,
            matrix,
            (CANON_W, CANON_H),
            flags=cv2.INTER_LINEAR,
            borderMode=cv2.BORDER_REPLICATE,
        )
        fiducials = _detect_fiducials(warped)
        fiducials['fine_alignment_applied'] = True
        fiducials['fine_alignment_rotation_degrees'] = round(angle, 3)
    else:
        fiducials['fine_alignment_applied'] = False
        fiducials['fine_alignment_rotation_degrees'] = 0.0
    return warped, fiducials


def rectify_document(image: np.ndarray) -> Tuple[np.ndarray, Dict[str, float]]:
    image, lens_corrected = _correct_lens_distortion(image)
    quad = _largest_document_quad(image)
    meta = {
        'quad_found': 0.0,
        'rotated_180': 0.0,
        'document_area_ratio': 0.0,
        'geometry_confidence': 0.0,
        'lens_profile_applied': 1.0 if lens_corrected else 0.0,
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
        # A phone may be held in portrait while photographing the landscape
        # form.  Rotate the source corner order before homography so the long
        # paper edge always maps to canonical X.
        if (top + bottom) < (left + right):
            src = src[[3, 0, 1, 2]]
            top, right, bottom, left = left, top, right, bottom
            meta['source_rotated_90'] = 1.0
        else:
            meta['source_rotated_90'] = 0.0
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

    # Keep only circles that lie on one of the selected rows.  A real answer
    # column repeats in nearly all ten rows; printed logos/labels do not.  This
    # repeatability test is safer than forcing every detected circle into
    # 20-way K-means (which can silently create a false column).
    keep = np.array([min(abs(int(y) - ry) for ry in row_centers) <= 7 for y in ys], dtype=bool)
    x_on_rows = xs[keep]
    repeated = [(center, n) for center, n in _cluster_1d(x_on_rows.astype(float), 7.0) if n >= 7]
    if len(repeated) >= 20:
        col_centers = sorted(int(round(center)) for center, _n in sorted(repeated, key=lambda item: item[1], reverse=True)[:20])
    else:
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

    # Each four-choice block must be tight and regular.  Gaps between blocks
    # are allowed to be larger, but no in-block gap may jump to unrelated art.
    for start in range(0, 20, 4):
        local_diffs = np.diff(np.asarray(col_centers[start:start + 4], dtype=float))
        if np.any(local_diffs < 20) or np.any(local_diffs > 45):
            return None

    x_groups = [col_centers[i:i+4] for i in range(0, 20, 4)]
    return x_groups, row_centers


def _build_local_mesh(
    warped: np.ndarray,
    side: str,
    x_groups: List[List[int]],
    ys: List[int],
) -> Tuple[Dict[Tuple[int, int, int], Tuple[int, int]], float]:
    """Snap each expected bubble to its nearby printed ring.

    Global homography handles a flat page.  These per-cell offsets compensate
    for mild paper curl and residual lens distortion without bending the whole
    image or moving metadata regions.
    """
    gray = cv2.cvtColor(warped, cv2.COLOR_BGR2GRAY)
    if side == 'front':
        x1, y1, x2, y2 = int(CANON_W * 0.39), int(CANON_H * 0.35), CANON_W, CANON_H
    else:
        x1, y1, x2, y2 = 0, int(CANON_H * 0.34), int(CANON_W * 0.63), CANON_H
    roi = cv2.GaussianBlur(gray[y1:y2, x1:x2], (3, 3), 1)
    circles = cv2.HoughCircles(
        roi, cv2.HOUGH_GRADIENT, dp=1.2, minDist=15,
        param1=120, param2=19, minRadius=8, maxRadius=15,
    )
    if circles is None:
        return {}, 0.0
    points = np.round(circles[0, :, :2]).astype(int)
    points[:, 0] += x1
    points[:, 1] += y1
    mesh: Dict[Tuple[int, int, int], Tuple[int, int]] = {}
    for group_index, xs in enumerate(x_groups):
        for row_index, y in enumerate(ys):
            for option_index, x in enumerate(xs):
                distances = np.hypot(points[:, 0] - x, points[:, 1] - y)
                nearest = int(np.argmin(distances))
                if distances[nearest] <= 13.0:
                    mesh[(group_index, row_index, option_index)] = (
                        int(points[nearest, 0]), int(points[nearest, 1]),
                    )
    return mesh, float(len(mesh) / max(len(x_groups) * len(ys) * 4, 1))


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


def _bubble_features(channels: OMRChannels, x: int, y: int) -> Dict[str, float]:
    gray_contrast, darkness = _bubble_metrics(channels.gray, x, y)
    pencil_contrast, _pencil_darkness = _bubble_metrics(channels.pencil, x, y)
    normalized_contrast, _ = _bubble_metrics(channels.normalized, x, y)
    r = 17
    h, w = channels.binary.shape
    if x - r < 0 or y - r < 0 or x + r >= w or y + r >= h:
        center_ink = annulus_ink = 0.0
    else:
        patch = channels.binary[y-r:y+r+1, x-r:x+r+1]
        yy, xx = np.ogrid[-r:r+1, -r:r+1]
        d2 = xx * xx + yy * yy
        center_ink = float(np.mean(patch[d2 <= 8 * 8] > 0))
        annulus_ink = float(np.mean(patch[(d2 >= 12 * 12) & (d2 <= 17 * 17)] > 0))
    ink_lift = center_ink - 0.55 * annulus_ink
    score = (
        0.55 * max(gray_contrast, 0.0)
        + 0.25 * max(pencil_contrast, 0.0)
        + 0.20 * max(normalized_contrast, 0.0)
        + 22.0 * max(ink_lift, 0.0)
    )
    return {
        'score': float(score),
        'contrast': float(gray_contrast),
        'pencil_contrast': float(pencil_contrast),
        'normalized_contrast': float(normalized_contrast),
        'darkness': float(darkness),
        'ink_coverage': float(center_ink),
        'ink_lift': float(ink_lift),
    }


def _borderline_mark_probability(feature: Dict[str, float], baseline: float, scale: float) -> float:
    """Small logistic classifier used only inside the ambiguous band.

    Its inputs are OMR fill features, never pixels/characters.  Coefficients
    are deliberately conservative; clear marks and clear blanks bypass it.
    Feature/audit output is retained so a site-specific trained model can
    replace these coefficients when labelled review data is available.
    """
    z_score = (feature['score'] - baseline) / max(scale, 4.0)
    logit = (
        -3.15
        + 0.72 * z_score
        + 0.055 * feature['contrast']
        + 0.032 * feature['pencil_contrast']
        + 2.4 * max(feature['ink_lift'], 0.0)
    )
    return float(1.0 / (1.0 + math.exp(-float(np.clip(logit, -20.0, 20.0)))))


def _read_question(
    channels: OMRChannels,
    positions: List[Tuple[int, int]],
    normalization: Dict[str, float],
) -> Dict:
    features = [_bubble_features(channels, x, y) for x, y in positions]
    contrasts = np.array([feature['contrast'] for feature in features], dtype=float)
    darkness = np.array([feature['darkness'] for feature in features], dtype=float)
    composite = np.array([feature['score'] for feature in features], dtype=float)
    baseline = normalization['median']
    scale = normalization['robust_scale']
    adaptive_threshold = normalization['mark_threshold']

    valid: List[int] = []
    for j in range(4):
        other_dark = max(float(darkness[k]) for k in range(4) if k != j)
        dark_margin = float(darkness[j] - other_dark)
        classical = contrasts[j] >= 25.0 or (contrasts[j] >= 13.0 and dark_margin >= 18.0)
        adaptive = (
            composite[j] >= adaptive_threshold + 6.0
            and features[j]['contrast'] >= 15.0
            and features[j]['pencil_contrast'] >= 12.0
            and features[j]['ink_lift'] >= 0.08
            and dark_margin >= 12.0
        )
        if classical or adaptive:
            valid.append(j)

    if len(valid) == 1:
        top = valid[0]
        status = 'ok'
        choice = OPTIONS[top]
        second = max(float(composite[k]) for k in range(4) if k != top)
        separation = float(composite[top] - second)
        strength = (float(composite[top]) - baseline) / max(scale, 4.0)
        confidence = float(np.clip(0.35 + 0.12 * strength + 0.018 * separation, 0.0, 1.0))
    elif len(valid) > 1:
        status = 'multiple'
        choice = None
        confidence = min(1.0, max(float(composite[j]) for j in valid) / max(adaptive_threshold, 30.0))
    else:
        status = 'blank'
        choice = None
        strongest = float(np.max(composite))
        second = float(np.partition(composite, -2)[-2])
        confidence = float(np.clip((adaptive_threshold - strongest + 12.0) / 24.0, 0.0, 1.0))

    top_order = np.argsort(composite)[::-1]
    top_idx, second_idx = int(top_order[0]), int(top_order[1])
    top_probability = _borderline_mark_probability(features[top_idx], baseline, scale)
    classifier_used = bool(
        status != 'multiple'
        and adaptive_threshold - 10.0 <= composite[top_idx] <= adaptive_threshold + 12.0
    )
    # The secondary classifier never promotes a blank into an answer.  It may
    # only request human review; this prevents shadows/print artefacts from
    # becoming a confident but wrong choice.

    if status == 'multiple':
        needs_review = True
        review_reason = 'multiple_marks'
    elif status == 'ok' and confidence < 0.62:
        needs_review = True
        review_reason = 'low_confidence'
    elif status == 'blank' and (confidence < 0.55 or (classifier_used and top_probability >= 0.72)):
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
        'scores': {OPTIONS[i]: round(float(composite[i]), 2) for i in range(4)},
        'margin': round(float(composite[top_idx] - composite[second_idx]), 2),
        'top1': {'choice': OPTIONS[top_idx], 'score': round(float(composite[top_idx]), 2)},
        'top2': {'choice': OPTIONS[second_idx], 'score': round(float(composite[second_idx]), 2)},
        'classifier_used': classifier_used,
        'classifier_policy': 'review_only',
        'mark_probability': round(top_probability, 3),
        'features': {
            OPTIONS[i]: {key: round(float(value), 3) for key, value in features[i].items() if key != 'score'}
            for i in range(4)
        },
    }


def read_answers(warped: np.ndarray, side: str, with_diagnostics: bool = False):
    channels = _build_omr_channels(warped)
    dynamic = _detect_answer_grid(warped, side)
    if dynamic is not None:
        x_groups, ys = dynamic
    elif side == 'front':
        x_groups, ys = FRONT_X, FRONT_Y
    else:
        x_groups, ys = BACK_X, BACK_Y

    local_mesh, mesh_coverage = _build_local_mesh(warped, side, x_groups, ys)

    def bubble_position(group_index: int, row_index: int, option_index: int) -> Tuple[int, int]:
        return local_mesh.get(
            (group_index, row_index, option_index),
            (x_groups[group_index][option_index], ys[row_index]),
        )

    all_scores = [
        _bubble_features(channels, *bubble_position(group_index, row_index, option_index))['score']
        for group_index, _xs in enumerate(x_groups)
        for row_index, _y in enumerate(ys)
        for option_index in range(4)
    ]
    score_values = np.asarray(all_scores, dtype=float)
    median = float(np.median(score_values))
    mad = float(np.median(np.abs(score_values - median)))
    robust_scale = max(4.0, 1.4826 * mad)
    # A sheet-level threshold adapts to pencil hardness and camera exposure.
    # The lower bound keeps pre-printed rings from becoming answers.
    normalization = {
        'median': median,
        'mad': mad,
        'robust_scale': robust_scale,
        'mark_threshold': max(30.0, median + 3.25 * robust_scale),
        'p95': float(np.percentile(score_values, 95)),
    }

    q0 = 1 if side == 'front' else 51
    out: List[Dict] = []
    for g, xs in enumerate(x_groups):
        for r, y in enumerate(ys):
            q = q0 + g * 10 + r
            positions = [bubble_position(g, r, option_index) for option_index in range(4)]
            row = _read_question(channels, positions, normalization)
            row['question'] = q
            out.append(row)
    out.sort(key=lambda x: x['question'])
    diagnostics = {
        'grid_source': 'detected' if dynamic is not None else 'calibrated_fallback',
        'x_groups': x_groups,
        'rows': ys,
        'local_mesh_coverage': round(mesh_coverage, 3),
        'normalization': {key: round(float(value), 3) for key, value in normalization.items()},
        'ambiguous_classifier_calls': sum(1 for answer in out if answer['classifier_used']),
    }
    return (out, diagnostics) if with_diagnostics else out


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
    if Image is not None and ImageOps is not None:
        try:
            with Image.open(io.BytesIO(data)) as source:
                oriented = ImageOps.exif_transpose(source).convert('RGB')
                return cv2.cvtColor(np.asarray(oriented), cv2.COLOR_RGB2BGR)
        except Exception:
            pass
    arr = np.frombuffer(data, np.uint8)
    image = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    if image is None:
        raise ValueError('Invalid image')
    return image


def _capture_quality(
    image: np.ndarray,
    warped: np.ndarray,
    geom: Dict[str, float],
    grid_found: bool,
    fiducials: Dict,
    requested_side: str,
) -> Dict:
    gray = cv2.cvtColor(warped, cv2.COLOR_BGR2GRAY)
    sharpness = float(cv2.Laplacian(gray, cv2.CV_64F).var())
    brightness = float(np.mean(gray))
    glare_ratio = float(np.mean(gray >= 248))
    dark_clip_ratio = float(np.mean(gray <= 18))
    illumination = cv2.GaussianBlur(gray, (0, 0), sigmaX=50, sigmaY=50)
    illumination_cv = float(np.std(illumination) / max(np.mean(illumination), 1.0))
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
    if dark_clip_ratio > 0.10:
        issues.append('shadow_clipping')
    if illumination_cv > 0.42:
        issues.append('uneven_lighting')
    if fiducials.get('timing_confidence', 0.0) < 0.55:
        issues.append('timing_marks_missing')
    if fiducials.get('registration_confidence', 0.0) < 0.5:
        issues.append('registration_marks_missing')
    detected_side = fiducials.get('detected_side', 'unknown')
    side_evidence_reliable = (
        fiducials.get('registration_confidence', 0.0) >= 0.5
        and fiducials.get('timing_confidence', 0.0) >= 0.55
    )
    if side_evidence_reliable and detected_side != 'unknown' and detected_side != requested_side:
        issues.append('side_mismatch')
    if not grid_found:
        issues.append('answer_grid_fallback')
    blocking = {
        'document_edges_missing', 'excessive_perspective', 'image_blur',
        'too_dark', 'too_bright', 'glare', 'shadow_clipping',
        'timing_marks_missing', 'registration_marks_missing',
        'side_mismatch', 'answer_grid_fallback',
    }
    capture_ok = not any(issue in blocking for issue in issues)
    quality_score = float(np.clip(
        0.25 * float(geom['geometry_confidence'])
        + 0.25 * float(fiducials.get('timing_confidence', 0.0))
        + 0.20 * float(fiducials.get('registration_confidence', 0.0))
        + 0.15 * min(sharpness / 240.0, 1.0)
        + 0.15 * max(0.0, 1.0 - illumination_cv / 0.50),
        0.0,
        1.0,
    ))
    return {
        'resolution_ok': bool(image.shape[1] >= 1400 and image.shape[0] >= 900),
        'capture_ok': capture_ok,
        'quality_gate': 'passed' if capture_ok else 'blocked',
        'quality_score': round(quality_score, 3),
        'issues': issues,
        'sharpness': round(sharpness, 1),
        'brightness': round(brightness, 1),
        'glare_ratio': round(glare_ratio, 4),
        'dark_clip_ratio': round(dark_clip_ratio, 4),
        'illumination_cv': round(illumination_cv, 4),
        'document_area_ratio': round(float(geom['document_area_ratio']), 3),
        'geometry_confidence': round(float(geom['geometry_confidence']), 3),
        'answer_grid_detected': grid_found,
        'timing_bar_count': int(fiducials.get('timing_bar_count', 0)),
        'timing_confidence': float(fiducials.get('timing_confidence', 0.0)),
        'registration_confidence': float(fiducials.get('registration_confidence', 0.0)),
        'detected_side': detected_side,
    }


def scan_image_bytes(data: bytes, side: str, include_debug: bool = True) -> Dict:
    started = time.perf_counter()
    img = _decode_mobile_image(data)
    decoded_at = time.perf_counter()
    warped, geom = rectify_document(img)
    warped, fiducials = _fine_align_with_timing_marks(warped)
    aligned_at = time.perf_counter()
    answers, read_diagnostics = read_answers(warped, side, with_diagnostics=True)
    grid_found = read_diagnostics['grid_source'] == 'detected'
    answers_at = time.perf_counter()
    meta = read_metadata(warped, side)
    debug = make_debug_overlay(warped, side, answers) if include_debug else None
    finished = time.perf_counter()
    answered = sum(1 for a in answers if a['status'] == 'ok')
    blank = sum(1 for a in answers if a['status'] == 'blank')
    multiple = sum(1 for a in answers if a['status'] == 'multiple')
    review = sum(1 for a in answers if a['needs_review'])
    average_confidence = round(float(np.mean([a['confidence'] for a in answers])), 3)
    quality = {
        **_capture_quality(img, warped, geom, grid_found, fiducials, side),
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
        quality['quality_gate'] = 'blocked'
    if side == 'front':
        identity_reliable = (
            meta.get('candidate_id', {}).get('complete')
            and meta.get('candidate_id', {}).get('confidence', 0) >= 0.55
            and meta.get('subject_code', {}).get('complete')
            and meta.get('subject_code', {}).get('confidence', 0) >= 0.55
            and meta.get('school_code', {}).get('complete')
            and meta.get('school_code', {}).get('confidence', 0) >= 0.55
        )
        if not identity_reliable:
            quality['issues'].append('metadata_unreliable')
            quality['capture_ok'] = False
            quality['quality_gate'] = 'blocked'
    audit = {
        'pipeline_version': PIPELINE_VERSION,
        'stages': {
            'document_segmentation': bool(geom['quad_found']),
            'global_homography': bool(geom['quad_found']),
            'lens_correction': 'calibrated-profile' if geom.get('lens_profile_applied') else 'device-jpeg-profile',
            'orientation_correction': True,
            'registration_marks': fiducials,
            'fine_alignment': bool(fiducials.get('fine_alignment_applied')),
            'local_mesh': {
                'source': read_diagnostics['grid_source'],
                'coverage': read_diagnostics['local_mesh_coverage'],
            },
            'color_normalization': 'red-suppressed-pencil-channel',
            'illumination_correction': 'local-retinex-clahe',
            'template_subtraction': 'center-minus-annulus',
            'bubble_scoring': 'contrast+pencil+adaptive-coverage',
            'ambiguous_classifier': 'logistic-feature-fusion',
        },
        'normalization': read_diagnostics['normalization'],
        'ambiguous_classifier_calls': read_diagnostics['ambiguous_classifier_calls'],
        'timings_ms': {
            'decode': round((decoded_at - started) * 1000.0, 1),
            'alignment': round((aligned_at - decoded_at) * 1000.0, 1),
            'answers': round((answers_at - aligned_at) * 1000.0, 1),
            'metadata_and_overlay': round((finished - answers_at) * 1000.0, 1),
            'total': round((finished - started) * 1000.0, 1),
        },
    }
    result = {
        'side': side,
        'answers': answers,
        'metadata': meta,
        'quality': quality,
        'audit': audit,
    }
    if debug is not None:
        result['debug_image_base64'] = encode_jpeg_b64(debug)
    return result
