"""Budgeted weighted coverage with immutable revisions and requirement closures.

The model owns validation and project-state semantics.  This module only searches
its legal active-card space; it never reads a corpus or uses request identifiers.
"""

from __future__ import annotations

import math
import random
import time


LAST_STATS: dict[str, object] = {}


def _indices(mask: int):
    while mask:
        bit = mask & -mask
        yield bit.bit_length() - 1
        mask ^= bit


class _Search:
    def __init__(self, model, deadline: float):
        self.model = model
        self.deadline = deadline
        self.costs = model.costs
        self.covers = model.cover_masks
        self.closures = model.closures
        self.conflicts = model.conflicts
        self.weights = model.feature_weights
        self.weight_planes = []
        for position in range(max(self.weights, default=0).bit_length()):
            plane = 0
            for i, weight in enumerate(self.weights):
                if weight & (1 << position):
                    plane |= 1 << i
            if plane:
                self.weight_planes.append((1 << position, plane))
        self.budget = model.budget
        self.mandatory = model.mandatory_mask
        self.n = len(self.costs)
        self.cost_cache = {0: 0}
        self.cover_cache = {0: 0}
        self.value_cache = {0: 0}
        self.closure_cost = [0] * self.n
        self.closure_cover = [0] * self.n
        self.closure_conflict = [0] * self.n
        self.dependents = [0] * self.n
        self.features = [tuple(_indices(mask)) for mask in self.covers]
        mandatory_conflicts = self.conflict_union(self.mandatory)
        self.eligible = 0
        for i in _indices(model.usable_mask):
            closure = self.closures[i]
            self.closure_cost[i] = self.cost(closure)
            self.closure_cover[i] = self.coverage(closure)
            self.closure_conflict[i] = self.conflict_union(closure)
            for j in _indices(closure):
                self.dependents[j] |= 1 << i
            if not closure & mandatory_conflicts:
                self.eligible |= 1 << i
        self.order, self.color_count = self.structural_order()
        self.rank = {i: position for position, i in enumerate(self.order)}
        self.candidates = tuple(i for i in self.order if not self.mandatory & (1 << i))
        self.excluded_by = [0] * self.n
        for i in self.candidates:
            excluded = 0
            for j in _indices(self.closure_conflict[i]):
                excluded |= self.dependents[j]
            self.excluded_by[i] = excluded
        self.best = self.mandatory
        self.best_value = self.value(self.coverage(self.best))
        self.nodes = 0
        self.moves = 0
        self.starts = 0

    def structural_order(self):
        """Refine graph colors without treating identifiers as semantic data.

        Most asymmetric projects acquire distinct colors.  Any ties left after
        refinement have the same observed structural signature; their input
        positions only decide which tied candidate is considered first.
        """
        indices = tuple(_indices(self.eligible))
        if not indices:
            return (), 0

        def label(signatures):
            labels = {signature: number for number, signature in
                      enumerate(sorted(set(signatures.values())))}
            return {key: labels[signature] for key, signature in signatures.items()}

        coverers = [[] for _ in self.weights]
        for i in indices:
            for f in self.features[i]:
                coverers[f].append(i)
        colors = label({i: (
            self.costs[i], bool(self.mandatory & (1 << i)),
            tuple(sorted(self.weights[f] for f in self.features[i])),
            self.closures[i].bit_count(),
            (self.conflicts[i] & self.eligible).bit_count(),
            (self.dependents[i] & self.eligible).bit_count(),
        ) for i in indices})
        feature_colors = label({f: (weight,) for f, weight in enumerate(self.weights)})
        for _ in range(5):
            feature_colors = label({f: (
                feature_colors[f], tuple(sorted(colors[i] for i in coverers[f]))
            ) for f in range(len(self.weights))})
            colors = label({i: (
                colors[i],
                tuple(sorted(feature_colors[f] for f in self.features[i])),
                tuple(sorted(colors[j] for j in
                             _indices(self.closures[i] & self.eligible & ~(1 << i)))),
                tuple(sorted(colors[j] for j in
                             _indices(self.dependents[i] & self.eligible & ~(1 << i)))),
                tuple(sorted(colors[j] for j in
                             _indices(self.conflicts[i] & self.eligible))),
            ) for i in indices})
        return tuple(sorted(indices, key=colors.__getitem__)), len(set(colors.values()))

    def cost(self, mask: int) -> int:
        cached = self.cost_cache.get(mask)
        if cached is not None:
            return cached
        total = sum(self.costs[i] for i in _indices(mask))
        if len(self.cost_cache) >= 4096:
            self.cost_cache.clear()
        self.cost_cache[mask] = total
        return total

    def coverage(self, mask: int) -> int:
        cached = self.cover_cache.get(mask)
        if cached is not None:
            return cached
        covered = 0
        for i in _indices(mask):
            covered |= self.covers[i]
        if len(self.cover_cache) >= 4096:
            self.cover_cache.clear()
        self.cover_cache[mask] = covered
        return covered

    def value(self, mask: int) -> int:
        cached = self.value_cache.get(mask)
        if cached is not None:
            return cached
        if mask.bit_count() < len(self.weight_planes):
            value = sum(self.weights[i] for i in _indices(mask))
        else:
            value = sum(unit * (mask & plane).bit_count()
                        for unit, plane in self.weight_planes)
        if len(self.value_cache) >= 8192:
            self.value_cache.clear()
        self.value_cache[mask] = value
        return value

    def conflict_union(self, mask: int) -> int:
        result = 0
        for i in _indices(mask):
            result |= self.conflicts[i]
        return result

    def record(self, selected: int, value: int | None = None):
        if value is None:
            value = self.value(self.coverage(selected))
        if value > self.best_value:
            self.best, self.best_value = selected, value

    def greedy(self, selected: int, alpha: float = 1.0, blocked: int = 0,
               limit: bool = True, candidates=None, beta: float = 0.0,
               noise=None) -> int:
        """Add complete closures using their current marginal gain and cost."""
        candidates = list(self.candidates if candidates is None else candidates)
        covered = self.coverage(selected)
        spent = self.cost(selected)
        inverses = {}
        degree_inverses = {}
        while candidates:
            if limit and time.perf_counter() >= self.deadline:
                break
            winner = -1
            score_best = -1.0
            gain_best = 0
            next_candidates = []
            candidate_mask = sum(1 << i for i in candidates)
            for i in candidates:
                bit = 1 << i
                if selected & bit or self.closures[i] & blocked:
                    continue
                if self.closure_conflict[i] & selected:
                    continue
                extra = self.closures[i] & ~selected
                additional_cost = self.cost(extra)
                if spent + additional_cost > self.budget:
                    # Cost only falls as selected grows, so retain this option.
                    next_candidates.append(i)
                    continue
                gain = self.value(self.closure_cover[i] & ~covered)
                if not gain:
                    continue
                next_candidates.append(i)
                inverse = inverses.get(additional_cost)
                if inverse is None:
                    inverse = additional_cost ** (-alpha)
                    inverses[additional_cost] = inverse
                score = gain * inverse
                if beta:
                    degree = (self.excluded_by[i] & candidate_mask).bit_count() + 1
                    degree_inverse = degree_inverses.get(degree)
                    if degree_inverse is None:
                        degree_inverse = degree ** (-beta)
                        degree_inverses[degree] = degree_inverse
                    score *= degree_inverse
                if noise is not None:
                    score *= noise[i]
                if (score > score_best or
                        (score == score_best and gain > gain_best)):
                    winner, score_best, gain_best = i, score, gain
            if winner < 0:
                break
            closure = self.closures[winner]
            spent += self.cost(closure & ~selected)
            selected |= closure
            covered |= self.closure_cover[winner]
            candidates = next_candidates
        self.record(selected, self.value(covered))
        return selected

    def independent_knapsack(self) -> int | None:
        """Exact fast path identified entirely from the selection structure."""
        union = 0
        for i in _indices(self.eligible):
            if self.closures[i] != 1 << i:
                return None
            if self.conflicts[i] & self.eligible:
                return None
            if union & self.covers[i]:
                return None
            union |= self.covers[i]
        candidates = self.candidates
        capacity = self.budget - self.cost(self.mandatory)
        if not candidates:
            return self.mandatory
        values = {i: self.value(self.covers[i]) for i in candidates}
        distinct_costs = {self.costs[i] for i in candidates}
        if len(distinct_costs) == 1:
            count = capacity // next(iter(distinct_costs))
            chosen = sorted(candidates, key=lambda i: values[i], reverse=True)
            return self.mandatory | sum(1 << i for i in chosen[:count])
        # Immutable integer masks store reconstruction without a table per item.
        utility = [-1] * (capacity + 1)
        masks = [0] * (capacity + 1)
        utility[0] = 0
        for i in candidates:
            cost, gain, bit = self.costs[i], values[i], 1 << i
            if not gain or cost > capacity:
                continue
            for amount in range(capacity, cost - 1, -1):
                previous = utility[amount - cost]
                if previous >= 0 and previous + gain > utility[amount]:
                    utility[amount] = previous + gain
                    masks[amount] = masks[amount - cost] | bit
        amount = max(range(capacity + 1), key=utility.__getitem__)
        return self.mandatory | masks[amount]

    def exact(self) -> int:
        """Exhaustive branch-and-bound; it has no heuristic time cutoff."""
        self.greedy(self.mandatory, 1.0, limit=False)
        self.greedy(self.mandatory, 0.0, limit=False)
        initial = self.eligible & ~self.mandatory
        costs, covers, closures = self.costs, self.covers, self.closures
        conflicts, dependents = self.closure_conflict, self.dependents
        budget = self.budget

        def visit(selected, remaining, spent, covered, value):
            self.nodes += 1
            if value > self.best_value:
                self.best, self.best_value = selected, value
            if not remaining:
                return
            reachable = covered
            items = []
            for i in _indices(remaining):
                reachable |= covers[i]
                gain = self.value(covers[i] & ~covered)
                if gain:
                    items.append((gain / costs[i], gain, costs[i], i))
            if self.value(reachable) <= self.best_value:
                return
            # Relax dependencies/conflicts and charge each distinct card once.
            # Repeated feature gain is overcounted, so this remains an upper bound.
            items.sort(reverse=True)
            capacity = budget - spent
            upper = value
            for _, gain, cost, _ in items:
                if cost <= capacity:
                    capacity -= cost
                    upper += gain
                else:
                    upper += gain * capacity // cost
                    break
            if upper <= self.best_value:
                return
            # A high-gain decision first establishes a strong incumbent early.
            pivot = items[0][3] if items else next(_indices(remaining))
            closure = closures[pivot]
            extra = closure & ~selected
            extra_cost = self.cost(extra)
            if (not extra & ~remaining and spent + extra_cost <= budget
                    and not conflicts[pivot] & selected):
                excluded = conflicts[pivot] & remaining
                blocked = 0
                for j in _indices(excluded):
                    blocked |= dependents[j]
                next_remaining = remaining & ~(extra | blocked)
                next_covered = covered | self.closure_cover[pivot]
                visit(selected | closure, next_remaining, spent + extra_cost,
                      next_covered, self.value(next_covered))
            visit(selected, remaining & ~dependents[pivot], spent, covered, value)

        visit(self.mandatory, initial, self.cost(self.mandatory),
              self.coverage(self.mandatory), self.best_value if self.best == self.mandatory
              else self.value(self.coverage(self.mandatory)))
        return self.best

    def removed_dependents(self, selected: int, roots: int) -> int:
        removed = 0
        for i in _indices(roots):
            removed |= self.dependents[i]
        return removed & selected

    def local_rebuild(self, selected: int, rounds: int = 2) -> int:
        """Force alternatives to a decision, removing its dependent cards too."""
        current_value = self.value(self.coverage(selected))
        for _ in range(rounds):
            improved = False
            pivots = list(_indices(selected & ~self.mandatory))
            # Large dependency moves receive attention before individual leaves.
            pivots.sort(key=lambda i: (-(self.dependents[i] & selected).bit_count(),
                                       self.rank[i]))
            for i in pivots:
                if time.perf_counter() >= self.deadline:
                    return selected
                removed = self.dependents[i] & selected
                if removed & self.mandatory:
                    continue
                candidate = self.greedy(selected & ~removed, blocked=1 << i)
                value = self.value(self.coverage(candidate))
                if value > current_value:
                    selected, current_value = candidate, value
                    improved = True
            if not improved:
                break
        return selected

    def counts(self, selected: int):
        result = [0] * len(self.weights)
        for i in _indices(selected):
            for f in self.features[i]:
                result[f] += 1
        return result

    def changes(self, counts, removed: int, added: int):
        differences = {}
        for i in _indices(removed):
            for f in self.features[i]:
                differences[f] = differences.get(f, 0) - 1
        for i in _indices(added):
            for f in self.features[i]:
                differences[f] = differences.get(f, 0) + 1
        gain = 0
        for f, difference in differences.items():
            before = counts[f]
            after = before + difference
            if before == 0 and after > 0:
                gain += self.weights[f]
            elif before > 0 and after == 0:
                gain -= self.weights[f]
        return gain, differences

    def anneal(self, selected: int, stop: float, seed: int,
               temperature_scale: float, proposal_limit: int):
        """Feasible closure exchanges, with occasional downhill exploration."""
        rng = random.Random(seed)
        candidates = self.candidates
        if not candidates:
            return
        counts = self.counts(selected)
        value = self.value(self.coverage(selected))
        spent = self.cost(selected)
        typical = sorted(self.value(self.covers[i]) for i in candidates)
        start_temperature = max(1.0, typical[len(typical) // 2] * temperature_scale)
        step = 0
        temperature = start_temperature
        while step < proposal_limit:
            if step % 16 == 0:
                now = time.perf_counter()
                if now >= stop:
                    break
                fraction = step / proposal_limit
                temperature = max(0.02, start_temperature * (1 - fraction) ** 3)
            step += 1
            i = candidates[rng.randrange(len(candidates))]
            if selected & (1 << i):
                continue
            closure = self.closures[i]
            removed = self.removed_dependents(
                selected, self.closure_conflict[i] & selected)
            if removed & self.mandatory:
                continue
            extra = closure & ~(selected & ~removed)
            next_spent = spent - self.cost(removed) + self.cost(extra)
            protected = self.mandatory | closure
            if next_spent > self.budget:
                options = sorted(_indices(selected & ~removed & ~protected),
                                 key=self.rank.__getitem__)
                rng.shuffle(options)
                for _ in range(4):
                    if time.perf_counter() >= stop:
                        return
                    victim = 0
                    victim_score = math.inf
                    for j in options[:16]:
                        trial = self.dependents[j] & selected & ~removed
                        if not trial or trial & protected:
                            continue
                        loss, _ = self.changes(counts, trial, 0)
                        score = -loss / self.cost(trial)
                        if score < victim_score:
                            victim, victim_score = trial, score
                    if not victim:
                        break
                    removed |= victim
                    next_spent -= self.cost(victim)
                    if next_spent <= self.budget:
                        break
                    options = [j for j in options if not removed & (1 << j)]
                if next_spent > self.budget:
                    continue
                extra = closure & ~(selected & ~removed)
                next_spent = spent - self.cost(removed) + self.cost(extra)
            gain, differences = self.changes(counts, removed, extra)
            if gain < 0 and rng.random() >= math.exp(gain / temperature):
                continue
            selected = (selected & ~removed) | extra
            spent = next_spent
            value += gain
            for f, difference in differences.items():
                counts[f] += difference
            self.moves += 1
            self.record(selected, value)
            if step % 512 == 0:
                selected = self.greedy(selected)
                value = self.value(self.coverage(selected))
                spent = self.cost(selected)
                counts = self.counts(selected)

    def run(self) -> int:
        independent = self.independent_knapsack()
        if independent is not None:
            self.record(independent)
            LAST_STATS.update(method="exact-independent-knapsack", exact=True)
            return independent
        if (self.model.active_count <= 24 or
                (self.eligible & ~self.mandatory).bit_count() <= 24):
            selected = self.exact()
            LAST_STATS.update(method="exact-branch-and-bound", exact=True,
                              search_nodes=self.nodes)
            return selected
        start = time.perf_counter()
        # Different ratios explore expensive high-value choices and shared costs.
        for alpha, beta in ((1.0, 0.0), (1.0, 1.0), (0.0, 1.0),
                            (0.65, 0.5), (1.35, 0.0), (0.0, 0.0),
                            (0.85, 0.0), (1.15, 0.5)):
            if time.perf_counter() >= self.deadline:
                break
            self.greedy(self.mandatory, alpha, beta=beta)
            self.starts += 1
        # Structural-order perturbations explore distinct independent sets;
        # their random stream is unchanged by card or feature renaming.
        rng = random.Random(271828)
        for trial in range(12):
            if time.perf_counter() >= self.deadline:
                break
            noise = {i: math.exp(rng.uniform(-1.1, 1.1)) for i in self.candidates}
            self.greedy(self.mandatory, (0.0, 0.65, 1.0)[trial % 3],
                        beta=(0.0, 0.5, 1.0)[trial % 3], noise=noise)
            self.starts += 1
        local_stop = min(self.deadline, start + (self.deadline - start) * 0.20)
        original_deadline = self.deadline
        self.deadline = local_stop
        self.local_rebuild(self.best)
        self.deadline = original_deadline
        baseline = self.best
        schedule = ((0, 0.35, 65536), (0, 1.5, 8192),
                    (1, 0.8, 8192), (2, 0.35, 8192))
        for position, (seed, temperature, proposals) in enumerate(schedule):
            now = time.perf_counter()
            if now >= self.deadline:
                break
            stop = now + (self.deadline - now) / (len(schedule) - position)
            self.anneal(baseline, stop, seed, temperature, proposals)
        # Continue the same deterministic stream after preserving incumbents
        # from the initial exchange trajectories.  More starts cannot discard
        # an earlier good set or change those trajectories' starting point.
        for trial in range(12, 128):
            if time.perf_counter() >= self.deadline:
                break
            noise = {i: math.exp(rng.uniform(-1.1, 1.1)) for i in self.candidates}
            self.greedy(self.mandatory, (0.0, 0.65, 1.0)[trial % 3],
                        beta=(0.0, 0.5, 1.0)[trial % 3], noise=noise)
            self.starts += 1
        self.local_rebuild(self.best, rounds=1)
        LAST_STATS.update(method="closure-greedy-and-feasible-exchanges", exact=False,
                          greedy_starts=self.starts, accepted_exchanges=self.moves)
        return self.best


def select(model, deadline: float) -> int:
    """Return a legal selected-card bitmask; exact for <=24 active cards."""
    LAST_STATS.clear()
    search = _Search(model, deadline)
    selected = search.run()
    LAST_STATS.update(selected_count=selected.bit_count(), utility=search.best_value)
    LAST_STATS.update(structural_classes=search.color_count,
                      eligible_cards=search.eligible.bit_count())
    return selected
