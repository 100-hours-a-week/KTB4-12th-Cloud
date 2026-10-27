import importlib.util
import io
import pathlib
import re
import unittest

SPEC = importlib.util.spec_from_file_location("ai_secrets", pathlib.Path(__file__).with_name("prepare-ai-staging-secrets.py"))
tool = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(tool)

DSNS = {k: f"postgresql://user:pw-{i}@host:5432/ai_db_staging?sslmode=verify-full&sslrootcert=/shared/tls/rds-ca.pem"
        for i, k in enumerate(tool.DSN_KEYS)}
CHAT_KEY = "sk-or-v1-" + "a1b2c3d4" * 8
PROFILER_KEY = "sk-or-v1-" + "0f9e8d7c" * 8
BE_CHAT = "chat-token-" + "x" * 30
BE_PROFILING = "profiling-token-" + "y" * 30


class FakeStore:
    def __init__(self, existing=None, account="721744297924", tamper_on_verify=False):
        self.account_id = account
        self.secrets = {
            tool.SECRET_ID: dict(DSNS if existing is None else existing),
            tool.BACKEND_SECRET_ID: {"AI_CHAT_SERVICE_TOKEN": BE_CHAT, "PROFILING_SERVICE_TOKEN": BE_PROFILING, "DB_PASSWORD": "never-copied"},
        }
        self.writes = []
        self.tamper = tamper_on_verify

    def account(self):
        return self.account_id

    def read(self, secret_id):
        data = dict(self.secrets[secret_id])
        if self.tamper and self.writes and secret_id == tool.SECRET_ID:
            data["DATABASE_URL"] = "changed"
        return data

    def write(self, secret_id, values):
        self.writes.append((secret_id, dict(values)))
        self.secrets[secret_id] = dict(values)


class Prompt:
    def __init__(self, answers):
        self.answers = dict(answers)
        self.asked = []

    def __call__(self, text):
        key = next(k for k in self.answers if text.startswith(k))
        self.asked.append(key)
        return self.answers[key]


def run(argv, store=None, answers=None):
    store = store or FakeStore()
    prompt = Prompt(answers if answers is not None else {"OPENROUTER_API_KEY_CHAT": CHAT_KEY, "OPENROUTER_API_KEY_PROFILER": PROFILER_KEY})
    out = io.StringIO()
    result = tool.main(argv, store=store, prompt=prompt, out=out)
    return result, store, prompt, out.getvalue()


class SecretsToolTests(unittest.TestCase):
    def test_dry_run_changes_nothing_and_asks_for_nothing(self):
        result, store, prompt, output = run([])
        self.assertEqual((store.writes, prompt.asked), ([], []))
        self.assertIn("OPENROUTER_API_KEY_CHAT", output)
        self.assertIn("실제 실행은 --execute", output)

    def test_execute_adds_generated_and_provider_keys_and_keeps_dsns(self):
        result, store, prompt, output = run(["--execute"])
        merged = store.secrets[tool.SECRET_ID]
        self.assertEqual(set(merged), set(tool.DSN_KEYS) | set(tool.GENERATED) | set(tool.PROVIDER_KEYS))
        for key in tool.DSN_KEYS:
            self.assertEqual(merged[key], DSNS[key])
        self.assertEqual(merged["OPENROUTER_API_KEY_CHAT"], CHAT_KEY)
        self.assertEqual(merged["OPENROUTER_API_KEY_PROFILER"], PROFILER_KEY)
        self.assertEqual(sorted(prompt.asked), sorted(tool.PROVIDER_KEYS))
        self.assertEqual(len(store.writes), 1)

    def test_generated_values_are_random_unique_and_safe(self):
        merged = run(["--execute"])[1].secrets[tool.SECRET_ID]
        values = [merged[k] for k in tool.GENERATED]
        self.assertEqual(len(set(values)), 3)
        self.assertTrue(all(re.fullmatch(r"[A-Za-z0-9_-]{30,}", v) for v in values))
        other = run(["--execute"])[1].secrets[tool.SECRET_ID]
        self.assertTrue(all(merged[k] != other[k] for k in tool.GENERATED))

    def test_output_never_contains_a_secret_value(self):
        _, store, _, output = run(["--execute"])
        merged = store.secrets[tool.SECRET_ID]
        for key, value in merged.items():
            self.assertNotIn(value, output, key)
        self.assertIn("OPENROUTER_API_KEY_CHAT", output)

    def test_bootstrap_must_run_first(self):
        store = FakeStore(existing={"DATABASE_URL": DSNS["DATABASE_URL"]})
        with self.assertRaises(SystemExit):
            run(["--execute"], store)
        self.assertEqual(store.writes, [])

    def test_existing_keys_are_not_overwritten_without_replace(self):
        existing = {**DSNS, "OPENROUTER_API_KEY_CHAT": "sk-or-v1-" + "9" * 64, "SESSION_SECRET": "keep-this-session-secret-value"}
        result, store, prompt, output = run(["--execute"], FakeStore(existing), {"OPENROUTER_API_KEY_PROFILER": PROFILER_KEY})
        merged = store.secrets[tool.SECRET_ID]
        self.assertEqual(merged["OPENROUTER_API_KEY_CHAT"], existing["OPENROUTER_API_KEY_CHAT"])
        self.assertEqual(merged["SESSION_SECRET"], "keep-this-session-secret-value")
        self.assertNotIn("OPENROUTER_API_KEY_CHAT", prompt.asked)
        self.assertEqual(sorted(result["added"]), sorted(["AI_INTERNAL_SERVICE_TOKEN", "QA_ADMIN_PASSWORD", "OPENROUTER_API_KEY_PROFILER"]))

    def test_replace_rotates_only_the_named_key(self):
        existing = {**DSNS, "OPENROUTER_API_KEY_CHAT": "sk-or-v1-" + "9" * 64, "SESSION_SECRET": "s" * 40}
        new_key = "sk-or-v1-" + "5" * 64
        result, store, _, _ = run(["--execute", "--only", "OPENROUTER_API_KEY_CHAT", "--replace", "OPENROUTER_API_KEY_CHAT"], FakeStore(existing), {"OPENROUTER_API_KEY_CHAT": new_key})
        merged = store.secrets[tool.SECRET_ID]
        self.assertEqual(merged["OPENROUTER_API_KEY_CHAT"], new_key)
        self.assertEqual(merged["SESSION_SECRET"], "s" * 40)
        self.assertEqual(result["replaced"], ["OPENROUTER_API_KEY_CHAT"])

    def test_malformed_provider_keys_are_rejected_before_any_write(self):
        bad_keys = ["sk-or-v1-" + "a" * 63, "sk-or-v1-" + "A" * 64, "sk-or-v1-" + "g" * 64, "sk-live-" + "a" * 64, "", " ", CHAT_KEY + "x"]
        for bad in bad_keys:
            store = FakeStore()
            with self.assertRaises(SystemExit, msg=repr(bad)):
                run(["--execute"], store, {"OPENROUTER_API_KEY_CHAT": bad, "OPENROUTER_API_KEY_PROFILER": PROFILER_KEY})
            self.assertEqual(store.writes, [])

    def test_pasted_key_with_surrounding_whitespace_is_trimmed(self):
        _, store, _, _ = run(["--execute"], answers={"OPENROUTER_API_KEY_CHAT": f"  {CHAT_KEY}\n", "OPENROUTER_API_KEY_PROFILER": PROFILER_KEY})
        self.assertEqual(store.secrets[tool.SECRET_ID]["OPENROUTER_API_KEY_CHAT"], CHAT_KEY)

    def test_be_service_token_can_be_copied_from_the_backend_secret_without_prompting(self):
        _, store, prompt, output = run(["--execute", "--be-token-from", "PROFILING_SERVICE_TOKEN"])
        self.assertEqual(store.secrets[tool.SECRET_ID]["BE_SERVICE_TOKEN"], BE_PROFILING)
        self.assertNotIn("BE_SERVICE_TOKEN", prompt.asked)
        self.assertNotIn(BE_PROFILING, output)
        self.assertNotIn("never-copied", str(store.secrets[tool.SECRET_ID]))

    def test_missing_backend_token_is_rejected(self):
        store = FakeStore()
        store.secrets[tool.BACKEND_SECRET_ID].pop("AI_CHAT_SERVICE_TOKEN")
        with self.assertRaises(SystemExit):
            run(["--execute", "--be-token-from", "AI_CHAT_SERVICE_TOKEN"], store)
        self.assertEqual(store.writes, [])

    def test_optional_keys_are_not_added_by_default(self):
        merged = run(["--execute"])[1].secrets[tool.SECRET_ID]
        self.assertNotIn("BE_SERVICE_TOKEN", merged)
        self.assertNotIn("DEMO_ACCESS_TOKEN", merged)

    def test_optional_demo_token_is_prompted_when_requested(self):
        demo = "demo-access-" + "z" * 24
        answers = {"OPENROUTER_API_KEY_CHAT": CHAT_KEY, "OPENROUTER_API_KEY_PROFILER": PROFILER_KEY, "DEMO_ACCESS_TOKEN": demo}
        _, store, prompt, _ = run(["--execute", "--prompt-optional", "DEMO_ACCESS_TOKEN"], answers=answers)
        self.assertEqual(store.secrets[tool.SECRET_ID]["DEMO_ACCESS_TOKEN"], demo)
        self.assertIn("DEMO_ACCESS_TOKEN", prompt.asked)

    def test_only_limits_the_keys_that_are_touched(self):
        result, store, prompt, _ = run(["--execute", "--only", "SESSION_SECRET"])
        self.assertEqual(result["added"], ["SESSION_SECRET"])
        self.assertEqual(set(store.secrets[tool.SECRET_ID]), set(tool.DSN_KEYS) | {"SESSION_SECRET"})
        self.assertEqual(prompt.asked, [])

    def test_nothing_to_do_makes_no_write(self):
        existing = {**DSNS, **{k: "sk-or-v1-" + "1" * 64 if k in tool.PROVIDER_KEYS else "v" * 40 for k in tool.GENERATED + tool.PROVIDER_KEYS}}
        result, store, _, output = run(["--execute"], FakeStore(existing))
        self.assertEqual(store.writes, [])
        self.assertIn("변경할 것이 없다", output)

    def test_wrong_account_stops_before_reading_or_writing(self):
        store = FakeStore(account="111111111111")
        with self.assertRaises(SystemExit):
            run(["--execute"], store)
        self.assertEqual(store.writes, [])

    def test_post_write_verification_failure_stops(self):
        with self.assertRaises(SystemExit):
            run(["--execute"], FakeStore(tamper_on_verify=True))

    def test_target_secret_is_the_staging_ai_runtime_secret_only(self):
        self.assertEqual(tool.SECRET_ID, "seonjalal/staging/ai/runtime")
        self.assertNotIn("production", tool.SECRET_ID + tool.BACKEND_SECRET_ID)
        self.assertTrue(set(tool.ALL_KEYS).isdisjoint(tool.DSN_KEYS))

    def test_key_sets_match_the_runtime_module_contract(self):
        text = (pathlib.Path(__file__).resolve().parent.parent
                / "infrastructure/terraform/modules/ai_ecs_runtime/tasks.tf").read_text(encoding="utf-8")
        for key in tool.ALL_KEYS + tool.DSN_KEYS:
            self.assertIn(f'"{key}"', text, key)


if __name__ == "__main__":
    unittest.main()
