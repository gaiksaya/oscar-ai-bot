# Copyright OpenSearch Contributors
# SPDX-License-Identifier: Apache-2.0

"""Configuration constants for the newsletter handler."""

import os

DEFAULT_PIPELINE = os.environ.get("DEFAULT_PIPELINE", "oscar-flow-agentic-pipeline")
COMPANY_TABLE_NAME = os.environ.get("COMPANY_TABLE_NAME", "")

COMPANY_ALIASES = {
    "aws": "Amazon",
    "amazon web services": "Amazon",
    "amazon.com": "Amazon",
    "apple inc": "Apple",
    "aiven.io": "Aiven",
    "uber": "Uber",
    "bytedance": "ByteDance",
    "opensource connections": "o19s",
    "nexgen cloud": "NexGen Cloud",
}

COMPANY_SUFFIXES_TO_STRIP = [".io", " Inc", " Inc.", " Corp", " Corp.", " Ltd", " Ltd.", " GmbH"]

BEDROCK_MESSAGE_VERSION = os.environ.get("BEDROCK_RESPONSE_MESSAGE_VERSION", "1.0")
