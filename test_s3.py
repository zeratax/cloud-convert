#!/usr/bin/env python3
"""Test S3 connection"""
import os
from dotenv import load_dotenv
import boto3

# Load environment
load_dotenv()

# Create S3 client
s3_client = boto3.client('s3', region_name=os.getenv('AWS_REGION'))

# Test connection
bucket = os.getenv('S3_BUCKET')
print(f"Testing connection to bucket: {bucket}")

try:
    response = s3_client.head_bucket(Bucket=bucket)
    print("✓ S3 Connection successful!")
    print(f"  Region: {os.getenv('AWS_REGION')}")
    print(f"  Bucket: {bucket}")
except Exception as e:
    print(f"✗ S3 Connection failed: {e}")
    exit(1)
