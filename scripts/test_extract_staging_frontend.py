"""합성 Archive만 사용하는 FE 추출 검사. Docker·AWS 호출 없음."""

import importlib.util
import io
from pathlib import Path
import tarfile
import tempfile
import unittest


spec = importlib.util.spec_from_file_location(
    "frontend", Path(__file__).with_name("extract-staging-frontend.py")
)
frontend = importlib.util.module_from_spec(spec)
spec.loader.exec_module(frontend)


def archive(entries):
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w") as target:
        for name, content in entries:
            member = tarfile.TarInfo(name)
            member.size = len(content)
            target.addfile(member, io.BytesIO(content))
    return output.getvalue()


class FrontendExtractionTest(unittest.TestCase):
    def test_extracts_static_files_and_reproducible_manifest(self):
        with tempfile.TemporaryDirectory() as folder:
            site = Path(folder)
            frontend.extract_files(archive([
                ("./index.html", b"<html></html>"),
                ("./assets/app.js", b'const base="/api";'),
            ]), site)
            result = frontend.inspect_files(site)
            self.assertEqual(result["file_count"], 2)
            self.assertEqual(result["relative_api_literal_files"], 1)
            self.assertEqual(result["tree_sha256"], frontend.inspect_files(site)["tree_sha256"])

    def test_rejects_traversal_and_absolute_paths(self):
        for name in ("../escape", "/absolute"):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as folder:
                with self.assertRaises(ValueError):
                    frontend.extract_files(archive([(name, b"x")]), Path(folder))

    def test_rejects_symbolic_links(self):
        output = io.BytesIO()
        with tarfile.open(fileobj=output, mode="w") as target:
            member = tarfile.TarInfo("link")
            member.type = tarfile.SYMTYPE
            member.linkname = "/outside"
            target.addfile(member)
        with tempfile.TemporaryDirectory() as folder, self.assertRaises(ValueError):
            frontend.extract_files(output.getvalue(), Path(folder))

    def test_requires_root_index(self):
        with tempfile.TemporaryDirectory() as folder, self.assertRaises(ValueError):
            frontend.extract_files(archive([("assets/app.js", b"x")]), Path(folder))

    def test_rejects_duplicate_files(self):
        with tempfile.TemporaryDirectory() as folder, self.assertRaises(FileExistsError):
            frontend.extract_files(archive([
                ("index.html", b"one"), ("index.html", b"two"),
            ]), Path(folder))

    def test_reports_webhook_presence_without_values(self):
        with tempfile.TemporaryDirectory() as folder:
            site = Path(folder)
            frontend.extract_files(archive([
                ("index.html", b"ok"),
                ("app.js", b'"https://discord.com/api/webhooks/synthetic-only"'),
                ("app.js.map", b"{}"),
            ]), site)
            result = frontend.inspect_files(site)
            self.assertEqual(result["discord_webhook_literal_files"], 1)
            self.assertEqual(result["source_map_count"], 1)
            self.assertNotIn("synthetic-only", str(result))

    def test_recognizes_minified_template_literal_api_base(self):
        with tempfile.TemporaryDirectory() as folder:
            site = Path(folder)
            frontend.extract_files(archive([
                ("index.html", b"ok"),
                ("app.js", b"let base=`/api`.replace(/x/,``);"),
            ]), site)
            self.assertEqual(frontend.inspect_files(site)["relative_api_literal_files"], 1)


if __name__ == "__main__":
    unittest.main()
