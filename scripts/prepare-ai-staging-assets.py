#!/usr/bin/env python3
"""운영 AI 모델 자산·카탈로그 번들을 스테이징 Prefix로 복사한다(내려받아 해시 검증 후 업로드).

동작: 운영 Manifest 검증 → 파일별 크기·SHA-256 검증 → 카탈로그 번들 해시(파일 이름) 검증
      → (--execute) 스테이징 Prefix가 비어 있는지 확인 → 업로드 → 키를 바꾼 새 Manifest 업로드 → 재검증.
기본은 검증만 하며 S3에 쓰지 않는다. 운영 Prefix는 읽기만 하고, 쓰기는 `ai/staging/` 아래로 제한한다.
이미 객체가 있는 스테이징 Prefix는 덮어쓰지 않고 중단한다. 출력의 값은 staging/ai-runtime 입력(asset_*·catalog_bundle_s3)에 쓴다.
"""
import argparse
import hashlib
import json
import pathlib
import re
import subprocess
import sys
import tempfile

ACCOUNT_ID = "721744297924"
REGION = "ap-northeast-2"
PROFILE = "seonjalal-production"
BUCKET = "gift-dev-product-images-721744297924-ap-northeast-2-an"
SOURCE_ROOT = "ai/production/"
TARGET_ROOT = "ai/staging/"
BUNDLE_PATTERN = re.compile(r"^catalog/catalog-bundle-([0-9a-f]{64})\.tar$")


class S3Cli:
    def _run(self, *args):
        result = subprocess.run(["aws", *args, "--profile", PROFILE, "--region", REGION], capture_output=True, text=True)
        if result.returncode != 0:
            first = (result.stderr.strip().splitlines() or ["(no stderr)"])[0][:240]
            raise RuntimeError(f"aws {args[0]} {args[1]} 실패: {first}")
        return result.stdout

    def account(self):
        return json.loads(self._run("sts", "get-caller-identity", "--output", "json"))["Account"]

    def keys(self, prefix):
        out = self._run("s3api", "list-objects-v2", "--bucket", BUCKET, "--prefix", prefix, "--query", "Contents[].Key", "--output", "json")
        return json.loads(out) or []

    def download(self, key, path):
        self._run("s3", "cp", f"s3://{BUCKET}/{key}", str(path), "--only-show-errors")

    def upload(self, path, key):
        self._run("s3", "cp", str(path), f"s3://{BUCKET}/{key}", "--only-show-errors")


def require(condition, message):
    if not condition:
        raise SystemExit("STOP: " + message)


def sha256_file(path):
    digest, size = hashlib.sha256(), 0
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
            size += len(chunk)
    return digest.hexdigest(), size


def check_prefixes(source, target):
    require(re.fullmatch(r"ai/production/[0-9a-f]{40}/", source) is not None, "원본 Prefix는 ai/production/<SHA40>/ 형식이어야 한다")
    require(re.fullmatch(r"ai/staging/[A-Za-z0-9._-]+/", target) is not None, "대상 Prefix는 ai/staging/<이름>/ 형식이어야 한다")
    require(".." not in target and not target.startswith(SOURCE_ROOT), "대상 Prefix가 안전하지 않다")


def load_manifest(raw, source):
    data = json.loads(raw)
    require(isinstance(data, dict) and set(data) == {"files"} and isinstance(data["files"], list) and data["files"], "Manifest 형식 오류")
    for item in data["files"]:
        require(set(item) == {"path", "key", "bytes", "sha256"}, "Manifest 항목 형식 오류")
        require(item["path"].startswith("assets/models/") and ".." not in item["path"] and not item["path"].startswith("/"), f"허용되지 않은 경로: {item['path']}")
        require(item["key"] == source + item["path"], f"키가 원본 Prefix와 맞지 않는다: {item['path']}")
        require(isinstance(item["bytes"], int) and re.fullmatch(r"[0-9a-f]{64}", item["sha256"]) is not None, f"크기·해시 형식 오류: {item['path']}")
    return data


def prepare(store, workdir, source, target, execute, expect_manifest_sha256=None):
    check_prefixes(source, target)
    require(store.account() == ACCOUNT_ID, "대상 계정이 다르다")
    workdir = pathlib.Path(workdir)
    source_keys = store.keys(source)
    require(source + "manifest.json" in source_keys, "원본 Manifest가 없다")
    store.download(source + "manifest.json", workdir / "manifest.json")
    raw = (workdir / "manifest.json").read_bytes()
    source_manifest_sha = hashlib.sha256(raw).hexdigest()
    if expect_manifest_sha256:
        require(source_manifest_sha == expect_manifest_sha256, "원본 Manifest 해시가 기대값과 다르다")
    manifest = load_manifest(raw, source)

    for item in manifest["files"]:
        local = workdir / "files" / item["path"]
        local.parent.mkdir(parents=True, exist_ok=True)
        store.download(item["key"], local)
        digest, size = sha256_file(local)
        require((digest, size) == (item["sha256"], item["bytes"]), f"파일 검증 실패: {item['path']}")
    bundles = [k[len(source):] for k in source_keys if BUNDLE_PATTERN.match(k[len(source):])]
    require(len(bundles) == 1, f"카탈로그 번들은 정확히 1개여야 한다(발견 {len(bundles)}개)")
    bundle_rel = bundles[0]
    bundle_local = workdir / "files" / bundle_rel
    bundle_local.parent.mkdir(parents=True, exist_ok=True)
    store.download(source + bundle_rel, bundle_local)
    bundle_sha, _ = sha256_file(bundle_local)
    require(bundle_sha == BUNDLE_PATTERN.match(bundle_rel).group(1), "카탈로그 번들 해시가 파일 이름과 다르다")

    staged = {"files": [{**item, "key": target + item["path"]} for item in manifest["files"]]}
    staged_raw = json.dumps(staged, separators=(",", ":")).encode("utf-8")
    staged_sha = hashlib.sha256(staged_raw).hexdigest()
    result = {
        "source_manifest_sha256": source_manifest_sha,
        "asset_prefix": target,
        "asset_manifest_key": target + "manifest.json",
        "asset_manifest_sha256": staged_sha,
        "catalog_bundle_s3": f"s3://{BUCKET}/{target}{bundle_rel}",
        "files": len(manifest["files"]) + 1,
        "bytes": sum(i["bytes"] for i in manifest["files"]) + bundle_local.stat().st_size,
        "uploaded": False,
    }
    if not execute:
        return result

    require(not store.keys(target), "대상 Prefix에 이미 객체가 있어 덮어쓰지 않는다")
    for item in manifest["files"]:
        store.upload(workdir / "files" / item["path"], target + item["path"])
    store.upload(bundle_local, target + bundle_rel)
    (workdir / "staged-manifest.json").write_bytes(staged_raw)
    store.upload(workdir / "staged-manifest.json", target + "manifest.json")
    verify = workdir / "verify-manifest.json"
    store.download(target + "manifest.json", verify)
    require(hashlib.sha256(verify.read_bytes()).hexdigest() == staged_sha, "업로드한 Manifest 재검증 실패")
    expected = {target + i["path"] for i in manifest["files"]} | {target + bundle_rel, target + "manifest.json"}
    require(set(store.keys(target)) == expected, "업로드 결과 객체 목록이 예상과 다르다")
    result["uploaded"] = True
    return result


def main(argv=None, store=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--source-prefix", required=True, help="예: ai/production/<SHA40>/")
    parser.add_argument("--target-prefix", required=True, help="예: ai/staging/<이름>/")
    parser.add_argument("--expect-manifest-sha256", help="원본 Manifest 해시 기대값(운영 입력의 asset_manifest_sha256)")
    parser.add_argument("--execute", action="store_true", help="없으면 검증만 하고 S3에 쓰지 않는다")
    args = parser.parse_args(argv)
    with tempfile.TemporaryDirectory(prefix="ai-assets-") as workdir:
        result = prepare(store or S3Cli(), workdir, args.source_prefix, args.target_prefix, args.execute, args.expect_manifest_sha256)
    print(f"검증 통과: 파일 {result['files']}개, {result['bytes']:,}바이트, 원본 Manifest {result['source_manifest_sha256'][:12]}…")
    print("== staging/ai-runtime 입력에 쓸 값")
    for key in ("asset_prefix", "asset_manifest_key", "asset_manifest_sha256", "catalog_bundle_s3"):
        print(f"  {key} = \"{result[key]}\"")
    print("업로드 완료" if result["uploaded"] else "검증만 수행했다. 업로드는 --execute를 붙여 다시 실행")
    return result


if __name__ == "__main__":
    try:
        main()
    except RuntimeError as error:
        print("실패:", error, file=sys.stderr)
        sys.exit(1)
