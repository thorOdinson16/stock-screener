"""Fixed model and train-only preprocessing for information ablations."""
import numpy as np
from .tournament_models import date_weights

class InformationModel:
    def __init__(self, features):
        self.features = features

    def fit(self, frame):
        from sklearn.impute import SimpleImputer
        from lightgbm import LGBMRegressor
        self.imputer = SimpleImputer(strategy='median', add_indicator=True, keep_empty_features=True)
        x = self.imputer.fit_transform(frame[self.features])
        self.model = LGBMRegressor(num_leaves=7, n_estimators=100, learning_rate=.04, min_child_samples=100,
                                  reg_lambda=10., random_state=42, n_jobs=4, verbosity=-1,
                                  deterministic=True, force_col_wise=True)
        self.model.fit(x, frame.net_return.to_numpy(), sample_weight=date_weights(frame))
        return self

    def predict(self, frame):
        result = self.model.booster_.predict(self.imputer.transform(frame[self.features]))
        if not np.isfinite(result).all():
            raise ValueError('Nonfinite predictions')
        return result

