#!/bin/bash
set -euo pipefail

cd "$(dirname "$0")/cdk"

echo "Installing dependencies..."
npm install

echo ""
echo "Bootstrapping CDK environment..."
npx cdk bootstrap

echo ""
echo "Deploying CDK stack..."
npx cdk deploy --require-approval never "$@"
