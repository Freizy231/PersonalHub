"""Tech Intel Hub: local, read-oriented research dashboard."""
from contextlib import closing
import pandas as pd
import streamlit as st

from analytics import filter_repos, load_repos
from db import DB_PATH, get_connection, init_db
from descriptions import EMPTY_DESCRIPTION, PENDING_DESCRIPTION

st.set_page_config(page_title="Tech Intel Hub", page_icon="📡", layout="wide")
try:
    init_db()
except Exception:
    st.error("数据库初始化失败。请检查路径、权限、备份和程序版本；现有数据库不会被重建覆盖。")
    st.stop()

STATUS_LABELS = {"running": "运行中", "success": "成功", "partial": "部分成功", "failed": "失败",
                 "interrupted": "已中断", "legacy": "旧版记录（状态未核实）"}
PHASE_LABELS = {"starting": "准备", "discovery": "发现候选仓库", "refresh": "更新仓库快照",
                "translation": "更新中文简介", "unknown": "未知"}


def number(value, signed=False, decimal=False):
    if pd.isna(value):
        return "—"
    return format(float(value), "+,.1f" if signed and decimal else ",.1f") if decimal else format(int(value), "+,d" if signed else ",d")


def timestamp(value):
    return "—" if pd.isna(value) else pd.Timestamp(value).strftime("%Y-%m-%d %H:%M:%S UTC")


@st.fragment(run_every="3s")
def collection_progress():
    """Poll committed progress only; never invoke the collector from a page rerun."""
    with closing(get_connection()) as con:
        row = con.execute("SELECT * FROM collection_runs ORDER BY id DESC LIMIT 1").fetchone()
    if row is None:
        st.info("尚无采集任务。")
        return
    status = row["status"]
    phase = PHASE_LABELS.get(row["phase"], row["phase"])
    st.write(f"任务 #{row['id']} · {STATUS_LABELS.get(status,status)} · {phase}")
    total, done = row["progress_total"], row["progress_done"]
    if total:
        st.progress(min(1.0, max(0.0, done/total)), text=f"当前阶段：{done:,} / {total:,}")
    st.caption(f"新增 {row['discovered']:,} · 本次匹配 {row['matched']:,} · 新快照 {row['updated']:,} · 错误 {row['errors']:,}")
    if row["message"]:
        # Old versions stored arbitrary exception strings; only new structured messages are shown live.
        st.text(row["message"] if status != "legacy" else "旧任务缺少可靠状态，保留原始记录供核对。")
    if row["current_item"]:
        st.text(row["current_item"])
    heartbeat = pd.to_datetime(row["heartbeat_at"], utc=True, errors="coerce")
    if status == "running" and (pd.isna(heartbeat) or pd.Timestamp.now(tz="UTC")-heartbeat > pd.Timedelta(minutes=2)):
        st.warning("超过两分钟未收到进度更新，请检查采集进程。这里只提示失联，不推断任务已经结束。")
    if row["finished_at"] is None and status == "legacy":
        st.warning("旧版任务未收尾；无法仅凭此记录判断是否仍在运行。")
    st.caption(f"最近心跳：{timestamp(heartbeat)} · 每 3 秒更新。列表数据请使用下方按钮刷新。")


st.title("📡 Tech Intel Hub")
st.caption("GitHub 项目发现、增长监控与研究筛选 · 所有时间均为 UTC")
with st.expander("采集实时进度", expanded=True):
    collection_progress()
if st.button("刷新项目数据", key="refresh_data"):
    st.rerun()

try:
    with closing(get_connection()) as con:
        repos = load_repos(con)
        runs = pd.read_sql_query("SELECT * FROM collection_runs ORDER BY id DESC LIMIT 20", con)
    repos["introduction"] = repos.description_zh.where(
        repos.description_zh_source.eq(repos.description.fillna("")) & repos.description_zh.fillna("").str.strip().ne(""))
    repos["introduction"] = repos.introduction.fillna(
        repos.description.fillna("").map(lambda text: EMPTY_DESCRIPTION if not text.strip() else PENDING_DESCRIPTION))
except Exception:
    st.error("读取数据失败，请检查数据库状态。")
    st.stop()

with st.sidebar:
    st.header("筛选条件")
    keyword = st.text_input("关键词 / 项目名称", placeholder="例如：恶意软件分析、AI agent")
    category_options = sorted(repos.category.dropna().unique().tolist())
    category = st.selectbox("研究领域", ["全部"]+category_options)
    growth_enabled = st.checkbox("启用周增长筛选", value=False)
    min_growth = st.number_input("近 7 天增长至少", value=100, step=25, disabled=not growth_enabled)
    min_stars = st.number_input("当前 Star 至少", min_value=0, value=0, step=100)
    quality = st.selectbox("数据质量", ["全部", "可比较", "历史不足", "基线过旧", "数据过期", "时间异常", "尚无快照"])
    only_recent = st.checkbox("仅显示近 90 天创建的项目", value=False)
    st.divider()
    st.caption("默认保留历史不足及负增长项目；启用周增长筛选后，只显示有效比较结果。")
    st.caption("独立终端启动采集，进度显示在终端及页面顶部。")
    st.code(".venv/bin/python collector.py")

view = filter_repos(repos, keyword, category, min_stars, growth_enabled, min_growth, quality, only_recent)
valid = view[view.quality == "可比较"]
c1, c2, c3, c4 = st.columns(4)
c1.metric("已追踪项目（全库）", f"{len(repos):,}")
c2.metric("筛选后项目", f"{len(view):,}")
c3.metric("可比较项目（筛选后）", f"{len(valid):,}")
c4.metric("平均近 7 天增长（可比较）", number(valid.growth_7d.mean(), signed=True, decimal=True))
st.caption("近 7 天增长为快照估算：截至最新快照向前取 7 天基线，实际间隔须为 7–8 天，最新数据须在 48 小时内；缺失值不按零计算。均值包含负增长。")

tab1, tab2, tab3 = st.tabs(["📋 项目与增长榜", "📊 趋势分析", "🛠 采集记录"])
with tab1:
    if repos.empty:
        st.info("数据库尚无项目。编辑已有 .env，或仅在不存在时从 .env.example 创建，再运行采集命令。")
    elif view.empty:
        st.info("没有符合条件的项目，请调整筛选条件。")
    if not view.empty:
        display = view[["full_name", "introduction", "category", "language", "current_stars", "growth_7d",
                        "growth_rate", "quality", "span_days", "last_captured", "baseline_captured",
                        "forks", "open_issues", "is_archived", "html_url"]].copy()
        display["html_url"] = display.full_name.map(lambda name: f"https://github.com/{name}")
        display = display.rename(columns={"full_name": "项目", "introduction": "中文简介", "category": "领域", "language": "语言",
            "current_stars": "Star", "growth_7d": "近7天增长（估算）", "growth_rate": "增长率%", "quality": "数据质量",
            "span_days": "比较间隔（天）", "last_captured": "最新采集（UTC）", "baseline_captured": "基线时间（UTC）",
            "forks": "Fork", "open_issues": "Open Issues", "is_archived": "已归档", "html_url": "链接"})
        st.dataframe(display, hide_index=True, column_config={
            "链接": st.column_config.LinkColumn("GitHub", display_text="打开仓库"),
            "中文简介": st.column_config.TextColumn(width="large"),
            "增长率%": st.column_config.NumberColumn(format="%.1f%%"),
            "比较间隔（天）": st.column_config.NumberColumn(format="%.3f"),
        })
        selected = st.selectbox("查看项目历史", view.full_name.tolist())
        row = view[view.full_name == selected].iloc[0]
        st.subheader(selected)
        # Do not interpolate repository text into Markdown or executable HTML.
        st.link_button("在 GitHub 查看仓库", f"https://github.com/{selected}")
        st.text(row.introduction)
        if pd.notna(row.description) and row.description.strip():
            with st.expander("查看原始简介"):
                st.text(row.description)
        st.caption(f"{row.language if pd.notna(row.language) else '未知语言'} · {row.category} · 创建于 {timestamp(row.created_at)}")
        c1, c2, c3 = st.columns(3)
        c1.metric("当前 Star", number(row.current_stars))
        c2.metric("近 7 天增长（估算）", number(row.growth_7d, signed=True))
        c3.metric("Fork", number(row.forks))
        st.write(f"数据质量：{row.quality}")
        st.caption(f"基线：{timestamp(row.baseline_captured)} → 最新：{timestamp(row.last_captured)}；实际间隔 {number(row.span_days,decimal=True)} 天")
        if pd.notna(row.observed_growth) and row.quality != "可比较":
            st.info(f"两次历史观察之间的增量为 {number(row.observed_growth,signed=True)}；因数据质量不达标，不计入周增长。")
        if pd.notna(row.stars_7d_ago) and row.stars_7d_ago == 0:
            st.caption("基线为零，增长率无定义，故不显示百分比。")
        with closing(get_connection()) as con:
            hist = pd.read_sql_query("SELECT captured_at,stars,forks FROM snapshots WHERE repo_id=? ORDER BY julianday(captured_at),id", con, params=(int(row.id),))
        if not hist.empty:
            hist.captured_at = pd.to_datetime(hist.captured_at, utc=True, errors="coerce", format="mixed")
            hist = hist.dropna(subset=["captured_at"])
            st.line_chart(hist.set_index("captured_at")[["stars", "forks"]])

with tab2:
    st.caption("所有图表均使用当前筛选结果；增长图仅包含可比较项目。")
    chart = valid.nlargest(20, "growth_7d")
    if chart.empty:
        st.info("当前筛选结果没有可比较的周增长数据。")
    else:
        st.subheader("近 7 天增长前 20 项（含负增长）")
        st.bar_chart(chart.set_index("full_name")["growth_7d"])
    created = view.dropna(subset=["created_at"]).copy()
    if not created.empty:
        st.caption("项目创建时间分布")
        created["创建月份"] = created.created_at.dt.strftime("%Y-%m")
        st.bar_chart(created.groupby("创建月份").size())
    if not view.empty:
        st.caption("数据质量分布")
        st.bar_chart(view.groupby("quality").size())

with tab3:
    if runs.empty:
        st.info("尚无采集记录。")
    else:
        records = runs[["id", "status", "phase", "started_at", "finished_at", "discovered", "matched", "updated",
                        "errors", "search_errors", "refresh_errors", "translation_errors", "system_errors", "progress_done", "progress_total"]].copy()
        records.status = records.status.map(lambda status: STATUS_LABELS.get(status, status))
        records.phase = records.phase.map(lambda phase: PHASE_LABELS.get(phase, phase))
        records = records.rename(columns={"id": "任务", "status": "状态", "phase": "阶段", "started_at": "开始时间（UTC）",
            "finished_at": "结束时间（UTC）", "discovered": "新增仓库", "matched": "匹配仓库", "updated": "新快照",
            "errors": "总错误", "search_errors": "搜索错误", "refresh_errors": "刷新错误", "translation_errors": "翻译错误", "system_errors": "系统错误",
            "progress_done": "阶段已处理", "progress_total": "阶段总数"})
        st.dataframe(records, hide_index=True)
        st.caption("旧版任务的计数沿用原始值，旧版“发现数”可能包含已存在的仓库。历史未收尾任务的真实结束时间无法还原。")
        run_id = st.selectbox("查看任务错误", runs.id.tolist())
        with closing(get_connection()) as con:
            errors = pd.read_sql_query("SELECT phase,item,message,created_at FROM collection_errors WHERE run_id=? ORDER BY id", con, params=(int(run_id),))
        if errors.empty:
            st.caption("该任务没有结构化错误记录；旧版任务未记录搜索错误，不代表完全成功。")
        else:
            st.dataframe(errors, hide_index=True)
    st.code(".venv/bin/python collector.py")

st.divider()
st.caption("GitHub 搜索仅覆盖配置关键词与最近 90 天创建的公开仓库。Star 是关注度信号，不代表项目质量。")
