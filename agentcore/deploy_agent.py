"""
Deploy Rosetta SDL MCP Server to AgentCore Gateway as a target.

Uses a SINGLE Cognito User Pool (from the CDK stack) for all authentication:
  - Gateway inbound auth (clients → Gateway)
  - Gateway → Runtime outbound auth (via Token Vault credential provider)
  - Runtime → FastAPI outbound auth (direct client_credentials token fetch)

Usage:
    python deploy_agent.py                     # Interactive (step-by-step)
    python deploy_agent.py --non-interactive   # Automated (no pauses)
    python deploy_agent.py --cleanup           # Delete all AgentCore resources
"""

import boto3
import json
import sys
import os
import logging
import time
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

# Resource Servers (scopes within the single Cognito pool)
GW_RESOURCE_SERVER_ID = "rosetta-sdl-gateway"
GW_RESOURCE_SERVER_NAME = "Rosetta SDL Gateway"
RT_RESOURCE_SERVER_ID = "rosetta-sdl-runtime"
RT_RESOURCE_SERVER_NAME = "Rosetta SDL Runtime"

# M2M Client Names (within the single Cognito pool)
GW_CLIENT_NAME = "rosetta-sdl-gateway-client"
RT_CLIENT_NAME = "rosetta-sdl-runtime-client"
OUTBOUND_CLIENT_NAME = "rosetta-sdl-outbound-client"

# MCP Server Deployment
ROSETTA_MCP_FILE = "rosetta_mcp.py"
ROSETTA_AGENT_NAME = "rosetta_sdl_mcp"

# Gateway Target
ROSETTA_TARGET_NAME = "rosetta-sdl-mcp-target"

# OAuth Credential Provider (Gateway → Runtime only)
GW_TO_RT_CREDENTIAL_PROVIDER_NAME = "rosetta-sdl-identity"

# ============================================================================


def discover_from_cloudformation():
    """Auto-discover ALB DNS and Cognito config from CDK CloudFormation stack outputs."""
    print("\nAuto-discovering config from CloudFormation...")

    try:
        cfn = boto3.client("cloudformation", region_name=REGION)
        outputs = cfn.describe_stacks(StackName=CDK_STACK_NAME)["Stacks"][0]["Outputs"]
        output_map = {o["OutputKey"]: o["OutputValue"] for o in outputs}

        api_url = f"http://{output_map['AlbDnsName']}"
        cognito_pool_id = output_map["CognitoUserPoolId"]
        cognito_domain = output_map["CognitoDomain"]
        # CognitoDomain may be full URL or just the prefix — handle both
        if cognito_domain.startswith("https://"):
            token_url = f"{cognito_domain}/oauth2/token"
        else:
            token_url = f"https://{cognito_domain}.auth.{REGION}.amazoncognito.com/oauth2/token"

        print(f"  API URL: {api_url}")
        print(f"  Cognito Pool ID: {cognito_pool_id}")
        print(f"  Cognito Domain: {cognito_domain}")
        print(f"  Token URL: {token_url}")

        return {
            "api_url": api_url,
            "cognito_pool_id": cognito_pool_id,
            "cognito_domain": cognito_domain,
            "token_url": token_url,
        }
    except Exception as e:
        print(f"  ERROR: Could not read CloudFormation outputs: {e}")
        sys.exit(1)


def setup_cognito_auth(cognito_pool_id):
    """Create resource servers and M2M clients in the single Cognito pool.

    Creates:
      - Gateway resource server + M2M client (for inbound client auth)
      - Runtime resource server + M2M client (for Gateway→Runtime auth)
      - Outbound M2M client (for Runtime→FastAPI direct token fetch)
    """
    print("\nSetting up auth in single Cognito pool...")

    cognito = boto3.client("cognito-idp", region_name=REGION)
    discovery_url = f"https://cognito-idp.{REGION}.amazonaws.com/{cognito_pool_id}/.well-known/openid-configuration"

    # --- Gateway resource server + M2M client (inbound auth) ---
    gw_scopes = [{"ScopeName": "invoke", "ScopeDescription": "Invoke the Rosetta SDL gateway"}]
    gw_scope_names = [f"{GW_RESOURCE_SERVER_ID}/{s['ScopeName']}" for s in gw_scopes]

    utils.get_or_create_resource_server(cognito, cognito_pool_id, GW_RESOURCE_SERVER_ID, GW_RESOURCE_SERVER_NAME, gw_scopes)
    gw_client_id, gw_client_secret = utils.get_or_create_m2m_client(
        cognito, cognito_pool_id, GW_CLIENT_NAME, GW_RESOURCE_SERVER_ID, gw_scope_names
    )
    print(f"  Gateway client: {gw_client_id}")

    # --- Runtime resource server + M2M client (Gateway→Runtime outbound auth) ---
    rt_scopes = [{"ScopeName": "invoke", "ScopeDescription": "Invoke the Rosetta SDL runtime"}]
    rt_scope_names = [f"{RT_RESOURCE_SERVER_ID}/{s['ScopeName']}" for s in rt_scopes]

    utils.get_or_create_resource_server(cognito, cognito_pool_id, RT_RESOURCE_SERVER_ID, RT_RESOURCE_SERVER_NAME, rt_scopes)
    rt_client_id, rt_client_secret = utils.get_or_create_m2m_client(
        cognito, cognito_pool_id, RT_CLIENT_NAME, RT_RESOURCE_SERVER_ID, rt_scope_names
    )
    print(f"  Runtime client: {rt_client_id}")

    # --- Outbound M2M client (Runtime→FastAPI direct token fetch) ---
    outbound_client_id, outbound_client_secret = utils.get_or_create_m2m_client(
        cognito, cognito_pool_id, OUTBOUND_CLIENT_NAME, RT_RESOURCE_SERVER_ID, rt_scope_names
    )
    print(f"  Outbound client: {outbound_client_id}")

    return {
        "discovery_url": discovery_url,
        "gateway": {
            "client_id": gw_client_id,
            "client_secret": gw_client_secret,
            "scope_string": " ".join(gw_scope_names),
        },
        "runtime": {
            "client_id": rt_client_id,
            "client_secret": rt_client_secret,
            "scope_string": " ".join(rt_scope_names),
        },
        "outbound": {
            "client_id": outbound_client_id,
            "client_secret": outbound_client_secret,
            "scope_string": " ".join(rt_scope_names),
        },
    }


def create_runtime_execution_role():
    """Create or update IAM role for AgentCore Runtime."""
    print("\nCreating/Updating IAM role for AgentCore Runtime...")

    iam_client = boto3.client('iam')
    role_name = f"agentcore-runtime-{RUNTIME_ROLE_NAME}-role"

    try:
        try:
            iam_client.get_role(RoleName=role_name)
            print(f"  Role exists, updating policies...")
            policies = iam_client.list_role_policies(RoleName=role_name, MaxItems=100)
            for policy_name in policies.get('PolicyNames', []):
                iam_client.delete_role_policy(RoleName=role_name, PolicyName=policy_name)
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


def create_agentcore_gateway(gateway_role_arn, auth_config):
    """Create the AgentCore Gateway or update existing one."""
    print("\nCreating/Getting AgentCore Gateway...")

    gateway_client = boto3.client('bedrock-agentcore-control', region_name=REGION)

    # Check for existing gateway — update auth config if it exists
    try:
        for gw in gateway_client.list_gateways().get('items', []):
            if gw.get('name') == GATEWAY_NAME:
                gateway_id = gw['gatewayId']
                details = gateway_client.get_gateway(gatewayIdentifier=gateway_id)
                gateway_url = details['gatewayUrl']
                print(f"  Found existing gateway: {gateway_id}")

                # Update authorizer to ensure it points to the correct Cognito pool
                print(f"  Updating gateway authorizer config...")
                gateway_client.update_gateway(
                    gatewayIdentifier=gateway_id,
                    name=GATEWAY_NAME,
                    roleArn=gateway_role_arn,
                    protocolType='MCP',
                    authorizerType='CUSTOM_JWT',
                    authorizerConfiguration={
                        'customJWTAuthorizer': {
                            'allowedClients': [auth_config["gateway"]["client_id"]],
                            'discoveryUrl': auth_config["discovery_url"],
                        }
                    },
                )
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
                'allowedClients': [auth_config["gateway"]["client_id"]],
                'discoveryUrl': auth_config["discovery_url"],
            }
        },
        description=GATEWAY_DESCRIPTION,
    )

    gateway_id = resp['gatewayId']
    gateway_url = resp['gatewayUrl']
    print(f"  Gateway ID: {gateway_id}")
    print(f"  Gateway URL: {gateway_url}")
    return {"gateway_id": gateway_id, "gateway_url": gateway_url}


def deploy_mcp_server_to_runtime(runtime_role_arn, auth_config, cfn_config):
    """Deploy the Rosetta SDL MCP server to AgentCore Runtime."""
    api_url = cfn_config["api_url"]
    token_url = cfn_config["token_url"]
    outbound = auth_config["outbound"]

    print(f"\nDeploying {ROSETTA_MCP_FILE} to AgentCore Runtime...")
    print(f"  API_URL: {api_url}")
    print(f"  Token URL: {token_url}")
    print(f"  Outbound client: {outbound['client_id']}")
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

        runtime_auth_config = {
            "customJWTAuthorizer": {
                "allowedClients": [auth_config["runtime"]["client_id"]],
                "discoveryUrl": auth_config["discovery_url"],
            }
        }

        agentcore_runtime.configure(
            entrypoint=ROSETTA_MCP_FILE,
            execution_role=runtime_role_arn,
            auto_create_ecr=True,
            requirements_file="requirements.txt",
            non_interactive=True,
            region=REGION,
            authorizer_configuration=runtime_auth_config,
            protocol="MCP",
            agent_name=ROSETTA_AGENT_NAME,
        )

        launch_result = agentcore_runtime.launch(
            auto_update_on_conflict=True,
            env_vars={
                "API_URL": api_url,
                "COGNITO_TOKEN_URL": token_url,
                "OUTBOUND_CLIENT_ID": outbound["client_id"],
                "OUTBOUND_CLIENT_SECRET": outbound["client_secret"],
                "OUTBOUND_SCOPE": outbound["scope_string"],
            },
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


def create_credential_provider(name, auth_config, client_key):
    """Create or update an OAuth2 credential provider in AgentCore Token Vault."""
    print(f"\nCreating/Updating OAuth2 credential provider: {name}...")

    identity_client = boto3.client('bedrock-agentcore-control', region_name=REGION)
    client_config = auth_config[client_key]

    # Check if it already exists — update it
    try:
        resp = identity_client.get_oauth2_credential_provider(name=name)
        arn = resp['credentialProviderArn']
        print(f"  Found existing provider: {arn}")
        print(f"  Updating to use current Cognito pool...")
        identity_client.update_oauth2_credential_provider(
            name=name,
            credentialProviderVendor="CustomOauth2",
            oauth2ProviderConfigInput={
                'customOauth2ProviderConfig': {
                    'oauthDiscovery': {'discoveryUrl': auth_config['discovery_url']},
                    'clientId': client_config['client_id'],
                    'clientSecret': client_config['client_secret'],
                }
            },
        )
        print(f"  Updated provider: {arn}")
        return arn
    except identity_client.exceptions.ResourceNotFoundException:
        pass
    except Exception as e:
        # get_oauth2_credential_provider may throw generic exception if not found
        if "not found" not in str(e).lower() and "ResourceNotFoundException" not in str(e):
            raise

    resp = identity_client.create_oauth2_credential_provider(
        name=name,
        credentialProviderVendor="CustomOauth2",
        oauth2ProviderConfigInput={
            'customOauth2ProviderConfig': {
                'oauthDiscovery': {'discoveryUrl': auth_config['discovery_url']},
                'clientId': client_config['client_id'],
                'clientSecret': client_config['client_secret'],
            }
        },
    )

    arn = resp['credentialProviderArn']
    print(f"  Created provider: {arn}")
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


# ============================================================================
# CLEANUP
# ============================================================================

def cleanup():
    """Delete all AgentCore resources (gateway targets, gateway, runtime, credential providers)."""
    print("\n" + "=" * 60)
    print("  CLEANUP — Deleting all AgentCore resources")
    print("=" * 60)

    ac = boto3.client('bedrock-agentcore-control', region_name=REGION)

    # 1. Find and delete gateway targets, then the gateway
    try:
        for gw in ac.list_gateways().get('items', []):
            if gw.get('name') == GATEWAY_NAME:
                gateway_id = gw['gatewayId']
                print(f"\n  Found gateway: {gateway_id}")

                # Delete targets first
                targets = ac.list_gateway_targets(gatewayIdentifier=gateway_id).get('items', [])
                for t in targets:
                    target_id = t['targetId']
                    print(f"  Deleting target: {t.get('name', target_id)}...")
                    ac.delete_gateway_target(gatewayIdentifier=gateway_id, targetId=target_id)
                    print(f"    Deleted.")

                # Wait for targets to be deleted
                if targets:
                    print("  Waiting for targets to be deleted...")
                    time.sleep(5)

                # Delete gateway
                print(f"  Deleting gateway: {GATEWAY_NAME}...")
                ac.delete_gateway(gatewayIdentifier=gateway_id)
                print(f"    Deleted.")
                break
        else:
            print(f"\n  No gateway named '{GATEWAY_NAME}' found.")
    except Exception as e:
        print(f"  Error deleting gateway: {e}")

    # 2. Delete agent runtimes
    try:
        runtimes = ac.list_agent_runtimes().get('agentRuntimeSummaries', [])
        for rt in runtimes:
            if rt.get('agentRuntimeName', '').startswith(ROSETTA_AGENT_NAME):
                agent_id = rt['agentRuntimeId']
                print(f"\n  Deleting agent runtime: {rt['agentRuntimeName']} ({agent_id})...")
                ac.delete_agent_runtime(agentRuntimeId=agent_id)
                print(f"    Deleted.")
        if not any(rt.get('agentRuntimeName', '').startswith(ROSETTA_AGENT_NAME) for rt in runtimes):
            print(f"\n  No agent runtime starting with '{ROSETTA_AGENT_NAME}' found.")
    except Exception as e:
        print(f"  Error deleting agent runtime: {e}")

    # 3. Delete credential providers
    for provider_name in [GW_TO_RT_CREDENTIAL_PROVIDER_NAME, "rosetta-sdl-outbound-identity"]:
        try:
            ac.get_oauth2_credential_provider(name=provider_name)
            print(f"\n  Deleting credential provider: {provider_name}...")
            ac.delete_oauth2_credential_provider(name=provider_name)
            print(f"    Deleted.")
        except Exception:
            print(f"\n  Credential provider '{provider_name}' not found (skipping).")

    # 4. Delete Cognito resource servers and M2M clients (from CDK pool)
    try:
        cfn_config = discover_from_cloudformation()
        cognito_pool_id = cfn_config["cognito_pool_id"]
        cognito = boto3.client("cognito-idp", region_name=REGION)

        # Delete M2M clients
        for client_name in [GW_CLIENT_NAME, RT_CLIENT_NAME, OUTBOUND_CLIENT_NAME]:
            clients = cognito.list_user_pool_clients(UserPoolId=cognito_pool_id, MaxResults=60)
            for c in clients.get("UserPoolClients", []):
                if c["ClientName"] == client_name:
                    print(f"\n  Deleting Cognito client: {client_name} ({c['ClientId']})...")
                    cognito.delete_user_pool_client(UserPoolId=cognito_pool_id, ClientId=c["ClientId"])
                    print(f"    Deleted.")
                    break

        # Delete resource servers
        for rs_id in [GW_RESOURCE_SERVER_ID, RT_RESOURCE_SERVER_ID]:
            try:
                cognito.describe_resource_server(UserPoolId=cognito_pool_id, Identifier=rs_id)
                print(f"\n  Deleting resource server: {rs_id}...")
                cognito.delete_resource_server(UserPoolId=cognito_pool_id, Identifier=rs_id)
                print(f"    Deleted.")
            except cognito.exceptions.ResourceNotFoundException:
                pass
    except Exception as e:
        print(f"  Error cleaning up Cognito: {e}")

    # 5. Clean up local files
    deployment_file = Path(__file__).parent / "deployment_info.json"
    if deployment_file.exists():
        deployment_file.unlink()
        print(f"\n  Deleted {deployment_file}")

    yaml_file = Path(__file__).parent / ".bedrock_agentcore.yaml"
    if yaml_file.exists():
        yaml_file.unlink()
        print(f"  Deleted {yaml_file}")

    print("\n" + "=" * 60)
    print("  CLEANUP COMPLETE")
    print("=" * 60)


# ============================================================================
# DISPLAY & HELPERS
# ============================================================================

def display_architecture():
    """Display what we're building."""
    print("""
============================================================================
                    ROSETTA SDL — AGENTCORE DEPLOYMENT
============================================================================

  Architecture (Single Cognito Pool):

  +-----------------------+
  |   Any AI Agent        |
  |  (Claude, Strands,    |
  |   QuickSuite)         |
  +----------+------------+
             | JWT Auth (Cognito - gateway scope)
             v
  +-----------------------+
  |  AgentCore Gateway    |
  |  (MCP Protocol)       |
  +----------+------------+
             | OAuth2 (Cognito - runtime scope via Token Vault)
             v
  +-----------------------+
  |  AgentCore Runtime    |
  |  Rosetta SDL MCP      |
  |  (10 tools)           |
  +----------+------------+
             | Bearer JWT (Cognito - direct client_credentials)
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

  Auth: ONE Cognito pool (from CDK stack) for all 3 layers.
  No API keys. Fresh JWT tokens at every layer.

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
    parser.add_argument('--cleanup', action='store_true',
                       help='Delete all AgentCore resources and exit')
    args = parser.parse_args()

    if args.cleanup:
        cleanup()
        return

    non_interactive = args.non_interactive

    display_architecture()

    if not non_interactive:
        input("Press Enter to start deployment...")
    else:
        print("Starting automated deployment...\n")

    # Step 1: Auto-discover from CloudFormation
    cfn_config = discover_from_cloudformation()
    cognito_pool_id = cfn_config["cognito_pool_id"]
    wait_for_user("Setup Cognito auth (single pool)", non_interactive)

    # Step 2: Setup auth in single Cognito pool
    auth_config = setup_cognito_auth(cognito_pool_id)
    wait_for_user("Create IAM roles", non_interactive)

    # Step 3: Create IAM roles
    gateway_role_arn = create_gateway_iam_role()
    runtime_role_arn = create_runtime_execution_role()
    wait_for_user("Create AgentCore Gateway", non_interactive)

    # Step 4: Create AgentCore Gateway
    gateway_info = create_agentcore_gateway(gateway_role_arn, auth_config)
    wait_for_user("Deploy Rosetta SDL MCP Server to Runtime", non_interactive)

    # Step 5: Deploy Rosetta SDL MCP Server
    rosetta_agent = deploy_mcp_server_to_runtime(runtime_role_arn, auth_config, cfn_config)
    wait_for_user("Create credential provider (Gateway → Runtime)", non_interactive)

    # Step 6: Create credential provider (Gateway → Runtime only)
    gw_to_rt_provider_arn = create_credential_provider(
        GW_TO_RT_CREDENTIAL_PROVIDER_NAME, auth_config, "runtime"
    )
    wait_for_user("Create Gateway Target", non_interactive)

    # Step 7: Create Gateway target
    target_id = create_gateway_target(
        gateway_id=gateway_info["gateway_id"],
        agent_url=rosetta_agent["agent_url"],
        credential_provider_arn=gw_to_rt_provider_arn,
        runtime_scope_string=auth_config["runtime"]["scope_string"],
    )
    wait_for_user("Verify deployment", non_interactive)

    # Step 8: Verify
    verify_gateway_targets(gateway_info["gateway_id"])

    # Summary
    token_url = cfn_config["token_url"]

    print("\n" + "=" * 60)
    print("  DEPLOYMENT COMPLETE")
    print("=" * 60)
    print(f"\n  Gateway URL:       {gateway_info['gateway_url']}")
    print(f"  Gateway ID:        {gateway_info['gateway_id']}")
    print(f"  Token URL:         {token_url}")
    print(f"  Client ID:         {auth_config['gateway']['client_id']}")
    print(f"  Client Secret:     {auth_config['gateway']['client_secret']}")
    print(f"  Scope:             {auth_config['gateway']['scope_string']}")
    print(f"  Agent ARN:         {rosetta_agent['agent_arn']}")
    print(f"  FastAPI URL:       {cfn_config['api_url']}")
    print(f"  Cognito Pool:      {cognito_pool_id} (single pool for all auth)")
    print(f"  Target ID:         {target_id}")

    # Save deployment info
    deployment_info = {
        "gateway": gateway_info,
        "gateway_role_arn": gateway_role_arn,
        "runtime_role_arn": runtime_role_arn,
        "auth": {
            "token_url": token_url,
            "client_id": auth_config["gateway"]["client_id"],
            "client_secret": auth_config["gateway"]["client_secret"],
            "discovery_url": auth_config["discovery_url"],
            "scope": auth_config["gateway"]["scope_string"],
            "cognito_pool_id": cognito_pool_id,
        },
        "agent": rosetta_agent,
        "credential_providers": {
            "gateway_to_runtime": gw_to_rt_provider_arn,
        },
        "target_id": target_id,
        "fastapi_url": cfn_config["api_url"],
    }

    deployment_file = Path(__file__).parent / "deployment_info.json"
    with open(deployment_file, 'w') as f:
        json.dump(deployment_info, f, indent=2)

    print(f"\n  Saved to: {deployment_file}")
    print("=" * 60)


if __name__ == "__main__":
    main()
