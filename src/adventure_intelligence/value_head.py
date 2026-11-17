"""A small preference head: learn from owner pairs which evidence predicts a Keep.

Bradley-Terry with a linear utility: P(A kept over B) = sigmoid(w . (x_A - x_B)).
The inputs are the measurements in value_features, the judge panel's agreed
facts and, optionally, a few principal components of the frame embedding. With
a few hundred pairs this is the largest model the data can support, and its
weights can be read: they say which evidence the owner's choices rewarded.

Scaling, the fact vocabulary and the embedding projection are all fitted on
training items only, so nothing about a held-out outing reaches the model.
"""
from __future__ import annotations

import numpy as np

from .value_features import SIGNALS, item_id

VERSION = "klip-value-head-v1"
VARIANTS = ("signals", "full")
L2_GRID = (1.0, 10.0, 100.0)
_FACT = {"agreed_yes": 1.0, "agreed_no": -1.0}  # unclear, disputed, unjudged: 0


def _ids(pair: dict) -> tuple[str, str]:
    return tuple(item_id(s["sourceSha256"], s["startMs"], s["endMs"])
                 for s in (pair["left"], pair["right"]))


def usable(pairs: list[dict], items: dict[str, dict]) -> list[dict]:
    """Pairs whose two sides both have evidence."""
    return [p for p in pairs if all(i in items for i in _ids(p))]


def _spec(train_items: list[dict], variant: str, embedding_dims: int) -> dict:
    spec = {"version": VERSION, "variant": variant, "signals": [], "facts": [],
            "embedding": None}
    for name in SIGNALS:
        values = np.array([i["signals"][name] for i in train_items
                           if i["signals"][name] is not None], dtype=float)
        if len(values) >= 2 and values.std() > 1e-9:
            spec["signals"].append({"name": name, "mean": float(values.mean()),
                                    "std": float(values.std())})
    if variant == "full":
        spec["facts"] = sorted({k for i in train_items if i["facts"] for k in i["facts"]})
        vectors = [i["embedding"] for i in train_items if i["embedding"] is not None]
        dims = min(embedding_dims, len(vectors) - 1)
        if dims >= 1:
            matrix = np.stack(vectors).astype(float)
            centre = matrix.mean(axis=0)
            _, scale, axes = np.linalg.svd(matrix - centre, full_matrices=False)
            keep = [k for k in range(dims) if scale[k] > 1e-9]
            if keep:
                spec["embedding"] = {"centre": centre, "axes": axes[keep],
                                     "scale": scale[keep] / np.sqrt(len(vectors))}
    return spec


def columns(spec: dict) -> list[str]:
    names = [s["name"] for s in spec["signals"]]
    names += ["has:" + g for g in ("motion", "audio", "embedding", "facts")]
    names += ["fact:" + k for k in spec["facts"]]
    if spec["embedding"] is not None:
        names += [f"embedding_pc{k + 1}" for k in range(len(spec["embedding"]["axes"]))]
    return names


def _row(item: dict, spec: dict) -> np.ndarray:
    signals = item["signals"]
    row = [0.0 if signals[s["name"]] is None else (signals[s["name"]] - s["mean"]) / s["std"]
           for s in spec["signals"]]
    # Missing evidence sits at the training mean, and these flags let the model
    # learn that "no audio" differs from "average audio".
    row += [float(signals["motion_mean"] is not None), float(signals["audio_mean"] is not None),
            float(item["embedding"] is not None), float(bool(item["facts"]))]
    row += [_FACT.get((item["facts"] or {}).get(k), 0.0) for k in spec["facts"]]
    if spec["embedding"] is not None:
        e = spec["embedding"]
        row += ([0.0] * len(e["axes"]) if item["embedding"] is None else
                list(((item["embedding"] - e["centre"]) @ e["axes"].T) / e["scale"]))
    return np.array(row, dtype=float)


def _solve(diff: np.ndarray, target: np.ndarray, weight: np.ndarray, l2: float) -> np.ndarray:
    """Weighted ridge logistic regression on feature differences, by damped Newton steps."""
    beta = np.zeros(diff.shape[1])

    def loss(b):
        z = diff @ b
        return float(np.sum(weight * (np.logaddexp(0, z) - target * z)) + 0.5 * l2 * b @ b)

    current = loss(beta)
    for _ in range(50):
        p = 1.0 / (1.0 + np.exp(-(diff @ beta)))
        gradient = diff.T @ (weight * (p - target)) + l2 * beta
        hessian = (diff * (weight * p * (1 - p))[:, None]).T @ diff + l2 * np.eye(len(beta))
        step = np.linalg.solve(hessian, gradient)
        scale = 1.0
        while scale > 1e-4 and loss(beta - scale * step) > current:
            scale /= 2
        beta = beta - scale * step
        updated = loss(beta)
        if current - updated < 1e-9:
            break
        current = updated
    return beta


def fit(pairs: list[dict], items: dict[str, dict], *, variant: str = "full",
        l2: float = 10.0, embedding_dims: int = 8) -> dict | None:
    """Fit on these pairs. Returns None when there is nothing decisive to learn from."""
    if variant not in VARIANTS or not l2 > 0:
        raise ValueError("Unknown head variant or non-positive l2")
    pairs = usable(pairs, items)
    if not any(p["outcome"] != 0.5 for p in pairs):
        return None
    train_ids = sorted({i for p in pairs for i in _ids(p)})
    spec = _spec([items[i] for i in train_ids], variant, embedding_dims)
    rows = {i: _row(items[i], spec) for i in train_ids}
    diff = np.stack([rows[a] - rows[b] for a, b in map(_ids, pairs)])
    beta = _solve(diff, np.array([p["outcome"] for p in pairs]),
                  np.array([p["weight"] for p in pairs]), l2)
    return {"spec": spec, "weights": beta, "l2": l2, "trainPairs": len(pairs),
            "trainItems": len(train_ids)}


def score(model: dict, item: dict) -> float:
    return float(_row(item, model["spec"]) @ model["weights"])


def contributions(model: dict, item: dict, top: int = 3) -> list[dict]:
    """The evidence that moved this item's score most, for the owner to read."""
    parts = _row(item, model["spec"]) * model["weights"]
    order = np.argsort(-np.abs(parts))[:top]
    names = columns(model["spec"])
    return [{"evidence": names[k], "contribution": round(float(parts[k]), 4)}
            for k in order if abs(parts[k]) > 1e-9]


def accuracy(model: dict, pairs: list[dict], items: dict[str, dict]) -> float | None:
    decisive = [p for p in usable(pairs, items) if p["outcome"] != 0.5 and p["evalEligible"]]
    if not decisive:
        return None
    hits = 0.0
    for p in decisive:
        a, b = (score(model, items[i]) for i in _ids(p))
        hits += 0.5 if a == b else float((a > b) == (p["outcome"] == 1.0))
    return hits / len(decisive)


def fit_selecting_l2(pairs: list[dict], items: dict[str, dict], *, variant: str,
                     default_l2: float = 10.0) -> dict | None:
    """Pick l2 by leaving out one training outing at a time, then refit on all of them.

    The outing being evaluated is never in `pairs`, so this choice cannot see it.
    With fewer than three training outings there is too little to choose on.
    """
    groups = sorted({p["group"] for p in usable(pairs, items)
                     if p["outcome"] != 0.5 and p["evalEligible"]})
    chosen = default_l2
    if len(groups) >= 3:
        best = None
        for l2 in L2_GRID:
            scores = []
            for held in groups:
                model = fit([p for p in pairs if p["group"] != held], items,
                            variant=variant, l2=l2)
                value = model and accuracy(model, [p for p in pairs if p["group"] == held], items)
                if value is not None:
                    scores.append(value)
            mean = sum(scores) / len(scores) if scores else 0.0
            if best is None or mean >= best[0] - 1e-12:  # on a tie, the stronger penalty
                best = (mean, l2)
        chosen = best[1]
    return fit(pairs, items, variant=variant, l2=chosen)


def export(model: dict) -> dict:
    """JSON-safe description: readable weights, largest first."""
    names = columns(model["spec"])
    order = np.argsort(-np.abs(model["weights"]))
    return {"version": VERSION, "variant": model["spec"]["variant"], "l2": model["l2"],
            "trainPairs": model["trainPairs"], "trainItems": model["trainItems"],
            "weights": [{"evidence": names[k], "weight": round(float(model["weights"][k]), 5)}
                        for k in order],
            "note": "Weights apply to standardized evidence differences between two "
                    "intervals of one outing. They are learned from owner pairs and are "
                    "not validated beyond the held-out results in the same report."}
