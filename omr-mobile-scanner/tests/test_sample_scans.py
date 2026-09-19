import base64
import os
import sys
import unittest
from pathlib import Path

import cv2
import numpy as np


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))

from omr import scan_image_bytes  # noqa: E402


DEFAULT_FRONT = ROOT / 'tests/fixtures/front.jpeg'
DEFAULT_BACK = ROOT / 'tests/fixtures/back.jpeg'
if not DEFAULT_FRONT.is_file():
    DEFAULT_FRONT = Path.home() / 'Downloads/IMG_4962.jpeg'
if not DEFAULT_BACK.is_file():
    DEFAULT_BACK = Path.home() / 'Downloads/IMG_4963.jpeg'
FRONT_IMAGE = Path(os.environ.get('OMR_FRONT_IMAGE', DEFAULT_FRONT))
BACK_IMAGE = Path(os.environ.get('OMR_BACK_IMAGE', DEFAULT_BACK))


class SampleScanTests(unittest.TestCase):
    @staticmethod
    def _encode(image):
        ok, buffer = cv2.imencode('.jpg', image, [cv2.IMWRITE_JPEG_QUALITY, 92])
        if not ok:
            raise AssertionError('could not encode test image')
        return buffer.tobytes()

    @unittest.skipUnless(FRONT_IMAGE.is_file(), 'front sample image is not available')
    def test_front_answers_and_candidate_id(self):
        result = scan_image_bytes(FRONT_IMAGE.read_bytes(), 'front')
        expected = list('DCACADBCADBDBACBDABC')

        self.assertTrue(result['quality']['quad_found'])
        self.assertRegex(result['metadata']['candidate_id']['value'], r'^\d{10}$')
        self.assertEqual(result['metadata']['school_code']['value'], '12')
        self.assertEqual(result['metadata']['subject_code']['value'], 'สค02037')
        self.assertTrue(all(
            result['metadata'][field]['grid_detected']
            for field in ('candidate_id', 'school_code', 'subject_code')
        ))
        self.assertEqual([a['choice'] for a in result['answers'][:20]], expected)
        self.assertTrue(all(a['status'] == 'blank' for a in result['answers'][20:]))
        self.assertEqual(result['quality']['answered'], 20)
        self.assertEqual(result['quality']['blank_answers'], 30)
        self.assertLessEqual(result['quality']['needs_review'], 2)
        self.assertEqual(result['audit']['pipeline_version'], 'professional-omr-1.1')
        self.assertGreaterEqual(result['quality']['timing_bar_count'], 45)
        self.assertGreaterEqual(result['quality']['registration_confidence'], 0.5)
        self.assertGreaterEqual(result['audit']['stages']['local_mesh']['coverage'], 0.9)
        self.assertTrue(all('top1' in answer and 'top2' in answer for answer in result['answers']))

        debug_bytes = base64.b64decode(result['debug_image_base64'])
        debug = cv2.imdecode(np.frombuffer(debug_bytes, np.uint8), cv2.IMREAD_COLOR)
        self.assertIsNotNone(debug)
        green = (
            (debug[:, :, 1] > 140)
            & (debug[:, :, 1] > debug[:, :, 0] + 40)
            & (debug[:, :, 1] > debug[:, :, 2] + 40)
        )
        # The chosen bubbles must be visible in every metadata section, not
        # only in the answer area.
        self.assertGreater(np.count_nonzero(green[180:750, 0:400]), 200)
        self.assertGreater(np.count_nonzero(green[700:1180, 0:400]), 40)
        self.assertGreater(np.count_nonzero(green[620:1120, 390:630]), 120)

    @unittest.skipUnless(BACK_IMAGE.is_file(), 'back sample image is not available')
    def test_back_is_blank_without_false_positives(self):
        result = scan_image_bytes(BACK_IMAGE.read_bytes(), 'back')

        self.assertTrue(result['quality']['quad_found'])
        self.assertEqual(result['quality']['answered'], 0)
        self.assertEqual(result['quality']['blank_answers'], 50)
        self.assertEqual(result['quality']['multiple_answers'], 0)
        self.assertEqual(result['quality']['needs_review'], 0)
        self.assertTrue(all(a['choice'] is None for a in result['answers']))

    @unittest.skipUnless(FRONT_IMAGE.is_file(), 'front sample image is not available')
    def test_portrait_camera_orientation_is_corrected(self):
        image = cv2.imdecode(np.frombuffer(FRONT_IMAGE.read_bytes(), np.uint8), cv2.IMREAD_COLOR)
        portrait = cv2.rotate(image, cv2.ROTATE_90_CLOCKWISE)
        result = scan_image_bytes(self._encode(portrait), 'front')

        self.assertTrue(result['quality']['capture_ok'])
        self.assertEqual([a['choice'] for a in result['answers'][:20]], list('DCACADBCADBDBACBDABC'))
        self.assertEqual(result['metadata']['candidate_id']['value'], '6823000662')

    @unittest.skipUnless(FRONT_IMAGE.is_file(), 'front sample image is not available')
    def test_perspective_photo_is_rectified(self):
        image = cv2.imdecode(np.frombuffer(FRONT_IMAGE.read_bytes(), np.uint8), cv2.IMREAD_COLOR)
        height, width = image.shape[:2]
        source = np.float32([[0, 0], [width - 1, 0], [width - 1, height - 1], [0, height - 1]])
        target = np.float32([[130, 90], [width - 170, 0], [width - 20, height - 80], [0, height - 10]])
        transformed = cv2.warpPerspective(
            image,
            cv2.getPerspectiveTransform(source, target),
            (width, height),
            borderValue=(35, 35, 35),
        )
        result = scan_image_bytes(self._encode(transformed), 'front')

        self.assertTrue(result['quality']['capture_ok'])
        self.assertEqual([a['choice'] for a in result['answers'][:20]], list('DCACADBCADBDBACBDABC'))

    @unittest.skipUnless(FRONT_IMAGE.is_file(), 'front sample image is not available')
    def test_wrong_side_is_blocked_by_registration_marks(self):
        result = scan_image_bytes(FRONT_IMAGE.read_bytes(), 'back')

        self.assertFalse(result['quality']['capture_ok'])
        self.assertIn('side_mismatch', result['quality']['issues'])

    @unittest.skipUnless(FRONT_IMAGE.is_file(), 'front sample image is not available')
    def test_blurred_capture_is_blocked_before_scoring(self):
        image = cv2.imdecode(np.frombuffer(FRONT_IMAGE.read_bytes(), np.uint8), cv2.IMREAD_COLOR)
        blurred = cv2.GaussianBlur(image, (31, 31), 12)
        result = scan_image_bytes(self._encode(blurred), 'front')

        self.assertFalse(result['quality']['capture_ok'])
        self.assertIn('image_blur', result['quality']['issues'])


if __name__ == '__main__':
    unittest.main()
