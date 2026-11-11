# 오류 분류가 인증값·내부 주소·원문을 다시 노출하지 않는지 로컬에서 검사합니다.
require 'minitest/autorun'
require_relative 'lib/v3_gitops_errors'

class V3GitopsErrorsTest < Minitest::Test
  def test_aws_session_and_profile_failures
    assert_includes V3GitopsErrors.describe('Error loading SSO Token: Token has expired'), 'AWS_SSO_SESSION'
    assert_includes V3GitopsErrors.describe('ExpiredTokenException: token expired'), 'AWS_CREDENTIALS'
    assert_includes V3GitopsErrors.describe('The config profile (private-profile) could not be found'), 'AWS_PROFILE'
  end

  def test_helm_and_network_failures_are_distinct
    assert_includes V3GitopsErrors.describe('Error: release: not found'), 'HELM_RELEASE_NOT_FOUND'
    assert_includes V3GitopsErrors.describe('dial tcp 10.0.0.1:443: i/o timeout'), 'API_NETWORK'
    assert_includes V3GitopsErrors.describe('Error: unknown flag: --bad'), 'CLI_OPTIONS'
  end

  def test_auth_error_does_not_include_credential_or_endpoint
    raw = 'getting credentials: exec failed; token=DO_NOT_PRINT_ME; https://private.example.invalid'
    result = V3GitopsErrors.describe(raw)
    assert_includes result, 'KUBE_AUTH'
    refute_includes result, 'DO_NOT_PRINT_ME'
    refute_includes result, 'private.example.invalid'
  end

  def test_unknown_error_does_not_echo_input
    result = V3GitopsErrors.describe('opaque failure password=DO_NOT_PRINT_ME')
    assert_includes result, 'UNKNOWN'
    refute_includes result, 'DO_NOT_PRINT_ME'
    refute_includes result, 'password='
  end
end
