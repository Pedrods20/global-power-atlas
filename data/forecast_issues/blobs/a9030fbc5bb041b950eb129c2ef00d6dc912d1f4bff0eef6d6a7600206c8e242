"""Dense linear algebra for the ridge fit, written out rather than imported.

The regression solves a small positive definite system per clock hour and
walk-forward origin. The cumulative-moment solver is retained for efficient
daily refits and checked against an independent NumPy solution in tests.

The solver takes **moments** rather than a design matrix, and that is what makes
refitting at every step affordable. An expanding training window grows by one
row per step, so its cross-moment matrix is a running sum, and polars computes
the matrix for every origin in a single cumulative pass. The Python loop then
only assembles and solves a small system. See :mod:`gpa.forecast.models`.

Centring and scaling are done from the moments too, analytically, rather than by
standardising a design matrix that is never materialised. That keeps the ridge
penalty comparable across features of very different units, which matters here
because the same model carries prices in euros per MWh alongside residual load
in megawatts and calendar dummies in zero-one.
"""

from __future__ import annotations

import math
from collections.abc import Sequence

__all__ = [
    "NotPositiveDefinite",
    "cholesky",
    "cholesky_solve",
    "predict",
    "ridge_from_moments",
]

_CONSTANT_TOLERANCE = 1e-9
"""Relative standard deviation below which a column is treated as constant.

A constant column carries no information and makes the centred system singular,
so it is dropped and its coefficient reported as zero. This happens in practice:
a day-of-week dummy is constant inside a training window that holds only one
occurrence of that weekday, which is the case early in a walk-forward run.
"""


class NotPositiveDefinite(ValueError):
    """The matrix offered to the factorisation is not positive definite.

    With a strictly positive ridge penalty this cannot happen, because the
    penalised matrix is a positive semidefinite Gram plus a positive multiple of
    the identity. It is reachable only at zero penalty, which is the ordinary
    least squares path the tests use.
    """


def cholesky(matrix: Sequence[Sequence[float]]) -> list[list[float]]:
    """Lower-triangular ``L`` such that ``L`` times its transpose is ``matrix``.

    Args:
        matrix: A square, symmetric, positive definite matrix. Only the lower
            triangle is read, so a caller that fills one triangle need not
            mirror it.

    Raises:
        ValueError: If ``matrix`` is not square.
        NotPositiveDefinite: If a leading minor is not positive.
    """
    size = len(matrix)
    lower = [[0.0] * size for _ in range(size)]

    for i in range(size):
        row = matrix[i]
        if len(row) != size:
            raise ValueError(f"matrix must be square; row {i} has {len(row)} of {size} entries")
        lower_i = lower[i]
        for j in range(i + 1):
            lower_j = lower[j]
            total = row[j] - sum(lower_i[k] * lower_j[k] for k in range(j))
            if i == j:
                if total <= 0.0:
                    raise NotPositiveDefinite(
                        f"leading minor {i + 1} of {size} is not positive ({total!r}); "
                        "the system is singular or indefinite"
                    )
                lower_i[j] = math.sqrt(total)
            else:
                lower_i[j] = total / lower_j[j]

    return lower


def cholesky_solve(matrix: Sequence[Sequence[float]], vector: Sequence[float]) -> list[float]:
    """Solve ``matrix @ x = vector`` for a symmetric positive definite matrix.

    Raises:
        ValueError: If the shapes disagree.
        NotPositiveDefinite: If the matrix is not positive definite.
    """
    size = len(matrix)
    if len(vector) != size:
        raise ValueError(f"vector has {len(vector)} entries for a {size}x{size} matrix")

    lower = cholesky(matrix)

    forward = [0.0] * size
    for i in range(size):
        row = lower[i]
        forward[i] = (vector[i] - sum(row[k] * forward[k] for k in range(i))) / row[i]

    solution = [0.0] * size
    for i in reversed(range(size)):
        total = sum(lower[k][i] * solution[k] for k in range(i + 1, size))
        solution[i] = (forward[i] - total) / lower[i][i]

    return solution


def ridge_from_moments(
    gram: Sequence[Sequence[float]],
    cross: Sequence[float],
    *,
    alpha: float,
) -> list[float]:
    """Ridge coefficients from accumulated cross-moments.

    The moments are taken over an augmented design matrix whose first column is
    a constant one, so ``gram[0][0]`` is the number of observations, ``gram[0][j]``
    is the sum of feature ``j`` and ``cross[0]`` is the sum of the target. Feature
    ``j`` of the caller's list therefore lives at index ``j + 1``.

    The fit is on centred, unit-variance features, so ``alpha`` means the same
    thing whatever units a feature carries. The penalty is scaled by the number
    of observations as well, which keeps a chosen ``alpha`` meaningful while an
    expanding training window grows. The intercept is never penalised.

    Args:
        gram: ``(p+1) x (p+1)`` matrix of summed pairwise products.
        cross: ``p+1`` sums of each column times the target.
        alpha: Ridge penalty on the standardised coefficients, at least zero.
            Zero gives ordinary least squares and can fail on a singular system.

    Returns:
        ``p+1`` coefficients on the caller's original scale, the intercept first.
        A column that is constant across the window gets a zero coefficient.

    Raises:
        ValueError: If the shapes disagree, ``alpha`` is negative, or the window
            holds too few observations to identify the coefficients.
        NotPositiveDefinite: Only reachable at ``alpha`` of zero.
    """
    size = len(gram)
    if len(cross) != size:
        raise ValueError(f"cross has {len(cross)} entries for a {size}x{size} gram")
    if alpha < 0.0:
        raise ValueError(f"alpha must not be negative, got {alpha}")

    features = size - 1
    count = gram[0][0]
    if count <= features:
        raise ValueError(
            f"need more than {features} observations to identify {features} features, got {count}"
        )

    means = [gram[0][j] / count for j in range(size)]
    target_mean = cross[0] / count

    # Centred second moments of the features about their window means. Working
    # from raw sums rather than from a centred design matrix is what lets the
    # window's moments come out of a cumulative sum.
    centred = [
        [gram[j][k] - count * means[j] * means[k] for k in range(1, size)] for j in range(1, size)
    ]
    centred_cross = [cross[j] - count * means[j] * target_mean for j in range(1, size)]

    scales = [math.sqrt(max(centred[j][j], 0.0) / count) for j in range(features)]
    active = [
        j for j in range(features) if scales[j] > _CONSTANT_TOLERANCE * (1.0 + abs(means[j + 1]))
    ]

    coefficients = [0.0] * size
    if not active:
        coefficients[0] = target_mean
        return coefficients

    penalty = alpha * count
    system = [
        [centred[a][b] / (scales[a] * scales[b]) + (penalty if a == b else 0.0) for b in active]
        for a in active
    ]
    right = [centred_cross[a] / scales[a] for a in active]

    standardised = cholesky_solve(system, right)

    intercept = target_mean
    for position, a in enumerate(active):
        coefficient = standardised[position] / scales[a]
        coefficients[a + 1] = coefficient
        intercept -= coefficient * means[a + 1]
    coefficients[0] = intercept

    return coefficients


def predict(coefficients: Sequence[float], row: Sequence[float]) -> float:
    """Apply coefficients from :func:`ridge_from_moments` to one feature row."""
    if len(coefficients) != len(row) + 1:
        raise ValueError(
            f"{len(coefficients)} coefficients do not match {len(row)} features plus an intercept"
        )
    return coefficients[0] + sum(c * x for c, x in zip(coefficients[1:], row, strict=True))
