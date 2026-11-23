"""日报对账领域服务：按日期范围核对日报明细，逐日确认，日期齐全才算完整。

数据链路位置：数据消费层。对账单只通过数据血缘的可读分区清单取数；
已确认的日期在单上留痕（确认时间、副本地址、金额小计），续办时
不重复计入金额合计，全部日期齐全后整单才标记「已完成」。
"""

from __future__ import annotations

from typing import Any

from app.services.base import BaseModuleService, now_stamp, parse_date_range
from app.store import store


class DailyReportReconService(BaseModuleService):
    MODULE = "daily_report_recon"
    ENTITY = "对账单"
    OPERATION_TERM = "核对"
    LIST_FIELDS = ["数据表", "对账范围", "已确认日期", "待确认日期", "金额合计"]
    REQUIRED_FIELDS = ["数据表", "对账范围"]
    STATUS_ORDER = ["待对账", "部分确认", "已完成"]
    ACTION_RULES = {"执行对账": "部分确认", "继续对账": "部分确认"}
    NEGATIVE_ACTIONS = []

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        entry, missing = super().create_entry(values)
        if entry is not None:
            dates = parse_date_range(entry.get("对账范围", ""))
            entry["已确认日期"] = "无"
            entry["待确认日期"] = ",".join(dates) if dates else "无"
            entry["金额合计"] = "0.00"
            entry["日期明细"] = {}
            entry["读取明细"] = []
            entry["失败记录"] = []
        return entry, missing

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(self.MODULE, entry_id)
        if entry is None:
            return None, f"{self.ENTITY} {entry_id} 不存在或已归档"
        if action == "执行对账":
            result = self.execute_recon(entry_id)
        elif action == "继续对账":
            result = self.resume_recon(entry_id)
        else:
            return None, f"动作「{action}」不属于{self.MODULE}可执行范围"
        if not result.get("ok"):
            return None, result["message"]
        return entry, result["message"]

    def execute_recon(self, entry_id: int) -> dict[str, Any]:
        """执行对账：按对账范围逐日取数，能取数的日期确认，其余记录失败原因。"""
        entry = store.find(self.MODULE, entry_id)
        if entry is None:
            return {"ok": False, "message": "对账单不存在"}
        if entry.get("status") != "待对账":
            return {"ok": False, "message": f"当前状态为「{entry.get('status')}」，只有待对账的单据能执行对账"}
        return self._recon_pass(entry)

    def resume_recon(self, entry_id: int) -> dict[str, Any]:
        """继续办理原对账单：只补核未确认的日期，已确认日期不重复计入合计。"""
        entry = store.find(self.MODULE, entry_id)
        if entry is None:
            return {"ok": False, "message": "对账单不存在"}
        if entry.get("status") != "部分确认":
            return {"ok": False, "message": f"当前状态为「{entry.get('status')}」，只有部分确认的对账单可以续办"}
        return self._recon_pass(entry)

    def confirm_date(self, entry_id: int, date: str) -> dict[str, Any]:
        """单独确认一个日期：该日可取数才确认；已确认的日期不重复计入。"""
        entry = store.find(self.MODULE, entry_id)
        if entry is None:
            return {"ok": False, "message": "对账单不存在"}
        date = (date or "").strip()
        dates = parse_date_range(entry.get("对账范围", ""))
        if date not in dates:
            return {"ok": False, "message": f"日期 {date} 不在对账范围 {entry.get('对账范围')} 内"}
        ledger = entry.setdefault("日期明细", {})
        if ledger.get(date, {}).get("状态") == "已确认":
            return {
                "ok": True,
                "confirmed": False,
                "message": f"{date} 已确认过，不重复计入合计",
                "detail": self.recon_detail(entry),
            }
        result = self._recon_pass(entry, only_dates=[date])
        if not result.get("ok"):
            return result
        if ledger.get(date, {}).get("状态") == "已确认":
            return {
                "ok": True,
                "confirmed": True,
                "message": f"{date} 已确认，金额小计 {ledger[date]['金额小计']}",
                "detail": self.recon_detail(entry),
            }
        return {
            "ok": False,
            "confirmed": False,
            "message": f"{date} 暂不能确认：{ledger.get(date, {}).get('失败原因', '未知原因')}",
            "detail": self.recon_detail(entry),
        }

    def recon_detail(self, entry: dict[str, Any]) -> dict[str, Any]:
        """对账单详情：实际读到哪些明细、每个日期何时确认、失败原因和金额合计。"""
        ledger = entry.get("日期明细", {})
        dates = parse_date_range(entry.get("对账范围", ""))
        return {
            "对账单号": entry.get("id"),
            "数据表": entry.get("数据表"),
            "对账范围": entry.get("对账范围"),
            "状态": entry.get("status"),
            "逐日状态": [
                {
                    "日期": date,
                    "状态": ledger.get(date, {}).get("状态", "待确认"),
                    "确认时间": ledger.get(date, {}).get("确认时间", ""),
                    "金额小计": ledger.get(date, {}).get("金额小计", ""),
                    "副本地址": ledger.get(date, {}).get("副本地址", ""),
                    "失败原因": ledger.get(date, {}).get("失败原因", ""),
                }
                for date in dates
            ],
            "读取明细": entry.get("读取明细", []),
            "失败记录": entry.get("失败记录", []),
            "金额合计": entry.get("金额合计"),
        }

    def _recon_pass(self, entry: dict[str, Any],
                    only_dates: list[str] | None = None) -> dict[str, Any]:
        table = str(entry.get("数据表", "")).strip()
        dates = parse_date_range(entry.get("对账范围", ""))
        if not table or not dates:
            return {"ok": False, "message": "对账单缺少数据表或对账范围无法解析"}
        from app.services import service_for
        from app.services.lineage_graph import LineageGraphService
        lineage = service_for("lineage_graph")
        assert isinstance(lineage, LineageGraphService)

        ledger = entry.setdefault("日期明细", {})
        reads = entry.setdefault("读取明细", [])
        failures_log = entry.setdefault("失败记录", [])
        targets = only_dates if only_dates is not None else dates
        newly: list[str] = []
        failures: dict[str, str] = {}
        for date in targets:
            if ledger.get(date, {}).get("状态") == "已确认":
                continue  # 已确认的日期不重复计入
            read = lineage.daily_report_read(table, date)
            if read.get("可取数"):
                ledger[date] = {
                    "状态": "已确认",
                    "确认时间": now_stamp(),
                    "金额小计": read["金额小计"],
                    "副本地址": read["副本地址"],
                    "失败原因": "",
                }
                for row in read["明细"]:
                    reads.append({
                        "日期": date,
                        "订单号": row["订单号"],
                        "金额": row["金额"],
                        "副本地址": read["副本地址"],
                    })
                newly.append(date)
            else:
                reason = str(read.get("原因", ""))
                prev = ledger.get(date, {})
                ledger[date] = {
                    "状态": "待确认",
                    "确认时间": prev.get("确认时间", ""),
                    "金额小计": prev.get("金额小计", ""),
                    "副本地址": prev.get("副本地址", ""),
                    "失败原因": reason,
                }
                failures[date] = reason
                failures_log.append({"日期": date, "原因": reason, "记录时间": now_stamp()})
        total = round(sum(
            float(v["金额小计"]) for v in ledger.values() if v.get("状态") == "已确认"
        ), 2)
        confirmed = sorted(d for d, v in ledger.items() if v.get("状态") == "已确认")
        pending_dates = [d for d in dates if ledger.get(d, {}).get("状态") != "已确认"]
        entry["金额合计"] = f"{total:.2f}"
        entry["已确认日期"] = ",".join(confirmed) if confirmed else "无"
        entry["待确认日期"] = ",".join(pending_dates) if pending_dates else "无"
        # 整段数据只有日期齐全才能标成完整
        entry["status"] = "已完成" if not pending_dates else ("部分确认" if confirmed else "待对账")
        entry["pending"] = bool(pending_dates)
        entry["abnormal"] = bool(failures)
        return {
            "ok": True,
            "newly_confirmed": newly,
            "failures": failures,
            "total": entry["金额合计"],
            "message": (
                f"本次新确认 {len(newly)} 个日期（{','.join(newly) or '无'}），"
                f"待确认 {len(pending_dates)} 个，金额合计 {entry['金额合计']}"
            ),
        }
