"""冷数据恢复 + 日报对账全流程演示。

场景：同一恢复单覆盖 ods_orders 的 2026-11-05 / 2026-11-06 两个日分区，
5 日文件通过校验，6 日只有归档条目（缺片）。值班员先用日报核对 5 日明细，
再提交覆盖 5~6 日的对账单；6 日先补来摘要不符的副本、再补来正确副本，
两次沿原恢复单续办，并继续办理原对账单。

运行：cd backend && .venv/bin/python scripts/demo_restore_recon.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services import service_for  # noqa: E402
from app.services.archive_file_catalog import ArchiveFileCatalogService  # noqa: E402
from app.services.cold_restore import ColdRestoreService  # noqa: E402
from app.services.daily_report_recon import DailyReportReconService  # noqa: E402
from app.services.lineage_graph import LineageGraphService  # noqa: E402

TABLE = "ods_orders"
DAY1, DAY2 = "2026-11-05", "2026-11-06"
WRONG_DIGEST = "sha256:0000deadbeef11112222333344445555"

restore = service_for("cold_restore")
catalog = service_for("archive_file_catalog")
lineage = service_for("lineage_graph")
recon = service_for("daily_report_recon")
assert isinstance(restore, ColdRestoreService)
assert isinstance(catalog, ArchiveFileCatalogService)
assert isinstance(lineage, LineageGraphService)
assert isinstance(recon, DailyReportReconService)

failures: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    mark = "✓" if condition else "✗"
    print(f"    {mark} {label}{('：' + detail) if detail else ''}")
    if not condition:
        failures.append(label)


def show_manifest(entry: dict) -> None:
    for row in entry.get("分区清单", []):
        line = f"    - {row['日期']} [{row['状态']}] 归档文件={row['归档文件'] or '—'}"
        if row.get("副本地址"):
            line += f" 副本={row['副本地址']}"
        if row.get("不可读原因"):
            line += f" 原因={row['不可读原因']}"
        print(line)
        if row.get("隔离说明"):
            print(f"      {row['隔离说明']}")
        cred = row.get("校验凭据")
        if cred:
            print(f"      校验凭据 {cred['凭据编号']} 验真时间 {cred['验真时间']} 方式 {cred['验真方式']}")


def show_recon(entry: dict) -> None:
    detail = recon.recon_detail(entry)
    print(f"    对账单状态={detail['状态']} 金额合计={detail['金额合计']}")
    for day in detail["逐日状态"]:
        line = f"    - {day['日期']} [{day['状态']}]"
        if day["确认时间"]:
            line += f" 确认时间={day['确认时间']} 小计={day['金额小计']}"
        if day["失败原因"]:
            line += f" 失败原因={day['失败原因']}"
        print(line)
    reads = detail["读取明细"]
    print(f"    实际读取明细 {len(reads)} 行："
          + ("、".join(f"{r['订单号']}({r['金额']})" for r in reads) if reads else "无"))


print("=" * 72)
print("步骤 1：登记恢复单（ods_orders，2026-11-05~2026-11-06）并发起恢复")
print("=" * 72)
entry, missing = restore.create_entry({
    "数据表": TABLE,
    "归档来源": "S3_IA/lake-archive",
    "恢复目标路径": "/data/hot_restore/ods_orders_20261105_06",
    "恢复时间范围": f"{DAY1}~{DAY2}",
    "恢复状态": "排队中",
    "预计文件大小": "-",
})
assert entry is not None, f"登记失败：{missing}"
RESTORE_ID = entry["id"]
print(f"恢复单 #{RESTORE_ID} 已登记，发起恢复……")
result = restore.execute_restore(RESTORE_ID)
print(f"结果：{result['message']}")
entry = restore.get_entry(RESTORE_ID)
show_manifest(entry)
check("5 日分区校验通过", result["passed_dates"] == [DAY1])
check("6 日分区缺片", result["pending"] == [{"日期": DAY2, "状态": "缺片",
      "原因": "归档条目在册但文件缺失（缺片），等待补片后续办"}])
row6 = [r for r in entry["分区清单"] if r["日期"] == DAY2][0]
check("相近表名已隔离", "ods_orders_v2" in row6.get("隔离说明", ""), row6.get("隔离说明", ""))
check("恢复单进入待补片", entry["status"] == "待补片")

print()
print("=" * 72)
print("步骤 2：值班员用日报核对 5 日明细（数据血缘查询接口）")
print("=" * 72)
read1 = lineage.daily_report_read(TABLE, DAY1)
print(f"5 日取数：副本地址={read1['副本地址']} 行数={read1['行数']} 小计={read1['金额小计']}")
for row in read1["明细"]:
    print(f"    - {row['订单号']} {row['买家']} 金额 {row['金额']}")
read2 = lineage.daily_report_read(TABLE, DAY2)
print(f"6 日取数：可取数={read2['可取数']} 原因={read2['原因']}")
manifest_view = lineage.readable_partitions(TABLE, DAY1, DAY2)
print(f"可读分区清单：可读 {manifest_view['可读日期数']} 天，不可读 {manifest_view['不可读日期数']} 天")
check("5 日可读且带校验凭据", read1["可取数"] and read1["校验凭据"]["凭据编号"].startswith("VFY-"))
check("6 日不可读且说明缺片", not read2["可取数"] and "缺片" in read2["原因"])
check("未引用相近表名的文件", "ods_orders_v2" not in read1["副本地址"])

print()
print("=" * 72)
print("步骤 3：提交覆盖 5~6 日的对账单，执行对账并单独确认 5 日")
print("=" * 72)
recon_entry, missing = recon.create_entry({"数据表": TABLE, "对账范围": f"{DAY1}~{DAY2}"})
assert recon_entry is not None, f"登记失败：{missing}"
RECON_ID = recon_entry["id"]
print(f"对账单 #{RECON_ID} 已提交，执行对账……")
result = recon.execute_recon(RECON_ID)
print(f"结果：{result['message']}")
recon_entry = recon.get_entry(RECON_ID)
show_recon(recon_entry)
check("5 日已确认", recon_entry["已确认日期"] == DAY1)
check("6 日待确认且原因缺片", "缺片" in result["failures"].get(DAY2, ""))
check("当前合计=448.50", recon_entry["金额合计"] == "448.50")
again = recon.confirm_date(RECON_ID, DAY1)
print(f"重复确认 5 日：{again['message']}")
recon_entry = recon.get_entry(RECON_ID)
check("已确认日期不重复计入", recon_entry["金额合计"] == "448.50"
      and len(recon_entry["读取明细"]) == 3)

print()
print("=" * 72)
print("步骤 4：6 日补来摘要不符的副本 → 沿原恢复单续办 → 继续办理原对账单")
print("=" * 72)
sup = catalog.supplement_copy(TABLE, DAY2, WRONG_DIGEST)
print(f"补登副本：{sup['message']}")
result = restore.resume_restore(RESTORE_ID)
print(f"恢复单续办：{result['message']}")
entry = restore.get_entry(RESTORE_ID)
show_manifest(entry)
check("6 日摘要异常", result["pending"][0]["状态"] == "摘要异常")
check("5 日凭据保留未重验", entry["分区清单"][0]["状态"] == "校验通过")
result = recon.resume_recon(RECON_ID)
print(f"对账单续办：{result['message']}")
recon_entry = recon.get_entry(RECON_ID)
show_recon(recon_entry)
check("6 日仍待确认且原因摘要异常", "摘要" in result["failures"].get(DAY2, ""))
check("合计仍=448.50", recon_entry["金额合计"] == "448.50")

print()
print("=" * 72)
print("步骤 5：6 日补来正确副本 → 沿原恢复单续办 → 继续办理原对账单")
print("=" * 72)
right_digest = catalog.find_exact(TABLE, DAY2)["登记摘要"]
sup = catalog.supplement_copy(TABLE, DAY2, right_digest)
print(f"补登副本：{sup['message']}")
result = restore.resume_restore(RESTORE_ID)
print(f"恢复单续办：{result['message']}")
entry = restore.get_entry(RESTORE_ID)
show_manifest(entry)
check("恢复单已完成", entry["status"] == "已完成")
result = recon.resume_recon(RECON_ID)
print(f"对账单续办：{result['message']}")
recon_entry = recon.get_entry(RECON_ID)
show_recon(recon_entry)

print()
print("=" * 72)
print("步骤 6：汇总 —— 每个日期何时确认、失败原因、最终金额合计")
print("=" * 72)
detail = recon.recon_detail(recon_entry)
for day in detail["逐日状态"]:
    print(f"    {day['日期']}：{day['状态']}，确认时间 {day['确认时间'] or '—'}")
print(f"    失败记录 {len(detail['失败记录'])} 条：")
for rec in detail["失败记录"]:
    print(f"    - {rec['记录时间']} {rec['日期']}：{rec['原因']}")
print(f"    本单最终金额合计：{detail['金额合计']}")
check("对账单已完成", recon_entry["status"] == "已完成")
check("最终合计=848.50", recon_entry["金额合计"] == "848.50")
check("读取明细 5 行且无重复", len(detail["读取明细"]) == 5
      and len({r["订单号"] for r in detail["读取明细"]}) == 5)
check("两个日期都有确认时间", all(d["确认时间"] for d in detail["逐日状态"]))

print()
if failures:
    print(f"场景校验未通过 {len(failures)} 项：{failures}")
    sys.exit(1)
print("场景校验全部通过。")
