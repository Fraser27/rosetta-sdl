"""
Deploy Rosetta SDL MCP Server to AgentCore Gateway as a target.

Deploys the governed semantic layer as a single MCP server behind an AgentCore Gateway,
enabling any AI agent to discover data assets, query governed metrics, and search
documents through the semantic layer — all via the MCP protocol.

Usage:
    python deploy_agent.py                     # Interactive (step-by-step)
    python deploy_agent.py --non-interactive   # Automated (no pauses)
"""

import boto3
import json
import sys
import os
import logging
import uuid
from pathlib import Path
from bedrock_agentcore_starter_toolkit import Runtime
from boto3.session import Session
import ac_utils as utils

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    handlers=[logging.StreamHandler()]
)

# Set AWS region
os.environ['AWS_DEFAULT_REGION'] = os.environ.get('AWS_REGION', 'us-east-1')
REGION = os.environ['AWS_DEFAULT_REGION']

# ============================================================================
# GLOBAL CONFIGURATION
# ============================================================================

# CDK Stack name for auto-discovery
CDK_STACK_NAME = "RosettaSdlStack"

# IAM Role Names
RUNTIME_ROLE_NAME = "rosetta-sdl-runtime"
GATEWAY_ROLE_NAME = "rosetta-sdl-gw"

# Gateway
GATEWAY_NAME = "rosetta-sdl-gateway"
GATEWAY_DESCRIPTION = "Rosetta SDL — Governed Semantic Data Layer MCP Gateway"

# Cognito - Gateway (Inbound Auth)
GW_USER_POOL_NAME = "rosetta-sdl-gateway-pool"
GW_RESOURCE_SERVER_ID = "rosetta-sdl-gateway"
GW_RESOURCE_SERVER_NAME = "Rosetta SDL Gateway"
GW_CLIENT_NAME = "rosetta-sdl-gateway-client"

# Cognito - Runtime (Outbound Auth)
RT_USER_POOL_NAME = "rosetta-sdl-runtime-pool"
RT_RESOURCE_SERVER_ID = "rosetta-sdl-runtime"
RT_RESOURCE_SERVER_NAME = "Rosetta SDL Runtime"
RT_CLIENT_NAME = "rosetta-sdl-runtime-client"

# MCP Server Deployment
ROSETTA_MCP_FILE = "rosetta_mcp.py"
ROSETTA_AGENT_NAME = "rosetta_sdl_mcp"

# Gateway Target
ROSETTA_TARGET_NAME = "rosetta-sdl-mcp-target"

# OAuth Credential Provider
OAUTH_CREDENTIAL_PROVIDER_NAME = "rosetta-sdl-identity"

# Secrets Manager
INTERNAL_API_KEY_SECRET_NAME = "rosetta-sdl/internal-api-key"

# ============================================================================


def get_or_create_internal_api_key():
    """Get or create the internal API key in Secrets Manager.

    If the secret already exists, returns the stored value.
    Otherwise generates a UUID, stores it, and returns it.
    Both AgentCore Runtime and EC2 FastAPI read from this same secret.
    """
    print("\nGetting/Creating internal API key in Secrets Manager...")

    sm = boto3.client("secretsmanager", region_name=REGION)

    # Try to retrieve existing secret
    try:
        resp = sm.get_secret_value(SecretId=INTERNAL_API_KEY_SECRET_NAME)
        api_key = resp["SecretString"]
        print(f"  Using existing secret: {INTERNAL_API_KEY_SECRET_NAME}")
        print(f"  Key prefix: {api_key[:8]}...")
        return api_key
    except sm.exceptions.ResourceNotFoundException:
        pass

    # Generate and store new key
    api_key = str(uuid.uuid4())
    sm.create_secret(
        Name=INTERNAL_API_KEY_SECRET_NAME,
        Description="Internal API key for Rosetta SDL service-to-service auth (Runtime → FastAPI)",
        SecretString=api_key,
    )
    print(f"  Created new secret: {INTERNAL_API_KEY_SECRET_NAME}")
    print(f"  Key prefix: {api_key[:8]}...")
    return api_key


def discover_api_url():
    """Auto-discover the ALB DNS from CDK CloudFormation stack outputs."""
    print("\nAuto-discovering Rosetta SDL API URL from CloudFormation...")

    try:
        cfn = boto3.client("cloudformation", region_name=REGION)
        outputs = cfn.describe_stacks(StackName=CDK_STACK_NAME)["Stacks"][0]["Outputs"]
        alb_dns = next(o["OutputValue"] for o in outputs if o["OutputKey"] == "AlbDnsName")
        api_url = f"http://{alb_dns}"
        print(f"  Discovered ALB from {CDK_STACK_NAME} stack")
        print(f"  API URL: {api_url}")
        return api_url
    except Exception as e:
        print(f"  WARNING: Could not auto-discover from CloudFormation: {e}")
        print(f"  Set API_URL environment variable manually.")
        api_url = os.environ.get("API_URL")
        if not api_url:
            print("  ERROR: API_URL not set and CloudFormation discovery failed.")
            sys.exit(1)
        print(f"  Using API_URL from environment: {api_url}")
        return api_url


def create_runtime_execution_role():
    """Create or update IAM role for AgentCore Runtime."""
    print("\nCreating/Updating IAM role for AgentCore Runtime...")

    iam_client = boto3.client('iam')

    try:
        try:
            response = iam_client.get_role(RoleName=f"agentcore-runtime-{RUNTIME_ROLE_NAME}-role")
            print(f"  Role exists, updating policies...")
            policies = iam_client.list_role_policies(RoleName=f"agentcore-runtime-{RUNTIME_ROLE_NAME}-role", MaxItems=100)
            for policy_name in policies.get('PolicyNames', []):
                iam_client.delete_role_policy(RoleName=f"agentcore-runtime-{RUNTIME_ROLE_NAME}-role", PolicyName=policy_name)
        except iam_client.exceptions.NoSuchEntityException:
            print(f"  Creating new role...")

        role_arn = utils.create_agentcore_runtime_role(RUNTIME_ROLE_NAME)
        print(f"  Runtime Role ARN: {role_arn}")
        return role_arn
    except Exception as e:
        print(f"  Error: {e}")
        sys.exit(1)


def create_gateway_iam_role():
    """Create or update IAM role for the Gateway."""
    print("\nCreating/Updating IAM role for AgentCore Gateway...")

    iam_client = boto3.client('iam')
    role_name = f"agentcore-{GATEWAY_ROLE_NAME}-role"

    try:
        try:
            iam_client.get_role(RoleName=role_name)
            print(f"  Role exists, updating policies...")
            policies = iam_client.list_role_policies(RoleName=role_name, MaxItems=100)
            for policy_name in policies.get('PolicyNames', []):
                iam_client.delete_role_policy(RoleName=role_name, PolicyName=policy_name)
        except iam_client.exceptions.NoSuchEntityException:
            print(f"  Creating new role...")

        role_arn = utils.create_agentcore_gateway_role(GATEWAY_ROLE_NAME)
        print(f"  Gateway Role ARN: {role_arn}")
        return role_arn
    except Exception as e:
        print(f"  Error: {e}")
        sys.exit(1)


def create_cognito_pool_for_gateway():
    """Create or get Cognito Pool for inbound authorization to Gateway."""
    print("\nCreating/Getting Cognito Pool for Gateway (Inbound Auth)...")

    SCOPES = [{"ScopeName": "invoke", "ScopeDescription": "Invoke the Rosetta SDL gateway"}]
    scope_names = [f"{GW_RESOURCE_SERVER_ID}/{s['ScopeName']}" for s in SCOPES]
    scope_string = " ".join(scope_names)

    cognito = boto3.client("cognito-idp", region_name=REGION)

    gw_user_pool_id = utils.get_or_create_user_pool(cognito, GW_USER_POOL_NAME)
    utils.get_or_create_resource_server(cognito, gw_user_pool_id, GW_RESOURCE_SERVER_ID, GW_RESOURCE_SERVER_NAME, SCOPES)
    gw_client_id, gw_client_secret = utils.get_or_create_m2m_client(cognito, gw_user_pool_id, GW_CLIENT_NAME, GW_RESOURCE_SERVER_ID, scope_names)
    gw_discovery_url = f'https://cognito-idp.{REGION}.amazonaws.com/{gw_user_pool_id}/.well-known/openid-configuration'

    print(f"  Gateway Pool ID: {gw_user_pool_id}")
    print(f"  Client ID: {gw_client_id}")

    return {
        "user_pool_id": gw_user_pool_id,
        "client_id": gw_client_id,
        "client_secret": gw_client_secret,
        "discovery_url": gw_discovery_url,
        "scope_string": scope_string,
    }


def create_cognito_pool_for_runtime():
    """Create or get Cognito Pool for Runtime (outbound auth from Gateway)."""
    print("\nCreating/Getting Cognito Pool for Runtime (Outbound Auth)...")

    SCOPES = [{"ScopeName": "invoke", "ScopeDescription": "Invoke the Rosetta SDL runtime"}]
    scope_names = [f"{RT_RESOURCE_SERVER_ID}/{s['ScopeName']}" for s in SCOPES]
    scope_string = " ".join(scope_names)

    cognito = boto3.client("cognito-idp", region_name=REGION)

    rt_user_pool_id = utils.get_or_create_user_pool(cognito, RT_USER_POOL_NAME)
    utils.get_or_create_resource_server(cognito, rt_user_pool_id, RT_RESOURCE_SERVER_ID, RT_RESOURCE_SERVER_NAME, SCOPES)
    rt_client_id, rt_client_secret = utils.get_or_create_m2m_client(cognito, rt_user_pool_id, RT_CLIENT_NAME, RT_RESOURCE_SERVER_ID, scope_names)
    rt_discovery_url = f'https://cognito-idp.{REGION}.amazonaws.com/{rt_user_pool_id}/.well-known/openid-configuration'

    print(f"  Runtime Pool ID: {rt_user_pool_id}")
    print(f"  Client ID: {rt_client_id}")

    return {
        "user_pool_id": rt_user_pool_id,
        "client_id": rt_client_id,
        "client_secret": rt_client_secret,
        "discovery_url": rt_discovery_url,
        "scope_string": scope_string,
    }


def create_agentcore_gateway(gateway_role_arn, gw_cognito_config):
    """Create the AgentCore Gateway or get existing one."""
    print("\nCreating/Getting AgentCore Gateway...")

    gateway_client = boto3.client('bedrock-agentcore-control', region_name=REGION)

    # Check for existing gateway
    try:
        for gw in gateway_client.list_gateways().get('items', []):
            if gw.get('name') == GATEWAY_NAME:
                gateway_id = gw['gatewayId']
                details = gateway_client.get_gateway(gatewayIdentifier=gateway_id)
                gateway_url = details['gatewayUrl']
                print(f"  Using existing gateway: {gateway_id}")
                print(f"  Gateway URL: {gateway_url}")
                return {"gateway_id": gateway_id, "gateway_url": gateway_url}
    except Exception as e:
        print(f"  Could not list gateways: {e}")

    # Create new gateway
    print(f"  Creating gateway: {GATEWAY_NAME}")
    resp = gateway_client.create_gateway(
        name=GATEWAY_NAME,
        roleArn=gateway_role_arn,
        protocolType='MCP',
        protocolConfiguration={
            'mcp': {
                'supportedVersions': ['2025-03-26'],
                'searchType': 'SEMANTIC',
            }
        },
        authorizerType='CUSTOM_JWT',
        authorizerConfiguration={
            'customJWTAuthorizer': {
                'allowedClients': [gw_cognito_config['client_id']],
                'discoveryUrl': gw_cognito_config['discovery_url'],
            }
        },
        description=GATEWAY_DESCRIPTION,
    )

    gateway_id = resp['gatewayId']
    gateway_url = resp['gatewayUrl']
    print(f"  Gateway ID: {gateway_id}")
    print(f"  Gateway URL: {gateway_url}")
    return {"gateway_id": gateway_id, "gateway_url": gateway_url}


def deploy_mcp_server_to_runtime(runtime_role_arn, runtime_cognito_config, api_url, internal_api_key):
    """Deploy the Rosetta SDL MCP server to AgentCore Runtime."""
    print(f"\nDeploying {ROSETTA_MCP_FILE} to AgentCore Runtime...")
    print(f"  API_URL: {api_url}")
    print(f"  INTERNAL_API_KEY: {internal_api_key[:8]}...")
    print("  This may take 5-10 minutes...")

    script_dir = Path(__file__).parent

    # Verify required files
    for f in [ROSETTA_MCP_FILE, 'requirements.txt']:
        if not (script_dir / f).exists():
            raise FileNotFoundError(f"Required file {f} not found at {script_dir / f}")
    print("  All required files found")

    original_dir = os.getcwd()
    os.chdir(script_dir)

    try:
        agentcore_runtime = Runtime()

        auth_config = {
            "customJWTAuthorizer": {
                "allowedClients": [runtime_cognito_config["client_id"]],
                "discoveryUrl": runtime_cognito_config["discovery_url"],
            }
        }

        agentcore_runtime.configure(
            entrypoint=ROSETTA_MCP_FILE,
            execution_role=runtime_role_arn,
            auto_create_ecr=True,
            requirements_file="requirements.txt",
            non_interactive=True,
            region=REGION,
            authorizer_configuration=auth_config,
            protocol="MCP",
            agent_name=ROSETTA_AGENT_NAME,
        )

        launch_result = agentcore_runtime.launch(
            auto_update_on_conflict=True,
            env_vars={"API_URL": api_url, "INTERNAL_API_KEY": internal_api_key},
        )

        agent_arn = launch_result.agent_arn
        agent_id = launch_result.agent_id
        encoded_arn = agent_arn.replace(':', '%3A').replace('/', '%2F')
        agent_url = f'https://bedrock-agentcore.{REGION}.amazonaws.com/runtimes/{encoded_arn}/invocations?qualifier=DEFAULT'

        print(f"  Agent ARN: {agent_arn}")
        print(f"  Agent ID: {agent_id}")
        print(f"  Agent URL: {agent_url}")

        return {"agent_arn": agent_arn, "agent_id": agent_id, "agent_url": agent_url}
    finally:
        os.chdir(original_dir)
        # Clean up Dockerfile left by toolkit
        dockerfile = script_dir / "Dockerfile"
        if dockerfile.exists():
            dockerfile.unlink()


def create_oauth_credential_provider(runtime_cognito_config):
    """Create OAuth credential provider for Gateway outbound auth."""
    print("\nCreating OAuth credential provider...")

    identity_client = boto3.client('bedrock-agentcore-control', region_name=REGION)

    resp = identity_client.create_oauth2_credential_provider(
        name=OAUTH_CREDENTIAL_PROVIDER_NAME,
        credentialProviderVendor="CustomOauth2",
        oauth2ProviderConfigInput={
            'customOauth2ProviderConfig': {
                'oauthDiscovery': {'discoveryUrl': runtime_cognito_config['discovery_url']},
                'clientId': runtime_cognito_config['client_id'],
                'clientSecret': runtime_cognito_config['client_secret'],
            }
        },
    )

    arn = resp['credentialProviderArn']
    print(f"  Provider ARN: {arn}")
    return arn


def create_gateway_target(gateway_id, agent_url, credential_provider_arn, runtime_scope_string):
    """Create a Gateway target for the Rosetta SDL MCP server."""
    print(f"\nCreating/Getting Gateway target: {ROSETTA_TARGET_NAME}...")

    gateway_client = boto3.client('bedrock-agentcore-control', region_name=REGION)

    # Check for existing target
    try:
        for target in gateway_client.list_gateway_targets(gatewayIdentifier=gateway_id).get('items', []):
            if target.get('name') == ROSETTA_TARGET_NAME:
                target_id = target['targetId']
                print(f"  Using existing target: {target_id}")
                return target_id
    except Exception as e:
        print(f"  Could not list targets: {e}")

    # Create new target
    resp = gateway_client.create_gateway_target(
        name=ROSETTA_TARGET_NAME,
        gatewayIdentifier=gateway_id,
        targetConfiguration={
            'mcp': {'mcpServer': {'endpoint': agent_url}}
        },
        credentialProviderConfigurations=[
            {
                'credentialProviderType': 'OAUTH',
                'credentialProvider': {
                    'oauthCredentialProvider': {
                        'providerArn': credential_provider_arn,
                        'scopes': [runtime_scope_string],
                    }
                },
            }
        ],
    )

    target_id = resp.get('targetId', 'N/A')
    print(f"  Target ID: {target_id}")
    return target_id


def verify_gateway_targets(gateway_id):
    """Verify Gateway targets are ready."""
    print("\nVerifying Gateway targets...")

    gateway_client = boto3.client('bedrock-agentcore-control', region_name=REGION)
    targets = gateway_client.list_gateway_targets(gatewayIdentifier=gateway_id).get('items', [])

    print(f"  Found {len(targets)} target(s)")
    for t in targets:
        print(f"  - {t.get('name', '?')}: {t.get('status', '?')}")
    return targets


def display_architecture():
    """Display what we're building."""
    print("""
============================================================================
                    ROSETTA SDL — AGENTCORE DEPLOYMENT
============================================================================

  Architecture:

  +-----------------------+
  |   Any AI Agent        |
  |  (Claude, Strands,    |
  |   QuickSuite)         |
  +----------+------------+
             | JWT Auth (Cognito)
             v
  +-----------------------+
  |  AgentCore Gateway    |
  |  (MCP Protocol)       |
  +----------+------------+
             | OAuth2 (Cognito M2M)
             v
  +-----------------------+
  |  AgentCore Runtime    |
  |  Rosetta SDL MCP      |
  |  (8 tools)            |
  +----------+------------+
             | HTTP (API_URL)
             v
  +-----------------------+
  |  EC2 (FastAPI+Neo4j)  |
  |  - Graph Ontology     |
  |  - Metric Compiler    |
  |  - SQL Firewall       |
  |  - Query Router       |
  +-----+----------+------+
        |          |
        v          v
     Athena    S3 Vectors

  Deployment Steps:
    1. Auto-discover API URL from CloudFormation
    2. Create Gateway IAM role
    3. Create Runtime IAM role
    4. Create Gateway Cognito pool (inbound auth)
    5. Create Runtime Cognito pool (outbound auth)
    6. Create AgentCore Gateway
    7. Deploy Rosetta SDL MCP Server to Runtime
    8. Create OAuth2 Credential Provider
    9. Create Gateway Target
   10. Verify Deployment

============================================================================
""")


def wait_for_user(step_name, non_interactive=False):
    """Pause before next step."""
    if non_interactive:
        print(f"\n{'─' * 60}")
        print(f"  Next: {step_name}")
        print(f"{'─' * 60}\n")
        return

    print(f"\n{'─' * 60}")
    input(f"  Next: {step_name}\n  Press Enter to continue...")
    print(f"{'─' * 60}\n")


def main():
    """Main deployment function."""
    import argparse

    parser = argparse.ArgumentParser(description='Deploy Rosetta SDL MCP Server to AgentCore Gateway')
    parser.add_argument('--non-interactive', action='store_true',
                       help='Run without pausing for user input')
    args = parser.parse_args()
    non_interactive = args.non_interactive

    display_architecture()

    if not non_interactive:
        input("Press Enter to start deployment...")
    else:
        print("Starting automated deployment...\n")

    # Step 1: Auto-discover API URL + create internal API key
    api_url = discover_api_url()
    internal_api_key = get_or_create_internal_api_key()
    wait_for_user("Create Gateway IAM role", non_interactive)

    # Step 2: Create Gateway IAM role
    gateway_role_arn = create_gateway_iam_role()
    wait_for_user("Create Runtime IAM role", non_interactive)

    # Step 3: Create Runtime IAM role
    runtime_role_arn = create_runtime_execution_role()
    wait_for_user("Create Gateway Cognito pool", non_interactive)

    # Step 4: Create Cognito pools
    gw_cognito_config = create_cognito_pool_for_gateway()
    wait_for_user("Create Runtime Cognito pool", non_interactive)

    runtime_cognito_config = create_cognito_pool_for_runtime()
    wait_for_user("Create AgentCore Gateway", non_interactive)

    # Step 5: Create AgentCore Gateway
    gateway_info = create_agentcore_gateway(gateway_role_arn, gw_cognito_config)
    wait_for_user("Deploy Rosetta SDL MCP Server to Runtime", non_interactive)

    # Step 6: Deploy Rosetta SDL MCP Server
    rosetta_agent = deploy_mcp_server_to_runtime(runtime_role_arn, runtime_cognito_config, api_url, internal_api_key)
    wait_for_user("Create OAuth credential provider", non_interactive)

    # Step 7: Create OAuth credential provider
    credential_provider_arn = create_oauth_credential_provider(runtime_cognito_config)
    wait_for_user("Create Gateway Target", non_interactive)

    # Step 8: Create Gateway target
    target_id = create_gateway_target(
        gateway_id=gateway_info["gateway_id"],
        agent_url=rosetta_agent["agent_url"],
        credential_provider_arn=credential_provider_arn,
        runtime_scope_string=runtime_cognito_config["scope_string"],
    )
    wait_for_user("Verify deployment", non_interactive)

    # Step 9: Verify
    verify_gateway_targets(gateway_info["gateway_id"])

    # Summary
    user_pool_id_lower = gw_cognito_config['user_pool_id'].lower().replace('_', '')
    token_url = f"https://{user_pool_id_lower}.auth.{REGION}.amazoncognito.com/oauth2/token"

    print("\n" + "=" * 60)
    print("  DEPLOYMENT COMPLETE")
    print("=" * 60)
    print(f"\n  Gateway URL:     {gateway_info['gateway_url']}")
    print(f"  Gateway ID:      {gateway_info['gateway_id']}")
    print(f"  Token URL:       {token_url}")
    print(f"  Client ID:       {gw_cognito_config['client_id']}")
    print(f"  Client Secret:   {gw_cognito_config['client_secret']}")
    print(f"  Scope:           {gw_cognito_config['scope_string']}")
    print(f"  Agent ARN:       {rosetta_agent['agent_arn']}")
    print(f"  FastAPI URL:     {api_url}")
    print(f"  API Key Secret:  {INTERNAL_API_KEY_SECRET_NAME} (in Secrets Manager)")
    print(f"  Target ID:       {target_id}")

    # Save deployment info
    deployment_info = {
        "gateway": gateway_info,
        "gateway_role_arn": gateway_role_arn,
        "runtime_role_arn": runtime_role_arn,
        "auth": {
            "token_url": token_url,
            "client_id": gw_cognito_config["client_id"],
            "client_secret": gw_cognito_config["client_secret"],
            "discovery_url": gw_cognito_config["discovery_url"],
            "scope": gw_cognito_config["scope_string"],
        },
        "agent": rosetta_agent,
        "internal_api_key_secret": INTERNAL_API_KEY_SECRET_NAME,
        "credential_provider_arn": credential_provider_arn,
        "target_id": target_id,
        "fastapi_url": api_url,
    }

    deployment_file = Path(__file__).parent / "deployment_info.json"
    with open(deployment_file, 'w') as f:
        json.dump(deployment_info, f, indent=2)

    print(f"\n  Saved to: {deployment_file}")
    print("=" * 60)


if __name__ == "__main__":
    main()
