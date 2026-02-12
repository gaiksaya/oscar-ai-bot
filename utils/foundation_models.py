"""
Foundation model definitions for AWS Bedrock.
"""

from enum import Enum


class FoundationModels(Enum):
    """Supported foundation models for Bedrock."""
    AMAZON_TITAN_2_0 = "amazon.titan-embed-text-v2:0"
    CLAUDE_4_5_SONNET = "us.anthropic.claude-sonnet-4-5-20250929-v1:0"
    CLAUDE_4_SONNET = "us.anthropic.claude-sonnet-4-20250514-v1:0"
    CLAUDE_3_7_SONNET = "us.anthropic.claude-3-7-sonnet-20250219-v1:0"
    CLAUDE_3_5_SONNET = "us.anthropic.claude-3-5-sonnet-20241022-v2:0"
    CLAUDE_3_5_HAIKU = "us.anthropic.claude-3-5-haiku-20241022-v1:0"
    CLAUDE_3_SONNET = "us.anthropic.claude-3-sonnet-20240229-v1:0"
    CLAUDE_3_HAIKU = "us.anthropic.claude-3-haiku-20240307-v1:0"
