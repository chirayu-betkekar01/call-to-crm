"""Agent 2 - Buying-Role Classifier.

Takes Agent 1's handoff (roster + transcript) and labels each buyer-side person Decision Maker,
Champion and/or Blocker, judged mostly by how they behave on the call. The model proposes roles,
and code keeps only roles whose evidence quotes really appear in the transcript.
"""
from .agent import BuyingRoleStage, quote_in_transcript
from .rubric import DEFAULT_ROLES, ROLE_LIBRARY, RoleDef

RoleClassifierStage = BuyingRoleStage  # same class, named after the agent

__all__ = ["BuyingRoleStage", "DEFAULT_ROLES", "ROLE_LIBRARY", "RoleClassifierStage", "RoleDef", "quote_in_transcript"]
