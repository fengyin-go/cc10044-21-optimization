"""领域服务注册表：通用资源路由按模块 key 分派到独立业务服务。"""
from __future__ import annotations

from app.services.base import BaseModuleService
from app.services.etl_pipeline import EtlPipelineService
from app.services.partition_manager import PartitionManagerService
from app.services.data_quality_rule import DataQualityRuleService
from app.services.schema_change import SchemaChangeService
from app.services.lineage_graph import LineageGraphService
from app.services.backfill_task import BackfillTaskService
from app.services.table_archive import TableArchiveService
from app.services.agg_builder import AggBuilderService
from app.services.pipeline_monitor import PipelineMonitorService
from app.services.cold_restore import ColdRestoreService
from app.services.archive_file_catalog import ArchiveFileCatalogService
from app.services.daily_report_recon import DailyReportReconService
from app.services.dimension_builder import DimensionBuilderService
from app.services.data_retention import DataRetentionService
from app.services.transform_plan import TransformPlanService
from app.services.data_migration import DataMigrationService
from app.services.delta_merge import DeltaMergeService
from app.services.snapshot_creator import SnapshotCreatorService
from app.services.data_lake_audit import DataLakeAuditService
from app.services.sla_policy import SlaPolicyService
from app.services.cleanup_job import CleanupJobService
from app.services.catalog_sync import CatalogSyncService

SERVICES: dict[str, BaseModuleService] = {
    "etl_pipeline": EtlPipelineService(),
    "partition_manager": PartitionManagerService(),
    "data_quality_rule": DataQualityRuleService(),
    "schema_change": SchemaChangeService(),
    "lineage_graph": LineageGraphService(),
    "backfill_task": BackfillTaskService(),
    "table_archive": TableArchiveService(),
    "agg_builder": AggBuilderService(),
    "pipeline_monitor": PipelineMonitorService(),
    "cold_restore": ColdRestoreService(),
    "archive_file_catalog": ArchiveFileCatalogService(),
    "daily_report_recon": DailyReportReconService(),
    "dimension_builder": DimensionBuilderService(),
    "data_retention": DataRetentionService(),
    "transform_plan": TransformPlanService(),
    "data_migration": DataMigrationService(),
    "delta_merge": DeltaMergeService(),
    "snapshot_creator": SnapshotCreatorService(),
    "data_lake_audit": DataLakeAuditService(),
    "sla_policy": SlaPolicyService(),
    "cleanup_job": CleanupJobService(),
    "catalog_sync": CatalogSyncService(),
}


def service_for(module: str) -> BaseModuleService | None:
    return SERVICES.get(module)
