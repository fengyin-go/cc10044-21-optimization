<template>
  <section class="page">
    <header class="page-head">
      <div>
        <h2>运营概览</h2>
        <p class="page-desc">汇总各业务模块的关键指标，先看总量再看异常。</p>
      </div>
    </header>
    <div class="stat-row">
      <article v-for="card in cards" :key="card.label" class="stat-card">
        <span class="stat-label">{{ card.label }}</span>
        <strong class="stat-value">{{ card.value }}</strong>
      </article>
    </div>
    <table class="data-table">
      <thead>
        <tr><th>业务模块</th><th>今日新增</th><th>待处理</th><th>异常量</th></tr>
      </thead>
      <tbody>
        <tr v-for="row in moduleRows" :key="row.name">
          <td>{{ row.name }}</td>
          <td>{{ row.created }}</td>
          <td>{{ row.pending }}</td>
          <td>{{ row.abnormal }}</td>
        </tr>
      </tbody>
    </table>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'

import { fetchJson } from '@/api/client'

type Overview = {
  cards: { label: string; value: number }[]
  modules: { name: string; created: number; pending: number; abnormal: number }[]
}

const cards = ref<Overview['cards']>([])
const moduleRows = ref<Overview['modules']>([])

onMounted(async () => {
  try {
    const payload = await fetchJson<Overview>('/api/overview')
    cards.value = payload.cards
    moduleRows.value = payload.modules
  } catch {
    cards.value = [{"label": "业务模块", "value": 0}, {"label": "今日新增", "value": 0}]
    moduleRows.value = [{"name": "ETL流水线", "created": 0, "pending": 0, "abnormal": 0}, {"name": "分区管理", "created": 0, "pending": 0, "abnormal": 0}, {"name": "数据质量规则", "created": 0, "pending": 0, "abnormal": 0}, {"name": "Schema变更管理", "created": 0, "pending": 0, "abnormal": 0}, {"name": "数据血缘", "created": 0, "pending": 0, "abnormal": 0}, {"name": "补数任务", "created": 0, "pending": 0, "abnormal": 0}, {"name": "表归档管理", "created": 0, "pending": 0, "abnormal": 0}, {"name": "聚合表构建器", "created": 0, "pending": 0, "abnormal": 0}, {"name": "流水线监控", "created": 0, "pending": 0, "abnormal": 0}, {"name": "冷数据恢复", "created": 0, "pending": 0, "abnormal": 0}, {"name": "归档文件清单", "created": 0, "pending": 0, "abnormal": 0}, {"name": "日报对账", "created": 0, "pending": 0, "abnormal": 0}, {"name": "维度表生成器", "created": 0, "pending": 0, "abnormal": 0}, {"name": "数据保留策略", "created": 0, "pending": 0, "abnormal": 0}, {"name": "转换方案", "created": 0, "pending": 0, "abnormal": 0}, {"name": "数据迁移任务", "created": 0, "pending": 0, "abnormal": 0}, {"name": "增量合并引擎", "created": 0, "pending": 0, "abnormal": 0}, {"name": "快照生成器", "created": 0, "pending": 0, "abnormal": 0}, {"name": "湖仓审计", "created": 0, "pending": 0, "abnormal": 0}, {"name": "SLA策略", "created": 0, "pending": 0, "abnormal": 0}, {"name": "清理作业", "created": 0, "pending": 0, "abnormal": 0}, {"name": "目录同步", "created": 0, "pending": 0, "abnormal": 0}]
  }
})
</script>
