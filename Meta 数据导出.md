# Meta 数据导出

最后更新：2026-04-29

本文档给后续 Agent 使用，用于从 ADS Power 启动的 Meta Ads Manager 浏览器导出 Newjoyloo 的 Meta 广告数据，并导入 AutoVideoSrt 线上广告分析库。

## 目标

- 按天导出 Meta 广告数据。
- 每天导两层：
  - 广告系列层：产品维度消耗，文件名形如 `newjoyloo_campaigns_2026-03-01.csv`。
  - 广告素材层：单条素材维度消耗，文件名形如 `newjoyloo_ads_2026-03-01.csv`。
- 导入线上数据库后，产品看板可以按月/周看到产品广告消耗。

## 当前环境

- ADS Power 环境：90 号环境。
- 本地 CDP 调试端口：`http://127.0.0.1:9845`。
- Meta ad account：`2110407576446225`。
- Meta business：`476723373113063`。
- 线上服务器：`172.30.254.14`。
- 线上 Web 目录：`/opt/autovideosrt`。
- 线上数据库：`auto_video`。
- 线上页面：[http://172.30.254.14/order-analytics](http://172.30.254.14/order-analytics)。
- SSH key：`C:\Users\admin\.ssh\CC.pem`。

注意：这套流程已经确认是导入线上环境，不是测试环境。测试环境目录是 `/opt/autovideosrt-test`，数据库是 `auto_video_test`，不要误用。

## 关键前提

1. ADS Power 90 号环境必须已经打开，并且 Meta 广告后台处于可访问/已登录状态。
2. ADS Power / SunBrowser 必须开放 CDP 调试端口 `9845`。
3. `127.0.0.1:9845` 只在本地 Windows 机器可访问，服务器不能直接连这个浏览器。所以“导出 CSV”必须在本地跑，“入库”再上传到服务器执行。
4. 当前可用实现位于 worktree：
   - `G:\Code\AutoVideoSrtLocal\.worktrees\meta-daily-ad-import\scripts\run_meta_ads_backfill_range.py`
   - `G:\Code\AutoVideoSrtLocal\.worktrees\meta-daily-ad-import\appcore\order_analytics.py`
5. 如果这些改动后续已经合并到主仓库，则优先使用主仓库同名路径。

## 数据表

日维度导入使用这三张表：

- `meta_ad_daily_import_batches`
- `meta_ad_daily_campaign_metrics`
- `meta_ad_daily_ad_metrics`

旧的手工上传总报表表：

- `meta_ad_import_batches`
- `meta_ad_campaign_metrics`

旧表历史数据已经在 2026-04-28 做过备份并清空。不要再次清空，除非用户明确要求。

## 已完成范围

截至 2026-04-29 前的操作记录：

- 已导入：`2026-01-01` 到 `2026-02-28`
- 已导入：`2026-03-01` 到 `2026-03-31`
- 已导入：`2026-04-01` 到 `2026-04-27`

不要凭记忆判断是否已导入。每次跑新范围前都先查库。

## 第一步：查库避重

先确认目标日期是否已经存在，避免重复导。

```powershell
@'
SELECT report_date, COUNT(*) AS campaign_rows, ROUND(SUM(spend_usd),2) AS spend
FROM meta_ad_daily_campaign_metrics
WHERE ad_account_id='2110407576446225'
  AND report_date BETWEEN '2026-01-01' AND '2026-02-28'
GROUP BY report_date
ORDER BY report_date;
'@ | ssh -i C:\Users\admin\.ssh\CC.pem -o StrictHostKeyChecking=no root@172.30.254.14 "mysql -N -B auto_video"
```

如果某一天已经有数据，就不要再导那一天。导出脚本会跳过本地已有 CSV，但真正避重以线上 DB 查询为准。

## 第二步：本地导出 CSV

导出脚本会用 Playwright 连接 ADS Power 浏览器 CDP：

- CDP：`http://127.0.0.1:9845`
- 广告系列 URL 层级：`/adsmanager/manage/campaigns`
- 广告素材 URL 层级：`/adsmanager/manage/ads`
- 日期参数：`date=YYYY-MM-DD_YYYY-MM-DD`
- 列配置：`column_preset=1658418688523178`

建议每个日期窗口至少 60 秒，每 7 天额外休息 3 到 4 分钟。更保守可以把 `--min-day-seconds` 改成 `300`。

示例：导出 2026-01-01 到 2026-02-28。

```powershell
$worktree = 'G:\Code\AutoVideoSrtLocal\.worktrees\meta-daily-ad-import'
$outDir = 'G:\Code\AutoVideoSrtLocal\scratch\meta_ads_backfill_2026_01_02'
$outLog = Join-Path $outDir 'export.log'
$errLog = Join-Path $outDir 'export.err.log'

New-Item -ItemType Directory -Force -Path $outDir | Out-Null
Remove-Item -LiteralPath $outLog,$errLog -ErrorAction SilentlyContinue

$p = Start-Process -FilePath python `
  -WorkingDirectory $worktree `
  -ArgumentList @(
    'scripts\run_meta_ads_backfill_range.py',
    '--start','2026-01-01',
    '--end','2026-02-28',
    '--out',$outDir,
    '--long-rest-every-days','7',
    '--min-day-seconds','60'
  ) `
  -RedirectStandardOutput $outLog `
  -RedirectStandardError $errLog `
  -PassThru `
  -WindowStyle Hidden

$p | Select-Object Id,StartTime,Path
```

轮询进度：

```powershell
$pid = 2632
$outDir = 'G:\Code\AutoVideoSrtLocal\scratch\meta_ads_backfill_2026_01_02'

Get-Process -Id $pid -ErrorAction SilentlyContinue | Select-Object Id,StartTime,CPU
Get-Content -Path (Join-Path $outDir 'export.log') -Tail 80
Get-Content -Path (Join-Path $outDir 'export.err.log') -Tail 20
(Get-ChildItem -Path $outDir -Filter *.csv).Count
```

成功日志应类似：

```text
DONE attempted 118 failures []
```

文件数计算：

- 59 天导出两层：`59 × 2 = 118` 个 CSV。
- 31 天导出两层：`31 × 2 = 62` 个 CSV。

如果有 `RETRYABLE_FAIL` 但最终 `failures []`，说明脚本已自动重试成功。只有出现 `FAILED_FINAL` 或最终 `failures [...]` 时才需要补跑。

## 第三步：上传 CSV 到服务器

```powershell
ssh -i C:\Users\admin\.ssh\CC.pem -o StrictHostKeyChecking=no root@172.30.254.14 "rm -rf /tmp/meta_ads_2026_01_02 && mkdir -p /tmp/meta_ads_2026_01_02"

scp -i C:\Users\admin\.ssh\CC.pem -o StrictHostKeyChecking=no `
  G:\Code\AutoVideoSrtLocal\scratch\meta_ads_backfill_2026_01_02\*.csv `
  root@172.30.254.14:/tmp/meta_ads_2026_01_02/
```

上传导入逻辑文件。若代码已经合并并发布到线上，可以直接用 `/opt/autovideosrt/appcore/order_analytics.py`；若还没合并，用当前 worktree 的文件上传到 `/tmp`：

```powershell
scp -i C:\Users\admin\.ssh\CC.pem -o StrictHostKeyChecking=no `
  G:\Code\AutoVideoSrtLocal\.worktrees\meta-daily-ad-import\appcore\order_analytics.py `
  root@172.30.254.14:/tmp/order_analytics_daily.py
```

确认上传数量：

```powershell
ssh -i C:\Users\admin\.ssh\CC.pem -o StrictHostKeyChecking=no root@172.30.254.14 "find /tmp/meta_ads_2026_01_02 -maxdepth 1 -name '*.csv' | wc -l"
```

## 第四步：导入线上库

下面脚本会解析 `/tmp/meta_ads_2026_01_02` 下所有 `newjoyloo_*.csv`，并导入线上 `auto_video`。

```powershell
$py = @'
from pathlib import Path
import importlib.util
import json

spec = importlib.util.spec_from_file_location('order_analytics_daily', '/tmp/order_analytics_daily.py')
oa = importlib.util.module_from_spec(spec)
spec.loader.exec_module(oa)

account_id = '2110407576446225'
account_name = 'Newjoyloo'
base = Path('/tmp/meta_ads_2026_01_02')
files = sorted(base.glob('newjoyloo_*.csv'))

summary = {
    'file_count': len(files),
    'campaign_files': 0,
    'ad_files': 0,
    'parsed_rows': 0,
    'parsed_spend': 0.0,
    'imported': 0,
    'updated': 0,
    'skipped': 0,
    'matched': 0,
    'errors': [],
}

for path in files:
    try:
        level = 'ads' if '_ads_' in path.name else 'campaigns'
        summary['ad_files' if level == 'ads' else 'campaign_files'] += 1
        file_bytes = path.read_bytes()
        rows = oa.parse_meta_daily_report(
            path.open('rb'),
            path.name,
            ad_account_id=account_id,
            ad_account_name=account_name,
        )
        result = oa.import_meta_daily_rows(
            rows,
            filename=path.name,
            file_bytes=file_bytes,
            source_timezone_name=None,
            source_utc_offset_minutes=-480,
        )
        summary['parsed_rows'] += len(rows)
        summary['parsed_spend'] += sum(float(r.get('spend_usd') or 0) for r in rows)
        for key in ['imported', 'updated', 'skipped', 'matched']:
            summary[key] += int(result.get(key) or 0)
    except Exception as exc:
        summary['errors'].append({'file': path.name, 'error': repr(exc)})

monthly_campaign = oa.query(
    "SELECT DATE_FORMAT(report_date, '%%Y-%%m') AS ym, COUNT(DISTINCT report_date) AS days, "
    "COUNT(*) AS campaign_rows, ROUND(SUM(spend_usd), 2) AS spend "
    "FROM meta_ad_daily_campaign_metrics WHERE ad_account_id=%s AND report_date BETWEEN %s AND %s "
    "GROUP BY ym ORDER BY ym",
    (account_id, '2026-01-01', '2026-02-28'),
)
monthly_ads = oa.query(
    "SELECT DATE_FORMAT(report_date, '%%Y-%%m') AS ym, COUNT(DISTINCT report_date) AS days, "
    "COUNT(*) AS ad_rows, ROUND(SUM(spend_usd), 2) AS spend "
    "FROM meta_ad_daily_ad_metrics WHERE ad_account_id=%s AND report_date BETWEEN %s AND %s "
    "GROUP BY ym ORDER BY ym",
    (account_id, '2026-01-01', '2026-02-28'),
)
day_count = oa.query(
    "SELECT COUNT(*) AS days, ROUND(SUM(spend),2) AS total_spend FROM ("
    "SELECT report_date, SUM(spend_usd) AS spend FROM meta_ad_daily_campaign_metrics "
    "WHERE ad_account_id=%s AND report_date BETWEEN %s AND %s GROUP BY report_date"
    ") x",
    (account_id, '2026-01-01', '2026-02-28'),
)

summary['parsed_spend'] = round(summary['parsed_spend'], 2)
print(json.dumps({
    'summary': summary,
    'monthly_campaign': monthly_campaign,
    'monthly_ads': monthly_ads,
    'day_count': day_count,
}, ensure_ascii=False, default=str))
'@

$encoded = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($py))
$encoded | ssh -i C:\Users\admin\.ssh\CC.pem -o StrictHostKeyChecking=no root@172.30.254.14 "cd /opt/autovideosrt && base64 -d -i | /opt/autovideosrt/venv/bin/python -"
```

重要：`source_utc_offset_minutes=-480` 是当前导入时使用的 Meta 源日期偏移。导出 URL 本身按 Meta Ads Manager 的日历日期查询，入库保留 `report_date`。不要在没有用户确认的情况下把 Meta 日历日重新按小时切分。

## 第五步：验数

核对天数、月度消耗、两层金额是否一致：

```powershell
@'
SELECT COUNT(*) AS days, ROUND(SUM(spend),2) AS total_spend FROM (
  SELECT report_date, SUM(spend_usd) AS spend
  FROM meta_ad_daily_campaign_metrics
  WHERE ad_account_id='2110407576446225' AND report_date BETWEEN '2026-01-01' AND '2026-02-28'
  GROUP BY report_date
) x;

SELECT DATE_FORMAT(report_date, '%Y-%m') AS ym, COUNT(DISTINCT report_date) AS days, COUNT(*) AS campaign_rows, ROUND(SUM(spend_usd),2) AS spend
FROM meta_ad_daily_campaign_metrics
WHERE ad_account_id='2110407576446225' AND report_date BETWEEN '2026-01-01' AND '2026-02-28'
GROUP BY ym ORDER BY ym;

SELECT DATE_FORMAT(report_date, '%Y-%m') AS ym, COUNT(DISTINCT report_date) AS days, COUNT(*) AS ad_rows, ROUND(SUM(spend_usd),2) AS spend
FROM meta_ad_daily_ad_metrics
WHERE ad_account_id='2110407576446225' AND report_date BETWEEN '2026-01-01' AND '2026-02-28'
GROUP BY ym ORDER BY ym;
'@ | ssh -i C:\Users\admin\.ssh\CC.pem -o StrictHostKeyChecking=no root@172.30.254.14 "mysql -N -B auto_video"
```

检查产品匹配/未匹配金额：

```powershell
@'
SELECT IF(product_id IS NULL,'unmatched','matched') AS product_status, COUNT(*) AS campaign_rows, ROUND(SUM(spend_usd),2) AS spend
FROM meta_ad_daily_campaign_metrics
WHERE ad_account_id='2110407576446225' AND report_date BETWEEN '2026-01-01' AND '2026-02-28'
GROUP BY product_status;
'@ | ssh -i C:\Users\admin\.ssh\CC.pem -o StrictHostKeyChecking=no root@172.30.254.14 "mysql -N -B auto_video"
```

检查产品看板函数能读到数据：

```powershell
$py = @'
import importlib.util, json
spec = importlib.util.spec_from_file_location('order_analytics_daily', '/tmp/order_analytics_daily.py')
oa = importlib.util.module_from_spec(spec)
spec.loader.exec_module(oa)

out = {}
for month in (1, 2):
    data = oa.get_dashboard(period='month', year=2026, month=month, compare=True)
    out[str(month)] = {
        'period': data['period'],
        'summary': data['summary'],
        'product_count': len(data['products']),
        'top_products': [
            {
                'product_code': r.get('product_code'),
                'product_name': r.get('product_name'),
                'spend': r.get('spend'),
                'orders': r.get('orders'),
                'revenue': r.get('revenue'),
            }
            for r in data['products'][:3]
        ],
    }
print(json.dumps(out, ensure_ascii=False, default=str))
'@

$encoded = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($py))
$encoded | ssh -i C:\Users\admin\.ssh\CC.pem -o StrictHostKeyChecking=no root@172.30.254.14 "cd /opt/autovideosrt && base64 -d -i | /opt/autovideosrt/venv/bin/python -"
```

页面入口：[http://172.30.254.14/order-analytics](http://172.30.254.14/order-analytics)

看“产品看板”，选择对应年月。注意“广告分析”tab 仍是旧的手工上传视图，不是这套 daily import 的主要查看入口。

## 已验证结果示例

2026-01-01 到 2026-02-28：

- CSV：118 个。
- 导出日志：`DONE attempted 118 failures []`。
- 入库天数：59 天。
- 1 月广告系列层总消耗：`$6,652.39`。
- 2 月广告系列层总消耗：`$5,963.00`。
- 1-2 月合计：`$12,615.39`。
- 广告系列层和素材层月度消耗一致。

2026-03-01 到 2026-03-31：

- CSV：62 个。
- 入库天数：31 天。
- 3 月广告系列层总消耗：`$43,737.11`。
- 广告系列层和素材层每日消耗一致。

2026-04-01 到 2026-04-27：

- 广告系列层总消耗：`$146,284.70`。

## 常见问题

### 1. ADS Power 浏览器连不上

先确认 90 号环境已打开，并且 CDP 端口是 `9845`。本地检查：

```powershell
netstat -ano | Select-String ':9845'
```

如果端口没开，需要在 ADS Power / SunBrowser 里打开调试端口后再跑。

### 2. 只有服务器可以跑吗

不可以。服务器访问不到 `127.0.0.1:9845`，导出必须在本地 Windows 机器跑。服务器只负责接收 CSV 和入库。

### 3. Meta 点击导出超时

日志出现一次 `RETRYABLE_FAIL` 不一定有问题。脚本最多重试 3 次，只要最终有 `SAVED ...csv` 且最后 `failures []`，就算成功。

### 4. 产品看板金额小于 Meta 总金额

这是产品匹配问题，不是 CSV 丢失。所有原始 daily 数据都入库了；`product_id IS NULL` 的行会保留在表里。后续补产品映射后，可以进入产品维度统计。

### 5. 是否可以重复导同一天

不建议。导入表有唯一键保护，但操作上应先查 DB，跳过已存在日期。确实要重导时，先和用户确认是否删除该日旧数据或允许覆盖。

## 后续建议

- 如果要继续历史回填，按月分批跑，每批跑前先查库确认缺口。
- 如果要每日自动导昨天数据，仍需本地 ADS Power 环境和 CDP 端口可用；服务器端定时任务无法直接控制本地浏览器。
- 每次导完都至少核对三件事：CSV 文件数、最终 `failures []`、DB 中 campaign/ad 两层月度消耗一致。
