"""Dependency graph validation: missing IDs, self-dependencies, cycles, blocked explanations."""
from __future__ import annotations

from typing import Iterable

from src.models.entities import Task


class DependencyError(ValueError):
    pass


def validate_prerequisites(tasks: Iterable[Task]) -> list[str]:
    """Return a list of human-readable problems (empty if the graph is valid)."""
    tasks = list(tasks)
    ids = {t.id for t in tasks}
    problems: list[str] = []
    for t in tasks:
        for p in t.prerequisites:
            if p == t.id:
                problems.append(f"'{t.title}' depends on itself.")
            elif p not in ids:
                problems.append(f"'{t.title}' has a nonexistent prerequisite ({p}).")
    cycle = find_cycle(tasks)
    if cycle:
        names = {t.id: t.title for t in tasks}
        problems.append("Dependency cycle: " + " -> ".join(names.get(i, i) for i in cycle))
    return problems


def find_cycle(tasks: Iterable[Task]) -> list[str] | None:
    """Return one cycle as a list of IDs (first == last), or None. Iterative DFS, deterministic order."""
    graph = {t.id: [p for p in t.prerequisites if p != t.id] for t in tasks}
    for t in tasks:  # self loops
        if t.id in t.prerequisites:
            return [t.id, t.id]
    WHITE, GREY, BLACK = 0, 1, 2
    color = {n: WHITE for n in graph}
    for root in sorted(graph):
        if color[root] != WHITE:
            continue
        stack: list[tuple[str, int]] = [(root, 0)]
        path: list[str] = []
        while stack:
            node, idx = stack.pop()
            if idx == 0:
                color[node] = GREY
                path.append(node)
            nbrs = [n for n in graph[node] if n in graph]
            if idx < len(nbrs):
                stack.append((node, idx + 1))
                nxt = nbrs[idx]
                if color[nxt] == GREY:
                    return path[path.index(nxt):] + [nxt]
                if color[nxt] == WHITE:
                    stack.append((nxt, 0))
            else:
                color[node] = BLACK
                path.pop()
    return None


def would_create_cycle(tasks: Iterable[Task], task_id: str, new_prereqs: list[str]) -> bool:
    """Check a proposed prerequisite list for `task_id` against the existing graph."""
    clones: list[Task] = []
    found = False
    for t in tasks:
        if t.id == task_id:
            found = True
            c = Task(title=t.title, id=t.id, prerequisites=list(new_prereqs))
        else:
            c = Task(title=t.title, id=t.id, prerequisites=list(t.prerequisites))
        clones.append(c)
    if not found:
        clones.append(Task(title="new", id=task_id, prerequisites=list(new_prereqs)))
    return find_cycle(clones) is not None


def topological_order(tasks: Iterable[Task]) -> list[str]:
    tasks = list(tasks)
    problems = validate_prerequisites(tasks)
    if problems:
        raise DependencyError("; ".join(problems))
    indeg = {t.id: len(t.prerequisites) for t in tasks}
    children: dict[str, list[str]] = {t.id: [] for t in tasks}
    for t in tasks:
        for p in t.prerequisites:
            children[p].append(t.id)
    ready = sorted(i for i, d in indeg.items() if d == 0)
    order: list[str] = []
    while ready:
        n = ready.pop(0)
        order.append(n)
        for c in sorted(children[n]):
            indeg[c] -= 1
            if indeg[c] == 0:
                ready.append(c)
        ready.sort()
    return order


def transitive_dependents(tasks: Iterable[Task]) -> dict[str, int]:
    """Number of tasks (directly or indirectly) waiting on each task."""
    tasks = list(tasks)
    children: dict[str, set[str]] = {t.id: set() for t in tasks}
    for t in tasks:
        for p in t.prerequisites:
            if p in children:
                children[p].add(t.id)
    memo: dict[str, set[str]] = {}

    def walk(n: str, seen: set[str]) -> set[str]:
        if n in memo:
            return memo[n]
        out: set[str] = set()
        for c in children.get(n, ()):
            if c in seen:
                continue
            out.add(c)
            out |= walk(c, seen | {n})
        memo[n] = out
        return out

    return {t.id: len(walk(t.id, set())) for t in tasks}


def blocking_reasons(task: Task, by_id: dict[str, Task]) -> list[str]:
    """Explain why a task cannot start now."""
    reasons: list[str] = []
    if task.status == "blocked":
        reasons.append(f"Manually marked blocked: {task.blocked_reason or 'no reason recorded'}.")
    for p in task.prerequisites:
        pre = by_id.get(p)
        if pre is None:
            reasons.append(f"Prerequisite {p} no longer exists.")
        elif pre.status != "done":
            reasons.append(f"Waiting for '{pre.title}' (status: {pre.status}).")
    return reasons
