"""求解器工具层：把「数学模型」变成「最优方案」。"""

from app.solver.scheduling import (
    PENALTY,
    RULE_ADVICE,
    RULE_LABELS,
    capacity_analysis,
    effective_cap,
    render_schedule_table,
    repair_problem,
    solve,
    validate_problem,
)

__all__ = [
    "PENALTY",
    "RULE_ADVICE",
    "RULE_LABELS",
    "capacity_analysis",
    "effective_cap",
    "render_schedule_table",
    "repair_problem",
    "solve",
    "validate_problem",
]
