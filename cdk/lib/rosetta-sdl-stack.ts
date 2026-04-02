import * as cdk from 'aws-cdk-lib';
import * as ec2 from 'aws-cdk-lib/aws-ec2';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as cognito from 'aws-cdk-lib/aws-cognito';
import * as s3 from 'aws-cdk-lib/aws-s3';
import * as s3deploy from 'aws-cdk-lib/aws-s3-deployment';
import * as cloudfront from 'aws-cdk-lib/aws-cloudfront';
import * as origins from 'aws-cdk-lib/aws-cloudfront-origins';
import * as elbv2 from 'aws-cdk-lib/aws-elasticloadbalancingv2';
import * as elbv2_targets from 'aws-cdk-lib/aws-elasticloadbalancingv2-targets';
import { Construct } from 'constructs';
import * as path from 'path';

export class RosettaSdlStack extends cdk.Stack {
  constructor(scope: Construct, id: string, props?: cdk.StackProps) {
    super(scope, id, props);

    // ─────────────────────────────────────────────
    // VPC — public (ALB, NAT) + private (EC2)
    // ─────────────────────────────────────────────
    const vpc = new ec2.Vpc(this, 'Vpc', {
      maxAzs: 2,
      natGateways: 1,
      subnetConfiguration: [
        {
          name: 'public',
          subnetType: ec2.SubnetType.PUBLIC,
          cidrMask: 24,
        },
        {
          name: 'private',
          subnetType: ec2.SubnetType.PRIVATE_WITH_EGRESS,
          cidrMask: 24,
        },
      ],
    });

    // ─────────────────────────────────────────────
    // Security Groups
    // ─────────────────────────────────────────────
    const albSg = new ec2.SecurityGroup(this, 'AlbSg', {
      vpc,
      description: 'ALB - accepts HTTP from internet',
      allowAllOutbound: true,
    });
    albSg.addIngressRule(ec2.Peer.anyIpv4(), ec2.Port.tcp(80), 'HTTP');

    const ec2Sg = new ec2.SecurityGroup(this, 'Ec2Sg', {
      vpc,
      description: 'EC2 - Neo4j + FastAPI (private, ALB-only)',
      allowAllOutbound: true,
    });
    ec2Sg.addIngressRule(albSg, ec2.Port.tcp(8000), 'FastAPI from ALB');

    // ─────────────────────────────────────────────
    // IAM Role for EC2
    // ─────────────────────────────────────────────
    const ec2Role = new iam.Role(this, 'Ec2Role', {
      assumedBy: new iam.ServicePrincipal('ec2.amazonaws.com'),
      managedPolicies: [
        iam.ManagedPolicy.fromAwsManagedPolicyName('AmazonSSMManagedInstanceCore'),
      ],
    });

    ec2Role.addToPolicy(new iam.PolicyStatement({
      actions: [
        'glue:GetDatabases', 'glue:GetDatabase',
        'glue:GetTables', 'glue:GetTable',
        'glue:GetPartitions',
      ],
      resources: ['*'],
    }));

    ec2Role.addToPolicy(new iam.PolicyStatement({
      actions: [
        'athena:StartQueryExecution', 'athena:GetQueryExecution',
        'athena:GetQueryResults', 'athena:StopQueryExecution',
      ],
      resources: ['*'],
    }));

    ec2Role.addToPolicy(new iam.PolicyStatement({
      actions: ['s3:GetObject', 's3:PutObject', 's3:ListBucket', 's3:GetBucketLocation'],
      resources: ['*'],
    }));

    ec2Role.addToPolicy(new iam.PolicyStatement({
      actions: ['s3vectors:*'],
      resources: ['*'],
    }));

    ec2Role.addToPolicy(new iam.PolicyStatement({
      actions: ['bedrock:InvokeModel', 'bedrock:InvokeModelWithResponseStream'],
      resources: ['*'],
    }));

    // ─────────────────────────────────────────────
    // EC2 Instance — private subnet, Neo4j + FastAPI
    // ─────────────────────────────────────────────
    const instance = new ec2.Instance(this, 'SemanticLayerEc2', {
      vpc,
      vpcSubnets: { subnetType: ec2.SubnetType.PRIVATE_WITH_EGRESS },
      instanceType: ec2.InstanceType.of(ec2.InstanceClass.T4G, ec2.InstanceSize.MEDIUM),
      machineImage: ec2.MachineImage.latestAmazonLinux2023({
        cpuType: ec2.AmazonLinuxCpuType.ARM_64,
      }),
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
    });

    instance.addUserData(`#!/bin/bash
set -ex

exec > /var/log/user-data.log 2>&1

# Install Docker
dnf install -y docker git
systemctl enable docker
systemctl start docker
usermod -aG docker ec2-user

# Install Docker Compose + Buildx
ARCH=$(uname -m)
curl -L "https://github.com/docker/compose/releases/latest/download/docker-compose-linux-$ARCH" -o /usr/local/bin/docker-compose
chmod +x /usr/local/bin/docker-compose

# Install Buildx plugin (required by compose build)
BUILDX_ARCH=$ARCH
if [ "$BUILDX_ARCH" = "aarch64" ]; then BUILDX_ARCH="arm64"; fi
if [ "$BUILDX_ARCH" = "x86_64" ]; then BUILDX_ARCH="amd64"; fi
mkdir -p /usr/local/lib/docker/cli-plugins
curl -L "https://github.com/docker/buildx/releases/download/v0.21.2/buildx-v0.21.2.linux-$BUILDX_ARCH" -o /usr/local/lib/docker/cli-plugins/docker-buildx
chmod +x /usr/local/lib/docker/cli-plugins/docker-buildx

# Clone the repo
git clone https://github.com/Fraser27/rosetta-sdl.git /opt/semantic-layer
cd /opt/semantic-layer

# Build Neo4j + FastAPI (without Cognito config yet — added after user pool is created)
/usr/local/bin/docker-compose up -d --build

echo "Semantic Layer EC2 setup complete" > /opt/semantic-layer/setup.log
`);

    // ─────────────────────────────────────────────
    // Application Load Balancer — public subnet
    // ─────────────────────────────────────────────
    const alb = new elbv2.ApplicationLoadBalancer(this, 'Alb', {
      vpc,
      internetFacing: true,
      securityGroup: albSg,
    });

    const targetGroup = new elbv2.ApplicationTargetGroup(this, 'ApiTargetGroup', {
      vpc,
      port: 8000,
      protocol: elbv2.ApplicationProtocol.HTTP,
      targetType: elbv2.TargetType.INSTANCE,
      targets: [new elbv2_targets.InstanceTarget(instance, 8000)],
      healthCheck: {
        path: '/health',
        healthyHttpCodes: '200',
        interval: cdk.Duration.seconds(30),
        timeout: cdk.Duration.seconds(10),
        healthyThresholdCount: 2,
        unhealthyThresholdCount: 5,
      },
      deregistrationDelay: cdk.Duration.seconds(30),
    });

    alb.addListener('HttpListener', {
      port: 80,
      defaultAction: elbv2.ListenerAction.forward([targetGroup]),
    });

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

    // Write docker-compose override with real Cognito config and restart
    instance.addUserData(`
cd /opt/semantic-layer
cat > docker-compose.override.yml << EOF
services:
  neo4j:
    restart: always
    environment:
      NEO4J_server_memory_heap_initial__size: 512m
      NEO4J_server_memory_heap_max__size: 1g

  rosetta:
    restart: always
    environment:
      COGNITO_USER_POOL_ID: "${userPool.userPoolId}"
      COGNITO_REGION: "${cdk.Aws.REGION}"
      AWS_DEFAULT_REGION: "${cdk.Aws.REGION}"
EOF

/usr/local/bin/docker-compose up -d
`);

    // ─────────────────────────────────────────────
    // S3 Bucket — React UI hosting
    // ─────────────────────────────────────────────
    const uiBucket = new s3.Bucket(this, 'UiBucket', {
      removalPolicy: cdk.RemovalPolicy.DESTROY,
      autoDeleteObjects: true,
      blockPublicAccess: s3.BlockPublicAccess.BLOCK_ALL,
    });

    // ─────────────────────────────────────────────
    // CloudFront Function — strip /api prefix
    //
    // The React UI calls /api/health, /api/catalog/*, etc.
    // FastAPI serves at /health, /catalog/*, etc.
    // This function rewrites the path before forwarding to the ALB.
    // ─────────────────────────────────────────────
    const stripApiPrefix = new cloudfront.Function(this, 'StripApiPrefix', {
      code: cloudfront.FunctionCode.fromInline(`
function handler(event) {
  var request = event.request;
  request.uri = request.uri.replace(/^\\/api/, '');
  if (request.uri === '') request.uri = '/';
  return request;
}
      `),
    });

    // ─────────────────────────────────────────────
    // CloudFront Distribution
    //   Default: S3 (React UI)
    //   /api/*:  ALB → EC2 (FastAPI)
    // ─────────────────────────────────────────────
    const distribution = new cloudfront.Distribution(this, 'UiDistribution', {
      defaultBehavior: {
        origin: origins.S3BucketOrigin.withOriginAccessControl(uiBucket),
        viewerProtocolPolicy: cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
        cachePolicy: cloudfront.CachePolicy.CACHING_OPTIMIZED,
      },
      additionalBehaviors: {
        '/api/*': {
          origin: new origins.HttpOrigin(alb.loadBalancerDnsName, {
            protocolPolicy: cloudfront.OriginProtocolPolicy.HTTP_ONLY,
          }),
          viewerProtocolPolicy: cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
          allowedMethods: cloudfront.AllowedMethods.ALLOW_ALL,
          cachePolicy: cloudfront.CachePolicy.CACHING_DISABLED,
          originRequestPolicy: cloudfront.OriginRequestPolicy.ALL_VIEWER_EXCEPT_HOST_HEADER,
          functionAssociations: [
            {
              function: stripApiPrefix,
              eventType: cloudfront.FunctionEventType.VIEWER_REQUEST,
            },
          ],
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

    // ─────────────────────────────────────────────
    // Cognito App Client — created after distribution
    // so the CloudFront URL resolves for callback URLs
    // ─────────────────────────────────────────────
    const userPoolClient = userPool.addClient('AppClient', {
      userPoolClientName: 'semantic-layer-ui',
      authFlows: {
        userSrp: true,
        userPassword: true,
      },
      oAuth: {
        flows: { authorizationCodeGrant: true, implicitCodeGrant: true },
        scopes: [cognito.OAuthScope.OPENID, cognito.OAuthScope.EMAIL, cognito.OAuthScope.PROFILE],
        callbackUrls: [
          `https://${distribution.distributionDomainName}/`,
          'http://localhost:3000/',
        ],
        logoutUrls: [
          `https://${distribution.distributionDomainName}/`,
          'http://localhost:3000/',
        ],
      },
      preventUserExistenceErrors: true,
    });

    // ─────────────────────────────────────────────
    // S3 Deployments — React build + runtime config
    // ─────────────────────────────────────────────
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
        s3deploy.Source.jsonData('runtime-config.json', {
          cognitoUserPoolId: userPool.userPoolId,
          cognitoClientId: userPoolClient.userPoolClientId,
          cognitoRegion: cdk.Aws.REGION,
          cognitoDomain: `${userPoolDomain.domainName}.auth.${cdk.Aws.REGION}.amazoncognito.com`,
        }),
      ],
      destinationBucket: uiBucket,
      distribution,
      distributionPaths: ['/*'],
    });

    // ─────────────────────────────────────────────
    // Outputs
    // ─────────────────────────────────────────────
    new cdk.CfnOutput(this, 'CloudFrontUrl', {
      value: `https://${distribution.distributionDomainName}`,
      description: 'React UI (Cognito-protected)',
    });

    new cdk.CfnOutput(this, 'AlbDnsName', {
      value: alb.loadBalancerDnsName,
      description: 'ALB DNS - internal API gateway for AgentCore MCP',
    });

    new cdk.CfnOutput(this, 'Ec2InstanceId', {
      value: instance.instanceId,
      description: 'EC2 Instance ID - connect via SSM',
    });

    new cdk.CfnOutput(this, 'SsmCommand', {
      value: `aws ssm start-session --target ${instance.instanceId}`,
      description: 'Connect to EC2 via SSM Session Manager',
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
  }
}
