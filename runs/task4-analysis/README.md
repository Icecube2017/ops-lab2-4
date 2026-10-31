# 任务 4：证据与复算

## 来源

- `runs/t1-light/`：本地主分支 `684fa93f3807ad47a58cd863a979224e6d7545ec` 已有数据。
- `runs/r1-pool4/`、`runs/r2-pool44/`：原样提取自远端分支
  `origin/feat/task2-results-ecs` 的提交 `900f0d69bb8c8c0f0036986e8df1c4beeec3551a`。
  两次实验记录时间分别为北京时间 2026-10-29 20:47:18、21:06:08。
- 本任务只复用原始数据，不改写 CSV，不把报告撰写时间当作测前预测时间。
- 任务 2 的文字报告另见远端 `origin/feat/task2-read-path-graham`
  提交 `5c6e3a923b520c7845ae4aeb71d9367f481d3ada`；本次不修改其负责的第 4 节。
- `runs/w1-each/`、`runs/w2-group/`、`runs/w3-interval/`：
  经用户已登录的华为云 CloudShell 从服务器 `/home/sample/lab2-task3`
  的提交 `6041c148488a5be92c70d09e39f93b6223f4cd27` 原样导出。
  三次实验分别开始于北京时间 2026-10-30 08:16:58、08:31:04、08:35:48。
  本次不重跑实验，不停止或修改服务器上的服务，不将这些数据描述为新测量。
- `task3-source-LAB2.md` 是该提交中的原始报告副本，保留其原文及已有问题，
  只用于追溯，不是本次任务 4 的最终答案。
- `task3-source-6041c14.tar.gz` 是 `git archive` 导出的原始归档，
  包含上述写路径目录和原始报告。其 PAX `comment` 保存完整提交号。
  CloudShell 文件下载入口未能触发传输，因此经同一远程终端编码导出并在本地解码，
  服务器及本地 SHA-256 均为
  `9be4ea76712e341cba82519ebc7e33070b79974ac6a94a7f7383d02aa503fe3a`。
  单元测试逐文件比对归档与导入内容，防止手工抄数或传输损坏。
- 四题所需读写数据已齐全。严格复算应输出 `"complete": true` 并返回 0；
  若任一写路径目录缺失，默认返回 2，不会以空表冒充完成。

## 复算

在仓库根目录执行：

```bash
python3 -B scripts/analyze_task4.py
python3 -B -m unittest discover -s scripts -p 'test_analyze_task4.py' -v
```

替换数据目录时可以显式指定：

```bash
python3 -B scripts/analyze_task4.py \
  --w1 w1-each --w2 w2-group --w3 w3-interval
```

`summary.json` 中包含源文件的 SHA-256；其余 CSV 是计算结果，不能冒充新的压测：

| 文件 | 内容 |
|---|---|
| `summary.json` | 资源上限、相对偏差、各阶段 p99 的错误平均、正确的已有整体 p99、错误预算 |
| `r1-latency-comparison.csv` | R1 客户端减服务端的平均延迟、p99 和 SLI 差值 |
| `r2-latency-comparison.csv` | R2 同口径对照 |
| `r1-little-law.csv` | R1 各阶段 `server_rps × server_mean_ms / 1000` 与在途均值 |
| `write-comparison.csv` | 三种写路径最大已测成功吞吐阶段的对照，不代表三者都已测出饱和容量 |
| `write-matched-loads.csv` | 相同目标到达率下 each/group、group/interval 的客户端延迟与吞吐比值 |

## 统计口径与限制

1. 客户端延迟只统计成功请求，从计划发送时刻到读完响应体。
   服务端延迟统计全部已进入处理函数且已完成的请求。
2. 客户端按计划发送时刻归属完整 30 秒阶段；Prometheus 查询为阶段末回看
   25 秒，舍去开头 5 秒，并受抓取间隔、外推及直方图桶影响。
3. `steps.csv` 的 `all` 来自 loadgen 合并成功请求的原始延迟后重新求分位数，
   不是阶段 p99 的平均。本仓库未保存逐请求延迟，不能仅凭阶段摘要重建它；
   报告引用的是原始 `all` 行，不是声称重新算出了它。
4. `cpu_ms_per_req` 的分母是全部已完成请求，不是只有成功请求；严重过载时
   不能直接把这一数值视为每个成功请求的 CPU 成本。
5. R2 第 7 阶段应用 `cpu_cores=0.238709` 与资源层单核 100% 不一致。
   `pidstat.log`、`mpstat.log` 支持 CPU 饱和；在没有原始抓取时间和样本数前，
   不能断言异常一定由抓取延迟引起。
6. 错误预算用客户端 `slo_good` 而非仅 HTTP 错误率；CSV 的 SLI 已舍入，
   燃烧率与耗尽时间为近似值。`30 / burn_rate` 还要求初始预算未消耗、
   未来请求率持续稳定，且该请求率代表预算窗口的请求规模。
7. 写路径 `meta.txt` 的 SLO 阈值是 **20 ms**，读路径是 **50 ms**。
   不把两者的 `slo_good` 直接混用；问题 4 的错误预算只基于 R1。
8. W1 最大成功吞吐在第 6 阶段。第 7、8 阶段服务端仍处理积压提交，
   不能把这些阶段的服务端完成率当作客户端成功 TPS；客户端超时也不保证没有落盘。
9. 相近磁盘忙碌率比较取 W1 第 6 阶段和 W2 第 7 阶段。
   interval 的延迟收益取相同目标 2692 RPS 的 W2 第 7、W3 第 3 阶段，
   不以不同到达率下的吞吐差异声称容量提升。W2/W3 最高负载仍无错误，
   因而其真正饱和上限未知。
10. 旧任务 3 报告记录了 W2 预测，但唯一导出的任务 3 提交在实验之后。
    W1/W2 `meta.txt` 只记录未提交改动数，不能证明当时预测的具体内容。
    因此本文只核对已有预测数值，不声称独立验证了测前固化。
    旧报告第 5.3 节还将 W1 第 6 阶段 p99 引为 1264 ms，与原始
    `runs/w1-each/steps.csv` 的 **389.23 ms** 不符；本文使用后者，
    原始报告副本保留不改。

`summary.json` 的 `complete` 仅表示四题所需的读写输入已齐全并通过计算检查，
不表示其他成员负责的任务 2、3 文字已合入主报告，也不表示旧预测的时间线已获验证。

## 官方参考

- Prometheus，Histograms and Summaries：
  https://prometheus.io/docs/practices/histograms/
- Prometheus，`histogram_quantile`：
  https://prometheus.io/docs/prometheus/latest/querying/functions/#histogram_quantile
- Go，`runtime/metrics` 中 `/sched/latencies:seconds`：
  https://pkg.go.dev/runtime/metrics
- Linux 内核，Pressure Stall Information：
  https://docs.kernel.org/accounting/psi.html
- Google SRE Workbook，Alerting on SLOs：
  https://sre.google/workbook/alerting-on-slos/

课程 L-06/L-07/L-08 的讲义不在当前目录，未声称逐页核对；
正文依据题面提供的课程要点、仓库实现、实测数据与上述官方定义撰写。
