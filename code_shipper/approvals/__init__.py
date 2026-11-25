"""Approvals package: when a PR counts as approved, and what stops it being self-approved."""

from .policy import ApprovalRule, Policy, PolicyError, glob_match, parse

__all__ = ["ApprovalRule", "Policy", "PolicyError", "glob_match", "parse"]
