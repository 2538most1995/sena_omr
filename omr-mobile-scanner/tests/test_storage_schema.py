from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))
import storage


class StorageSchemaTests(unittest.TestCase):
    def test_backend_schema_parser_ignores_phpmyadmin_database_directives(self):
        statements = storage._schema_statements(ROOT)

        self.assertEqual(len(statements), 3)
        self.assertTrue(all(statement.startswith('CREATE TABLE IF NOT EXISTS') for statement in statements))
        self.assertIn('omr_answer_keys', statements[0])
        self.assertIn('omr_scores', statements[1])
        self.assertIn('omr_scan_audit', statements[2])


if __name__ == '__main__':
    unittest.main()
