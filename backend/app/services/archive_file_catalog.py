"""归档文件清单领域服务：登记归档存储中的文件条目、摘要与缺片状态。

数据链路位置：数据归档层，归档文件的台账。冷数据恢复按确切表名逐条验真，
缺片条目等补片后才能被恢复单引用；表名相近的条目互相隔离，互不顶替。
"""

from __future__ import annotations

from typing import Any

from app.services.base import BaseModuleService, now_stamp
from app.store import store


class ArchiveFileCatalogService(BaseModuleService):
    MODULE = "archive_file_catalog"
    ENTITY = "归档条目"
    OPERATION_TERM = "登记"
    LIST_FIELDS = ["数据表", "分区日期", "归档文件", "登记摘要", "实物摘要"]
    REQUIRED_FIELDS = ["数据表", "分区日期", "归档文件", "登记摘要"]
    STATUS_ORDER = ["缺片", "有文件", "已补充"]
    ACTION_RULES = {"补登文件": "已补充"}
    NEGATIVE_ACTIONS = []

    def find_exact(self, table: str, date: str) -> dict[str, Any] | None:
        """按确切表名+分区日期找条目。

        恢复验真只认完全相等的表名：表名相近（如 ods_orders_v2）的条目
        即使有文件也不返回，避免张冠李戴。
        """
        for row in store.rows(self.MODULE):
            if row.get("数据表") == table and row.get("分区日期") == date:
                return row
        return None

    def lookalikes(self, table: str, date: str) -> list[dict[str, Any]]:
        """找同一分区日期下表名相近的其他条目，用于隔离说明。"""
        result = []
        for row in store.rows(self.MODULE):
            other = str(row.get("数据表", ""))
            if row.get("分区日期") != date or other == table:
                continue
            if other.startswith(table) or table.startswith(other):
                result.append(row)
        return result

    def supplement_copy(self, table: str, date: str, digest: str) -> dict[str, Any]:
        """补来一份副本：按确切表名落到在册条目上，记录实物摘要等待验真。"""
        digest = (digest or "").strip()
        if not digest:
            return {"ok": False, "message": "补登副本必须给出实物摘要"}
        entry = self.find_exact(table, date)
        if entry is None:
            note = ""
            lookalikes = self.lookalikes(table, date)
            if lookalikes:
                names = "、".join(f"{r.get('数据表')}/{date}" for r in lookalikes)
                note = f"（发现相近表名条目 {names}，已按确切表名隔离，不接受顶替补登）"
            return {"ok": False, "message": f"归档清单中无 {table} {date} 的条目，补登被拒绝{note}"}
        entry["实物摘要"] = digest
        entry["status"] = "已补充"
        entry["pending"] = True
        entry["abnormal"] = False
        entry["补充时间"] = now_stamp()
        match = digest == str(entry.get("登记摘要", ""))
        return {
            "ok": True,
            "entry": entry,
            "digest_match": match,
            "message": (
                f"已补登 {table} {date} 的副本，实物摘要与登记摘要"
                f"{'一致' if match else '不符，需恢复单续办验真确认'}"
            ),
        }

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(self.MODULE, entry_id)
        if entry is None:
            return None, f"{self.ENTITY} {entry_id} 不存在或已归档"
        if action != "补登文件":
            return None, f"动作「{action}」不属于{self.MODULE}可执行范围"
        registered = str(entry.get("登记摘要", "")).strip()
        if not registered:
            return None, "该条目缺少登记摘要，无法补登"
        # 页面上的补登：文件按登记摘要原样补回；
        # 摘要不符的副本走 /api/restore-flow/catalog/supplement 显式登记实物摘要。
        entry["实物摘要"] = registered
        entry["status"] = "已补充"
        entry["pending"] = True
        entry["abnormal"] = False
        entry["补充时间"] = now_stamp()
        return entry, f"{self.ENTITY}已补登文件，等待恢复单续办验真"
