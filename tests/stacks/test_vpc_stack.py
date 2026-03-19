# Copyright OpenSearch Contributors
# SPDX-License-Identifier: Apache-2.0
"""Tests for OSCAR VPC stack."""

import pytest
from aws_cdk import App, Environment
from aws_cdk.assertions import Template

from stacks.vpc_stack import OscarVpcStack


@pytest.fixture
def template():
    """Synthesise the VPC stack (creates a new VPC when no VPC_ID context is set)."""
    app = App()
    stack = OscarVpcStack(
        app, "TestVpcStack",
        env=Environment(account="123456789012", region="us-east-1"),
    )
    return Template.from_stack(stack)


class TestVpcStack:
    """Test cases for OscarVpcStack."""

    def test_vpc_created(self, template):
        """A VPC should be created when no VPC_ID context is provided."""
        template.resource_count_is("AWS::EC2::VPC", 1)

    def test_lambda_security_group_created(self, template):
        """Lambda security group should be created."""
        template.has_resource_properties("AWS::EC2::SecurityGroup", {
            "GroupDescription":
                "Security group for OSCAR Lambda functions with OpenSearch access",
        })

    def test_security_group_allows_all_outbound(self, template):
        """Lambda security group should allow all outbound traffic."""
        template_dict = template.to_json()
        for resource in template_dict["Resources"].values():
            if resource.get("Type") == "AWS::EC2::SecurityGroup":
                props = resource.get("Properties", {})
                if "OSCAR Lambda" in props.get("GroupDescription", ""):
                    egress = props.get("SecurityGroupEgress", [])
                    protocols = {r.get("IpProtocol") for r in egress}
                    assert "-1" in protocols, "Missing allow-all outbound rule"
                    return
        pytest.fail("Lambda security group not found")

    def test_vpc_endpoints_created(self, template):
        """STS and Secrets Manager VPC endpoints should be created."""
        template.resource_count_is("AWS::EC2::VPCEndpoint", 2)
