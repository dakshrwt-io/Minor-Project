from app.planner.service import DeterministicTaskPlanner


def test_planner_creates_ordered_read_only_plan() -> None:
    plan = DeterministicTaskPlanner().create_plan("Update README", apply_changes=False)

    assert plan.goal == "Update README"
    assert [step.id for step in plan.steps] == ["inspect", "decide", "complete"]
    assert "Describe" in plan.steps[-1].description


def test_planner_marks_final_step_as_applying_changes_when_requested() -> None:
    plan = DeterministicTaskPlanner().create_plan("Update README", apply_changes=True)

    assert "Apply" in plan.steps[-1].description
