-- print_templates：模板的当前版本（设计计划 §2）。⚠️ 不分区——模板是
-- 全局资产（一个客户就一套送货单模板），数量随"客户配了多少张单据模板"
-- 有界增长，不随渲染次数增长，不属于 §11.2.1"可能无限增长的业务表"，
-- 不强制那四个字段；`version` 在这里身兼两职：既是"这是第几个版本"的
-- 业务编号，也天然充当乐观并发的版本计数器——每次编辑都恰好加一，两个
-- 概念在这张表上重合，不是遗漏了标准的版本列。
CREATE TABLE print_templates (
    id         TEXT        PRIMARY KEY,
    name       TEXT        NOT NULL,
    channel    TEXT        NOT NULL CHECK (channel IN ('PDF', 'ZPL')),
    content    TEXT        NOT NULL,   -- HTML+CSS 源码，或 ZPL 指令模板文本
    version    INT         NOT NULL DEFAULT 1,
    enabled    BOOLEAN     NOT NULL DEFAULT true,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- print_template_versions：历史版本，只增不改（设计计划 §2）。回滚 =
-- 把某个历史版本复制成新的当前版本，不是删掉新版本——本表因此永远只
-- INSERT，从不 UPDATE/DELETE。同样不分区（§7：模板历史要能一直回滚，
-- 永远热）。
CREATE TABLE print_template_versions (
    id          BIGSERIAL   PRIMARY KEY,
    template_id TEXT        NOT NULL REFERENCES print_templates (id),
    name        TEXT        NOT NULL,
    channel     TEXT        NOT NULL CHECK (channel IN ('PDF', 'ZPL')),
    content     TEXT        NOT NULL,
    version     INT         NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX print_template_versions_uniq ON print_template_versions (template_id, version);
CREATE INDEX print_template_versions_lookup ON print_template_versions (template_id, version DESC);

-- print_jobs：渲染记录，纯审计与排障，不存渲染结果字节（设计计划 §2）。
-- ⚠️ 本组件唯一"可能无限增长"的表（随渲染调用次数增长，不是随模板
-- 数量），按月分区，§11.2.1 的强制字段在这张表上真的适用。写入即终态
-- （§2 终态列表）——status 只在 INSERT 时定好，不存在"先 PENDING 再
-- 转终态"这种流程，version/updated_at 永远保持默认值，仍然按规范建，
-- 是给"这张表以后要不要支持修正记录"这种假设性演进留的一致性余量，
-- 不是当前就会用到。
CREATE TABLE print_jobs (
    id               BIGSERIAL,
    template_id      TEXT        NOT NULL,
    actor            TEXT        NOT NULL DEFAULT '',  -- 调用方的 sub；组件间 gRPC 调用可能是空字符串
    source_component TEXT        NOT NULL DEFAULT '',  -- 调用方组件 id，如 erp/sales
    status           TEXT        NOT NULL CHECK (status IN ('SUCCESS', 'FAILED')),
    error            TEXT        NOT NULL DEFAULT '',
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    version          BIGINT      NOT NULL DEFAULT 1,
    PRIMARY KEY (id, created_at)
) PARTITION BY RANGE (created_at);
CREATE INDEX print_jobs_template_idx ON print_jobs (template_id, created_at);

-- ⚠️ 初始分区覆盖当前月起 3 个月（迁移执行时是 2026-09），同
-- infra-notification 的既有余量。归档窗口是 3 个月（§7），其余分区由
-- 组件内置定时任务自动建，分区名格式必须是 "表名_YYYY_MM_01"。
CREATE TABLE print_jobs_2026_09_01 PARTITION OF print_jobs
  FOR VALUES FROM ('2026-09-01') TO ('2026-10-01');
CREATE TABLE print_jobs_2026_10_01 PARTITION OF print_jobs
  FOR VALUES FROM ('2026-10-01') TO ('2026-11-01');
CREATE TABLE print_jobs_2026_11_01 PARTITION OF print_jobs
  FOR VALUES FROM ('2026-11-01') TO ('2026-12-01');

-- ⚠️ 只有分区表需要转 owner（给 infra_print_rw 以后做 DETACH PARTITION
-- 之类的分区级 DDL）——print_templates/print_template_versions 不分区，
-- DML 靠 schema 的 ALTER DEFAULT PRIVILEGES 就有权限，不需要是 owner
-- （真机验证过的既有结论，同 infra-notification/integration-im-dingtalk
-- 的既有判据；同 infra-workflow 的既有先例：它的两张非分区表同样没有
-- 这一行）。
ALTER TABLE print_jobs OWNER TO infra_print_rw;
