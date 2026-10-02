-- print_template_versions.enabled：写入这个版本时模板的启用状态，版本列表
-- （GET /templates/{id}/versions）要返回它。已有行按当前模板回填：当前版本取
-- print_templates.enabled，更早的版本无从得知，按启用记。
ALTER TABLE print_template_versions ADD COLUMN IF NOT EXISTS enabled BOOLEAN NOT NULL DEFAULT true;

UPDATE print_template_versions v
   SET enabled = t.enabled
  FROM print_templates t
 WHERE v.template_id = t.id AND v.version = t.version AND v.enabled <> t.enabled;
