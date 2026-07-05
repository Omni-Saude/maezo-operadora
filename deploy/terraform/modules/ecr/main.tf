# Module: ecr
# ECR repository for the maezo-agent container image.
# Single image per cluster; AGENT_ID env var selects the agent at runtime.
# Per binding direction: ECR in sa-east-1.

locals {
  repo_name = var.repository_name
  common_tags = merge(var.tags, {
    Module    = "ecr"
    ManagedBy = "terraform"
    Platform  = "maezo"
  })
}

resource "aws_ecr_repository" "this" {
  name                 = local.repo_name
  image_tag_mutability = var.image_tag_mutability

  image_scanning_configuration {
    scan_on_push = true
  }

  encryption_configuration {
    encryption_type = "KMS"
    kms_key         = var.kms_key_arn
  }

  tags = merge(local.common_tags, { Name = local.repo_name })
}

resource "aws_ecr_lifecycle_policy" "this" {
  repository = aws_ecr_repository.this.name
  policy     = jsonencode({
    rules = [
      {
        rulePriority = 1
        description  = "Keep last 30 tagged release images"
        selection = {
          tagStatus     = "tagged"
          tagPrefixList = ["v", "release-"]
          countType     = "imageCountMoreThan"
          countNumber   = 30
        }
        action = { type = "expire" }
      },
      {
        rulePriority = 2
        description  = "Expire untagged images older than 7 days"
        selection = {
          tagStatus   = "untagged"
          countType   = "sinceImagePushed"
          countUnit   = "days"
          countNumber = 7
        }
        action = { type = "expire" }
      }
    ]
  })
}

# Allow GitHub Actions OIDC deploy role to push and pull.
data "aws_iam_policy_document" "ecr_policy" {
  statement {
    sid    = "AllowGitHubActionsPush"
    effect = "Allow"
    principals {
      type        = "AWS"
      identifiers = var.push_role_arns
    }
    actions = [
      "ecr:GetDownloadUrlForLayer",
      "ecr:BatchGetImage",
      "ecr:BatchCheckLayerAvailability",
      "ecr:PutImage",
      "ecr:InitiateLayerUpload",
      "ecr:UploadLayerPart",
      "ecr:CompleteLayerUpload",
    ]
  }

  statement {
    sid    = "AllowEKSNodePull"
    effect = "Allow"
    principals {
      type        = "AWS"
      identifiers = var.pull_role_arns
    }
    actions = [
      "ecr:GetDownloadUrlForLayer",
      "ecr:BatchGetImage",
      "ecr:BatchCheckLayerAvailability",
    ]
  }
}

resource "aws_ecr_repository_policy" "this" {
  repository = aws_ecr_repository.this.name
  policy     = data.aws_iam_policy_document.ecr_policy.json
}
