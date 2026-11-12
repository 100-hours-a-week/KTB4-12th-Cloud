"""네트워크 없는 추가 경계·드리프트·비밀값 stdin·복구 조건 검사."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from lib import v3_ai_qa_routing as qa


def config():
    return {'Origins': {'Quantity': 1, 'Items': [{'Id': 'seonjalal-v3-backend',
             'CustomHeaders': {'Items': [{'HeaderName': 'X-Test-Origin', 'HeaderValue': 'synthetic-secret'}]}}]},
            'DefaultCacheBehavior': {'TargetOriginId': 'frontend', 'FunctionAssociations': {'Quantity': 1}},
            'CacheBehaviors': {'Quantity': 1, 'Items': [{'PathPattern': '/api/*', 'TargetOriginId': 'seonjalal-v3-backend',
             'CachePolicyId': qa.CACHE_DISABLED, 'OriginRequestPolicyId': qa.ORIGIN_POLICY,
             'FunctionAssociations': {'Quantity': 1, 'Items': [{'EventType': 'viewer-request', 'FunctionARN': 'synthetic-api-rewrite'}]},
             'LambdaFunctionAssociations': {'Quantity': 0}, 'ViewerProtocolPolicy': 'redirect-to-https'}]}}


class RoutingBoundaries(unittest.TestCase):
    def test_only_one_behavior_added_and_existing_origin_secret_preserved_in_memory(self):
        old = config()
        before = copy.deepcopy(old)
        new = qa.add_behavior(old)
        self.assertEqual(old, before)
        admin = new['CacheBehaviors']['Items'].pop()
        new['CacheBehaviors']['Quantity'] -= 1
        self.assertEqual(new, before)
        self.assertEqual(admin['PathPattern'], '/admin/*')
        self.assertEqual(admin['FunctionAssociations'], {'Quantity': 0})
        self.assertEqual(admin['CachePolicyId'], qa.CACHE_DISABLED)
        self.assertEqual(set(admin['AllowedMethods']['Items']), {'GET', 'HEAD', 'OPTIONS', 'POST', 'PUT', 'PATCH', 'DELETE'})

    def test_existing_admin_behavior_not_overwritten(self):
        with self.assertRaisesRegex(RuntimeError, 'already_exists'):
            qa.add_behavior(qa.add_behavior(config()))

    def test_unreviewed_cache_origin_or_lambda_refused(self):
        for key, value in [('CachePolicyId', 'caching-enabled'), ('TargetOriginId', 'other-origin'),
                           ('OriginRequestPolicyId', 'cookies-not-forwarded'),
                           ('LambdaFunctionAssociations', {'Quantity': 1})]:
            current = config()
            current['CacheBehaviors']['Items'][0][key] = value
            with self.assertRaises(RuntimeError):
                qa.add_behavior(current)

    def test_ingress_patch_has_resource_and_old_paths_preconditions(self):
        old_paths = [{'path': '/recommendations', 'pathType': 'Prefix',
                      'backend': {'service': {'name': 'recommendation-service', 'port': {'number': 8080}}}}]
        old = {'metadata': {'resourceVersion': '123'}, 'spec': {'rules': [{'http': {'paths': old_paths}}]}}
        before = copy.deepcopy(old)
        result = qa.ingress_patch(old)
        self.assertEqual(old, before)
        self.assertEqual(result[0], {'op': 'test', 'path': '/metadata/resourceVersion', 'value': '123'})
        self.assertEqual(result[1]['value'], old_paths)
        self.assertEqual(result[2], {'op': 'add', 'path': '/spec/rules/0/http/paths/-', 'value': qa.PATH})

    def test_ingress_existing_admin_conflict_rejected(self):
        old = {'metadata': {'resourceVersion': '123'}, 'spec': {'rules': [{'http': {'paths': [qa.PATH]}}]}}
        with self.assertRaisesRegex(RuntimeError, 'already_exists'):
            qa.ingress_patch(old)

    def test_cloudfront_payload_not_passed_in_command_arguments_or_saved(self):
        class Result:
            returncode = 0
            stdout = '{}'
        with patch.object(qa.subprocess, 'run', return_value=Result()) as mocked:
            qa.change_cloudfront(config(), 'etag-lease')
        args, kwargs = mocked.call_args
        self.assertNotIn('synthetic-secret', ' '.join(args[0]))
        self.assertIn('file:///dev/stdin', args[0])
        self.assertIn('synthetic-secret', kwargs['input'])
        self.assertIn('--if-match', args[0])

    def test_pre_mutation_failure_does_not_rollback_old_execution(self):
        with tempfile.TemporaryDirectory() as temp:
            Path(temp, 'apply-receipt.json').write_text(json.dumps({'operationId': 'old', 'events': [{'phase': 'old'}]}))
            with patch.object(qa, 'ARTIFACTS', Path(temp)), patch.object(qa, '_apply', side_effect=RuntimeError('drift')), patch.object(qa, 'rollback') as undo:
                with self.assertRaisesRegex(RuntimeError, 'drift'):
                    qa.apply()
                undo.assert_not_called()

    def test_this_execution_failure_triggers_bounded_rollback(self):
        with tempfile.TemporaryDirectory() as temp:
            def failed(operation_id):
                qa.save('apply-receipt.json', {'operationId': operation_id, 'events': [{'phase': 'service_created'}]})
                raise RuntimeError('probe_failed')
            with patch.object(qa, 'ARTIFACTS', Path(temp)), patch.object(qa, '_apply', side_effect=failed), patch.object(qa, 'rollback') as undo:
                with self.assertRaisesRegex(RuntimeError, 'probe_failed'):
                    qa.apply()
                undo.assert_called_once()
                self.assertTrue(json.loads(Path(temp, 'apply-receipt.json').read_text())['rollbackCompleted'])


if __name__ == '__main__':
    unittest.main()
