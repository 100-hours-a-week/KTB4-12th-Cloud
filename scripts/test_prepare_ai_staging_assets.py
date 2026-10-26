import hashlib
import importlib.util
import json
import pathlib
import tempfile
import unittest

SPEC = importlib.util.spec_from_file_location("assets", pathlib.Path(__file__).with_name("prepare-ai-staging-assets.py"))
assets = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(assets)

SOURCE = "ai/production/" + "a" * 40 + "/"
TARGET = "ai/staging/" + "b" * 40 + "/"


def sha(data):
    return hashlib.sha256(data).hexdigest()


class FakeS3:
    def __init__(self, account="721744297924", tamper=None, bundle_count=1, bundle_name_matches=True, existing_target=False,
                 manifest_key_prefix=None, extra_path=None):
        self.account_id = account
        self.writes = []
        self.objects = {}
        files = {"assets/models/model.onnx": b"model-bytes", "assets/models/tokenizer.json": b"tokenizer-bytes"}
        if extra_path:
            files[extra_path] = b"x"
        entries = []
        for path, data in files.items():
            key = (manifest_key_prefix or SOURCE) + path
            entries.append({"path": path, "key": key, "bytes": len(data), "sha256": sha(data)})
            self.objects[SOURCE + path] = data
        if tamper:
            self.objects[SOURCE + tamper] = b"tampered!!!"
        self.manifest = json.dumps({"files": entries}, separators=(",", ":")).encode()
        self.objects[SOURCE + "manifest.json"] = self.manifest
        bundle = b"catalog-bundle-bytes"
        name = sha(bundle) if bundle_name_matches else "0" * 64
        for index in range(bundle_count):
            self.objects[f"{SOURCE}catalog/catalog-bundle-{name if index == 0 else sha(bytes([index]))}.tar"] = bundle
        if existing_target:
            self.objects[TARGET + "old.txt"] = b"old"

    def account(self):
        return self.account_id

    def keys(self, prefix):
        return sorted(k for k in self.objects if k.startswith(prefix))

    def download(self, key, path):
        pathlib.Path(path).write_bytes(self.objects[key])

    def upload(self, path, key):
        assert key.startswith(assets.TARGET_ROOT), f"스테이징 밖에 쓰려 했다: {key}"
        self.writes.append(key)
        self.objects[key] = pathlib.Path(path).read_bytes()


def run(store, execute=False, source=SOURCE, target=TARGET, expect=None):
    with tempfile.TemporaryDirectory() as workdir:
        return assets.prepare(store, workdir, source, target, execute, expect)


class PrepareAssetsTest(unittest.TestCase):
    def test_verification_only_writes_nothing_and_reports_values(self):
        store = FakeS3()
        result = run(store)
        self.assertEqual(store.writes, [])
        self.assertFalse(result["uploaded"])
        self.assertEqual(result["asset_prefix"], TARGET)
        self.assertEqual(result["asset_manifest_key"], TARGET + "manifest.json")
        self.assertRegex(result["catalog_bundle_s3"], r"^s3://[^/]+/" + TARGET + r"catalog/catalog-bundle-[0-9a-f]{64}\.tar$")
        self.assertEqual(result["files"], 3 + 0)

    def test_execute_uploads_files_bundle_and_rewritten_manifest_under_target_only(self):
        store = FakeS3()
        result = run(store, execute=True)
        self.assertTrue(result["uploaded"])
        self.assertTrue(all(k.startswith(TARGET) for k in store.writes))
        expected = {TARGET + "assets/models/model.onnx", TARGET + "assets/models/tokenizer.json", TARGET + "manifest.json"}
        self.assertTrue(expected <= set(store.keys(TARGET)))
        self.assertEqual(len([k for k in store.keys(TARGET) if "catalog-bundle" in k]), 1)
        staged = json.loads(store.objects[TARGET + "manifest.json"])
        self.assertTrue(all(i["key"].startswith(TARGET) and i["key"] == TARGET + i["path"] for i in staged["files"]))
        self.assertEqual(sha(store.objects[TARGET + "manifest.json"]), result["asset_manifest_sha256"])

    def test_file_content_is_copied_byte_for_byte_and_hashes_stay_the_same(self):
        store = FakeS3()
        run(store, execute=True)
        source = {i["path"]: i for i in json.loads(store.manifest)["files"]}
        staged = {i["path"]: i for i in json.loads(store.objects[TARGET + "manifest.json"])["files"]}
        for path, item in source.items():
            self.assertEqual(store.objects[TARGET + path], store.objects[SOURCE + path])
            self.assertEqual((item["bytes"], item["sha256"]), (staged[path]["bytes"], staged[path]["sha256"]))

    def test_source_objects_are_never_modified(self):
        store = FakeS3()
        before = {k: v for k, v in store.objects.items() if k.startswith(SOURCE)}
        run(store, execute=True)
        self.assertEqual({k: v for k, v in store.objects.items() if k.startswith(SOURCE)}, before)

    def test_existing_target_prefix_is_not_overwritten(self):
        store = FakeS3(existing_target=True)
        with self.assertRaises(SystemExit):
            run(store, execute=True)
        self.assertEqual(store.writes, [])

    def test_tampered_source_file_is_rejected_before_any_write(self):
        store = FakeS3(tamper="assets/models/model.onnx")
        with self.assertRaises(SystemExit):
            run(store, execute=True)
        self.assertEqual(store.writes, [])

    def test_bundle_whose_name_does_not_match_its_content_is_rejected(self):
        with self.assertRaises(SystemExit):
            run(FakeS3(bundle_name_matches=False), execute=True)

    def test_missing_or_multiple_bundles_are_rejected(self):
        for count in (0, 2):
            with self.assertRaises(SystemExit, msg=str(count)):
                run(FakeS3(bundle_count=count), execute=True)

    def test_unsafe_prefixes_are_rejected(self):
        store = FakeS3()
        for source, target in (
            (SOURCE, "ai/production/" + "c" * 40 + "/"),
            (SOURCE, "ai/staging/../production/x/"),
            ("ai/staging/" + "a" * 40 + "/", TARGET),
            ("ai/production/short/", TARGET),
            (SOURCE, "other/prefix/"),
        ):
            with self.assertRaises(SystemExit, msg=f"{source} {target}"):
                run(store, execute=True, source=source, target=target)
        self.assertEqual(store.writes, [])

    def test_wrong_account_stops_before_any_work(self):
        store = FakeS3(account="111111111111")
        with self.assertRaises(SystemExit):
            run(store, execute=True)
        self.assertEqual(store.writes, [])

    def test_manifest_key_outside_the_source_prefix_is_rejected(self):
        with self.assertRaises(SystemExit):
            run(FakeS3(manifest_key_prefix="ai/production/" + "d" * 40 + "/"))

    def test_unexpected_manifest_path_is_rejected(self):
        for path in ("media/extra.png", "assets/models/../escape.bin"):
            with self.assertRaises(SystemExit, msg=path):
                run(FakeS3(extra_path=path))

    def test_expected_manifest_hash_must_match_the_source(self):
        store = FakeS3()
        run(store, expect=sha(store.manifest))
        with self.assertRaises(SystemExit):
            run(FakeS3(), expect="0" * 64)

    def test_target_root_never_overlaps_the_source_root(self):
        self.assertFalse(assets.TARGET_ROOT.startswith(assets.SOURCE_ROOT))
        self.assertFalse(assets.SOURCE_ROOT.startswith(assets.TARGET_ROOT))


if __name__ == "__main__":
    unittest.main()
