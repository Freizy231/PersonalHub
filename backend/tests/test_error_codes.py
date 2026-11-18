"""Every error code the API can return must have a user-facing message.

The frontend keeps its own copy of this map so messages stay local and no
error text is read from the server. This test fails when the two drift.
"""
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / 'backend' / 'app'
MESSAGES = ROOT / 'frontend' / 'src' / 'shared' / 'api.ts'

CODE = r'[A-Z][A-Z0-9_]*'


def calls(text, name):
    """Yields the argument text of every `name(...)` call, handling nesting."""
    for match in re.finditer(re.escape(name) + r'\s*\(', text):
        depth, index = 1, match.end()
        while index < len(text) and depth:
            if text[index] == '(':
                depth += 1
            elif text[index] == ')':
                depth -= 1
            index += 1
        yield text[match.end():index - 1]


def backend_codes():
    codes = set()
    for path in BACKEND.rglob('*.py'):
        text = path.read_text(encoding='utf-8')
        for args in list(calls(text, 'HubError')) + list(calls(text, 'CapabilityError')):
            match = re.match(r"\s*['\"](" + CODE + r")['\"]", args)
            if match:
                codes.add(match.group(1))
        for args in calls(text, 'error_response'):
            # The first argument may be a ternary choosing between two codes.
            for code in re.findall(r"['\"](" + CODE + r")['\"]", args):
                codes.add(code)
        for args in calls(text, 'HTTPException'):
            for code in re.findall(r"['\"](" + CODE + r")['\"]", args):
                codes.add(code)
    return codes


def frontend_codes():
    text = MESSAGES.read_text(encoding='utf-8')
    block = text.split('const messages', 1)[1].split('\n}', 1)[0]
    return set(re.findall(r'^\s*(' + CODE + r')\s*:', block, re.MULTILINE))


class ErrorCodeTests(unittest.TestCase):
    def test_backend_error_codes_are_found(self):
        codes = backend_codes()
        self.assertGreater(len(codes), 100, 'error codes should be discoverable by static scan')
        for expected in ('VERSION_CONFLICT', 'RESOURCE_NOT_FOUND', 'TASK_NOT_FOUND', 'VAULT_LOCKED'):
            self.assertIn(expected, codes)
        self.assertIn('REQUEST_TOO_LARGE', codes)
        self.assertIn('TAG_REQUEST_TOO_LARGE', codes)

    def test_every_backend_code_has_a_user_message(self):
        missing = sorted(backend_codes() - frontend_codes())
        self.assertEqual(missing, [], 'add these codes to frontend/src/shared/api.ts: ' + ', '.join(missing))

    def test_frontend_has_no_orphan_codes(self):
        orphans = sorted(frontend_codes() - backend_codes())
        self.assertEqual(orphans, [], 'these codes no longer exist in the backend: ' + ', '.join(orphans))

    def test_messages_are_static_strings(self):
        text = MESSAGES.read_text(encoding='utf-8')
        block = text.split('const messages', 1)[1].split('\n}', 1)[0]
        self.assertIn('操作未完成，请重试。', text)
        self.assertNotIn('${', block, 'messages must never interpolate request data')


if __name__ == '__main__':
    unittest.main()
