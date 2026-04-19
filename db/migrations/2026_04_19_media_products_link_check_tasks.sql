ALTER TABLE media_products
  ADD COLUMN link_check_tasks_json JSON NULL COMMENT '按语种保存最近一次链接检测任务摘要 {lang: payload}';
