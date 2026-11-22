#!/usr/bin/env bash
set -e
echo "Checking service health..."
curl -f http://127.0.0.1:8000/health
echo "Health check passed!"
