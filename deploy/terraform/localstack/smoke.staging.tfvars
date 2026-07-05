# tfvars for the LocalStack smoke plan of staging-sa-east-1.
# Values MUST match what seed.sh creates in LocalStack. These are fake — they
# only need to satisfy no-default variables and let data sources resolve.
# Consumed by `make tf-smoke` (terraform plan -var-file=...).

aws_account_id = "000000000000"          # LocalStack's canonical mock account
owner_email    = "smoke@localstack.test"
cost_center    = "maezo-smoke"

# Must match EKS_CLUSTER_NAME in docker-compose.localstack.yml / seed.sh.
eks_cluster_name = "amh-data-platform-eks-staging"

# Left empty: SG/role wiring is real-AWS-only; not exercised by plan-ability.
eks_node_security_group_ids = []
eks_node_role_arns          = []
