"""冷数据恢复与日报对账的领域接口：可读分区清单、日报取数、补片登记和单日确认。

通用资源路由之外的专用查询/操作入口，路径挂在 /api/restore-flow 下，
必须比 /api/{module} 先注册，避免被动态模块路由抢先匹配。
"""
from __future__ import annotations

from typing import Any, cast

from fastapi import APIRouter, HTTPException, Query

from app.schemas import ConfirmDatePayload, SupplementPayload
from app.services import service_for
from app.services.archive_file_catalog import ArchiveFileCatalogService
from app.services.daily_report_recon import DailyReportReconService
from app.services.lineage_graph import LineageGraphService

router = APIRouter(prefix="/api/restore-flow", tags=["冷数据恢复与日报对账"])


def _lineage() -> LineageGraphService:
    return cast(LineageGraphService, service_for("lineage_graph"))


def _catalog() -> ArchiveFileCatalogService:
    return cast(ArchiveFileCatalogService, service_for("archive_file_catalog"))


def _recon() -> DailyReportReconService:
    return cast(DailyReportReconService, service_for("daily_report_recon"))


@router.get("/readable-partitions")
def readable_partitions(
    table: str = Query(..., description="确切数据表名"),
    start: str = Query(..., description="起始日期 YYYY-MM-DD"),
    end: str = Query(..., description="截止日期 YYYY-MM-DD"),
) -> dict[str, Any]:
    """可读分区清单：区分当日没有文件、文件摘要异常和已经校验通过的日期。"""
    return _lineage().readable_partitions(table.strip(), start.strip(), end.strip())


@router.get("/daily-report")
def daily_report(
    table: str = Query(..., description="确切数据表名"),
    date: str = Query(..., description="取数日期 YYYY-MM-DD"),
) -> dict[str, Any]:
    """日报取数：通过验真的日期返回副本地址与明细，否则说明无法取数的原因。"""
    return _lineage().daily_report_read(table.strip(), date.strip())


@router.post("/catalog/supplement")
def supplement(payload: SupplementPayload) -> dict[str, Any]:
    """补来一份副本：按确切表名+分区日期登记实物摘要，等待恢复单续办验真。"""
    return _catalog().supplement_copy(
        payload.table.strip(), payload.date.strip(), payload.digest.strip())


@router.post("/recon/{recon_id}/confirm")
def confirm_date(recon_id: int, payload: ConfirmDatePayload) -> dict[str, Any]:
    """对账单单独确认一个日期；已确认的日期不重复计入合计。"""
    result = _recon().confirm_date(recon_id, payload.date.strip())
    if not result.get("ok") and "不存在" in str(result.get("message", "")):
        raise HTTPException(status_code=404, detail=result["message"])
    return result


@router.get("/recon/{recon_id}/detail")
def recon_detail(recon_id: int) -> dict[str, Any]:
    """对账单详情：实际读到的明细、逐日确认时间、失败原因和金额合计。"""
    service = _recon()
    entry = service.get_entry(recon_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"对账单 {recon_id} 不存在或已归档")
    return service.recon_detail(entry)
