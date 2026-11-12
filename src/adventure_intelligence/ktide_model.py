"""K-TIDE V1: trainable gap-aware temporal dynamics encoder.

This is a *new adapter trained over frozen frame embeddings*, not a pixel
foundation model or a verified physical action recognizer. No pre-trained
weights are downloaded. torch is an optional training dependency.
"""
from __future__ import annotations

import torch
from torch import nn
from torch.nn import functional as F

MODEL_VERSION = "ktide-gap-aware-temporal-v1"
# Model architecture is unchanged; V1 checkpoints remain inference-compatible.
# Only the self-supervised objective semantics are updated.
ORDER_OBJECTIVE_VERSION = "ktide-matched-local-temporal-reversal-v2"


class KTIDEEncoder(nn.Module):
    """Timestamp-aware local dynamics plus segment-blocked temporal attention.

    Dense frame embedding and signed observed change are projected into a
    token space. Encoder attention never crosses an unsupported time gap.
    Pooling yields one scene representation; per-token output predicts masked
    frozen teacher vectors. No event labels, speeds or species are inferred.
    """

    def __init__(self, input_dim: int, *, hidden: int = 128,
                 layers: int = 2, heads: int = 4,
                 dropout: float = .1, max_gap_ms: int = 7000):
        super().__init__()
        if (not 8 <= input_dim <= 8192 or not 32 <= hidden <= 1024
                or not 1 <= layers <= 8 or not 1 <= heads <= 16
                or hidden % heads or not 0 <= dropout < .5
                or not 250 <= max_gap_ms <= 60000):
            raise ValueError("Invalid K-TIDE model configuration")
        self.config = {
            "input_dim": input_dim, "hidden": hidden, "layers": layers,
            "heads": heads, "dropout": dropout, "max_gap_ms": max_gap_ms,
        }
        self.max_gap_ms, self.heads = max_gap_ms, heads
        self.mask_token = nn.Parameter(torch.zeros(input_dim))
        self.frame_proj = nn.Sequential(
            nn.Linear(input_dim * 2 + 6, hidden),
            nn.LayerNorm(hidden), nn.GELU(),
        )
        layer = nn.TransformerEncoderLayer(
            d_model=hidden, nhead=heads, dim_feedforward=hidden * 3,
            dropout=dropout, activation="gelu", batch_first=True,
            norm_first=True,
        )
        self.temporal = nn.TransformerEncoder(layer, num_layers=layers,
                                              enable_nested_tensor=False)
        self.norm = nn.LayerNorm(hidden)
        self.pool_gate = nn.Linear(hidden, 1)
        self.reconstruct = nn.Linear(hidden, input_dim)
        self.order_head = nn.Linear(hidden, 1)

    def forward(self, x: torch.Tensor, t_ms: torch.Tensor,
                valid: torch.Tensor, visible: torch.Tensor | None = None) -> dict:
        if x.ndim != 3 or t_ms.shape != x.shape[:2] or valid.shape != t_ms.shape:
            raise ValueError("Expected [batch,time,feature] with matched times/mask")
        if x.shape[-1] != self.config["input_dim"] or not valid.any(dim=1).all():
            raise ValueError("Unexpected feature dimension or empty sequence")
        if visible is None:
            visible = valid
        if visible.shape != valid.shape:
            raise ValueError("Visible frame mask shape mismatch")
        # Prevent reconstruction target leakage: all dynamics are derived
        # from *masked input*, not the original target features.
        inp = torch.where(visible.unsqueeze(-1), x, self.mask_token)
        dt = torch.cat((torch.zeros_like(t_ms[:, :1]),
                        t_ms[:, 1:] - t_ms[:, :-1]), dim=1)
        contiguous = (dt > 0) & (dt <= self.max_gap_ms) & valid
        contiguous[:, 0] = False
        contiguous[:, 1:] &= valid[:, :-1]
        previous = torch.cat((inp[:, :1], inp[:, :-1]), dim=1)
        delta = (inp - previous) * contiguous.unsqueeze(-1)
        elapsed = ((t_ms - t_ms[:, :1]).float() / 1000.0).clamp(min=0)
        secs = dt.float().clamp(min=0) / 1000.0
        pos = torch.stack((
            torch.sin(elapsed / 2), torch.cos(elapsed / 2),
            torch.sin(elapsed / 15), torch.cos(elapsed / 15),
            torch.log1p(secs), contiguous.float(),
        ), dim=-1)
        tokens = self.frame_proj(torch.cat((inp, delta, pos), dim=-1))
        # A large gap is a new segment: no attention across missing time.
        seg = torch.cumsum((dt > self.max_gap_ms).long(), dim=1)
        blocked = (seg[:, :, None] != seg[:, None, :]) | (~valid[:, None, :])
        # Padding queries may self-attend rather than softmax an all-masked row.
        eye = torch.eye(x.shape[1], dtype=torch.bool, device=x.device)
        blocked = blocked & ~eye[None, :, :]
        mask = blocked.repeat_interleave(self.heads, dim=0)
        h = self.norm(self.temporal(tokens, mask=mask))
        scores = self.pool_gate(h).squeeze(-1).masked_fill(~valid, -1e4)
        pooled = (torch.softmax(scores, dim=1).unsqueeze(-1) * h).sum(dim=1)
        return {
            "tokens": h, "embedding": F.normalize(pooled, dim=-1),
            "reconstruction": self.reconstruct(h),
            "orderLogits": self.order_head(pooled).squeeze(-1),
            "contiguous": contiguous,
        }


def sample_visibility(valid: torch.Tensor, ratio: float) -> torch.Tensor:
    if not 0.05 <= ratio <= .85:
        raise ValueError("mask ratio must be in [.05,.85]")
    visible = valid & (torch.rand(valid.shape, device=valid.device) >= ratio)
    for row in range(valid.shape[0]):
        indices = torch.nonzero(valid[row], as_tuple=False).flatten()
        if len(indices) < 4:
            raise ValueError("At least four observed frames per training sample")
        if not (valid[row] & ~visible[row]).any():
            visible[row, indices[-1]] = False
        if not visible[row].any():
            visible[row, indices[0]] = True
    return visible


def group_safe_contrastive(z1: torch.Tensor, z2: torch.Tensor,
                           groups: list[str], temperature: float = .12) -> torch.Tensor:
    """In-batch positives are different masks of one clip.

    Other clips from the SAME trip are excluded from negative denominators.
    If no independent negative exists, the contrastive term is exactly zero.
    """
    batch = z1.shape[0]
    if z1.shape != z2.shape or len(groups) != batch or not .02 <= temperature <= 1:
        raise ValueError("Invalid contrastive batch")
    if batch < 2 or len(set(groups)) < 2:
        return z1.sum() * 0.0
    z = torch.cat((F.normalize(z1, dim=-1),
                   F.normalize(z2, dim=-1)), dim=0)
    logits = z @ z.T / temperature
    sources = groups + groups
    allowed = torch.tensor(
        [[(i != j and (sources[i] != sources[j] or
                         j == (i + batch) % (2 * batch)))
          for j in range(2 * batch)] for i in range(2 * batch)],
        dtype=torch.bool, device=z.device)
    selected = logits.masked_fill(~allowed, -1e4)
    positive_index = (torch.arange(2 * batch, device=z.device) + batch) % (2 * batch)
    return (torch.logsumexp(selected, dim=1) -
            logits[torch.arange(2 * batch, device=z.device), positive_index]).mean()


def reverse_connected_observations(x: torch.Tensor, t_ms: torch.Tensor,
                                   valid: torch.Tensor, visible: torch.Tensor,
                                   max_gap_ms: int) -> tuple[torch.Tensor, torch.Tensor, torch.Tensor]:
    """Reverse only consecutive observed runs, with identical mask exposure.

    Reversing across an unsupported long temporal gap would fabricate a
    chronological relation. Visibility is reversed *with* the features to
    prevent an order-classification shortcut from unmatched masking.
    Only runs with >=3 connected frames qualify for order supervision.
    """
    if (x.ndim != 3 or x.shape[:2] != t_ms.shape
            or t_ms.shape != valid.shape or visible.shape != valid.shape):
        raise ValueError("Invalid temporal reversal shapes")
    t = t_ms.detach().cpu().tolist()
    present = valid.detach().cpu().tolist()
    flipped_x, flipped_visible = x.clone(), visible.clone()
    eligible = []
    for b, mask in enumerate(present):
        start = None
        segments = []
        for i, observed in enumerate(mask):
            if not observed:
                if start is not None:
                    segments.append((start, i))
                    start = None
                continue
            if start is None:
                start = i
            elif not 0 < t[b][i] - t[b][i-1] <= max_gap_ms:
                segments.append((start, i))
                start = i
        if start is not None:
            segments.append((start, len(mask)))
        for first, last in segments:
            if last - first >= 2:
                flipped_x[b, first:last] = x[b, first:last].flip(0)
                flipped_visible[b, first:last] = visible[b, first:last].flip(0)
        eligible.append(any(last - first >= 3 for first, last in segments))
    return (flipped_x, flipped_visible,
            torch.tensor(eligible, dtype=torch.bool, device=x.device))


def self_supervised_loss(model: KTIDEEncoder, x: torch.Tensor,
                         t_ms: torch.Tensor, valid: torch.Tensor,
                         groups: list[str], *, mask_ratio: float = .3) -> tuple[torch.Tensor, dict]:
    v1, v2 = sample_visibility(valid, mask_ratio), sample_visibility(valid, mask_ratio)
    a = model(x, t_ms, valid, v1)
    b = model(x, t_ms, valid, v2)
    missing = valid & ~v1
    pred = F.normalize(a["reconstruction"][missing], dim=-1)
    target = F.normalize(x[missing], dim=-1)
    masked_loss = (1.0 - (pred * target).sum(dim=-1)).mean()
    contrastive = group_safe_contrastive(a["embedding"], b["embedding"], groups)
    # Both chronological and reversed views see EXACTLY the same feature
    # content and masking exposure. Reverse within actual connected runs
    # only: no pseudo-order labels across disconnected observation gaps.
    reversed_x, reversed_visible, order_eligible = reverse_connected_observations(
        x, t_ms, valid, v1, model.max_gap_ms)
    if bool(order_eligible.any()):
        reverse = model(reversed_x, t_ms, valid, reversed_visible)
        positive = F.binary_cross_entropy_with_logits(
            a["orderLogits"][order_eligible],
            torch.ones_like(a["orderLogits"][order_eligible]))
        negative = F.binary_cross_entropy_with_logits(
            reverse["orderLogits"][order_eligible],
            torch.zeros_like(reverse["orderLogits"][order_eligible]))
        order_loss = .5 * (positive + negative)
    else:
        # Some sparse histograms contain no actual sequence of 3 connected
        # frames. They can train reconstruction, not chronological ordering.
        order_loss = a["orderLogits"].sum() * 0.0
    total = masked_loss + .15 * contrastive + .1 * order_loss
    return total, {
        "maskedCosine": float(masked_loss.detach()),
        "groupContrastive": float(contrastive.detach()),
        "orderControl": float(order_loss.detach()),
        "orderEligibleSamples": int(order_eligible.sum().item()),
        "orderTotalSamples": int(valid.shape[0]),
        "orderObjectiveVersion": ORDER_OBJECTIVE_VERSION,
    }
