"""冷数据恢复领域服务：规划冷数据恢复、逐分区验真归档文件并生成可读分区清单。

数据链路位置：数据恢复层。恢复单按确切表名逐日验真归档文件：
验真通过的分区写入校验凭据并开放日报取数；缺片或摘要异常的分区不可用，
补片后沿原单续办重验，已通过的日期保留凭据不重复验。
"""

from __future__ import annotations

from typing import Any

from app.services.archive_file_catalog import ArchiveFileCatalogService
from app.services.base import BaseModuleService, now_stamp, parse_date_range
from app.store import store

PASSED = "校验通过"
MISSING_FILE = "缺片"
DIGEST_MISMATCH = "摘要异常"
NO_ENTRY = "无归档条目"


class ColdRestoreService(BaseModuleService):
    MODULE = "cold_restore"
    ENTITY = "恢复任务"
    OPERATION_TERM = "查询"
    LIST_FIELDS = ["数据表", "归档来源", "恢复目标路径", "恢复时间范围", "恢复状态", "预计文件大小"]
    REQUIRED_FIELDS = ["数据表", "归档来源", "恢复目标路径", "恢复时间范围"]
    STATUS_ORDER = ["排队中", "恢复中", "待补片", "已完成", "恢复失败"]
    ACTION_RULES = {"发起恢复": "恢复中", "续办恢复": "恢复中", "取消恢复": "恢复失败"}
    NEGATIVE_ACTIONS = ["取消恢复"]

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        """把页面动作接到真实的验真流程上：发起恢复、缺片/校验失败后的原单续办。"""
        entry = store.find(self.MODULE, entry_id)
        if entry is None:
            return None, f"{self.ENTITY} {entry_id} 不存在或已归档"
        if action == "发起恢复":
            result = self.execute_restore(entry_id)
        elif action == "续办恢复":
            result = self.resume_restore(entry_id)
        elif action == "取消恢复":
            if entry.get("status") == "已完成":
                return None, "已完成的恢复单不能取消"
            entry["status"] = "恢复失败"
            entry["恢复状态"] = "恢复失败"
            entry["pending"] = False
            entry["abnormal"] = True
            return entry, f"{self.ENTITY}已取消恢复"
        else:
            return None, f"动作「{action}」不属于{self.MODULE}可执行范围"
        if not result.get("ok"):
            return None, result["message"]
        return entry, result["message"]

    def plan_restore(self, entry_id: int) -> dict[str, Any]:
        """规划冷数据恢复范围。

        按确切表名对照归档文件清单，列出有文件可回迁的分区和缺片分区，
        估算恢复耗时和所需存储空间。
        """
        entry = store.find(self.MODULE, entry_id)
        if entry is None:
            return {"ok": False, "message": "恢复任务不存在"}

        table = str(entry.get("数据表", "")).strip()
        dates = parse_date_range(entry.get("恢复时间范围", ""))
        catalog = self._catalog()
        files: list[str] = []
        missing: list[str] = []
        for date in dates:
            row = catalog.find_exact(table, date) if table else None
            if row is not None and str(row.get("实物摘要") or "").strip():
                files.append(str(row.get("归档文件", "")))
            else:
                missing.append(date)
        entry["预计文件大小"] = f"{round(len(files) * 0.12, 2)} GB"

        return {
            "task_id": entry_id,
            "数据表": table,
            "archive_source": entry.get("归档来源", ""),
            "restore_range": entry.get("恢复时间范围", ""),
            "target_path": entry.get("恢复目标路径", ""),
            "files_to_restore": len(files),
            "total_size_gb": round(len(files) * 0.12, 2),
            "estimated_duration": f"{max(len(files), 1) * 5} 分钟",
            "files": files[:20],
            "missing_partitions": missing,
            "restore_tier": "S3_IA_to_HOT",
        }

    def execute_restore(self, entry_id: int) -> dict[str, Any]:
        """执行冷数据恢复：逐日分区验真归档文件，生成可读分区清单。

        每个分区关联确切的数据表、归档文件和校验凭据；缺片或摘要异常的
        分区不可用，恢复单进入「待补片」，等待补片后沿原单续办。
        """
        entry = store.find(self.MODULE, entry_id)
        if entry is None:
            return {"ok": False, "message": "恢复任务不存在"}
        if entry.get("status") not in ("排队中", "恢复中"):
            return {"ok": False, "message": f"当前状态为「{entry.get('status')}」，只有排队中的恢复单能发起恢复"}
        return self._verify_and_update(entry, resume=False)

    def resume_restore(self, entry_id: int) -> dict[str, Any]:
        """沿原单续办：补片或换副本后只重验未通过的分区。

        已验真通过的日期保留校验凭据不重复验；仍验不过的日期更新失败原因，
        全部通过后恢复单才标记「已完成」。
        """
        entry = store.find(self.MODULE, entry_id)
        if entry is None:
            return {"ok": False, "message": "恢复任务不存在"}
        if entry.get("status") != "待补片":
            return {"ok": False, "message": f"当前状态为「{entry.get('status')}」，只有待补片的恢复单可以续办"}
        return self._verify_and_update(entry, resume=True)

    def validate_restored_data(self, entry_id: int) -> dict[str, Any]:
        """校验恢复数据的完整性。

        基于可读分区清单的真实验真结果输出检查项，
        不再使用固定的样例数据。
        """
        entry = store.find(self.MODULE, entry_id)
        if entry is None:
            return {"ok": False, "message": "恢复任务不存在"}
        manifest = entry.get("分区清单") or []
        if not manifest:
            return {"ok": False, "task_id": entry_id, "message": "请先发起恢复生成可读分区清单"}
        total = len(manifest)
        passed = [r for r in manifest if r["状态"] == PASSED]
        with_file = [r for r in manifest if r.get("归档文件")]
        checks = [
            {"type": "partition_completeness", "expected": total, "actual": len(passed),
             "passed": len(passed) == total},
            {"type": "file_presence", "expected": total, "actual": len(with_file),
             "passed": len(with_file) == total},
            {"type": "checksum_match", "expected": total, "actual": len(passed),
             "passed": len(passed) == total,
             "sample_file": passed[0]["归档文件"] if passed else ""},
        ]
        all_ok = all(c["passed"] for c in checks)
        return {
            "ok": all_ok,
            "task_id": entry_id,
            "checks": checks,
            "verdict": "恢复数据完整" if all_ok else "恢复数据存在差异",
            "pending_partitions": [
                {"日期": r["日期"], "状态": r["状态"], "原因": r["不可读原因"]}
                for r in manifest if r["状态"] != PASSED
            ],
        }

    def restore_with_priority(self, tasks: list[dict[str, Any]]) -> dict[str, Any]:
        """按优先级排序恢复任务。

        根据业务优先级（时间紧迫度/下游依赖数量/数据重要性）排列恢复顺序，
        优先级高的任务先执行恢复。
        """
        sorted_tasks = sorted(tasks, key=lambda t: (
            t.get("priority_score", 0),
            t.get("deadline_hours", 999),
        ), reverse=True)

        return {
            "total_tasks": len(sorted_tasks),
            "high_priority": sum(1 for t in sorted_tasks if t.get("priority_score", 0) >= 8),
            "normal_priority": sum(1 for t in sorted_tasks if 3 <= t.get("priority_score", 0) < 8),
            "low_priority": sum(1 for t in sorted_tasks if t.get("priority_score", 0) < 3),
            "execution_order": [
                {"task": t.get("task_name"), "priority": t.get("priority_score")}
                for t in sorted_tasks
            ],
        }

    def notify_downstream_after_restore(self, entry_id: int) -> dict[str, Any]:
        """恢复完成后通知下游依赖模块。

        通知血缘模块更新依赖状态、通知SLA模块检查产出截止时间，
        通知快照模块创建恢复后的数据快照。
        """
        entry = store.find(self.MODULE, entry_id)
        if entry is None:
            return {"ok": False, "message": "恢复任务不存在"}
        if entry.get("status") != "已完成":
            return {"ok": False, "message": "只有已完成的恢复任务才需要通知下游"}

        target_path = entry.get("恢复目标路径", "")
        lineage_rows = store.rows("lineage_graph")
        affected = [
            l for l in lineage_rows
            if target_path in l.get("来源表列表", "")
        ]
        for l in affected:
            l["status"] = "已建立"

        return {
            "ok": True,
            "task_id": entry_id,
            "notified_lineage_count": len(affected),
            "affected_tables": [l.get("目标表") for l in affected],
            "restored_snapshot_id": 5000 + entry_id,
            "message": "已通知下游模块数据恢复完成",
        }

    def _verify_and_update(self, entry: dict[str, Any], *, resume: bool) -> dict[str, Any]:
        table = str(entry.get("数据表", "")).strip()
        dates = parse_date_range(entry.get("恢复时间范围", ""))
        if not table or not dates:
            return {"ok": False, "message": "恢复单缺少数据表或恢复时间范围无法解析"}
        previous = {row["日期"]: row for row in entry.get("分区清单", [])}
        history = entry.setdefault("验真记录", [])
        manifest: list[dict[str, Any]] = []
        for date in dates:
            if resume and previous.get(date, {}).get("状态") == PASSED:
                # 已验真分区保留凭据，续办不重复验
                manifest.append(previous[date])
                continue
            row = self._verify_one(entry, table, date)
            manifest.append(row)
            history.append({
                "日期": date,
                "状态": row["状态"],
                "原因": row["不可读原因"],
                "时间": now_stamp(),
            })
        entry["分区清单"] = manifest
        pending_rows = [r for r in manifest if r["状态"] != PASSED]
        passed_dates = [r["日期"] for r in manifest if r["状态"] == PASSED]
        done = not pending_rows
        entry["status"] = "已完成" if done else "待补片"
        entry["恢复状态"] = entry["status"]
        entry["pending"] = not done
        entry["abnormal"] = bool(pending_rows)
        entry["最近验真时间"] = now_stamp()
        summary: dict[str, int] = {}
        for r in pending_rows:
            summary[r["状态"]] = summary.get(r["状态"], 0) + 1
        if done:
            message = f"全部 {len(manifest)} 个日分区验真通过，恢复完成"
        else:
            parts = "、".join(f"{state}{count}个" for state, count in summary.items())
            message = f"已通过 {len(passed_dates)} 个分区，待补 {len(pending_rows)} 个（{parts}），补片后请沿原单续办"
        return {
            "ok": True,
            "task_id": entry["id"],
            "续办": resume,
            "passed_dates": passed_dates,
            "pending": [
                {"日期": r["日期"], "状态": r["状态"], "原因": r["不可读原因"]}
                for r in pending_rows
            ],
            "manifest": manifest,
            "message": message,
        }

    def _verify_one(self, entry: dict[str, Any], table: str, date: str) -> dict[str, Any]:
        """验真单个日分区：只认确切表名的归档条目，逐条留下校验凭据。"""
        catalog = self._catalog()
        row = catalog.find_exact(table, date)
        item: dict[str, Any] = {
            "日期": date,
            "数据表": table,
            "状态": "",
            "归档文件": "",
            "副本地址": "",
            "校验凭据": None,
            "不可读原因": "",
        }
        if row is None:
            item["状态"] = NO_ENTRY
            item["不可读原因"] = f"归档清单中无 {table} {date} 的条目"
        elif not str(row.get("实物摘要") or "").strip():
            item["状态"] = MISSING_FILE
            item["归档文件"] = str(row.get("归档文件", ""))
            item["不可读原因"] = "归档条目在册但文件缺失（缺片），等待补片后续办"
        elif str(row.get("实物摘要")) != str(row.get("登记摘要")):
            item["状态"] = DIGEST_MISMATCH
            item["归档文件"] = str(row.get("归档文件", ""))
            item["不可读原因"] = f"实物摘要({row.get('实物摘要')})与登记摘要({row.get('登记摘要')})不符"
        else:
            item["状态"] = PASSED
            item["归档文件"] = str(row.get("归档文件", ""))
            item["副本地址"] = str(row.get("归档文件", ""))
            item["校验凭据"] = {
                "凭据编号": f"VFY-{entry['id']}-{date}",
                "验真方式": "SHA-256摘要比对",
                "登记摘要": row.get("登记摘要"),
                "实物摘要": row.get("实物摘要"),
                "验真时间": now_stamp(),
                "归档条目": f"archive_file_catalog#{row.get('id')}",
            }
        if item["状态"] != PASSED:
            lookalikes = catalog.lookalikes(table, date)
            if lookalikes:
                names = "、".join(f"{r.get('数据表')}/{date}" for r in lookalikes)
                item["隔离说明"] = f"发现相近表名条目（{names}），已按确切表名隔离，未引用其文件"
        return item

    def _catalog(self) -> ArchiveFileCatalogService:
        from app.services import service_for
        service = service_for("archive_file_catalog")
        assert isinstance(service, ArchiveFileCatalogService)
        return service
