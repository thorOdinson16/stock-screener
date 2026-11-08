"""Time-separated probability and net-return-band research.

Portable JSON models: API inference needs only the Python standard library.
No stock score is relabeled as confidence. Historical checks and deployment
readiness are separate; overlapping observations are evaluated in date blocks.
"""

from collections import defaultdict
import hashlib
import json
import math
from pathlib import Path
import random

DEFAULT_ROOT = Path(__file__).resolve().parent / "results/forecast_lab"
FEATURES = ("rank_percentile", "volatility")
VERSION = "forecast-calibration-v1"


def weights(rows):
    counts = defaultdict(int)
    for r in rows:
        counts[r["signal_date"]] += 1
    return [1 / counts[r["signal_date"]] for r in rows]


def weighted_quantile(values, masses, q):
    ordered = sorted(zip(values, masses))
    total = sum(masses)
    if not ordered or total <= 0:
        raise ValueError("A nonempty weighted sample is required")
    cumulative = 0.
    for value, mass in ordered:
        cumulative += mass
        if cumulative >= q * total:
            return float(value)
    return float(ordered[-1][0])


def fit_probability(rows, target):
    import numpy as np
    sample = [r for r in rows if r.get(target) is not None]
    if len(sample) < 100 or len({r["signal_date"] for r in sample}) < 40:
        return None
    w = np.asarray(weights(sample))
    x = np.asarray([[r[k] for k in FEATURES] for r in sample], dtype=float)
    y = np.asarray([r[target] for r in sample], dtype=float)
    mean = np.average(x, axis=0, weights=w)
    scale = np.sqrt(np.average((x - mean) ** 2, axis=0, weights=w))
    scale = np.maximum(scale, 1e-6)
    design = np.column_stack([np.ones(len(x)), (x - mean) / scale])
    base = float(np.average(y, weights=w))
    beta = np.array([math.log((base + 1e-6) / (1 - base + 1e-6)), 0., 0.])
    penalty = np.diag([1e-6, 1., 1.])
    for _ in range(60):
        p = 1 / (1 + np.exp(-np.clip(design @ beta, -35, 35)))
        gradient = design.T @ (w * (p - y)) + penalty @ beta
        hessian = design.T @ ((w * p * (1 - p))[:, None] * design) + penalty
        delta = np.linalg.solve(hessian, gradient)
        beta -= delta
        if float(np.max(np.abs(delta))) < 1e-7:
            break
    return {"features": list(FEATURES), "mean": mean.tolist(), "scale": scale.tolist(),
            "coefficients": beta.tolist(), "baseline_probability": base,
            "fit_rows": len(sample), "fit_dates": len({r["signal_date"] for r in sample})}


def probability(model, row):
    z = model["coefficients"][0]
    for i, name in enumerate(model["features"]):
        value = (float(row[name]) - model["mean"][i]) / model["scale"][i]
        z += model["coefficients"][i + 1] * value
    return 1 / (1 + math.exp(-max(-35., min(35., z))))


def block_interval(values, block_size, repetitions=500):
    """Non-overlapping contiguous date blocks, not independently resampled stocks."""
    blocks = [sum(values[i:i + block_size]) / block_size
              for i in range(0, len(values) - block_size + 1, block_size)]
    if len(blocks) < 2:
        return {"blocks": len(blocks), "low": None, "high": None}
    rng = random.Random(42)
    samples = sorted(sum(rng.choice(blocks) for _ in blocks) / len(blocks) for _ in range(repetitions))
    return {"blocks": len(blocks), "low": samples[int(.025 * repetitions)], "high": samples[int(.975 * repetitions)]}


def assess_probability(model, rows, target, horizon):
    sample = [r for r in rows if r.get(target) is not None]
    if model is None or not sample:
        return {"passed": False, "reasons": ["Insufficient fit or assessment data"], "reliability": []}
    by_date, bins = defaultdict(list), defaultdict(list)
    baseline = model["baseline_probability"]
    for row in sample:
        p, y = probability(model, row), row[target]
        by_date[row["signal_date"]].append((p, y))
        bins[min(4, int(p * 5))].append((p, y, row["signal_date"]))
    daily, baseline_daily, logs = [], [], []
    for day in sorted(by_date):
        values = by_date[day]
        daily.append(sum((p - y) ** 2 for p, y in values) / len(values))
        baseline_daily.append(sum((baseline - y) ** 2 for _, y in values) / len(values))
        logs.append(sum(-y * math.log(max(p, 1e-12)) - (1 - y) * math.log(max(1 - p, 1e-12)) for p, y in values) / len(values))
    sample_weights = weights(sample)
    weight_by_id = {id(row): weight for row, weight in zip(sample, sample_weights)}
    weighted_bins = defaultdict(list)
    for row in sample:
        p = probability(model, row)
        weighted_bins[min(4, int(p * 5))].append((p, row[target], weight_by_id[id(row)], row["signal_date"]))
    reliability, ece = [], 0.
    total_weight = sum(sample_weights)
    for bucket, values in sorted(weighted_bins.items()):
        mass = sum(w for _, _, w, _ in values)
        predicted = sum(p * w for p, _, w, _ in values) / mass
        observed = sum(y * w for _, y, w, _ in values) / mass
        ece += mass / total_weight * abs(predicted - observed)
        reliability.append({"bin": bucket, "predicted": predicted, "observed": observed,
                            "rows": len(values), "dates": len({d for _, _, _, d in values})})
    difference = [a - b for a, b in zip(daily, baseline_daily)]
    interval = block_interval(difference, horizon)
    reasons = []
    if interval["blocks"] < 8:
        reasons.append("Fewer than eight non-overlapping assessment date blocks")
    if interval["high"] is None or interval["high"] >= 0:
        reasons.append("Brier improvement over the fit-period constant baseline is not established")
    if ece > .05:
        reasons.append("Date-weighted calibration error exceeds five percentage points")
    return {"passed": not reasons, "reasons": reasons, "rows": len(sample), "dates": len(by_date),
            "brier": sum(daily) / len(daily), "baseline_brier": sum(baseline_daily) / len(baseline_daily),
            "log_loss": sum(logs) / len(logs), "calibration_error": ece,
            "brier_difference_block_interval": interval, "reliability": reliability}


def fit_ranges(rows, calibration_rows):
    bands = {}
    for bucket in range(4):
        sample = [r for r in rows if min(3, (r["rank"] - 1) // 5) == bucket]
        if len({r["signal_date"] for r in sample}) < 40:
            return None
        w = weights(sample)
        values = [r["net_return"] for r in sample]
        bands[str(bucket)] = {"low": weighted_quantile(values, w, .1),
                              "median": weighted_quantile(values, w, .5),
                              "high": weighted_quantile(values, w, .9), "rows": len(sample),
                              "dates": len({r["signal_date"] for r in sample})}
    if len({r["signal_date"] for r in calibration_rows}) < 40:
        return None
    scores = []
    for row in calibration_rows:
        band = bands[str(min(3, (row["rank"] - 1) // 5))]
        scores.append(max(band["low"] - row["net_return"], row["net_return"] - band["high"], 0.))
    expansion = weighted_quantile(scores, weights(calibration_rows), .8)
    return {"nominal_coverage": .8, "bands": bands, "expansion": expansion,
            "baseline_median": weighted_quantile([r["net_return"] for r in rows], weights(rows), .5),
            "calibration_dates": len({r["signal_date"] for r in calibration_rows}),
            "method": "Rank-group empirical net-return quantiles, expanded on a separate time block; no exchangeability guarantee is claimed"}


def return_band(model, rank):
    band = model["bands"][str(min(3, (int(rank) - 1) // 5))]
    return {"low": max(-1., band["low"] - model["expansion"]), "median": band["median"],
            "high": band["high"] + model["expansion"], "nominal_coverage": model["nominal_coverage"]}


def assess_ranges(model, rows, horizon):
    if model is None or not rows:
        return {"passed": False, "reasons": ["Insufficient range fit/calibration/assessment data"]}
    days = defaultdict(list)
    for row in rows:
        band = return_band(model, row["rank"])
        inside = band["low"] <= row["net_return"] <= band["high"]
        days[row["signal_date"]].append((float(inside), band["high"] - band["low"],
                                         abs(row["net_return"] - band["median"]),
                                         abs(row["net_return"] - model["baseline_median"])))
    coverage = [sum(v[0] for v in values) / len(values) for _, values in sorted(days.items())]
    width = sum(sum(v[1] for v in values) / len(values) for values in days.values()) / len(days)
    observed = sum(coverage) / len(coverage)
    median_mae = sum(sum(v[2] for v in values) / len(values) for values in days.values()) / len(days)
    baseline_mae = sum(sum(v[3] for v in values) / len(values) for values in days.values()) / len(days)
    ci = block_interval(coverage, horizon)
    reasons = []
    if ci["blocks"] < 8:
        reasons.append("Fewer than eight non-overlapping assessment date blocks")
    if not .75 <= observed <= .90:
        reasons.append("Later-period coverage is outside the 75–90% check for an 80% band")
    if width > .75:
        reasons.append("The average band is wider than 75 percentage points")
    if median_mae > baseline_mae + 1e-12:
        reasons.append("The rank-group median is worse than the fit-period constant median")
    return {"passed": not reasons, "reasons": reasons, "observed_coverage": observed,
            "nominal_coverage": .8, "mean_width": width, "date_blocks": ci,
            "median_absolute_error": median_mae, "baseline_median_absolute_error": baseline_mae,
            "rows": len(rows), "dates": len(days)}


def build_artifact(cases, audit):
    horizons = {}
    for horizon in (21, 63):
        all_rows = [r for r in cases if r["horizon"] == horizon]
        eligible = [r for r in all_rows if r["eligible"]]
        complete = [r for r in eligible if r["status"] == "complete"]
        third_start = min(r["signal_date"] for r in all_rows if r["fold"] == 3)
        fourth_start = min(r["signal_date"] for r in all_rows if r["fold"] == 4)
        fit = [r for r in complete if r["fold"] <= 2 and r["scheduled_exit_date"] < third_start]
        calibrate = [r for r in complete if r["fold"] == 3 and r["scheduled_exit_date"] < fourth_start]
        assess = [r for r in complete if r["fold"] >= 4]
        attempted = [r for r in eligible if r["fold"] >= 4 and r.get("scheduled_exit_date") and
                     r["scheduled_exit_date"] <= audit["last_completed_session"]]
        missing_fraction = 1 - len(assess) / len(attempted) if attempted else 1.
        models, metrics = {}, {}
        for target in ("profit", "outperform"):
            model = fit_probability(fit, target)
            result = assess_probability(model, assess, target, horizon)
            if missing_fraction > .02:
                result["passed"] = False
                result["reasons"].append("More than 2% of eligible matured outcomes are unavailable")
            # Outperformance also requires the full same-date universe benchmark.
            if target == "outperform" and assess:
                availability = sum(r.get(target) is not None for r in assess) / len(assess)
                if availability < .98:
                    result["passed"] = False
                    result["reasons"].append("Less than 98% of assessment cases have a complete universe benchmark")
            models[target], metrics[target] = model, result
        range_model = fit_ranges(fit, calibrate)
        range_metrics = assess_ranges(range_model, assess, horizon)
        if missing_fraction > .02:
            range_metrics["passed"] = False
            range_metrics["reasons"].append("More than 2% of eligible matured outcomes are unavailable")
        horizons[str(horizon)] = {"fit_rows": len(fit), "fit_dates": len({r["signal_date"] for r in fit}),
            "range_calibration_rows": len(calibrate), "assessment_rows": len(assess),
            "assessment_dates": len({r["signal_date"] for r in assess}), "matured_missing_fraction": missing_fraction,
            "split": {"probability_fit_before": third_start, "range_calibration_before": fourth_start,
                      "assessment_from": fourth_start, "assessment_through": audit["last_completed_session"]},
            "probability_models": models, "probability_metrics": metrics, "range_model": range_model, "range_metrics": range_metrics,
            "historical_checks_passed": bool(metrics["profit"]["passed"] and range_metrics["passed"]),
            "production_ready": False}
    return {"version": VERSION, "status": "research_only", "production_changed": False,
            "algorithm_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            "source_status": audit["status"], "price_basis": audit["price_basis"],
            "outcome_audit_sha256": hashlib.sha256(json.dumps(audit, sort_keys=True).encode()).hexdigest(),
            "outcomes_sha256": audit["outcomes_sha256"], "events_digest": audit["events_digest"],
            "lineage": {"models": audit.get("research_config", {}).get("models", {}),
                        "variants": audit.get("research_config", {}).get("variants", {}),
                        "feature_events_digest": audit.get("feature_events_digest")},
            "policy": audit["policy"], "horizons": horizons,
            "limits": ["Earlier out-of-sample scores fit the calibrator; later dates assess it. The history has already been researched.",
                       "Date weighting and block resampling account for overlapping cases; raw stock counts are not independent trials.",
                       "The outcome is next-session-close entry with an 8% stop, time exit, 10 bps fees and 10 bps slippage per side, no flat fee.",
                       "Bands describe net scenario proceeds, not the stock's closing market price at the end of the horizon.",
                       "Provider-adjusted quotes, incomplete action coverage and absent prospective evidence prevent production promotion."]}


def read_artifact(root=DEFAULT_ROOT):
    path = Path(root) / "calibration.json"
    if not path.exists():
        return {"status": "not_available", "horizons": {}, "limits": []}
    artifact = json.loads(path.read_text())
    if artifact.get("algorithm_sha256") != hashlib.sha256(Path(__file__).read_bytes()).hexdigest():
        artifact["status"] = "stale_evidence"
        for item in artifact.get("horizons", {}).values():
            item["production_ready"] = False
    audit_path = Path(root) / "outcome_audit.json"
    if audit_path.exists():
        audit = json.loads(audit_path.read_text())
        current = hashlib.sha256(json.dumps(audit, sort_keys=True).encode()).hexdigest()
        if artifact.get("outcome_audit_sha256") != current:
            artifact["status"] = "stale_evidence"
            for item in artifact.get("horizons", {}).values():
                item["production_ready"] = False
    return artifact


def forecast(artifact, horizon, row=None, reference_price=None, live_context=None):
    result = {"status": "insufficient_evidence", "horizon": horizon, "profit_probability": None,
              "outperform_probability": None, "net_return_band": None, "exit_value_band": None}
    reasons = []
    entry = artifact.get("horizons", {}).get(str(horizon))
    if entry is None:
        reasons.append("No fitted calibration artifact for this horizon")
    else:
        if not entry.get("production_ready") or artifact.get("source_status") != "verified":
            reasons.append("Underlying price/outcome evidence is still research-grade")
        if not live_context or not live_context.get("lineage_matches"):
            reasons.append("The served model has not been matched to this calibration lineage")
        if not live_context or not live_context.get("fresh"):
            reasons.append("A fresh compatible market snapshot is required")
        if live_context is not None and not live_context.get("policy_matches", True):
            reasons.append("Entry/stop/cost or eligibility settings differ from the calibrated scenario")
        try:
            valid_price = reference_price is not None and math.isfinite(reference_price) and reference_price > 0
        except TypeError:
            valid_price = False
        if row is None or not valid_price:
            reasons.append("Reference price and prediction context are unavailable")
    if reasons:
        return {**result, "reasons": reasons}
    try:
        invalid = any(not math.isfinite(float(row[k])) for k in FEATURES) or not 1 <= int(row["rank"]) <= 20
    except (TypeError, ValueError, KeyError, OverflowError):
        invalid = True
    if invalid:
        return {**result, "reasons": ["Prediction context is outside the calibrated top-20 domain"]}
    model = entry["probability_models"]["profit"]
    if model is None:
        return {**result, "reasons": ["Calibration model is incomplete"]}
    if any(abs((float(row[k]) - model["mean"][i]) / model["scale"][i]) > 4 for i, k in enumerate(FEATURES)):
        return {**result, "reasons": ["Market context is outside fitted feature support"]}
    component_reasons = []
    if entry["probability_metrics"]["profit"]["passed"]:
        result["profit_probability"] = probability(model, row)
    else:
        component_reasons.append("Profit-probability validation did not pass")
    if entry["range_metrics"]["passed"]:
        band = return_band(entry["range_model"], row["rank"])
        result.update(net_return_band=band, exit_value_band={k: reference_price * (1 + band[k]) for k in ("low", "median", "high")})
    else:
        component_reasons.append("Return-band validation did not pass")
    if entry["probability_metrics"]["outperform"]["passed"]:
        result["outperform_probability"] = probability(entry["probability_models"]["outperform"], row)
    available = sum(result[k] is not None for k in ("profit_probability", "net_return_band"))
    result["status"] = "available" if available == 2 else "partial" if available else "insufficient_evidence"
    result["reasons"] = component_reasons
    return result
