#!/usr/bin/env node
import * as cdk from 'aws-cdk-lib';
import { RosettaSdlStack } from '../lib/rosetta-sdl-stack';

const app = new cdk.App();

new RosettaSdlStack(app, 'RosettaSdlStack', {
  env: {
    account: process.env.CDK_DEFAULT_ACCOUNT,
    region: process.env.CDK_DEFAULT_REGION || 'us-east-1',
  },
  description: 'Rosetta SDL — Neo4j + FastAPI on EC2, React on App Runner, Cognito auth',
});
