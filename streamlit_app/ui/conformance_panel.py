"""Conformance rules + optional reference checks (standalone or embedded in Twin)."""
from __future__ import annotations

import streamlit as st

import part_track.flow_classification as flow_classification
import services.mqtt_backend as mqtt_backend
import services.neo4j_backend as neo4j_backend
import part_track.part_track_model as part_track_model


def _is_subsequence(needle: list[str], haystack: list[str]) -> bool:
    if not needle:
        return True
    j = 0
    for x in haystack:
        if j < len(needle) and needle[j] == x:
            j += 1
    return j == len(needle)


_RULES_MD = (
    "### Transport vs process\n"
    "- **Transport path** (splitter / merger / corner / return belt): can loop **physically** — "
    "that does **not** mean rework.\n"
    "- **Process stage sequence** (for rollback): built from **PROCESS / PASS** events only "
    "(consecutive duplicate stages merged), not LOAD/UNLOAD/TRANSFER.\n"
    "- **Rework** = rollback to an **earlier** stage *within the same FINISH-delimited cycle*, or "
    "**FAIL** later closed with **FINISH**.\n"
    "\n### Flow (what happened)\n"
    "1. **SCRAP** in log → **Scrap**.\n"
    "2. Else if last event ≠ **FINISH**: **FAIL** → **≥2× FAIL** or PROCESS/PASS after last FAIL → **Rework**; "
    "else **FAIL**; else **In progress**.\n"
    "3. Else (last = **FINISH**): **FAIL** or **stage rollback** → **Rework**; else **Normal**.\n"
    "\n### Conformance (vs expected process model)\n"
    "| Flow | Conformance |\n"
    "|------|-------------|\n"
    "| Normal | Conformant |\n"
    "| Rework | Deviated (allowed) |\n"
    "| Scrap | Deviated |\n"
    "| FAIL | Incomplete (error) |\n"
    "| In progress | Incomplete |\n"
    "\n*Parts in overview = Neo4j in session; optional `part_track_overview_ids` adds placeholder rows.*"
)


def _graphviz_activities() -> None:
    try:
        st.graphviz_chart(
            """
            digraph G {
                rankdir=LR;
                START -> LOAD -> PROCESS -> UNLOAD -> FINISH;
                PROCESS -> SCRAP [color=red, label="abnormal"];
            }
            """
        )
    except Exception:
        st.code("START → LOAD → PROCESS → UNLOAD → FINISH (PROCESS → SCRAP abnormal)")


def render_conformance_panel(
    *,
    use_page_session: bool = False,
    show_session_summary: bool = True,
    show_extension_tools: bool = True,
    twin_embed: bool = False,
) -> None:
    if twin_embed and not show_session_summary:
        pass
    elif show_session_summary:
        st.markdown(
            "##### Conformance\n"
            "**Flow** = observed outcome; **Conformance** = fit to the fixed process model (mapped). "
            "Same rules as Part track. "
            + ("Reference check & station query below (optional)." if show_extension_tools else "")
        )
    else:
        if show_extension_tools:
            st.markdown(
                "##### Rules & tools\n"
                "Per-part Flow & Conformance are in Part track overview; **Rework** = process stage "
                "rollback (not transport geometry). Here: diagram, rules, optional reference check, station events."
            )
        else:
            st.markdown(
                "##### Rules (same as overview)\n"
                "Outcomes are in Part track; diagram and rules only here."
            )

    if twin_embed and not show_session_summary:
        with st.expander("Activity diagram & rules", expanded=False):
            st.markdown(_RULES_MD)
            _graphviz_activities()
    else:
        with st.expander("Classification rules (brief)", expanded=False):
            st.markdown(_RULES_MD)
        _graphviz_activities()

    session_id: str | None = None
    need_session = show_session_summary or show_extension_tools
    if need_session:
        if use_page_session:
            if st.session_state.get("cp_data_source") == "live":
                session_id = mqtt_backend.physical_kpi_session_id()
            else:
                _rps = mqtt_backend.get_replay_pipeline_session_id()
                session_id = _rps or st.session_state.get(
                    "dt_resolved_session"
                )
            if show_session_summary:
                st.caption("Session matches Factory Layout.")
            elif show_extension_tools and not twin_embed:
                st.caption(
                    "Session matches Factory Layout / Part Tracking & Conformance Check."
                )
        else:
            sessions = neo4j_backend.list_recent_sessions(40)
            opts = [("Latest session", None)]
            for s in sessions:
                sid = s.get("id") or ""
                desc = (s.get("description") or "").strip()
                label = (sid[:12] + "...") if len(sid) > 12 else sid
                if desc:
                    label = "{} · {}".format(label, desc[:20])
                opts.append((label, sid))

            ix = st.selectbox(
                "Session",
                range(len(opts)),
                format_func=lambda i: opts[i][0],
                key="cf_session_ix",
            )
            session_id = opts[ix][1]

    if show_session_summary:
        pdata = neo4j_backend.query_part_flow(None, session_id)
        if pdata.get("error"):
            st.warning(pdata["error"])
        else:
            summary_rows = []
            for p in pdata.get("parts") or []:
                steps = list(p.get("steps") or [])
                info = flow_classification.classify_flow_from_steps(steps)
                oc = info.get("outcome") or ""
                summary_rows.append(
                    {
                        "part_id": p.get("part_id"),
                        "Flow Type": flow_classification.flow_type_badge(info),
                        "Conformance": part_track_model.conformance_display_badge(oc),
                        "Deviation": " · ".join(info.get("reasons") or []) or "—",
                        "path_summary": flow_classification.truncate_path(
                            info.get("station_path") or "—"
                        ),
                    }
                )
            if summary_rows:
                st.dataframe(summary_rows, use_container_width=True, hide_index=True)
            else:
                st.info("No part traces in this session.")

    if show_extension_tools:

        def _reference_station_block() -> None:
            if not (twin_embed and not show_session_summary):
                st.markdown("##### Reference check (optional)")
                st.caption(
                    "Subsequence check vs expected activities — **validation / explainability only**; "
                    "it does **not** drive Flow or Conformance classification."
                )
            part_id = st.text_input(
                "Part ID (Entity.sysId)",
                value="",
                key="cf_part_id",
            )
            expected_raw = st.text_input(
                "Expected activity sequence (comma-separated)",
                value="START,LOAD,PROCESS,UNLOAD,FINISH",
                key="cf_expected_raw",
            )

            if st.button("Check", key="cf_check_btn"):
                if not part_id.strip():
                    st.error("Enter Part ID.")
                else:
                    expected = [x.strip() for x in expected_raw.split(",") if x.strip()]
                    acts, meta = neo4j_backend.get_part_activity_sequence(
                        part_id, session_id
                    )
                    if meta.get("error"):
                        st.warning(meta["error"])
                    elif not acts:
                        st.warning("No step data for this part in this session.")
                    else:
                        ok = _is_subsequence(expected, acts)
                        _fp = neo4j_backend.query_part_flow(
                            part_id.strip(), session_id
                        )
                        _parts = _fp.get("parts") or []
                        _steps_c = (
                            list(_parts[0].get("steps") or []) if _parts else []
                        )
                        info = flow_classification.classify_flow_from_steps(_steps_c)
                        st.subheader("**{}**".format("PASS" if ok else "FAIL"))
                        st.caption(
                            "Flow outcome: **{}**".format(info.get("label_zh") or "—")
                        )
                        c1, c2 = st.columns(2)
                        with c1:
                            st.write("**Expected**")
                            st.code(", ".join(expected))
                        with c2:
                            st.write("**Actual (activities)**")
                            st.code(" → ".join(acts))

            if not (twin_embed and not show_session_summary):
                st.divider()
            station = st.text_input("Station.sysId", value="", key="cf_station_id")
            if station.strip() and st.button(
                "Query station events", key="cf_station_query_btn"
            ):
                rows = neo4j_backend.query_station_events(
                    station.strip(), 80, session_id
                )
                if not rows:
                    st.info("No data.")
                else:
                    st.dataframe(rows, use_container_width=True)

        if twin_embed and not show_session_summary:
            with st.expander("Reference sequence & station query", expanded=False):
                _reference_station_block()
        else:
            st.divider()
            _reference_station_block()
