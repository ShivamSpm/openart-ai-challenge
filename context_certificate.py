"""Recomputable integer dual prices for the contract's coverage relaxation.

All active visible cards participate, even when selection constraints make them
unusable. Prices certify an upper bound, not the success of the price search.
"""
from __future__ import annotations

from fractions import Fraction
import time

SCALE = 1_000_000


def make_certificate(model, utility: int, deadline: float) -> tuple[dict, int]:
    """Return public prices and their exactly recomputed upper numerator.

    Disjoint coverage reduces to the fractional-knapsack dual; its optimal
    budget price is found analytically. Overlapping coverage also tries bounded
    projected subgradient steps, retaining the best *valid integer* prices.
    """
    active = [i for i in range(len(model.costs)) if model.active_mask >> i & 1]
    features = []
    coverers = [[] for _ in model.feature_weights]
    for position, i in enumerate(active):
        covered = tuple(f for f in range(len(model.feature_weights))
                        if model.cover_masks[i] >> f & 1)
        features.append(covered)
        for f in covered:
            coverers[f].append(position)
    costs = [model.costs[i] for i in active]
    ceilings = [w * SCALE for w in model.feature_weights]
    total = sum(ceilings)

    def evaluate(prices):
        gains = [sum(prices[f] for f in covered) for covered in features]
        # The slope B - sum(costs of profitable cards) changes at these ratios.
        order = sorted(range(len(costs)), key=lambda i: Fraction(gains[i], costs[i]), reverse=True)
        spent = 0
        numerator, denominator = 0, 1
        for i in order:
            if spent + costs[i] > model.budget:
                numerator, denominator = gains[i], costs[i]
                break
            spent += costs[i]
        floor_price = numerator // denominator
        choices = {floor_price, (numerator + denominator - 1) // denominator}
        remainder = total - sum(prices)
        upper, price = min((model.budget * p + remainder +
                            sum(max(0, gain - p * cost) for gain, cost in zip(gains, costs)), p)
                           for p in choices)
        return upper, price, gains, numerator, denominator

    # z=0 on reachable features certifies their total weight. Unreachable
    # features can always take full prices to remove their constant slack.
    fallback = [0 if coverers[f] else ceiling for f, ceiling in enumerate(ceilings)]
    best_upper, best_price, _, _, _ = evaluate(fallback)
    best_prices = fallback
    prices = ceilings[:]
    upper, price, gains, numerator, denominator = evaluate(prices)
    if upper < best_upper:
        best_upper, best_price, best_prices = upper, price, prices[:]
    disjoint = all(len(items) <= 1 for items in coverers)
    for iteration in range(80 if not disjoint else 0):
        if best_upper <= utility * SCALE or time.perf_counter() >= deadline:
            break
        # At a breakpoint, distribute the remaining fractional capacity among
        # tied cards. This yields a subgradient of the minimized-price bound.
        residual = [gain * denominator - numerator * cost for gain, cost in zip(gains, costs)]
        positive_cost = sum(cost for cost, r in zip(costs, residual) if r > 0)
        tied_cost = sum(cost for cost, r in zip(costs, residual) if r == 0)
        fraction = min(1.0, max(0.0, (model.budget - positive_cost) / tied_cost)) if tied_cost else 0.0
        activity = [1.0 if r > 0 else fraction if r == 0 else 0.0 for r in residual]
        gradient = [sum(activity[i] for i in items) - 1.0 for items in coverers]
        norm = sum(g * g for g, z, cap in zip(gradient, prices, ceilings)
                   if not (g > 0 and z == 0 or g < 0 and z == cap))
        if not norm:
            break
        step = 1.2 * (upper - utility * SCALE) / norm
        updated = [max(0, min(cap, round(z - step * g)))
                   for z, g, cap in zip(prices, gradient, ceilings)]
        if updated == prices:
            break
        prices = updated
        upper, price, gains, numerator, denominator = evaluate(prices)
        if upper < best_upper:
            best_upper, best_price, best_prices = upper, price, prices[:]
    return {"scale": SCALE, "budget_price": best_price,
            "feature_prices": dict(zip(model.feature_ids, best_prices))}, best_upper
