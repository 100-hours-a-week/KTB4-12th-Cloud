// 네트워크 없는 정책 계약 검사. AWS IAM Simulator/실제 접속 검증의 대체 아님.
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const root = path.resolve(__dirname, '..');
const settings = 'infrastructure/terraform/environments/shared/team-settings/';
const read = (file) => fs.readFileSync(path.join(root, file), 'utf8');
const policy = JSON.parse(read(settings + 'team-ssm-policy.json'));
const prefix = 'arn:aws:ec2:ap-northeast-2:721744297924:instance/';
const shell = 'arn:aws:ssm:ap-northeast-2:721744297924:document/SSM-SessionManagerRunShell';
const session = 'arn:aws:ssm:ap-northeast-2:721744297924:session/';
const login = 'arn:aws:signin:ap-northeast-2:721744297924:oauth2/public-client/localhost';
const members = ['fs-neo', 'fs-theo', 'fs-ava', 'ai-dylan', 'ai-emet'];
const context = {
  'aws:username': 'fs-neo',
  'aws:MultiFactorAuthPresent': 'true',
  'aws:RequestedRegion': 'ap-northeast-2',
  'ssm:resourceTag/Project': 'seonjalal',
  'ssm:resourceTag/Environment': 'develop',
  'ssm:resourceTag/TeamSSMAccess': 'true',
};
const list = (value) => Array.isArray(value) ? value : [value];
function match(pattern, value, ctx) {
  const expanded = pattern.replace(/\$\{([^}]+)\}/g, (_, key) => ctx[key] ?? '__MISSING__');
  const regex = expanded.split('*').map((part) => part.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')).join('.*');
  return new RegExp('^' + regex + '$').test(value);
}
function conditions(condition, ctx) {
  return Object.entries(condition || {}).every(([op, entries]) =>
    Object.entries(entries).every(([key, expected]) => {
      const equal = list(expected).includes(ctx[key]);
      if (op === 'Bool' || op === 'StringEquals') return equal;
      if (op === 'StringNotEquals') return !equal;
      throw new Error('미지원 조건: ' + op);
    })
  );
}
function decision(action, resource, ctx = context, extra = []) {
  // 실제 정책에 사용된 연산자만 평가. 미지원 구문을 성공으로 처리하지 않음.
  let allowed = false;
  for (const s of [...policy.Statement, ...extra]) {
    if (!list(s.Action).some((p) => match(p, action, ctx))) continue;
    const resourceMatches = s.NotResource
      ? !list(s.NotResource).some((p) => match(p, resource, ctx))
      : list(s.Resource).some((p) => match(p, resource, ctx));
    if (!resourceMatches || !conditions(s.Condition, ctx)) continue;
    if (s.Effect === 'Deny') return 'explicitDeny';
    allowed = true;
  }
  return allowed ? 'allowed' : 'implicitDeny';
}
const broadStart = [{ Effect: 'Allow', Action: 'ssm:StartSession', Resource: '*' }];

test('관리형 정책 크기와 모든 Allow의 MFA·서울 리전 조건', () => {
  assert.ok(JSON.stringify(policy).length <= 6144);
  for (const s of policy.Statement.filter((s) => s.Effect === 'Allow')) {
    assert.equal(s.Condition.Bool['aws:MultiFactorAuthPresent'], 'true');
    assert.equal(s.Condition.StringEquals['aws:RequestedRegion'], 'ap-northeast-2');
    assert.notEqual(s.Resource, '*');
  }
});
for (const member of members) {
  test(`${member}: 개발·스테이징 태그와 기본 문서 허용`, () => {
    for (const environment of ['develop', 'staging']) {
      const ctx = { ...context, 'aws:username': member, 'ssm:resourceTag/Environment': environment };
      assert.equal(decision('ssm:StartSession', prefix + 'i-example', ctx), 'allowed');
      assert.equal(decision('ssm:StartSession', shell, ctx), 'allowed');
    }
  });
  test(`${member}: 본인 세션만 연결·재접속·종료`, () => {
    for (const action of ['ssmmessages:OpenDataChannel', 'ssm:ResumeSession', 'ssm:TerminateSession']) {
      const ctx = { ...context, 'aws:username': member };
      assert.equal(decision(action, session + member + '-example', ctx), 'allowed');
      assert.equal(decision(action, session + 'infra-owner-example', ctx), 'explicitDeny');
    }
  });
}
for (const [name, patch] of [
  ['운영', { 'ssm:resourceTag/Environment': 'production' }],
  ['환경 태그 누락', { 'ssm:resourceTag/Environment': undefined }],
  ['접근 태그 누락(NAT·모니터링 기본)', { 'ssm:resourceTag/TeamSSMAccess': undefined }],
  ['접근 태그 false', { 'ssm:resourceTag/TeamSSMAccess': 'false' }],
  ['다른 프로젝트', { 'ssm:resourceTag/Project': 'other' }],
  ['프로젝트 누락', { 'ssm:resourceTag/Project': undefined }],
]) {
  test(`${name}: 다른 정책의 Allow가 있어도 차단`, () => {
    assert.equal(decision('ssm:StartSession', prefix + 'i-example', { ...context, ...patch }, broadStart), 'explicitDeny');
  });
}
test('MFA 미인증·누락과 다른 리전은 Allow 없음', () => {
  for (const mfa of ['false', undefined]) {
    assert.equal(decision('ssm:StartSession', prefix + 'i-example', { ...context, 'aws:MultiFactorAuthPresent': mfa }), 'implicitDeny');
  }
  assert.equal(decision('ssm:StartSession', prefix.replace('ap-northeast-2', 'us-east-1') + 'i-example'), 'explicitDeny');
});
test('포트 포워딩·SSH·사용자 문서 및 다른 계정 대상 차단', () => {
  for (const document of ['AWS-StartPortForwardingSession', 'AWS-StartPortForwardingSessionToRemoteHost', 'AWS-StartSSHSession', 'Custom-Shell']) {
    const arn = shell.replace('721744297924:document/SSM-SessionManagerRunShell', ':document/' + document);
    assert.equal(decision('ssm:StartSession', arn, context, broadStart), 'explicitDeny');
  }
  assert.equal(decision('ssm:StartSession', shell.replace('SSM-SessionManagerRunShell', 'OtherShell'), context, broadStart), 'explicitDeny');
  assert.equal(decision('ssm:StartSession', prefix.replace('721744297924', '000000000000') + 'i-example', context, broadStart), 'explicitDeny');
});
test('EC2 수명주기·RunCommand·EC2/ASG 태그 변경 명시 차단', () => {
  for (const action of [
    'ec2:RunInstances', 'ec2:TerminateInstances', 'ec2:StartInstances', 'ec2:StopInstances', 'ec2:RebootInstances',
    'ec2:CreateTags', 'ec2:DeleteTags', 'autoscaling:CreateOrUpdateTags', 'autoscaling:DeleteTags', 'ssm:SendCommand',
  ]) assert.equal(decision(action, '*'), 'explicitDeny');
});
test('SSM·브라우저 로그인 외 기존 Secret/배포 권한 추가 없음', () => {
  const actions = policy.Statement.filter((s) => s.Effect === 'Allow').flatMap((s) => list(s.Action));
  assert.deepEqual([...new Set(actions)].sort(), [
    'ssm:StartSession', 'ssm:ResumeSession', 'ssm:TerminateSession', 'ssmmessages:OpenDataChannel',
    'signin:AuthorizeOAuth2Access', 'signin:CreateOAuth2Token',
  ].sort());
});
test('5명 모두 MFA 인증·서울·현재 계정·동일 PC 로그인만 허용', () => {
  for (const member of members) {
    for (const action of ['signin:AuthorizeOAuth2Access', 'signin:CreateOAuth2Token']) {
      const ctx = { ...context, 'aws:username': member };
      assert.equal(decision(action, login, ctx), 'allowed');
      for (const mfa of ['false', undefined]) {
        assert.equal(decision(action, login, { ...ctx, 'aws:MultiFactorAuthPresent': mfa }), 'implicitDeny');
      }
      for (const resource of [
        login.replace('/localhost', '/remote'),
        login.replace('/localhost', '/other-tool'),
        login.replace('721744297924', '000000000000'),
        login.replace('ap-northeast-2', 'us-east-1'),
      ]) assert.equal(decision(action, resource, ctx), 'implicitDeny');
      assert.equal(decision(action, login, { ...ctx, 'aws:RequestedRegion': 'us-east-1' }), 'implicitDeny');
    }
  }
});
test('개발 EC2와 스테이징 ASG에만 접근 태그 추가', () => {
  const develop = read('infrastructure/terraform/environments/develop/runtime/main.tf');
  assert.match(develop, /resource "aws_instance" "develop"/);
  assert.match(develop, /TeamSSMAccess\s*=\s*"true"/);
  const staging = read('infrastructure/terraform/environments/staging/rehearsal/compute.tf');
  const asg = staging.split('resource "aws_autoscaling_group" "application"')[1].split('resource "aws_ecs_capacity_provider"')[0];
  assert.match(asg, /Project\s*= local\.required_tags\.Project/);
  assert.match(asg, /Environment\s*= local\.required_tags\.Environment/);
  assert.match(asg, /TeamSSMAccess\s*= "true"/);
  assert.match(asg, /key\s*= tag\.key\s+value\s*= tag\.value\s+propagate_at_launch = true/);
  for (const file of [
    'infrastructure/terraform/environments/staging/observability/main.tf',
    'infrastructure/terraform/environments/staging/egress/main.tf',
    'infrastructure/terraform/environments/production/runtime/compute.tf',
  ]) assert.doesNotMatch(read(file), /TeamSSMAccess\s*=\s*"true"/);
});
