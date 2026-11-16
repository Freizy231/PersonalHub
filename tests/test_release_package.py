from pathlib import Path
import tarfile
import tempfile
import unittest

from scripts.package_release import package_release
from scripts.data_safety import SafetyError


class ReleasePackageTests(unittest.TestCase):
    def test_source_archive_excludes_secrets_data_dependencies_and_rejects_overwrite(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name) / "project"
            root.mkdir()
            for filename in ["README.md", "frontend/src/App.vue", "backend/maintenance.py", "frontend/tests/tasks.test.cjs", ".env", "runtime/private.md",
                             "backend/.venv/private.py", "frontend/node_modules/pkg/data.ts", "data/intel.db"]:
                path = root / filename
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("secret" if filename == ".env" else "test")
            output = Path(name) / "release"
            result = package_release(root, output, "0.1.0")
            with tarfile.open(output / "source.tar.gz") as archive:
                self.assertEqual(sorted(archive.getnames()), ["README.md", "backend/maintenance.py", "frontend/src/App.vue", "frontend/tests/tasks.test.cjs"])
            self.assertEqual(len(result["files"]), 4)
            with self.assertRaises(SafetyError):
                package_release(root, output, "0.1.0")
