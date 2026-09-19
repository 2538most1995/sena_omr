import os
import sys
import unittest
from pathlib import Path

import cv2
import numpy as np


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))

from main import _preflight_response  # noqa: E402
from omr import scan_image_bytes  # noqa: E402


DEFAULT_FRONT = ROOT / 'tests/fixtures/front.jpeg'
if not DEFAULT_FRONT.is_file():
    DEFAULT_FRONT = Path.home() / 'Downloads/IMG_4962.jpeg'
FRONT_IMAGE = Path(os.environ.get('OMR_FRONT_IMAGE', DEFAULT_FRONT))


class PreflightTests(unittest.TestCase):
    @staticmethod
    def _encode(image):
        ok, buffer = cv2.imencode('.jpg', image, [cv2.IMWRITE_JPEG_QUALITY, 85])
        if not ok:
            raise AssertionError('could not encode test image')
        return buffer.tobytes()

    @unittest.skipUnless(FRONT_IMAGE.is_file(), 'front sample image is not available')
    def test_readable_frame_returns_green_before_capture(self):
        result = scan_image_bytes(FRONT_IMAGE.read_bytes(), 'front', include_debug=False)
        response = _preflight_response(result, 'front')

        self.assertTrue(response['ready'])
        self.assertEqual(response['frame_color'], 'green')
        self.assertTrue(all(response['checks'].values()))
        self.assertNotIn('debug_image_base64', result)

    @unittest.skipUnless(FRONT_IMAGE.is_file(), 'front sample image is not available')
    def test_blurred_frame_returns_red_with_focus_instruction(self):
        image = cv2.imdecode(np.frombuffer(FRONT_IMAGE.read_bytes(), np.uint8), cv2.IMREAD_COLOR)
        blurred = cv2.GaussianBlur(image, (31, 31), 12)
        result = scan_image_bytes(self._encode(blurred), 'front', include_debug=False)
        response = _preflight_response(result, 'front')

        self.assertFalse(response['ready'])
        self.assertEqual(response['frame_color'], 'red')
        self.assertFalse(response['checks']['focus'])
        self.assertTrue(any('โฟกัส' in instruction for instruction in response['guidance']))

    @unittest.skipUnless(FRONT_IMAGE.is_file(), 'front sample image is not available')
    def test_wrong_side_returns_red_with_flip_instruction(self):
        result = scan_image_bytes(FRONT_IMAGE.read_bytes(), 'back', include_debug=False)
        response = _preflight_response(result, 'back')

        self.assertFalse(response['ready'])
        self.assertFalse(response['checks']['correct_side'])
        self.assertTrue(any('กลับกระดาษ' in instruction for instruction in response['guidance']))


if __name__ == '__main__':
    unittest.main()
