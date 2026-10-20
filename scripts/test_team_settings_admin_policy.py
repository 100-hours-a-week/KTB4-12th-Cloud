"""AWS 호출 없는 설정 서비스 관리자 정책 검사."""
import json
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
DIRECTORY = ROOT / 'infrastructure/terraform/environments/shared/team-settings'
POLICY = json.loads((DIRECTORY / 'settings-admin-policy.json').read_text())


class SettingsAdminPolicyTests(unittest.TestCase):
    def test_allows_secret_administration(self):
        statement = POLICY['Statement'][0]
        self.assertEqual(statement['Action'], 'secretsmanager:*')
        self.assertEqual(statement['Resource'], '*')

    def test_parameter_crud_and_listing(self):
        self.assertEqual(POLICY['Statement'][1]['Action'], 'ssm:DescribeParameters')
        self.assertTrue({'ssm:PutParameter', 'ssm:DeleteParameter', 'ssm:GetParametersByPath'}
                        <= set(POLICY['Statement'][2]['Action']))

    def test_every_statement_requires_mfa_and_region(self):
        for statement in POLICY['Statement']:
            self.assertEqual(statement['Effect'], 'Allow')
            self.assertEqual(statement['Condition']['Bool']['aws:MultiFactorAuthPresent'], 'true')
            self.assertEqual(statement['Condition']['StringEquals']['aws:RequestedRegion'], 'ap-northeast-2')

    def test_no_server_iam_or_kms_admin_permissions(self):
        actions = []
        for statement in POLICY['Statement']:
            value = statement['Action']
            actions.extend([value] if isinstance(value, str) else value)
        for action in actions:
            self.assertTrue(action.startswith(('secretsmanager:', 'ssm:', 'kms:')))
        for prohibited in ('*', 'ssm:*', 'ssm:SendCommand', 'ssm:StartSession',
                           'iam:*', 'kms:*', 'kms:ScheduleKeyDeletion', 'kms:CreateGrant'):
            self.assertNotIn(prohibited, actions)

    def test_parameter_tags_do_not_apply_to_other_ssm_resources(self):
        self.assertEqual(POLICY['Statement'][2]['Resource'],
                         'arn:aws:ssm:ap-northeast-2:721744297924:parameter/*')

    def test_kms_crypto_via_settings_services_only(self):
        condition = POLICY['Statement'][3]['Condition']['StringEquals']
        self.assertEqual(condition['kms:CallerAccount'], '721744297924')
        self.assertEqual(set(condition['kms:ViaService']), {
            'secretsmanager.ap-northeast-2.amazonaws.com', 'ssm.ap-northeast-2.amazonaws.com'})

    def test_admin_mode_is_opt_in(self):
        self.assertIn('default     = false', (DIRECTORY / 'access-mode.tf').read_text())
        self.assertIn('var.team_settings_admin_enabled ? file(', (DIRECTORY / 'main.tf').read_text())


if __name__ == '__main__':
    unittest.main()
