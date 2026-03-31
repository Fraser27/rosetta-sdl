"""
AgentCore deployment utilities for Rosetta SDL.

Handles IAM roles, Cognito setup, and Gateway management.
Adapted from serverless-datalake/agentcore/ac_utils.py.
"""

import boto3
import json
import time
from boto3.session import Session
from botocore.exceptions import ClientError


def get_or_create_user_pool(cognito, pool_name):
    """Get existing user pool by name or create a new one with a domain."""
    response = cognito.list_user_pools(MaxResults=60)
    for pool in response["UserPools"]:
        if pool["Name"] == pool_name:
            return pool["Id"]

    print(f"  Creating new user pool: {pool_name}")
    created = cognito.create_user_pool(PoolName=pool_name)
    user_pool_id = created["UserPool"]["Id"]

    # Create domain for OAuth2 token endpoint
    domain = user_pool_id.replace("_", "").lower()
    cognito.create_user_pool_domain(Domain=domain, UserPoolId=user_pool_id)
    print(f"  Domain created: {domain}")
    return user_pool_id


def get_or_create_resource_server(cognito, user_pool_id, resource_server_id, resource_server_name, scopes):
    """Get or create a Cognito resource server for OAuth2 scopes."""
    try:
        cognito.describe_resource_server(
            UserPoolId=user_pool_id,
            Identifier=resource_server_id,
        )
        return resource_server_id
    except cognito.exceptions.ResourceNotFoundException:
        print(f"  Creating resource server: {resource_server_name}")
        cognito.create_resource_server(
            UserPoolId=user_pool_id,
            Identifier=resource_server_id,
            Name=resource_server_name,
            Scopes=scopes,
        )
        return resource_server_id


def get_or_create_m2m_client(cognito, user_pool_id, client_name, resource_server_id, scopes=None):
    """Get or create an M2M (client_credentials) app client."""
    response = cognito.list_user_pool_clients(UserPoolId=user_pool_id, MaxResults=60)
    for client in response["UserPoolClients"]:
        if client["ClientName"] == client_name:
            describe = cognito.describe_user_pool_client(
                UserPoolId=user_pool_id, ClientId=client["ClientId"]
            )
            return client["ClientId"], describe["UserPoolClient"]["ClientSecret"]

    print(f"  Creating M2M client: {client_name}")
    if scopes is None:
        scopes = [f"{resource_server_id}/invoke"]

    created = cognito.create_user_pool_client(
        UserPoolId=user_pool_id,
        ClientName=client_name,
        GenerateSecret=True,
        AllowedOAuthFlows=["client_credentials"],
        AllowedOAuthScopes=scopes,
        AllowedOAuthFlowsUserPoolClient=True,
        SupportedIdentityProviders=["COGNITO"],
        ExplicitAuthFlows=["ALLOW_REFRESH_TOKEN_AUTH"],
    )
    return created["UserPoolClient"]["ClientId"], created["UserPoolClient"]["ClientSecret"]


def create_agentcore_runtime_role(agent_name):
    """Create IAM role for AgentCore Runtime. Rosetta MCP only needs network access to EC2."""
    iam_client = boto3.client("iam")
    role_name = f"agentcore-runtime-{agent_name}-role"
    boto_session = Session()
    region = boto_session.region_name
    account_id = boto3.client("sts").get_caller_identity()["Account"]

    role_policy = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Sid": "ECRImageAccess",
                "Effect": "Allow",
                "Action": ["ecr:BatchGetImage", "ecr:GetDownloadUrlForLayer"],
                "Resource": [f"arn:aws:ecr:{region}:{account_id}:repository/*"],
            },
            {
                "Sid": "ECRTokenAccess",
                "Effect": "Allow",
                "Action": ["ecr:GetAuthorizationToken"],
                "Resource": "*",
            },
            {
                "Effect": "Allow",
                "Action": ["logs:DescribeLogStreams", "logs:CreateLogGroup"],
                "Resource": [f"arn:aws:logs:{region}:{account_id}:log-group:/aws/bedrock-agentcore/runtimes/*"],
            },
            {
                "Effect": "Allow",
                "Action": ["logs:DescribeLogGroups"],
                "Resource": [f"arn:aws:logs:{region}:{account_id}:log-group:*"],
            },
            {
                "Effect": "Allow",
                "Action": ["logs:CreateLogStream", "logs:PutLogEvents"],
                "Resource": [f"arn:aws:logs:{region}:{account_id}:log-group:/aws/bedrock-agentcore/runtimes/*:log-stream:*"],
            },
            {
                "Effect": "Allow",
                "Action": ["xray:PutTraceSegments", "xray:PutTelemetryRecords", "xray:GetSamplingRules", "xray:GetSamplingTargets"],
                "Resource": ["*"],
            },
            {
                "Effect": "Allow",
                "Action": "cloudwatch:PutMetricData",
                "Resource": "*",
                "Condition": {"StringEquals": {"cloudwatch:namespace": "bedrock-agentcore"}},
            },
            {
                "Sid": "BedrockModelInvocation",
                "Effect": "Allow",
                "Action": ["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"],
                "Resource": [
                    "arn:aws:bedrock:*::foundation-model/*",
                    f"arn:aws:bedrock:*:{account_id}:inference-profile/*",
                ],
            },
            {
                "Sid": "AgentCorePermissions",
                "Effect": "Allow",
                "Action": ["bedrock-agentcore:*", "iam:PassRole"],
                "Resource": "*",
            },
            {
                "Sid": "GetAgentAccessToken",
                "Effect": "Allow",
                "Action": [
                    "bedrock-agentcore:GetWorkloadAccessToken",
                    "bedrock-agentcore:GetWorkloadAccessTokenForJWT",
                    "bedrock-agentcore:GetWorkloadAccessTokenForUserId",
                ],
                "Resource": [
                    f"arn:aws:bedrock-agentcore:{region}:{account_id}:workload-identity-directory/default",
                    f"arn:aws:bedrock-agentcore:{region}:{account_id}:workload-identity-directory/default/workload-identity/{agent_name}-*",
                ],
            },
        ],
    }

    assume_role_policy = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Sid": "AssumeRolePolicy",
                "Effect": "Allow",
                "Principal": {"Service": "bedrock-agentcore.amazonaws.com"},
                "Action": "sts:AssumeRole",
                "Condition": {
                    "StringEquals": {"aws:SourceAccount": account_id},
                    "ArnLike": {"aws:SourceArn": f"arn:aws:bedrock-agentcore:{region}:{account_id}:*"},
                },
            }
        ],
    }

    try:
        role = iam_client.create_role(
            RoleName=role_name,
            AssumeRolePolicyDocument=json.dumps(assume_role_policy),
            Description=f"AgentCore Runtime role for {agent_name}",
        )
        print(f"  Created role: {role_name}")
        time.sleep(10)
    except iam_client.exceptions.EntityAlreadyExistsException:
        print(f"  Role '{role_name}' exists, updating policies...")
        policies = iam_client.list_role_policies(RoleName=role_name, MaxItems=100)
        for policy_name in policies["PolicyNames"]:
            iam_client.delete_role_policy(RoleName=role_name, PolicyName=policy_name)
        role = iam_client.get_role(RoleName=role_name)

    iam_client.put_role_policy(
        PolicyDocument=json.dumps(role_policy),
        PolicyName="AgentCoreRuntimePolicy",
        RoleName=role_name,
    )
    return role["Role"]["Arn"]


def create_agentcore_gateway_role(gateway_name):
    """Create IAM role for AgentCore Gateway."""
    iam_client = boto3.client("iam")
    role_name = f"agentcore-{gateway_name}-role"
    boto_session = Session()
    region = boto_session.region_name
    account_id = boto3.client("sts").get_caller_identity()["Account"]

    role_policy = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Action": [
                    "bedrock-agentcore:*",
                    "bedrock:*",
                    "agent-credential-provider:*",
                    "iam:PassRole",
                    "secretsmanager:GetSecretValue",
                    "lambda:InvokeFunction",
                ],
                "Resource": "*",
            }
        ],
    }

    assume_role_policy = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Sid": "AssumeRolePolicy",
                "Effect": "Allow",
                "Principal": {"Service": "bedrock-agentcore.amazonaws.com"},
                "Action": "sts:AssumeRole",
                "Condition": {
                    "StringEquals": {"aws:SourceAccount": account_id},
                    "ArnLike": {"aws:SourceArn": f"arn:aws:bedrock-agentcore:{region}:{account_id}:*"},
                },
            }
        ],
    }

    try:
        role = iam_client.create_role(
            RoleName=role_name,
            AssumeRolePolicyDocument=json.dumps(assume_role_policy),
        )
        print(f"  Created role: {role_name}")
        time.sleep(10)
    except iam_client.exceptions.EntityAlreadyExistsException:
        print(f"  Role '{role_name}' exists, updating policies...")
        policies = iam_client.list_role_policies(RoleName=role_name, MaxItems=100)
        for policy_name in policies["PolicyNames"]:
            iam_client.delete_role_policy(RoleName=role_name, PolicyName=policy_name)
        role = iam_client.get_role(RoleName=role_name)

    iam_client.put_role_policy(
        PolicyDocument=json.dumps(role_policy),
        PolicyName="AgentCoreGatewayPolicy",
        RoleName=role_name,
    )
    return role["Role"]["Arn"]
