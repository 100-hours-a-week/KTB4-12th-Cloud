#!/usr/bin/env python3
"""Develop 복구 전 보호 DB 백업 요청 준비. 로컬 준비만 수행."""
import importlib.util
import json
import os
from pathlib import Path
import tempfile

spec = importlib.util.spec_from_file_location("runtime", Path(__file__).with_name("prepare-develop-runtime-update.py"))
runtime = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runtime)

REMOTE = r'''
# 배포 잠금은 앞선 대상 검증과 함께 획득. DB 컨테이너는 재시작하지 않음.
os.umask(0o077)
container = "seonjalal-v2-develop-database-1"
def docker_json():
    result = subprocess.run(["docker", "inspect", container], capture_output=True, text=True, timeout=20)
    if result.returncode:
        raise SystemExit("STOP: Develop DB 컨테이너 확인 실패. 원문 비공개")
    return json.loads(result.stdout)[0]

def db_mount_safe(mounts):
    data = [item for item in mounts if item.get("Destination") == "/var/lib/mysql"]
    return (len(data) == 1 and data[0].get("Type") == "bind"
            and data[0].get("Source") == "/var/lib/seonjalal-develop/mysql"
            and data[0].get("RW") is True)

before = docker_json()
if before.get("State", {}).get("Health", {}).get("Status") != "healthy":
    raise SystemExit("STOP: DB healthy 아님. 백업/재배포 중단")
env = dict(item.split("=", 1) for item in before["Config"].get("Env", []) if "=" in item)
if env.get("MYSQL_DATABASE") != "seonjalal_develop":
    raise SystemExit("STOP: Develop DB명 불일치")
if not db_mount_safe(before.get("Mounts", [])):
    raise SystemExit("STOP: 확인된 Develop DB bind 경로 불일치")
data_path = pathlib.Path("/var/lib/seonjalal-develop/mysql")
if not data_path.is_dir() or data_path.resolve() != data_path:
    raise SystemExit("STOP: DB 호스트 데이터 경로 확인 필요")
engines = subprocess.run(["docker", "exec", container, "sh", "-c",
    'MYSQL_PWD="$MYSQL_ROOT_PASSWORD" exec mysql -h 127.0.0.1 -u root --batch --skip-column-names '
    '--execute="SELECT COUNT(*) FROM information_schema.tables WHERE table_schema=\'seonjalal_develop\' '
    'AND table_type=\'BASE TABLE\' AND ENGINE<>\'InnoDB\';"'],
    capture_output=True, text=True, timeout=30)
if engines.returncode or engines.stdout.strip() != "0":
    raise SystemExit("STOP: InnoDB 일관성 백업 전제 확인 실패. 원문 비공개")
size = subprocess.run(["docker", "exec", container, "du", "-sk", "/var/lib/mysql"],
                      capture_output=True, text=True, timeout=30)
try:
    estimated = int(size.stdout.split()[0]) * 1024
except (ValueError, IndexError):
    raise SystemExit("STOP: DB 용량 확인 실패") from None
if size.returncode or shutil.disk_usage(root).free < max(2 * 1024**3, estimated * 2):
    raise SystemExit("STOP: 안전 백업 여유 디스크 부족. 재배포 금지")
backup = pathlib.Path(tempfile.mkdtemp(prefix="db-before-recovery-", dir=root))
os.chmod(backup, 0o700)
dump = backup / "develop.sql"
with open(dump, "xb") as stream:
    result = subprocess.run(["docker", "exec", container, "sh", "-c",
        'MYSQL_PWD="$MYSQL_ROOT_PASSWORD" exec mysqldump -h 127.0.0.1 -u root '
        '--single-transaction --quick --routines --events --triggers --hex-blob '
        '--no-tablespaces --set-gtid-purged=OFF --databases seonjalal_develop'],
        stdout=stream, stderr=subprocess.PIPE, timeout=300)
    stream.flush()
    os.fsync(stream.fileno())
if result.returncode or dump.stat().st_size == 0:
    raise SystemExit("STOP: DB 백업 실패. 부분 파일은 보호 폴더에 보존, 재배포 금지")
with open(dump, "rb") as stream:
    stream.seek(max(0, dump.stat().st_size - 4096))
    if b"-- Dump completed on" not in stream.read():
        raise SystemExit("STOP: DB 백업 완료 표식 없음. 재배포 금지")
after = docker_json()
if (after["Id"], after["Image"], after["State"].get("StartedAt")) != (
        before["Id"], before["Image"], before["State"].get("StartedAt")) or after.get("Mounts") != before.get("Mounts"):
    raise SystemExit("STOP: 백업 중 DB 재기동/교체 감지. 재배포 금지")
digest = hashlib.sha256()
with open(dump, "rb") as stream:
    for block in iter(lambda: stream.read(1048576), b""):
        digest.update(block)
record = {"database": "seonjalal_develop", "container_id": before["Id"],
          "image_id": before["Image"], "started_at": before["State"].get("StartedAt"),
          "dump_bytes": dump.stat().st_size, "dump_sha256": digest.hexdigest(),
          "mounts": [{"type": item.get("Type"), "name": item.get("Name"), "source": item.get("Source"),
                      "destination": item.get("Destination"), "rw": item.get("RW")}
                     for item in before.get("Mounts", [])],
          "restore_tested": False}
(backup / "record.json").write_text(json.dumps(record))
print("DEVELOP_DB_BACKUP_COMPLETED")
print("BACKUP_DIR=" + str(backup))
print("DUMP_BYTES=" + str(record["dump_bytes"]))
print("DUMP_SHA256=" + record["dump_sha256"])
print("DB_CONTAINER_IMAGE_AND_VOLUME_PRESERVED")
print("RESTORE_NOT_TESTED_NO_REDEPLOY_PERFORMED")
'''


def build_request():
    script = runtime.REMOTE.split("files = __PUBLIC_FILES__", 1)[0] + REMOTE
    compile(script, "<develop-db-backup>", "exec")
    return {"DocumentName": "AWS-RunShellScript", "InstanceIds": [runtime.INSTANCE],
            "Comment": "Backup Develop DB only before approved recovery; no restart",
            "TimeoutSeconds": 420, "Parameters": {"commands": ["python3 - <<'PY'\n" + script + "\nPY"],
                                                   "executionTimeout": ["420"]}}


def main():
    os.umask(0o077)
    request = build_request()
    path = Path(tempfile.mkdtemp(prefix="seonjalal-develop-db-backup.", dir="/private/tmp")) / "request.json"
    path.write_text(json.dumps(request))
    print("SSM_REQUEST_FILE=" + str(path))
    print("LOCAL_PREPARE_ONLY_NO_AWS_OR_SERVER_CHANGE")


if __name__ == "__main__":
    main()
