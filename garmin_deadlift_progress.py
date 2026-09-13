#!/usr/bin/env python3
"""Fetch and analyze Garmin Connect barbell lift strength sets."""

from __future__ import annotations

import argparse
import json
import math
import os
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from getpass import getpass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
os.environ.setdefault("MPLCONFIGDIR", str(ROOT / ".matplotlib"))

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt
import pandas as pd


TOKENSTORE = ROOT / ".garmin_tokens"
EXPORTS = ROOT / "exports"
RAW_DIR = EXPORTS / "raw"
CACHE_DIR = EXPORTS / "cache"
PROCESSED_DIR = EXPORTS / "processed"
PLOTS_DIR = EXPORTS / "plots"
CACHE_JSON = CACHE_DIR / "garmin_strength_cache.json"
DASHBOARD_DATA = ROOT / "dashboard" / "data" / "strength-progress.json"

@dataclass
class WeightResult:
    kg: float | None
    conversion: str
    source_key: str | None
    source_value: Any
    source_unit: str | None


@dataclass(frozen=True)
class ExerciseSpec:
    slug: str
    label: str
    aliases: tuple[str, ...]
    search_terms: tuple[str, ...]
    color: str


EXERCISES = (
    ExerciseSpec(
        slug="deadlift",
        label="Barbell Deadlift",
        aliases=(
            "BARBELL_DEADLIFT",
            "Barbell Deadlift",
            "barbell dead lift",
            "martwy ciąg ze sztangą",
            "martwy ciag ze sztanga",
        ),
        search_terms=("deadlift", "martwy ciąg", "martwy ciag"),
        color="#2878B5",
    ),
    ExerciseSpec(
        slug="back_squat",
        label="Barbell Back Squat",
        aliases=(
            "BARBELL_BACK_SQUAT",
            "Barbell Back Squat",
            "back squat with barbell",
            "przysiad ze sztangą z tyłu",
            "przysiad ze sztanga z tylu",
        ),
        search_terms=("squat", "przysiad"),
        color="#2E8B57",
    ),
    ExerciseSpec(
        slug="bench_press",
        label="Barbell Bench Press",
        aliases=(
            "BARBELL_BENCH_PRESS",
            "Barbell Bench Press",
            "bench press with barbell",
            "wyciskanie sztangi leżąc",
            "wyciskanie sztangi lezac",
            "wyciskanie sztangi na ławce płaskiej",
            "wyciskanie sztangi na lawce plaskiej",
        ),
        search_terms=("bench press", "wyciskanie"),
        color="#C44E52",
    ),
)


def ensure_dirs() -> None:
    for directory in (TOKENSTORE, RAW_DIR, CACHE_DIR, PROCESSED_DIR, PLOTS_DIR):
        directory.mkdir(parents=True, exist_ok=True)
    try:
        TOKENSTORE.chmod(0o700)
    except OSError:
        pass


def load_garmin() -> Any:
    try:
        from garminconnect import (  # type: ignore
            Garmin,
            GarminConnectAuthenticationError,
            GarminConnectConnectionError,
            GarminConnectTooManyRequestsError,
        )
    except ImportError as exc:
        raise SystemExit(
            "Brakuje zaleznosci. Uruchom: python -m pip install -r requirements.txt"
        ) from exc

    try:
        client = Garmin()
        client.login(str(TOKENSTORE))
        print("Zalogowano z lokalnych tokenow.")
        return client
    except GarminConnectTooManyRequestsError:
        raise
    except GarminConnectAuthenticationError:
        print("Brak waznych tokenow. Zaloguj sie lokalnie w terminalu.")
    except GarminConnectConnectionError as exc:
        raise SystemExit(
            "Nie udalo sie polaczyc z Garmin Connect podczas odczytu tokenow: "
            f"{exc}"
        ) from exc

    while True:
        email = os.getenv("GARMIN_EMAIL") or input("Garmin email: ").strip()
        password = os.getenv("GARMIN_PASSWORD") or getpass("Garmin password: ")
        client = Garmin(
            email=email,
            password=password,
            prompt_mfa=lambda: input("Garmin MFA code: ").strip(),
        )
        try:
            client.login(str(TOKENSTORE))
            print(f"Login OK. Tokeny zapisane w: {TOKENSTORE}")
            return client
        except GarminConnectAuthenticationError:
            print("Logowanie nieudane. Sprobuj ponownie.")
        except GarminConnectTooManyRequestsError:
            raise


def activity_id(activity: dict[str, Any]) -> str:
    for key in ("activityId", "activityIdStr", "id"):
        value = activity.get(key)
        if value is not None:
            return str(value)
    raise ValueError(f"Nie umiem znalezc ID aktywnosci: {activity}")


def activity_name(activity: dict[str, Any]) -> str:
    for key in ("activityName", "activityNameOriginal", "name"):
        value = activity.get(key)
        if value:
            return str(value)
    return ""


def activity_start(activity: dict[str, Any]) -> str:
    for key in ("startTimeLocal", "startTimeGMT", "beginTimestamp", "date"):
        value = activity.get(key)
        if value:
            return str(value)
    return ""


def normalize_activity_type(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(value or "").strip().lower()).strip("_")


def activity_type_keys(activity: dict[str, Any]) -> set[str]:
    values: list[Any] = []
    for key in ("activityTypeKey", "activityTypeName", "sportType", "typeKey"):
        values.append(activity.get(key))
    for container_key in ("activityType", "activityTypeDTO"):
        container = activity.get(container_key)
        if isinstance(container, dict):
            for key in ("typeKey", "typeName", "key", "name"):
                values.append(container.get(key))
    return {normalize_activity_type(value) for value in values if value}


def read_json_if_exists(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(f"{path.suffix}.tmp")
    temporary.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False, default=str),
        encoding="utf-8",
    )
    temporary.replace(path)


def parsed_activity_date(activity: dict[str, Any]) -> date | None:
    value = activity_start(activity)
    try:
        return date.fromisoformat(value[:10])
    except (TypeError, ValueError):
        return None


def fetch_raw(
    days: int,
    activity_type: str,
    raw_json: Path,
    cache_json: Path = CACHE_JSON,
    overlap_days: int = 7,
    full_refresh: bool = False,
) -> Path:
    ensure_dirs()
    client = load_garmin()
    end = date.today()
    latest = RAW_DIR / "garmin_strength_raw_latest.json"
    cached = None if full_refresh else read_json_if_exists(cache_json)
    if cached is None and not full_refresh:
        cached = read_json_if_exists(latest)
    dashboard_seed = None if cached or full_refresh else read_json_if_exists(DASHBOARD_DATA)

    last_sync_value = None
    if cached:
        last_sync_value = cached.get("sync", {}).get("last_successful_sync")
        last_sync_value = last_sync_value or cached.get("fetched_at")
    elif dashboard_seed:
        last_sync_value = dashboard_seed.get("generatedAt")
    try:
        last_sync_date = date.fromisoformat(str(last_sync_value)[:10])
    except (TypeError, ValueError):
        last_sync_date = None

    if (cached or dashboard_seed) and last_sync_date:
        start = last_sync_date - timedelta(days=max(overlap_days, 0))
        sync_mode = "incremental"
    else:
        start = end - timedelta(days=days)
        sync_mode = "full"

    requested_type = normalize_activity_type(activity_type)
    parent_types = {"strength_training": "fitness_equipment"}
    query_type = parent_types.get(requested_type, requested_type)

    print(
        f"Pobieram aktywnosci API typu '{query_type}' od {start} do {end}; "
        f"docelowy podtyp: '{requested_type}'..."
    )
    parent_activities = client.get_activities_by_date(
        start.isoformat(), end.isoformat(), query_type, sortorder="asc"
    )
    if query_type == requested_type:
        fresh_activities = parent_activities
    else:
        fresh_activities = [
            activity
            for activity in parent_activities
            if requested_type in activity_type_keys(activity)
        ]
    print(
        f"API zwrocilo {len(parent_activities)} aktywnosci typu '{query_type}'; "
        f"po filtrze '{requested_type}': {len(fresh_activities)}"
    )

    cached_activities = cached.get("activities", []) if cached else []
    cached_sets = cached.get("exercise_sets_by_activity", {}) if cached else {}
    retained_activities = {
        activity_id(activity): activity
        for activity in cached_activities
        if parsed_activity_date(activity) is None
        or parsed_activity_date(activity) < start
    }
    fresh_by_id = {activity_id(activity): activity for activity in fresh_activities}
    merged_activities = {**retained_activities, **fresh_by_id}
    merged_sets = {
        str(aid): payload
        for aid, payload in cached_sets.items()
        if str(aid) in retained_activities
    }

    raw: dict[str, Any] = {
        "fetched_at": datetime.now().isoformat(timespec="seconds"),
        "date_range": {
            "start": min(
                (
                    parsed_activity_date(activity)
                    for activity in merged_activities.values()
                    if parsed_activity_date(activity) is not None
                ),
                default=start,
            ).isoformat(),
            "end": end.isoformat(),
            "initial_days": days,
        },
        "activity_type": requested_type,
        "api_activity_type": query_type,
        "api_activity_count": len(parent_activities),
        "activities": [],
        "exercise_sets_by_activity": merged_sets,
        "errors": [],
        "sync": {
            "mode": sync_mode,
            "requested_start": start.isoformat(),
            "requested_end": end.isoformat(),
            "overlap_days": overlap_days,
            "cached_activity_count_before": len(cached_activities),
            "fresh_activity_count": len(fresh_activities),
            "last_successful_sync": datetime.now().isoformat(timespec="seconds"),
            "dashboard_seeded": bool(dashboard_seed),
        },
        "library": {
            "method": "python-garminconnect",
            "sets_endpoint": "/activity-service/activity/{activityId}/exerciseSets",
        },
    }

    for index, activity in enumerate(fresh_activities, start=1):
        aid = activity_id(activity)
        print(
            f"[{index}/{len(fresh_activities)}] Pobieram serie dla aktywnosci {aid}..."
        )
        try:
            raw["exercise_sets_by_activity"][aid] = client.get_activity_exercise_sets(aid)
        except Exception as exc:  # noqa: BLE001 - keep activity-level failures in raw JSON
            raw["errors"].append(
                {
                    "activity_id": aid,
                    "activity_name": activity_name(activity),
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                }
            )

    raw["activities"] = sorted(
        merged_activities.values(), key=lambda activity: activity_start(activity)
    )
    raw["sync"]["merged_activity_count"] = len(raw["activities"])
    write_json_atomic(cache_json, raw)
    write_json_atomic(raw_json, raw)
    write_json_atomic(latest, raw)
    print(
        f"Tryb synchronizacji: {sync_mode}; cache: {len(cached_activities)} -> "
        f"{len(raw['activities'])} aktywnosci."
    )
    print(f"Zapisano surowy JSON: {raw_json}")
    return raw_json


def normalize_text(value: Any) -> str:
    text = str(value or "").strip().lower()
    text = text.replace("_", " ").replace("-", " ")
    text = re.sub(r"\s+", " ", text)
    return text


def matching_exercise(name: str) -> ExerciseSpec | None:
    normalized = normalize_text(name)
    for spec in EXERCISES:
        if normalized in {normalize_text(alias) for alias in spec.aliases}:
            return spec
    return None


def iter_dicts(value: Any) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    if isinstance(value, dict):
        found.append(value)
        for child in value.values():
            found.extend(iter_dicts(child))
    elif isinstance(value, list):
        for child in value:
            found.extend(iter_dicts(child))
    return found


def get_nested_strings(value: Any, key_terms: tuple[str, ...]) -> list[str]:
    names: list[str] = []
    if isinstance(value, dict):
        for key, child in value.items():
            key_norm = normalize_text(key)
            if any(term in key_norm for term in key_terms) and isinstance(
                child, str | int | float
            ):
                names.append(str(child))
            names.extend(get_nested_strings(child, key_terms))
    elif isinstance(value, list):
        for child in value:
            names.extend(get_nested_strings(child, key_terms))
    return names


def get_exercise_names(record: dict[str, Any]) -> list[str]:
    keys = ("exercise", "name", "category", "sub category", "subcategory", "movement")
    names = get_nested_strings(record, keys)
    cleaned: list[str] = []
    for name in names:
        if name and normalize_text(name) not in {"none", "null"}:
            cleaned.append(name)
    return list(dict.fromkeys(cleaned))


def get_named_exercises(record: dict[str, Any]) -> list[str]:
    names: list[str] = []
    exercises = record.get("exercises")
    if isinstance(exercises, list):
        for exercise in exercises:
            if not isinstance(exercise, dict):
                continue
            for key in ("name", "exerciseName", "exercise_name"):
                value = exercise.get(key)
                if value:
                    names.append(str(value))
    return list(dict.fromkeys(names))


def looks_like_set(record: dict[str, Any]) -> bool:
    keys = {normalize_text(key) for key in record}
    has_reps = any("rep" in key for key in keys)
    has_weight = any("weight" in key or "kilogram" in key or "pound" in key for key in keys)
    has_exercise = bool(get_exercise_names(record))
    return has_exercise and (has_reps or has_weight)


def extract_candidate_sets(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, dict) and isinstance(payload.get("exerciseSets"), list):
        top = [item for item in payload["exerciseSets"] if isinstance(item, dict)]
        nested = [item for item in iter_dicts(payload) if looks_like_set(item)]
        combined = top + nested
    else:
        combined = [item for item in iter_dicts(payload) if looks_like_set(item)]

    unique: list[dict[str, Any]] = []
    seen: set[int] = set()
    for item in combined:
        marker = id(item)
        if marker not in seen and looks_like_set(item):
            seen.add(marker)
            unique.append(item)
    return unique


def first_number(record: dict[str, Any], patterns: tuple[str, ...]) -> tuple[float | None, str | None]:
    for key, value in record.items():
        key_norm = normalize_text(key)
        if any(pattern in key_norm for pattern in patterns):
            number = to_float(value)
            if number is not None:
                return number, key
    return None, None


def to_float(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        if math.isfinite(float(value)):
            return float(value)
        return None
    if isinstance(value, str):
        match = re.search(r"-?\d+(?:[.,]\d+)?", value)
        if match:
            return float(match.group(0).replace(",", "."))
    return None


def detect_unit(record: dict[str, Any], source_key: str | None) -> str | None:
    candidates: list[str] = []
    for key, value in record.items():
        key_norm = normalize_text(key)
        if "unit" in key_norm and ("weight" in key_norm or "mass" in key_norm):
            candidates.append(str(value))
        elif key_norm in {"unit", "weightunit", "weight unit"}:
            candidates.append(str(value))
    if source_key:
        candidates.append(source_key)
    joined = " ".join(normalize_text(item) for item in candidates)
    tokens = set(joined.split())
    if tokens.intersection({"pound", "pounds", "lbs", "lb"}):
        return "lb"
    if tokens.intersection({"gram", "grams", "g"}) and not tokens.intersection(
        {"kilogram", "kilograms", "kg", "kgs"}
    ):
        return "g"
    if tokens.intersection({"kilogram", "kilograms", "kg", "kgs"}):
        return "kg"
    return None


def extract_weight_kg(record: dict[str, Any]) -> WeightResult:
    prioritized = (
        ("weight kg", "weightinkg", "kilogram"),
        ("weight lb", "weight lbs", "weightinpounds", "pound"),
        ("weight gram", "weightingrams", "grams"),
        ("weight",),
    )
    for patterns in prioritized:
        value, key = first_number(record, patterns)
        if value is None:
            continue
        unit = detect_unit(record, key)
        key_norm = normalize_text(key)
        if "gram" in key_norm and "kilogram" not in key_norm:
            unit = "g"
        elif "pound" in key_norm or "lbs" in key_norm or " lb" in f" {key_norm}":
            unit = "lb"
        elif "kg" in key_norm or "kilogram" in key_norm:
            unit = "kg"

        if unit == "lb":
            return WeightResult(round(value * 0.45359237, 3), "lb_to_kg", key, value, unit)
        if unit == "g":
            return WeightResult(round(value / 1000, 3), "g_to_kg", key, value, unit)
        if unit is None and key_norm == "weight":
            return WeightResult(
                round(value / 1000, 3),
                "garmin_exercise_sets_grams_to_kg",
                key,
                value,
                "g",
            )
        return WeightResult(value, "assumed_or_reported_kg", key, value, unit)
    return WeightResult(None, "missing", None, None, None)


def extract_reps(record: dict[str, Any]) -> tuple[int | None, str | None]:
    value, key = first_number(
        record,
        (
            "reps",
            "rep count",
            "repetition count",
            "repetitioncount",
            "repetitions",
            "repeat",
        ),
    )
    if value is None:
        return None, key
    return int(value), key


def read_raw(raw_json: Path) -> dict[str, Any]:
    with raw_json.open("r", encoding="utf-8") as file:
        return json.load(file)


def analyze(raw_json: Path) -> dict[str, Any]:
    ensure_dirs()
    raw = read_raw(raw_json)
    activities = {activity_id(item): item for item in raw.get("activities", [])}
    rows_by_exercise: dict[str, list[dict[str, Any]]] = {
        spec.slug: [] for spec in EXERCISES
    }
    candidate_names: dict[str, Counter[str]] = {
        spec.slug: Counter() for spec in EXERCISES
    }
    matched_names: dict[str, Counter[str]] = {
        spec.slug: Counter() for spec in EXERCISES
    }
    all_exercise_names: Counter[str] = Counter()
    missing_weight: Counter[str] = Counter()
    missing_reps: Counter[str] = Counter()
    nonpositive_reps: Counter[str] = Counter()
    conversions: dict[str, Counter[str]] = {
        spec.slug: Counter() for spec in EXERCISES
    }
    sample_raw_records: dict[str, list[dict[str, Any]]] = {
        spec.slug: [] for spec in EXERCISES
    }
    set_numbers: defaultdict[tuple[str, str], int] = defaultdict(int)

    for aid, payload in raw.get("exercise_sets_by_activity", {}).items():
        activity = activities.get(str(aid), {})
        candidates = extract_candidate_sets(payload)
        for record in candidates:
            names = get_named_exercises(record)
            for name in names:
                all_exercise_names[name] += 1
                normalized = normalize_text(name)
                for spec in EXERCISES:
                    if any(normalize_text(term) in normalized for term in spec.search_terms):
                        candidate_names[spec.slug][name] += 1

            matches: dict[str, tuple[ExerciseSpec, str]] = {}
            for name in names:
                spec = matching_exercise(name)
                if spec is not None:
                    matches.setdefault(spec.slug, (spec, name))
            if not matches:
                continue

            weight = extract_weight_kg(record)
            reps, reps_key = extract_reps(record)
            tonnage = None
            if weight.kg is not None and reps is not None:
                tonnage = weight.kg * reps

            for slug, (spec, matched_name) in matches.items():
                conversions[slug][weight.conversion] += 1
                matched_names[slug][matched_name] += 1
                quality: list[str] = []
                if weight.kg is None:
                    missing_weight[slug] += 1
                    quality.append("missing_weight")
                if reps is None:
                    missing_reps[slug] += 1
                    quality.append("missing_reps")
                elif reps <= 0:
                    nonpositive_reps[slug] += 1
                    quality.append("nonpositive_reps")

                set_numbers[(str(aid), slug)] += 1
                if len(sample_raw_records[slug]) < 5:
                    sample_raw_records[slug].append(record)

                rows_by_exercise[slug].append(
                    {
                        "workout_datetime": activity_start(activity),
                        "activity_id": aid,
                        "activity_name": activity_name(activity),
                        "garmin_exercise_name": matched_name,
                        "all_detected_exercise_names": " | ".join(names),
                        "set_number": set_numbers[(str(aid), slug)],
                        "weight_kg": weight.kg,
                        "reps": reps,
                        "set_tonnage_kg": tonnage,
                        "weight_conversion": weight.conversion,
                        "weight_source_key": weight.source_key,
                        "weight_source_value": weight.source_value,
                        "weight_source_unit": weight.source_unit,
                        "reps_source_key": reps_key,
                        "garmin_message_index": record.get("messageIndex"),
                        "garmin_workout_step_index": record.get("wktStepIndex"),
                        "data_quality": ";".join(quality) if quality else "ok",
                    }
                )

    columns = [
        "workout_datetime",
        "activity_id",
        "activity_name",
        "garmin_exercise_name",
        "set_number",
        "weight_kg",
        "reps",
        "set_tonnage_kg",
        "weight_conversion",
        "weight_source_key",
        "weight_source_value",
        "weight_source_unit",
        "reps_source_key",
        "garmin_message_index",
        "garmin_workout_step_index",
        "data_quality",
        "all_detected_exercise_names",
    ]
    cleanup_obsolete_plots()
    reports: dict[str, Any] = {}
    for spec in EXERCISES:
        df = pd.DataFrame(rows_by_exercise[spec.slug], columns=columns)
        for column in ("weight_kg", "set_tonnage_kg"):
            df[column] = pd.to_numeric(df[column], errors="coerce").round(3)

        sets_csv = PROCESSED_DIR / f"{spec.slug}_sets.csv"
        df.to_csv(sets_csv, index=False)
        summary_df = build_summary(df)
        summary_csv = PROCESSED_DIR / f"{spec.slug}_workouts_summary.csv"
        summary_df.to_csv(summary_csv, index=False)
        plot_path = make_max_weight_plot(summary_df, spec)

        exercise_report = {
            "exercise": spec.label,
            "sets_csv": str(sets_csv),
            "summary_csv": str(summary_csv),
            "plot": str(plot_path) if plot_path else None,
            "rows": len(df),
            "workouts": int(df["activity_id"].nunique()) if not df.empty else 0,
            "matched_names": dict(matched_names[spec.slug]),
            "candidate_names": dict(candidate_names[spec.slug]),
            "conversion_counts": dict(conversions[spec.slug]),
            "missing_weight_sets": missing_weight[spec.slug],
            "missing_reps_sets": missing_reps[spec.slug],
            "nonpositive_reps_sets": nonpositive_reps[spec.slug],
            "sample_raw_records": sample_raw_records[spec.slug],
            "progress": progress_summary(summary_df),
        }
        report_path = PROCESSED_DIR / f"{spec.slug}_analysis_report.json"
        exercise_report["report_json"] = str(report_path)
        report_path.write_text(
            json.dumps(exercise_report, indent=2, ensure_ascii=False, default=str),
            encoding="utf-8",
        )
        reports[spec.slug] = exercise_report
        print_human_report(exercise_report)

    report = {
        "raw_json": str(raw_json),
        "raw_fetch_errors": raw.get("errors", []),
        "all_unique_exercise_names_count": len(all_exercise_names),
        "exercises": reports,
    }
    dashboard_path = write_dashboard_data(raw, reports)
    report["dashboard_data"] = str(dashboard_path)
    report_path = PROCESSED_DIR / "strength_analysis_report.json"
    report["report_json"] = str(report_path)
    report_path.write_text(
        json.dumps(report, indent=2, ensure_ascii=False, default=str),
        encoding="utf-8",
    )
    if report["raw_fetch_errors"]:
        print("\nBledy pobierania serii:")
        for err in report["raw_fetch_errors"]:
            print(f"  - {err['activity_id']}: {err['error_type']} {err['error']}")
    return report


def json_number(value: Any) -> float | None:
    if value is None or pd.isna(value):
        return None
    return round(float(value), 3)


def json_date(value: Any) -> str | None:
    if value is None or (not isinstance(value, str) and pd.isna(value)):
        return None
    text = str(value).strip()
    return text[:10] if text else None


def write_dashboard_data(
    raw: dict[str, Any], reports: dict[str, dict[str, Any]]
) -> Path:
    existing = read_json_if_exists(DASHBOARD_DATA)
    use_dashboard_seed = bool(raw.get("sync", {}).get("dashboard_seeded"))
    refresh_start = str(raw.get("sync", {}).get("requested_start", ""))[:10]
    existing_by_slug = {
        item.get("slug"): item
        for item in (existing or {}).get("exercises", [])
        if isinstance(item, dict)
    }
    exercises: list[dict[str, Any]] = []
    for spec in EXERCISES:
        report = reports[spec.slug]
        summary = pd.read_csv(report["summary_csv"])
        if not summary.empty:
            summary["sort_date"] = pd.to_datetime(summary["date"], errors="coerce")
            summary = summary.sort_values("sort_date", na_position="last")
        workouts = [
            {
                "date": str(row["date"])[:10],
                "sets": row["sets"],
                "setCount": int(row["set_count"]),
                "maxWeightKg": json_number(row["max_weight_kg"]),
                "tonnageKg": json_number(row["total_tonnage_kg"]),
            }
            for _, row in summary.iterrows()
        ]
        if use_dashboard_seed:
            old_workouts = [
                workout
                for workout in existing_by_slug.get(spec.slug, {}).get("workouts", [])
                if str(workout.get("date", ""))[:10] < refresh_start
            ]
            workouts = sorted(old_workouts + workouts, key=lambda item: item["date"])

        for workout in workouts:
            if "setCount" not in workout:
                workout["setCount"] = len(
                    [part for part in str(workout.get("sets", "")).split(",") if part.strip()]
                )

        weighted = [workout for workout in workouts if workout.get("maxWeightKg") is not None]
        record = max(weighted, key=lambda item: item["maxWeightKg"], default=None)
        first = weighted[0] if weighted else None
        last = weighted[-1] if weighted else None
        exercises.append(
            {
                "slug": spec.slug,
                "label": spec.label,
                "color": spec.color,
                "setCount": sum(int(workout["setCount"]) for workout in workouts),
                "workoutCount": len(workouts),
                "firstWorkout": first["date"] if first else None,
                "lastWorkout": last["date"] if last else None,
                "maxWeightChangeKg": json_number(
                    last["maxWeightKg"] - first["maxWeightKg"]
                    if first and last
                    else None
                ),
                "recordWeightKg": record["maxWeightKg"] if record else None,
                "recordWeightDate": record["date"] if record else None,
                "workouts": workouts,
            }
        )

    coverage_starts = [
        value
        for value in (
            (existing or {}).get("coverageStart") if use_dashboard_seed else None,
            raw.get("date_range", {}).get("start"),
        )
        if value
    ]
    payload = {
        "generatedAt": raw.get("fetched_at"),
        "coverageStart": min(coverage_starts) if coverage_starts else None,
        "coverageEnd": raw.get("date_range", {}).get("end"),
        "exercises": exercises,
    }
    write_json_atomic(DASHBOARD_DATA, payload)
    print(f"Zapisano publiczne dane dashboardu: {DASHBOARD_DATA}")
    return DASHBOARD_DATA


def build_summary(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame(
            columns=[
                "date",
                "activity_id",
                "activity_name",
                "sets",
                "set_count",
                "max_weight_kg",
                "total_tonnage_kg",
            ]
        )

    work = df.copy()
    work["workout_datetime_sort"] = pd.to_datetime(work["workout_datetime"], errors="coerce")
    work = work.sort_values(["workout_datetime_sort", "set_number"], na_position="last")

    rows: list[dict[str, Any]] = []
    for aid, group in work.groupby("activity_id", sort=False):
        set_strings = []
        for _, row in group.iterrows():
            weight = row.get("weight_kg")
            reps = row.get("reps")
            if pd.notna(weight) and pd.notna(reps):
                weight_fmt = f"{float(weight):g}"
                set_strings.append(f"{weight_fmt}×{int(reps)}")
            elif pd.notna(weight):
                set_strings.append(f"{float(weight):g}×?")
            elif pd.notna(reps):
                set_strings.append(f"?×{int(reps)}")
            else:
                set_strings.append("?×?")
        first = group.iloc[0]
        rows.append(
            {
                "date": first["workout_datetime"],
                "activity_id": aid,
                "activity_name": first["activity_name"],
                "sets": ", ".join(set_strings),
                "set_count": int(len(group)),
                "max_weight_kg": round(group["weight_kg"].max(skipna=True), 3),
                "total_tonnage_kg": round(group["set_tonnage_kg"].sum(min_count=1), 3),
            }
        )
    return pd.DataFrame(rows)


def cleanup_obsolete_plots() -> None:
    for filename in (
        "deadlift_all_sets_weight.png",
        "deadlift_tonnage_by_workout.png",
    ):
        path = PLOTS_DIR / filename
        if path.exists():
            path.unlink()


def make_max_weight_plot(
    summary_df: pd.DataFrame, spec: ExerciseSpec
) -> Path | None:
    path = PLOTS_DIR / f"{spec.slug}_max_weight_by_workout.png"
    if summary_df.empty:
        if path.exists():
            path.unlink()
        return None

    summary = summary_df.copy()
    summary["dt"] = pd.to_datetime(summary["date"], errors="coerce")
    summary = summary.dropna(subset=["dt", "max_weight_kg"])
    if summary.empty:
        if path.exists():
            path.unlink()
        return None

    plt.style.use("seaborn-v0_8-whitegrid")
    fig, ax = plt.subplots(figsize=(14, 6))
    ax.plot(
        summary["dt"],
        summary["max_weight_kg"],
        marker="o",
        linewidth=2,
        color=spec.color,
    )
    ax.set_title(f"{spec.label} - maksymalny ciężar w treningu")
    ax.set_xlabel("Data treningu")
    ax.set_ylabel("Maksymalny ciężar [kg]")
    fig.autofmt_xdate()
    fig.tight_layout()
    fig.savefig(path, dpi=160)
    plt.close(fig)
    return path


def progress_summary(summary_df: pd.DataFrame) -> dict[str, Any]:
    if summary_df.empty:
        return {}
    summary = summary_df.copy()
    summary["dt"] = pd.to_datetime(summary["date"], errors="coerce")
    summary = summary.sort_values("dt", na_position="last")
    first = summary.iloc[0]
    last = summary.iloc[-1]
    weight_values = summary["max_weight_kg"].dropna()
    max_weight_record = (
        summary.loc[weight_values.idxmax()] if not weight_values.empty else None
    )
    return {
        "first_workout": first["date"],
        "last_workout": last["date"],
        "max_weight_change_kg": none_safe_delta(first["max_weight_kg"], last["max_weight_kg"]),
        "weight_record_kg": (
            max_weight_record["max_weight_kg"] if max_weight_record is not None else None
        ),
        "weight_record_date": (
            max_weight_record["date"] if max_weight_record is not None else None
        ),
        "workout_count": int(len(summary)),
    }


def none_safe_delta(first: Any, last: Any) -> float | None:
    if pd.isna(first) or pd.isna(last):
        return None
    return float(last) - float(first)


def print_human_report(report: dict[str, Any]) -> None:
    print(f"\n=== {report['exercise']} ===")
    print(f"Serie: {report['rows']}")
    print(f"Treningi: {report['workouts']}")
    print(f"Konwersje jednostek: {report['conversion_counts']}")
    print(f"Braki ciezaru: {report['missing_weight_sets']}")
    print(f"Braki powtorzen: {report['missing_reps_sets']}")
    print(f"Serie z liczba powtorzen <= 0: {report['nonpositive_reps_sets']}")
    if report["matched_names"]:
        print("Dopasowane nazwy cwiczenia:")
        for name, count in sorted(report["matched_names"].items()):
            print(f"  - {name}: {count}")
    if report["candidate_names"]:
        excluded = {
            name: count
            for name, count in report["candidate_names"].items()
            if name not in report["matched_names"]
        }
        if excluded:
            print("Podobne nazwy pominiete przez filtr:")
        for name, count in sorted(excluded.items()):
            print(f"  - {name}: {count}")
    if report["progress"]:
        print("Progres:")
        for key, value in report["progress"].items():
            print(f"  - {key}: {value}")
    print("\nPrzykladowe surowe rekordy serii:")
    for record in report["sample_raw_records"]:
        print(json.dumps(record, ensure_ascii=False, default=str)[:1200])


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    def add_pull_arguments(command_parser: argparse.ArgumentParser) -> None:
        command_parser.add_argument("--days", type=int, default=365)
        command_parser.add_argument("--activity-type", default="strength_training")
        command_parser.add_argument("--overlap-days", type=int, default=7)
        command_parser.add_argument("--cache-json", type=Path, default=CACHE_JSON)
        command_parser.add_argument("--full-refresh", action="store_true")
        command_parser.add_argument(
            "--raw-json",
            type=Path,
            default=RAW_DIR / f"garmin_strength_raw_{date.today().isoformat()}.json",
        )

    fetch_parser = sub.add_parser("fetch", help="Pobierz i scal surowe dane z cache'em")
    add_pull_arguments(fetch_parser)

    analyze_parser = sub.add_parser("analyze", help="Przelicz lokalny surowy JSON")
    analyze_parser.add_argument("--raw-json", type=Path, required=True)

    pull_parser = sub.add_parser(
        "pull", help="Pobierz nowe dane, przelicz wyniki i zbuduj dashboard"
    )
    add_pull_arguments(pull_parser)

    all_parser = sub.add_parser("all", help="Alias polecenia pull")
    add_pull_arguments(all_parser)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    ensure_dirs()
    if args.command == "fetch":
        fetch_raw(
            args.days,
            args.activity_type,
            args.raw_json,
            args.cache_json,
            args.overlap_days,
            args.full_refresh,
        )
        return 0
    if args.command == "analyze":
        analyze(args.raw_json)
        return 0
    if args.command in {"pull", "all"}:
        raw_path = fetch_raw(
            args.days,
            args.activity_type,
            args.raw_json,
            args.cache_json,
            args.overlap_days,
            args.full_refresh,
        )
        analyze(raw_path)
        return 0
    raise AssertionError(args.command)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\nPrzerwano.")
        raise SystemExit(130)
