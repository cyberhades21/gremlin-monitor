"""Run hidden tests against a final repo and interpret the results."""

from gremlin_assess.grading.harness import grade
from gremlin_assess.grading.results import Flag, GradeResult, ItemResult, TestOutcome

__all__ = ["grade", "Flag", "GradeResult", "ItemResult", "TestOutcome"]
