"""Fixed-budget context, categorical, excess-return and probability learners."""

import numpy as np
import pandas as pd
from sklearn.impute import SimpleImputer
from sklearn.preprocessing import OneHotEncoder, StandardScaler
from sklearn.linear_model import Ridge, LogisticRegression
from .tournament_models import date_weights


class Learner:
    def __init__(self, family, objective, features, categorical=True):
        self.family, self.objective, self.features, self.categorical = family, objective, features, categorical

    def inputs(self, frame, fit=False):
        if fit:
            self.imputer = SimpleImputer(strategy="median", add_indicator=True, keep_empty_features=True)
            x = self.imputer.fit_transform(frame[self.features])
        else:
            x = self.imputer.transform(frame[self.features])
        if self.categorical:
            industry = frame[["industry"]].fillna("Unknown").astype(str)
            if self.family == "catboost":
                x = pd.DataFrame(x, columns=[f"numeric_{i}" for i in range(x.shape[1])])
                x["industry"] = industry.industry.to_numpy()
            else:
                if fit:
                    self.encoder = OneHotEncoder(handle_unknown="ignore", sparse_output=False)
                    encoded = self.encoder.fit_transform(industry)
                else:
                    encoded = self.encoder.transform(industry)
                x = np.column_stack([x, encoded])
        return x

    def fit(self, frame):
        from lightgbm import LGBMRanker, LGBMClassifier, LGBMRegressor
        from catboost import CatBoostRanker, CatBoostClassifier, Pool
        frame = frame.sort_values(["trade_date", "symbol"])
        label = {"rank": "net_return", "profit": "profit_label", "outperform": "outperform_label", "excess": "excess_net"}[self.objective]
        values = frame[label]
        if self.objective == "rank":
            group = frame.groupby("trade_date")[label]
            y = ((group.rank(method="average") - 1) / (group.transform("size") - 1).clip(lower=1)).to_numpy() - .5
        else:
            y = values.to_numpy()
        x, weights = self.inputs(frame, True), date_weights(frame)
        if self.family == "linear":
            self.scaler = StandardScaler()
            x = self.scaler.fit_transform(x)
            self.model = Ridge(alpha=100.).fit(x, y, sample_weight=weights)
        elif self.family == "lightgbm":
            common = dict(num_leaves=7, n_estimators=100, learning_rate=.04, min_child_samples=100,
                          reg_lambda=10., random_state=42, n_jobs=4, verbosity=-1, deterministic=True, force_col_wise=True)
            if self.objective == "rank":
                self.model = LGBMRanker(objective="lambdarank", label_gain=list(range(10)), lambdarank_truncation_level=20, **common)
                self.model.fit(x, np.minimum(9, np.floor((y + .5) * 10)).astype(int),
                               group=frame.groupby("trade_date", sort=False).size().to_numpy(), sample_weight=weights)
            else:
                cls = LGBMClassifier if self.objective in ("profit", "outperform") else LGBMRegressor
                self.model = cls(**common).fit(x, y, sample_weight=weights)
        else:
            common = dict(depth=4, iterations=150, learning_rate=.04, l2_leaf_reg=10., random_seed=42,
                          thread_count=4, has_time=True, verbose=False, allow_writing_files=False)
            cats = ["industry"] if self.categorical else []
            if self.objective == "rank":
                self.model = CatBoostRanker(loss_function="QueryRMSE", **common)
                self.model.fit(Pool(x, label=y, cat_features=cats, group_id=frame.trade_date.to_numpy(), group_weight=weights.astype(np.float32)))
            else:
                self.model = CatBoostClassifier(loss_function="Logloss", **common)
                self.model.fit(x, y, cat_features=cats, sample_weight=weights)
        return self

    def predict(self, frame):
        x = self.inputs(frame)
        if self.family == "linear":
            x = self.scaler.transform(x)
        if self.objective in ("profit", "outperform"):
            result = self.model.booster_.predict(x) if self.family == "lightgbm" else self.model.predict_proba(x)[:, 1]
        else:
            result = self.model.booster_.predict(x) if self.family == "lightgbm" else self.model.predict(x)
        result = np.asarray(result).reshape(-1)
        if not np.isfinite(result).all():
            raise ValueError("Nonfinite model predictions")
        return result


def fit_sigmoid(rows, target):
    from .calibration import weights
    sample = [r for r in rows if r.get(target) is not None]
    if len(sample) < 100 or len({r["signal_date"] for r in sample}) < 40 or len({r[target] for r in sample}) < 2:
        return None
    w = np.asarray(weights(sample))
    x = np.asarray([r["logit_probability"] for r in sample])
    y = np.asarray([r[target] for r in sample])
    mean = np.average(x, weights=w)
    scale = max(1e-6, np.sqrt(np.average((x - mean) ** 2, weights=w)))
    model = LogisticRegression(C=1., max_iter=200).fit(((x - mean) / scale).reshape(-1, 1), y, sample_weight=w)
    return {"features": ["logit_probability"], "mean": [float(mean)], "scale": [float(scale)],
            "coefficients": [float(model.intercept_[0]), float(model.coef_[0, 0])],
            "baseline_probability": float(np.average(y, weights=w)), "fit_rows": len(sample),
            "fit_dates": len({r["signal_date"] for r in sample})}
