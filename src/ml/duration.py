"""Task-duration estimation: transparent baseline first, ML only with enough history.

Leakage control: features are limited to information known BEFORE work starts
(estimate, priority, category, prerequisite count, splittable flag). Actual minutes are only the target.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from statistics import median
from typing import Any

from src.models.entities import Task

try:                                        # optional dependency
    import numpy as np
    from sklearn.ensemble import GradientBoostingRegressor
    from sklearn.model_selection import KFold, cross_val_predict
    from sklearn.dummy import DummyRegressor
    SKLEARN = True
except Exception:  # noqa: BLE001
    SKLEARN = False

FEATURES = ["estimate_minutes", "priority", "n_prerequisites", "splittable", "category (one-hot)"]


@dataclass
class DurationInsight:
    status: str                       # "insufficient_data" | "baseline" | "trained" | "unavailable"
    message: str
    samples: int
    needed: int
    correction_ratio: float | None = None       # heuristic (median actual/estimate)
    metrics: dict[str, float] = field(default_factory=dict)   # only real cross-validation numbers
    features: list[str] = field(default_factory=list)
    kind: str = "heuristic estimate"


def history(tasks: list[Task]) -> list[Task]:
    return [t for t in tasks if t.status == "done" and t.actual_minutes > 0 and t.estimate_minutes > 0]


def baseline_ratio(tasks: list[Task], category: str | None = None, min_n: int = 3) -> float | None:
    """Median(actual / estimate), optionally per category. A simple, explainable heuristic."""
    h = history(tasks)
    sel = [t for t in h if category is None or t.category == category]
    if len(sel) < min_n:
        sel = h if len(h) >= min_n else []
    return round(median(t.actual_minutes / t.estimate_minutes for t in sel), 3) if sel else None


class DurationModel:
    def __init__(self, min_samples: int = 30):
        self.min_samples = min_samples
        self.model: Any = None
        self.categories: list[str] = []
        self.insight = DurationInsight("insufficient_data", "Not trained.", 0, min_samples)

    def _row(self, t: Task) -> list[float]:
        return [t.estimate_minutes, t.priority, len(t.prerequisites), float(t.splittable)] + \
               [float(t.category == c) for c in self.categories]

    def train(self, tasks: list[Task]) -> DurationInsight:
        h = history(tasks)
        n = len(h)
        ratio = baseline_ratio(tasks)
        if n < self.min_samples:
            self.model = None
            self.insight = DurationInsight(
                "insufficient_data",
                f"Insufficient historical data: {n} of {self.min_samples} completed tasks with recorded time. "
                + ("Showing a simple heuristic ratio only." if ratio else "No estimate available yet."),
                n, self.min_samples, ratio)
            return self.insight
        if not SKLEARN:
            self.model = None
            self.insight = DurationInsight("unavailable", "scikit-learn is not installed; showing the heuristic baseline only.",
                                           n, self.min_samples, ratio)
            return self.insight
        self.categories = sorted({t.category for t in h})
        X = np.array([self._row(t) for t in h])
        y = np.array([t.actual_minutes / t.estimate_minutes for t in h])     # target: actual/estimate ratio
        k = min(5, max(2, n // 6))
        cv = KFold(n_splits=k, shuffle=True, random_state=0)
        model = GradientBoostingRegressor(n_estimators=80, max_depth=2, random_state=0)
        pred = cross_val_predict(model, X, y, cv=cv)
        base_pred = cross_val_predict(DummyRegressor(strategy="median"), X, y, cv=cv)
        est = np.array([t.estimate_minutes for t in h])
        actual = np.array([t.actual_minutes for t in h])
        mae_model = float(np.mean(np.abs(pred * est - actual)))
        mae_naive = float(np.mean(np.abs(est - actual)))
        mae_base = float(np.mean(np.abs(base_pred * est - actual)))
        model.fit(X, y)
        self.model = model
        useful = mae_model < mae_base
        self.insight = DurationInsight(
            "trained",
            f"Gradient boosting trained on {n} tasks ({k}-fold cross-validation)."
            + ("" if useful else " It did NOT beat the median-ratio baseline, so predictions are shown for reference only."),
            n, self.min_samples, ratio,
            {"cv_mae_minutes_model": round(mae_model, 1), "cv_mae_minutes_median_ratio_baseline": round(mae_base, 1),
             "cv_mae_minutes_raw_estimate": round(mae_naive, 1), "folds": float(k)},
            FEATURES, kind="ML prediction")
        return self.insight

    def predict_minutes(self, t: Task) -> tuple[float, str]:
        """Return (minutes, kind). Falls back to the raw estimate when nothing trustworthy exists."""
        if self.model is not None and self.insight.status == "trained":
            row = np.array([self._row(t)])
            return float(self.model.predict(row)[0] * t.estimate_minutes), "ML prediction"
        if self.insight.correction_ratio:
            return t.estimate_minutes * self.insight.correction_ratio, "heuristic estimate"
        return float(t.estimate_minutes), "your estimate (no history)"
