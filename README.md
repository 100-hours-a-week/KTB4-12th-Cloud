# KTB4-12th-Cloud

KTB4 12th team cloud infrastructure

## Production deployment

Production releases are started manually from `.github/workflows/cd-production.yml` on the `main` branch.
GitHub Actions exchanges its OIDC token for short-lived AWS credentials and runs the deployment on the EC2 instance through AWS Systems Manager Run Command. No inbound SSH access from GitHub-hosted runners is required.

### Required repository variables

| Variable | Example |
|---|---|
| `PROD_AWS_ROLE_ARN` | `arn:aws:iam::721744297924:role/seonjalal-v1-github-actions-role` |
| `PROD_AWS_REGION` | `ap-northeast-2` |
| `PROD_EC2_INSTANCE_ID` | `i-0417358a95b551c12` |
| `PROD_DEPLOY_PATH` | `/opt/seonjalal` |
| `PROD_BASE_URL` | `https://www.seonjalal.com` |
| `PROD_READ_ONLY_SMOKE_PATH` | `/api/products` |

The OIDC role trust policy must restrict access to `100-hours-a-week/KTB4-12th-Cloud` on `refs/heads/main`. Its permissions must allow `ssm:SendCommand` for the production instance and `AWS-RunShellScript`, plus `ssm:GetCommandInvocation` and `ssm:ListCommandInvocations` for reading the result.

### EC2 prerequisites

The deployment directory must already contain:

- `.env`
- `compose.yaml`
- `compose.production.yaml`
- executable `scripts/verify.sh`

The database container must also be running and healthy before a release starts. The workflow transfers `scripts/deploy-production.sh` through SSM, deploys Backend and Frontend in order, runs internal and external smoke tests, and rolls back to the previous release environment if an application replacement fails.
