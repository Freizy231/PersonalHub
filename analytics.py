"""Snapshot comparisons. Missing observations are never treated as zero."""
from datetime import datetime, timezone

import pandas as pd

STALE_HOURS = 48
MAX_BASELINE_LAG_HOURS = 24


def utc_microseconds(value):
    """Refine SQLite's millisecond time ordering without changing historical text."""
    if value is None:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)  # SQLite's legacy CURRENT_TIMESTAMP is UTC.
        delta = parsed.astimezone(timezone.utc)-datetime(1970, 1, 1, tzinfo=timezone.utc)
        return (delta.days*86400+delta.seconds)*1000000+delta.microseconds
    except (ValueError, TypeError, OverflowError):
        return None


def load_repos(con, as_of=None):
    con.create_function("utc_microseconds", 1, utc_microseconds, deterministic=True)
    frame = pd.read_sql_query("""
        SELECT r.*, s0.stars AS current_stars, s0.forks AS forks,
               s0.open_issues AS open_issues, s0.captured_at AS last_captured,
               s7.stars AS stars_7d_ago, s7.captured_at AS baseline_captured
        FROM repos r
        LEFT JOIN snapshots s0 ON s0.id=(
            SELECT id FROM snapshots WHERE repo_id=r.id
            ORDER BY julianday(captured_at) DESC,utc_microseconds(captured_at) DESC,id DESC LIMIT 1)
        LEFT JOIN snapshots s7 ON s7.id=(
            SELECT id FROM snapshots WHERE repo_id=r.id
              AND julianday(captured_at)<=julianday(s0.captured_at)-7
              AND utc_microseconds(captured_at)<=utc_microseconds(s0.captured_at)-604800000000
            ORDER BY julianday(captured_at) DESC,utc_microseconds(captured_at) DESC,id DESC LIMIT 1)
        """, con)
    now = pd.Timestamp(as_of) if as_of is not None else pd.Timestamp.now(tz="UTC")
    now = now.tz_localize("UTC") if now.tzinfo is None else now.tz_convert("UTC")
    for name in ("created_at", "updated_at", "last_captured", "baseline_captured"):
        frame[name] = pd.to_datetime(frame[name], utc=True, errors="coerce", format="mixed")
    for name in ("current_stars", "forks", "open_issues", "stars_7d_ago"):
        frame[name] = pd.to_numeric(frame[name], errors="coerce").astype("Int64")
    frame["span_days"] = (frame.last_captured-frame.baseline_captured).dt.total_seconds()/86400
    frame["age_hours"] = (now-frame.last_captured).dt.total_seconds()/3600
    frame["observed_growth"] = frame.current_stars-frame.stars_7d_ago
    frame["quality"] = "可比较"
    frame.loc[frame.span_days > 7+MAX_BASELINE_LAG_HOURS/24, "quality"] = "基线过旧"
    frame.loc[frame.baseline_captured.isna(), "quality"] = "历史不足"
    frame.loc[frame.age_hours > STALE_HOURS, "quality"] = "数据过期"
    frame.loc[frame.age_hours < 0, "quality"] = "时间异常"
    frame.loc[frame.last_captured.isna(), "quality"] = "尚无快照"
    frame["growth_7d"] = frame.observed_growth.where(frame.quality == "可比较")
    frame["growth_rate"] = (frame.growth_7d/frame.stars_7d_ago.where(frame.stars_7d_ago>0)*100).astype("Float64")
    return frame.sort_values(["growth_7d", "current_stars", "full_name"], ascending=[False,False,True], na_position="last")


def filter_repos(frame, keyword="", category="全部", min_stars=0,
                 growth_enabled=False, min_growth=100, quality="全部", recent=False, as_of=None):
    view = frame.copy()
    if keyword.strip():
        matches = pd.Series(False, index=view.index)
        for column in ("full_name", "description", "description_zh", "topics"):
            matches |= view[column].fillna("").str.contains(keyword.strip(), case=False, regex=False)
        view = view[matches]
    if category != "全部":
        view = view[view.category == category]
    view = view[view.current_stars.ge(min_stars).fillna(min_stars == 0)]
    if quality != "全部":
        view = view[view.quality == quality]
    if growth_enabled:
        view = view[view.growth_7d.ge(min_growth).fillna(False)]
    if recent:
        now = pd.Timestamp(as_of) if as_of is not None else pd.Timestamp.now(tz="UTC")
        now = now.tz_localize("UTC") if now.tzinfo is None else now.tz_convert("UTC")
        view = view[view.created_at >= now-pd.Timedelta(days=90)]
    return view
