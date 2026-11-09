"""Bounded CPU models and chronological sample preparation for research only."""

import numpy as np
import pandas as pd


GRIDS = {
    "linear": [{"alpha": 1.0}, {"alpha": 100.0}],
    "lightgbm": [{"num_leaves": 7, "n_estimators": 100}, {"num_leaves": 15, "n_estimators": 150}],
    "catboost": [{"depth": 4, "iterations": 150}, {"depth": 6, "iterations": 150}],
}


def training_rows(frame, target, train_dates, before):
    """Maturity purge applies even to stopped trades that exited early."""
    end = "legacy_maturity" if target == "legacy" else "scheduled_exit_date"
    label = "excess_ret_21d" if target == "legacy" else "net_return"
    mask = frame.trade_date.isin(train_dates) & frame[label].notna() & frame[end].notna() & (frame[end] < before)
    return frame.loc[mask].sort_values(["trade_date", "symbol"])


def rank_labels(frame, target):
    label = "excess_ret_21d" if target == "legacy" else "net_return"
    group = frame.groupby("trade_date")[label]
    # Average ranks preserve ties, especially identical stop/cost outcomes.
    percentile = (group.rank(method="average") - 1) / (group.transform("size") - 1).clip(lower=1)
    return percentile.to_numpy(dtype=np.float32) - .5


def date_weights(frame):
    weights = 1 / frame.groupby("trade_date").symbol.transform("size").to_numpy(dtype=float)
    return weights / weights.mean()


def fit_model(family, params, frame, features, target, seed=42):
    x = frame[features].to_numpy(dtype=np.float32)
    if not np.isfinite(x).all():
        raise ValueError("Models must receive the same finite feature rows")
    y, weights = rank_labels(frame, target), date_weights(frame)
    if family == "linear":
        from sklearn.pipeline import make_pipeline
        from sklearn.preprocessing import StandardScaler
        from sklearn.linear_model import Ridge
        model = make_pipeline(StandardScaler(), Ridge(**params))
        model.fit(x, y, ridge__sample_weight=weights)
    elif family == "lightgbm":
        from lightgbm import LGBMRanker
        # LambdaRank needs nonnegative integer relevance labels, grouped in
        # contiguous date blocks. Linear gains avoid exponential tail weighting.
        relevance = np.minimum(9, np.floor((y + .5) * 10)).astype(int)
        model = LGBMRanker(objective="lambdarank", learning_rate=.04, min_child_samples=100,
                           reg_lambda=10., label_gain=list(range(10)), lambdarank_truncation_level=20,
                           deterministic=True, force_col_wise=True, random_state=seed, n_jobs=4,
                           verbosity=-1, **params)
        model.fit(x, relevance, group=frame.groupby("trade_date", sort=False).size().to_numpy(), sample_weight=weights)
    elif family == "catboost":
        from catboost import CatBoostRanker, Pool
        model = CatBoostRanker(loss_function="QueryRMSE", learning_rate=.04, l2_leaf_reg=10.,
                              random_seed=seed, thread_count=4, has_time=True, verbose=False,
                              allow_writing_files=False, **params)
        pool = Pool(x, label=y, group_id=frame.trade_date.to_numpy(), group_weight=weights.astype(np.float32))
        model.fit(pool)
    else:
        raise ValueError(f"Unknown model family: {family}")
    return model


def predict(model, frame, features):
    scores = np.asarray(model.predict(frame[features].to_numpy(dtype=np.float32))).reshape(-1)
    if not np.isfinite(scores).all():
        raise ValueError("Nonfinite predictions invalidate the candidate")
    return scores


def sequences(frame, features, sessions, length=20):
    """Past-only windows with exact common-calendar continuity and finite inputs."""
    calendar = {d: i for i, d in enumerate(sessions)}
    values = frame[features].to_numpy(dtype=np.float32)
    tensor = np.zeros((len(frame), len(features), length), dtype=np.float32)
    valid = np.zeros(len(frame), dtype=bool)
    for _, part in frame.groupby("symbol", sort=False):
        part = part.sort_values("trade_date")
        ids = part.index.to_numpy()
        if len(ids) < length:
            continue
        windows = np.lib.stride_tricks.sliding_window_view(values[ids], length, axis=0)
        positions = part.trade_date.map(calendar).to_numpy(dtype=float)
        contiguous = np.lib.stride_tricks.sliding_window_view(positions, length)
        good = np.isfinite(windows).all(axis=(1, 2)) & (np.diff(contiguous, axis=1) == 1).all(axis=1)
        ends = ids[length - 1:][good]
        tensor[ends] = windows[good]
        valid[ends] = True
    return tensor, valid


def make_tcn(channels):
    import torch
    from torch import nn
    class CausalLayer(nn.Module):
        def __init__(self, incoming, outgoing, dilation):
            super().__init__()
            self.padding = 2 * dilation
            self.conv = nn.Conv1d(incoming, outgoing, kernel_size=3, dilation=dilation)
        def forward(self, x):
            return torch.relu(self.conv(nn.functional.pad(x, (self.padding, 0))))
    return nn.Sequential(CausalLayer(channels, 16, 1), CausalLayer(16, 16, 2),
                         CausalLayer(16, 16, 4), CausalLayer(16, 16, 8),
                         LastStep(), nn.Linear(16, 1))


def LastStep():
    from torch import nn
    class Last(nn.Module):
        def forward(self, x):
            return x[:, :, -1]
    return Last()


class SequenceModel:
    """Fixed small TCN; scaling fits training windows only, no test early stopping."""
    def __init__(self, seed=42, epochs=3):
        self.seed, self.epochs = seed, epochs

    def fit(self, tensor, indices, labels, weights):
        import torch
        torch.set_num_threads(4)
        torch.manual_seed(self.seed)
        torch.use_deterministic_algorithms(True)
        train = tensor[indices]
        self.mean = train.mean(axis=(0, 2), keepdims=True)
        self.scale = np.maximum(train.std(axis=(0, 2), keepdims=True), 1e-5)
        x = torch.from_numpy(np.clip((train - self.mean) / self.scale, -10, 10))
        y = torch.from_numpy(np.asarray(labels, dtype=np.float32))
        w = torch.from_numpy(np.asarray(weights, dtype=np.float32))
        self.model = make_tcn(tensor.shape[1])
        optimizer = torch.optim.AdamW(self.model.parameters(), lr=.001, weight_decay=.01)
        generator = torch.Generator().manual_seed(self.seed)
        self.model.train()
        for _ in range(self.epochs):
            for batch in torch.randperm(len(indices), generator=generator).split(2048):
                optimizer.zero_grad()
                error = self.model(x[batch]).flatten() - y[batch]
                loss = (error.square() * w[batch]).mean()
                loss.backward()
                torch.nn.utils.clip_grad_norm_(self.model.parameters(), 1.)
                optimizer.step()
        return self

    def predict(self, tensor, indices):
        import torch
        self.model.eval()
        values = []
        with torch.no_grad():
            for batch in np.array_split(indices, max(1, math_ceil(len(indices) / 4096))):
                x = np.clip((tensor[batch] - self.mean) / self.scale, -10, 10)
                values.extend(self.model(torch.from_numpy(x)).flatten().numpy().tolist())
        return np.asarray(values)


def math_ceil(value):
    import math
    return math.ceil(value)
