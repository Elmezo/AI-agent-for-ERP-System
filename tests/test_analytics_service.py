"""Tests for the in-memory analytics ("SQL mode") engine."""

from __future__ import annotations

import pytest

from src.models.plan import AggregateOp, AggregateSpec, FilterClause, FilterOp
from src.services.analytics_service import AnalyticsService

_PROJECTS = [
    {"name": "ERP Modernization", "budget": 250000, "spent": 180000, "status": "Active", "owner": "Ahmed", "orgUnit": "IT Department"},
    {"name": "Payroll Automation", "budget": 120000, "spent": 120000, "status": "Completed", "owner": "Youssef", "orgUnit": "Finance Department"},
    {"name": "Enterprise Data Lake", "budget": 300000, "spent": 90000, "status": "Active", "owner": "Mona", "orgUnit": "IT Department"},
    {"name": "HR Self-Service Portal", "budget": 80000, "spent": 60000, "status": "Active", "owner": "Sara", "orgUnit": "Finance Department"},
    {"name": "Sales Analytics Dashboard", "budget": 150000, "spent": 150000, "status": "On Hold", "owner": "Layla", "orgUnit": "Finance Department"},
]


@pytest.fixture
def service() -> AnalyticsService:
    return AnalyticsService()


def test_count_with_filter(service: AnalyticsService) -> None:
    spec = AggregateSpec(op=AggregateOp.COUNT, filters=[FilterClause(field="status", value="Active")])
    result = service.aggregate("projects", _PROJECTS, spec)
    assert result.ok
    assert result.value == 3.0
    assert result.matched_rows == 3
    assert result.total_rows == 5


def test_count_contains_filter(service: AnalyticsService) -> None:
    spec = AggregateSpec(
        op=AggregateOp.COUNT,
        filters=[FilterClause(field="orgUnit", op=FilterOp.CONTAINS, value="Finance")],
    )
    assert service.aggregate("projects", _PROJECTS, spec).value == 3.0


def test_avg_and_sum(service: AnalyticsService) -> None:
    avg = service.aggregate("projects", _PROJECTS, AggregateSpec(op=AggregateOp.AVG, metric="budget"))
    assert avg.value == pytest.approx(180000.0)
    total = service.aggregate("projects", _PROJECTS, AggregateSpec(op=AggregateOp.SUM, metric="budget"))
    assert total.value == 900000.0


def test_min_max(service: AnalyticsService) -> None:
    assert service.aggregate("projects", _PROJECTS, AggregateSpec(op=AggregateOp.MIN, metric="budget")).value == 80000.0
    assert service.aggregate("projects", _PROJECTS, AggregateSpec(op=AggregateOp.MAX, metric="budget")).value == 300000.0


def test_top_n_ranking(service: AnalyticsService) -> None:
    spec = AggregateSpec(op=AggregateOp.MAX, metric="budget", limit=2)
    result = service.aggregate("projects", _PROJECTS, spec)
    assert [g.key for g in result.groups] == ["Enterprise Data Lake", "ERP Modernization"]
    assert result.groups[0].value == 300000.0


def test_bottom_n_ranking(service: AnalyticsService) -> None:
    spec = AggregateSpec(op=AggregateOp.MIN, metric="budget", limit=1, sort_desc=False)
    result = service.aggregate("projects", _PROJECTS, spec)
    assert result.groups[0].key == "HR Self-Service Portal"
    assert result.groups[0].value == 80000.0


def test_group_by_count(service: AnalyticsService) -> None:
    spec = AggregateSpec(op=AggregateOp.COUNT, group_by="orgUnit")
    result = service.aggregate("projects", _PROJECTS, spec)
    groups = {g.key: g.value for g in result.groups}
    assert groups == {"Finance Department": 3.0, "IT Department": 2.0}


def test_group_by_sum_sorted_desc(service: AnalyticsService) -> None:
    # IT = 250000 + 300000 = 550000 ; Finance = 120000 + 80000 + 150000 = 350000
    spec = AggregateSpec(op=AggregateOp.SUM, metric="budget", group_by="orgUnit")
    result = service.aggregate("projects", _PROJECTS, spec)
    assert result.groups[0].key == "IT Department"
    assert result.groups[0].value == 550000.0
    assert result.groups[1].key == "Finance Department"
    assert result.groups[1].value == 350000.0


def test_missing_metric_field_is_reported(service: AnalyticsService) -> None:
    result = service.aggregate("projects", _PROJECTS, AggregateSpec(op=AggregateOp.AVG, metric="cost"))
    assert not result.ok
    assert result.error == "field 'cost' not found"
    assert result.value is None


def test_non_numeric_metric_is_reported(service: AnalyticsService) -> None:
    result = service.aggregate("projects", _PROJECTS, AggregateSpec(op=AggregateOp.SUM, metric="status"))
    assert not result.ok
    assert "not numeric" in result.error


def test_unknown_group_by_is_reported(service: AnalyticsService) -> None:
    result = service.aggregate("projects", _PROJECTS, AggregateSpec(op=AggregateOp.COUNT, group_by="region"))
    assert result.error == "group-by field 'region' not found"


def test_numeric_filter_on_string_values(service: AnalyticsService) -> None:
    """Budgets stored as strings still compare numerically."""
    rows = [{"name": "A", "budget": "250000"}, {"name": "B", "budget": "80000"}]
    spec = AggregateSpec(op=AggregateOp.COUNT, filters=[FilterClause(field="budget", op=FilterOp.GT, value=100000)])
    assert service.aggregate("projects", rows, spec).value == 1.0


def test_empty_rows_count_is_zero(service: AnalyticsService) -> None:
    result = service.aggregate("projects", [], AggregateSpec(op=AggregateOp.COUNT))
    assert result.value == 0.0
    assert result.ok is True
