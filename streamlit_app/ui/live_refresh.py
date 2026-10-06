"""Live / replay UI：KPI、Digital Twin 地图与 Part Tracking 共用刷新周期，避免多 fragment 错拍滞后。"""
from __future__ import annotations

from datetime import timedelta

# 与 main_service KPI MQTT（约 2s）解耦：Neo4j 工位事件可更密；全站同频减少「地图与表格不同步」感。
LIVE_UI_REFRESH_SEC: float = 1.0


def live_ui_refresh_delta() -> timedelta:
    return timedelta(seconds=LIVE_UI_REFRESH_SEC)
