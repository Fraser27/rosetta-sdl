import * as cdk from 'aws-cdk-lib';
import * as ec2 from 'aws-cdk-lib/aws-ec2';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as cognito from 'aws-cdk-lib/aws-cognito';
import * as s3 from 'aws-cdk-lib/aws-s3';
import * as s3deploy from 'aws-cdk-lib/aws-s3-deployment';
import * as cloudfront from 'aws-cdk-lib/aws-cloudfront';
import * as origins from 'aws-cdk-lib/aws-cloudfront-origins';
import { Construct } from 'constructs';
import * as path from 'path';

export class RosettaSdlStack extends cdk.Stack {
  constructor(scope: Construct, id: string, props?: cdk.StackProps) {
    super(scope, id, props);

    // ─────────────────────────────────────────────
    // VPC
    // ─────────────────────────────────────────────
    const vpc = new ec2.Vpc(this, 'Vpc', {
      maxAzs: 2,
      natGateways: 0,
      subnetConfiguration: [
        {
          name: 'public',
          subnetType: ec2.SubnetType.PUBLIC,
          cidrMask: 24,
        },
      ],
    });

    // ─────────────────────────────────────────────
    // Security Group for EC2
    // ─────────────────────────────────────────────
    const ec2Sg = new ec2.SecurityGroup(this, 'Ec2Sg', {
      vpc,
      description: 'Semantic Layer EC2 — Neo4j + FastAPI',
      allowAllOutbound: true,
    });

    // FastAPI (8000) — open to CloudFront and direct access
    ec2Sg.addIngressRule(ec2.Peer.anyIpv4(), ec2.Port.tcp(8000), 'FastAPI');
    // Neo4j Browser (7474) — for admin access
    ec2Sg.addIngressRule(ec2.Peer.anyIpv4(), ec2.Port.tcp(7474), 'Neo4j Browser');
    // SSH
    ec2Sg.addIngressRule(ec2.Peer.anyIpv4(), ec2.Port.tcp(22), 'SSH');

    // ─────────────────────────────────────────────
    // IAM Role for EC2
    // ─────────────────────────────────────────────
    const ec2Role = new iam.Role(this, 'Ec2Role', {
      assumedBy: new iam.ServicePrincipal('ec2.amazonaws.com'),
      managedPolicies: [
        iam.ManagedPolicy.fromAwsManagedPolicyName('AmazonSSMManagedInstanceCore'),
      ],
    });

    // Glue read access
    ec2Role.addToPolicy(new iam.PolicyStatement({
      actions: [
        'glue:GetDatabases', 'glue:GetDatabase',
        'glue:GetTables', 'glue:GetTable',
        'glue:GetPartitions',
      ],
      resources: ['*'],
    }));

    // Athena execute
    ec2Role.addToPolicy(new iam.PolicyStatement({
      actions: [
        'athena:StartQueryExecution', 'athena:GetQueryExecution',
        'athena:GetQueryResults', 'athena:StopQueryExecution',
      ],
      resources: ['*'],
    }));

    // S3 read/write (Athena results + data)
    ec2Role.addToPolicy(new iam.PolicyStatement({
      actions: ['s3:GetObject', 's3:PutObject', 's3:ListBucket', 's3:GetBucketLocation'],
      resources: ['*'],
    }));

    // S3 Vectors
    ec2Role.addToPolicy(new iam.PolicyStatement({
      actions: ['s3vectors:*'],
      resources: ['*'],
    }));

    // Bedrock invoke
    ec2Role.addToPolicy(new iam.PolicyStatement({
      actions: ['bedrock:InvokeModel', 'bedrock:InvokeModelWithResponseStream'],
      resources: ['*'],
    }));

    // ─────────────────────────────────────────────
    // EC2 Instance — Neo4j + FastAPI via Docker Compose
    // ─────────────────────────────────────────────
    const ami = ec2.MachineImage.latestAmazonLinux2023({
      cpuType: ec2.AmazonLinuxCpuType.ARM_64,
    });

    const instance = new ec2.Instance(this, 'SemanticLayerEc2', {
      vpc,
      vpcSubnets: { subnetType: ec2.SubnetType.PUBLIC },
      instanceType: ec2.InstanceType.of(ec2.InstanceClass.T4G, ec2.InstanceSize.MEDIUM),
      machineImage: ami,
      securityGroup: ec2Sg,
      role: ec2Role,
      blockDevices: [
        {
          deviceName: '/dev/xvda',
          volume: ec2.BlockDeviceVolume.ebs(30, {
            volumeType: ec2.EbsDeviceVolumeType.GP3,
            encrypted: true,
          }),
        },
      ],
      associatePublicIpAddress: true,
    });

    // User data — install Docker, docker-compose, clone and run
    instance.addUserData(`#!/bin/bash
set -e

# Install Docker
dnf install -y docker git
systemctl enable docker
systemctl start docker
usermod -aG docker ec2-user

# Install Docker Compose
ARCH=$(uname -m)
curl -L "https://github.com/docker/compose/releases/latest/download/docker-compose-linux-$ARCH" -o /usr/local/bin/docker-compose
chmod +x /usr/local/bin/docker-compose

# Create app directory
mkdir -p /opt/semantic-layer
cd /opt/semantic-layer

# Write docker-compose.yml
cat > docker-compose.yml << 'COMPOSE_EOF'
services:
  neo4j:
    image: neo4j:5-community
    restart: always
    ports:
      - "7474:7474"
      - "7687:7687"
    environment:
      NEO4J_AUTH: neo4j/semantic-layer
      NEO4J_PLUGINS: '["apoc"]'
      NEO4J_server_memory_heap_initial__size: 512m
      NEO4J_server_memory_heap_max__size: 1g
    volumes:
      - neo4j_data:/data
    healthcheck:
      test: ["CMD-SHELL", "wget --no-verbose --tries=1 --spider http://localhost:7474 || exit 1"]
      interval: 5s
      timeout: 5s
      retries: 20
      start_period: 40s

  semantic-layer:
    image: semantic-layer:latest
    build:
      context: .
      dockerfile: Dockerfile
    restart: always
    ports:
      - "8000:8000"
    environment:
      NEO4J_URI: bolt://neo4j:7687
      NEO4J_USER: neo4j
      NEO4J_PASSWORD: semantic-layer
      GLUE_DATABASES: ""
      VECTOR_BUCKETS: ""
      ATHENA_WORKGROUP: semantic-layer-wg
      ATHENA_OUTPUT_BUCKET: ""
      METRICS_FILE: /app/sample/metrics.yaml
      BEDROCK_QUERY_MODEL: anthropic.claude-sonnet-4-20250514
      BEDROCK_ENRICHMENT_MODEL: anthropic.claude-haiku-4-5-20251001
      COGNITO_USER_POOL_ID: "__COGNITO_POOL_ID__"
      COGNITO_REGION: "${cdk.Aws.REGION}"
    depends_on:
      neo4j:
        condition: service_healthy

volumes:
  neo4j_data:
COMPOSE_EOF

# Clone the repo and build
# For now, we pull a pre-built image or build from source
# Replace with your actual repo URL
cat > Dockerfile << 'DOCKER_EOF'
FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml .
COPY src/ src/
COPY sample/ sample/
RUN pip install --no-cache-dir .
EXPOSE 8000
CMD ["uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8000"]
DOCKER_EOF

# Create placeholder files (will be replaced by deployment)
mkdir -p src sample
echo "# placeholder" > pyproject.toml

# Log completion
echo "Semantic Layer EC2 setup complete" > /opt/semantic-layer/setup.log
`);

    // ─────────────────────────────────────────────
    // Cognito User Pool
    // ─────────────────────────────────────────────
    const userPool = new cognito.UserPool(this, 'UserPool', {
      userPoolName: 'semantic-layer-users',
      selfSignUpEnabled: true,
      signInAliases: { email: true },
      autoVerify: { email: true },
      passwordPolicy: {
        minLength: 8,
        requireLowercase: true,
        requireUppercase: true,
        requireDigits: true,
        requireSymbols: false,
      },
      accountRecovery: cognito.AccountRecovery.EMAIL_ONLY,
      removalPolicy: cdk.RemovalPolicy.DESTROY,
    });

    const userPoolDomain = userPool.addDomain('Domain', {
      cognitoDomain: {
        domainPrefix: `semantic-layer-${cdk.Aws.ACCOUNT_ID}`,
      },
    });

    const userPoolClient = userPool.addClient('AppClient', {
      userPoolClientName: 'semantic-layer-ui',
      authFlows: {
        userSrp: true,
        userPassword: true,
      },
      oAuth: {
        flows: { authorizationCodeGrant: true, implicitCodeGrant: true },
        scopes: [cognito.OAuthScope.OPENID, cognito.OAuthScope.EMAIL, cognito.OAuthScope.PROFILE],
        callbackUrls: ['http://localhost:3000/'],
        logoutUrls: ['http://localhost:3000/'],
      },
      preventUserExistenceErrors: true,
    });

    // ─────────────────────────────────────────────
    // CloudFront + S3 — React UI
    // ─────────────────────────────────────────────
    const uiBucket = new s3.Bucket(this, 'UiBucket', {
      removalPolicy: cdk.RemovalPolicy.DESTROY,
      autoDeleteObjects: true,
      blockPublicAccess: s3.BlockPublicAccess.BLOCK_ALL,
    });

    const distribution = new cloudfront.Distribution(this, 'UiDistribution', {
      defaultBehavior: {
        origin: origins.S3BucketOrigin.withOriginAccessControl(uiBucket),
        viewerProtocolPolicy: cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
        cachePolicy: cloudfront.CachePolicy.CACHING_OPTIMIZED,
      },
      additionalBehaviors: {
        '/api/*': {
          origin: new origins.HttpOrigin(instance.instancePublicIp, {
            protocolPolicy: cloudfront.OriginProtocolPolicy.HTTP_ONLY,
            httpPort: 8000,
          }),
          viewerProtocolPolicy: cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
          allowedMethods: cloudfront.AllowedMethods.ALLOW_ALL,
          cachePolicy: cloudfront.CachePolicy.CACHING_DISABLED,
          originRequestPolicy: cloudfront.OriginRequestPolicy.ALL_VIEWER,
        },
      },
      defaultRootObject: 'index.html',
      errorResponses: [
        {
          httpStatus: 403,
          responseHttpStatus: 200,
          responsePagePath: '/index.html',
        },
        {
          httpStatus: 404,
          responseHttpStatus: 200,
          responsePagePath: '/index.html',
        },
      ],
    });

    // Deploy built UI assets
    new s3deploy.BucketDeployment(this, 'UiDeploy', {
      sources: [
        s3deploy.Source.asset(path.join(__dirname, '../../ui'), {
          bundling: {
            image: cdk.DockerImage.fromRegistry('node:20-slim'),
            command: [
              'bash', '-c',
              'npm ci && npm run build && cp -r dist/* /asset-output/',
            ],
          },
        }),
      ],
      destinationBucket: uiBucket,
      distribution,
      distributionPaths: ['/*'],
    });

    // Deploy runtime config for Cognito (values only known at deploy time)
    new s3deploy.BucketDeployment(this, 'UiConfig', {
      sources: [
        s3deploy.Source.jsonData('runtime-config.json', {
          cognitoUserPoolId: userPool.userPoolId,
          cognitoClientId: userPoolClient.userPoolClientId,
          cognitoRegion: cdk.Aws.REGION,
          cognitoDomain: `${userPoolDomain.domainName}.auth.${cdk.Aws.REGION}.amazoncognito.com`,
        }),
      ],
      destinationBucket: uiBucket,
      distribution,
      distributionPaths: ['/runtime-config.json'],
    });

    // ─────────────────────────────────────────────
    // Outputs
    // ─────────────────────────────────────────────
    new cdk.CfnOutput(this, 'Ec2PublicIp', {
      value: instance.instancePublicIp,
      description: 'EC2 public IP — FastAPI at :8000, Neo4j Browser at :7474',
    });

    new cdk.CfnOutput(this, 'Ec2InstanceId', {
      value: instance.instanceId,
      description: 'EC2 Instance ID — use SSM Session Manager to connect',
    });

    new cdk.CfnOutput(this, 'CloudFrontUrl', {
      value: `https://${distribution.distributionDomainName}`,
      description: 'React UI URL on CloudFront',
    });

    new cdk.CfnOutput(this, 'CognitoUserPoolId', {
      value: userPool.userPoolId,
      description: 'Cognito User Pool ID',
    });

    new cdk.CfnOutput(this, 'CognitoClientId', {
      value: userPoolClient.userPoolClientId,
      description: 'Cognito App Client ID',
    });

    new cdk.CfnOutput(this, 'CognitoDomain', {
      value: `https://${userPoolDomain.domainName}.auth.${cdk.Aws.REGION}.amazoncognito.com`,
      description: 'Cognito Hosted UI domain',
    });

    new cdk.CfnOutput(this, 'FastApiUrl', {
      value: `http://${instance.instancePublicIp}:8000`,
      description: 'FastAPI API URL',
    });

    new cdk.CfnOutput(this, 'Neo4jBrowserUrl', {
      value: `http://${instance.instancePublicIp}:7474`,
      description: 'Neo4j Browser URL',
    });

    new cdk.CfnOutput(this, 'SshCommand', {
      value: `aws ssm start-session --target ${instance.instanceId}`,
      description: 'Connect to EC2 via SSM (no SSH key needed)',
    });
  }
}
