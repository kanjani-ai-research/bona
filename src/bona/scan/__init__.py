"""Bona Scan — security and compliance query packs for the Asset Property Graph.

Inspired by Joern Scan: run named queries against the graph to detect
security issues, compliance violations, and misconfigurations.

Usage:
    bona scan --profile nw --region us-east-1
    bona scan --profile nw --region us-east-1 --tags security
    bona scan --profile nw --region us-east-1 --names public-s3,unencrypted-ebs
    bona scan --profile nw --region us-east-1 --min-score 7
"""
