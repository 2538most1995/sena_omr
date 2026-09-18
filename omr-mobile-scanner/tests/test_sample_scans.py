import os
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))

from omr import scan_image_bytes  # noqa: E402


FRONT_IMAGE = Path(os.environ.get('OMR_FRONT_IMAGE', ROOT / 'tests/fixtures/front.jpeg'))
BACK_IMAGE = Path(os.environ.get('OMR_BACK_IMAGE', ROOT / 'tests/fixtures/back.jpeg'))


class SampleScanTests(unittest.TestCase):
    @unittest.skipUnless(FRONT_IMAGE.is_file(), 'front sample image is not available')
    def test_front_answers_and_candidate_id(self):
        result = scan_image_bytes(FRONT_IMAGE.read_bytes(), 'front')
        expected = list('DCACADBCADBDBACBDABC')

        self.assertTrue(result['quality']['quad_found'])
        self.assertRegex(result['metadata']['candidate_id']['value'], r'^\d{10}$')
        self.assertEqual(result['metadata']['school_code']['value'], '12')
        self.assertEqual(result['metadata']['subject_code']['value'], 'สค02037')
        self.assertEqual([a['choice'] for a in result['answers'][:20]], expected)
        self.assertTrue(all(a['status'] == 'blank' for a in result['answers'][20:]))
        self.assertEqual(result['quality']['answered'], 20)
        self.assertEqual(result['quality']['blank_answers'], 30)
        self.assertLessEqual(result['quality']['needs_review'], 2)

    @unittest.skipUnless(BACK_IMAGE.is_file(), 'back sample image is not available')
    def test_back_is_blank_without_false_positives(self):
        result = scan_image_bytes(BACK_IMAGE.read_bytes(), 'back')

        self.assertTrue(result['quality']['quad_found'])
        self.assertEqual(result['quality']['answered'], 0)
        self.assertEqual(result['quality']['blank_answers'], 50)
        self.assertEqual(result['quality']['multiple_answers'], 0)
        self.assertEqual(result['quality']['needs_review'], 0)
        self.assertTrue(all(a['choice'] is None for a in result['answers']))


if __name__ == '__main__':
    unittest.main()
