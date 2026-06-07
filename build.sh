#!/usr/bin/env bash
# Exit on error
set -o errexit

# Install Node.js if not available
if ! command -v node &> /dev/null; then
    echo "Node.js not found, installing..."
    NODE_VERSION=v20.11.0
    curl -sL https://nodejs.org/dist/${NODE_VERSION}/node-${NODE_VERSION}-linux-x64.tar.xz | tar -xJ
    export PATH=$PWD/node-${NODE_VERSION}-linux-x64/bin:$PATH
fi

# Install dependencies and build frontend
echo "Building Astro frontend..."
npm install
npm run build

# Install python dependencies
echo "Installing Python dependencies..."
pip install -r requirements.txt
