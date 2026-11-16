"""Out-of-range or malformed instants and zones are 422 validation errors, never a 500."""
import unittest

from pydantic import ValidationError

from backend.app.modules.life.tasks.schemas import TaskCreate, TaskPatch


class InstantValidationTests(unittest.TestCase):
    def test_instant_that_overflows_utc_is_a_validation_error(self):
        for value in ('9999-12-31T23:59:59-05:00', '0001-01-01T00:00:00+05:00'):
            with self.assertRaises(ValidationError, msg=value):
                TaskCreate(title='x', due_at=value)
            with self.assertRaises(ValidationError, msg=value):
                TaskPatch(expected_version=1, due_at=value)

    def test_a_directory_name_is_not_a_timezone(self):
        for value in ('America', 'Europe', 'Asia/'):
            with self.assertRaises(ValidationError, msg=value):
                TaskCreate(title='x', timezone=value)

    def test_ordinary_instants_still_normalise_to_utc(self):
        task = TaskCreate(title='x', due_at='2026-11-12T09:00:00+08:00', timezone='Asia/Shanghai')
        self.assertEqual(task.due_at.isoformat(), '2026-11-12T01:00:00+00:00')


if __name__ == '__main__':
    unittest.main()
