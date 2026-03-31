#!/usr/bin/env node
import * as cdk from 'aws-cdk-lib';
import { SemanticLayerStack } from '../lib/semantic-layer-stack';

const app = new cdk.App();

new SemanticLayerStack(app, 'SemanticLayerStack', {
  env: {
    account: process.env.CDK_DEFAULT_ACCOUNT,
    region: process.env.CDK_DEFAULT_REGION || 'us-east-1',
  },
  description: 'AWS Semantic Layer — Neo4j + FastAPI on EC2, React on App Runner, Cognito auth',
});
