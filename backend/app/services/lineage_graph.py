"""数据血缘领域服务：管理表级和字段级的数据血缘图、上下游追踪和断裂检测。

数据链路位置：元数据层，维护数据从源到目标的完整血缘关系图；
同时承担日报取数接口：按冷数据恢复的可读分区清单回答哪些日期能取数。"""

from __future__ import annotations

from typing import Any

from app.services.base import BaseModuleService, parse_date_range
from app.store import store


class LineageGraphService(BaseModuleService):
    MODULE = "lineage_graph"
    ENTITY = "血缘关系"
    OPERATION_TERM = "关联"
    LIST_FIELDS = ["目标表", "来源表列表", "转换逻辑", "中间层级", "血缘深度"]
    REQUIRED_FIELDS = ["目标表", "来源表列表", "转换逻辑"]
    STATUS_ORDER = ["已建立", "待验证", "已断裂"]
    ACTION_RULES = {"刷新血缘": "已建立", "标记断裂": "已断裂"}
    NEGATIVE_ACTIONS = []

    # 副本内容：日报从通过验真的副本地址实际能读到的明细行。
    # 真实项目里这里是对副本存储的读取，当前用内置样例代替。
    REPLICA_DETAILS: dict[tuple[str, str], list[dict[str, Any]]] = {
        ("ods_orders", "2026-11-05"): [
            {"订单号": "SO20261105001", "买家": "杭州·王女士", "金额": 128.50},
            {"订单号": "SO20261105002", "买家": "上海·李先生", "金额": 256.00},
            {"订单号": "SO20261105003", "买家": "北京·赵先生", "金额": 64.00},
        ],
        ("ods_orders", "2026-11-06"): [
            {"订单号": "SO20261106001", "买家": "深圳·陈女士", "金额": 99.90},
            {"订单号": "SO20261106002", "买家": "广州·周先生", "金额": 300.10},
        ],
    }

    def readable_partitions(self, table: str, start: str, end: str) -> dict[str, Any]:
        """可读分区清单：按恢复单验真结果回答每个日分区能否取数。

        供日报取数前调用，区分当日没有文件（缺片/无归档条目）、
        文件摘要异常和已经校验通过的日期：通过的日期返回副本地址和
        校验凭据，其余说明无法取数的原因。
        """
        dates = parse_date_range(f"{start}~{end}")
        table = (table or "").strip()
        if not table or not dates:
            return {"ok": False, "message": "表名或日期范围无法解析"}
        restores = [
            r for r in store.rows("cold_restore")
            if r.get("数据表") == table and r.get("分区清单")
        ]
        items: list[dict[str, Any]] = []
        for date in dates:
            covering: list[tuple[int, dict[str, Any]]] = []
            for restore in restores:
                for row in restore["分区清单"]:
                    if row.get("日期") == date:
                        covering.append((int(restore.get("id", 0)), row))
            if not covering:
                items.append({
                    "日期": date,
                    "状态": "未验真",
                    "可取数": False,
                    "原因": "没有覆盖该日期的恢复单验真记录",
                })
                continue
            restore_id, row = max(covering, key=lambda item: item[0])
            if row.get("状态") == "校验通过":
                items.append({
                    "日期": date,
                    "状态": "校验通过",
                    "可取数": True,
                    "副本地址": row.get("副本地址", ""),
                    "校验凭据": row.get("校验凭据"),
                    "恢复单号": restore_id,
                })
            else:
                items.append({
                    "日期": date,
                    "状态": row.get("状态"),
                    "可取数": False,
                    "原因": row.get("不可读原因", ""),
                    "恢复单号": restore_id,
                })
        readable = [i for i in items if i["可取数"]]
        return {
            "ok": True,
            "数据表": table,
            "范围": f"{dates[0]}~{dates[-1]}",
            "清单": items,
            "可读日期数": len(readable),
            "不可读日期数": len(items) - len(readable),
        }

    def daily_report_read(self, table: str, date: str) -> dict[str, Any]:
        """日报取数：从通过验真的副本地址读取当日明细。

        缺失或验不过的分区一律不可取数，返回原因；通过验真的分区
        返回副本地址、校验凭据和当日明细行。
        """
        manifest = self.readable_partitions(table, date, date)
        if not manifest.get("ok"):
            return {"可取数": False, "数据表": table, "日期": date,
                    "原因": manifest.get("message", "查询失败")}
        item = manifest["清单"][0]
        if not item["可取数"]:
            return {"可取数": False, "数据表": table, "日期": date,
                    "状态": item["状态"], "原因": item["原因"]}
        details = [dict(row) for row in self.REPLICA_DETAILS.get((table, date), [])]
        subtotal = round(sum(float(row["金额"]) for row in details), 2)
        return {
            "可取数": True,
            "数据表": table,
            "日期": date,
            "副本地址": item["副本地址"],
            "校验凭据": item["校验凭据"],
            "恢复单号": item["恢复单号"],
            "明细": details,
            "行数": len(details),
            "金额小计": subtotal,
        }

    def trace_upstream(self, target_table: str, max_depth: int = 5) -> dict[str, Any]:
        """从目标表向上追溯所有上游来源表和转换链路。

        返回完整的上游依赖树，每个节点包含表名、所在层级和转换逻辑。
        用于变更影响分析。
        """
        all_entries = store.rows(self.MODULE)
        visited: set[str] = set()
        tree: list[dict[str, Any]] = []
        queue = [(target_table, 0)]
        while queue and len(visited) < 50:
            current, depth = queue.pop(0)
            if current in visited or depth > max_depth:
                continue
            visited.add(current)
            for entry in all_entries:
                if entry.get("目标表") == current and entry.get("status") == "已建立":
                    sources = entry.get("来源表列表", "").split(",")
                    node = {
                        "table": current,
                        "depth": depth,
                        "sources": [s.strip() for s in sources if s.strip()],
                        "transform": entry.get("转换逻辑"),
                        "lineage_id": entry.get("id"),
                    }
                    tree.append(node)
                    for src in node["sources"]:
                        if src not in visited:
                            queue.append((src, depth + 1))

        return {
            "target_table": target_table,
            "max_depth": max_depth,
            "total_nodes": len(tree),
            "upstream_tree": tree,
            "leaf_tables": [n["table"] for n in tree if not n["sources"]],
        }

    def trace_downstream(self, source_table: str, max_depth: int = 5) -> dict[str, Any]:
        """从来源表向下追溯所有下游受影响表和转换链路。

        用于评估某个表变更时的影响范围，联动质量规则模块通知下游。
        """
        all_entries = store.rows(self.MODULE)
        visited: set[str] = set()
        tree: list[dict[str, Any]] = []
        queue = [(source_table, 0)]
        while queue and len(visited) < 50:
            current, depth = queue.pop(0)
            if current in visited or depth > max_depth:
                continue
            visited.add(current)
            for entry in all_entries:
                sources = entry.get("来源表列表", "").split(",")
                source_set = {s.strip() for s in sources if s.strip()}
                if current in source_set and entry.get("status") == "已建立":
                    target = entry.get("目标表", "")
                    node = {
                        "table": target,
                        "depth": depth + 1,
                        "upstream_of": current,
                        "transform": entry.get("转换逻辑"),
                        "lineage_id": entry.get("id"),
                    }
                    tree.append(node)
                    if target not in visited:
                        queue.append((target, depth + 1))

        return {
            "source_table": source_table,
            "max_depth": max_depth,
            "total_affected": len(tree),
            "downstream_tree": tree,
            "end_consumers": [n["table"] for n in tree if not any(
                e.get("来源表列表", "").find(n["table"]) >= 0
                for e in all_entries if e.get("status") == "已建立"
            )],
        }

    def detect_broken_lineage(self) -> dict[str, Any]:
        """扫描并检测断裂的血缘关系。

        当上游来源表已被迁移、归档或删除时，其下游血缘关系变为断裂状态。
        检测结果包含受影响的目标表和对应的修复建议。
        """
        all_entries = store.rows(self.MODULE)
        broken: list[dict[str, Any]] = []
        for entry in all_entries:
            if entry.get("status") == "已断裂":
                broken.append({
                    "lineage_id": entry.get("id"),
                    "target_table": entry.get("目标表"),
                    "sources": entry.get("来源表列表"),
                    "needs_revalidation": True,
                })

        table_archive_rows = store.rows("table_archive")
        migration_rows = store.rows("data_migration")

        for entry in all_entries:
            if entry.get("status") != "已建立":
                continue
            sources = entry.get("来源表列表", "").split(",")
            for src in sources:
                src = src.strip()
                if not src:
                    continue
                archived = any(
                    a.get("目标表") == src and a.get("status") == "已启用"
                    for a in table_archive_rows
                )
                if archived:
                    entry["status"] = "已断裂"
                    broken.append({
                        "lineage_id": entry.get("id"),
                        "target_table": entry.get("目标表"),
                        "source": src,
                        "reason": "来源表已归档",
                        "needs_revalidation": True,
                    })

        return {
            "broken_count": len(broken),
            "broken_lineages": broken,
            "suggestion": "请重新校验断裂血缘并更新目标表关联",
        }

    def build_inheritance_chain(self, table_name: str) -> dict[str, Any]:
        """为指定表构建完整的继承链（上下游合起来）。

        从该表出发，同时向上追溯祖先表和向下追溯后代表，
        生成完整的数据流转全景图。
        """
        upstream = self.trace_upstream(table_name)
        downstream = self.trace_downstream(table_name)
        return {
            "table": table_name,
            "upstream_depth": upstream["total_nodes"],
            "downstream_affected": downstream["total_affected"],
            "upstream_chain": upstream["upstream_tree"],
            "downstream_chain": downstream["downstream_tree"],
            "orphan_risk": upstream["total_nodes"] == 0 and downstream["total_affected"] > 0,
        }

    def impact_analysis(self, entry_id: int) -> dict[str, Any]:
        """变更影响分析：当某个血缘条目的转换逻辑发生变更时，
        评估对下游的级联影响范围和受影响的数据质量规则。
        """
        entry = store.find(self.MODULE, entry_id)
        if entry is None:
            return {"ok": False, "message": "血缘条目不存在"}

        target_table = entry.get("目标表", "")
        downstream = self.trace_downstream(target_table)

        quality_rows = store.rows("data_quality_rule")
        affected_quality_rules = [
            q for q in quality_rows
            if q.get("目标表") in [n["table"] for n in downstream["downstream_tree"]]
        ]

        return {
            "lineage_id": entry_id,
            "source_table": target_table,
            "change_type": "transform_logic_changed",
            "direct_downstream_tables": len(downstream["downstream_tree"]),
            "cascaded_tables": downstream["total_affected"],
            "affected_quality_rule_count": len(affected_quality_rules),
            "affected_quality_rules": [
                {"rule_id": r["id"], "rule_name": r.get("规则名称"), "target_table": r.get("目标表")}
                for r in affected_quality_rules[:10]
            ],
            "recommendation": "建议在变更后立即执行受影响的质量规则校验",
        }