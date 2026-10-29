# 实验 2 记录：第 4 组

评分时依据本文件和 `runs/` 中的原始数据核对。本文件中的每一个数值，都须能在某个 `runs/<名字>/` 的文件中找到：表格用 `scripts/table.sh` 生成后粘贴，其他数值注明出处（例如“`runs/r1-pool4/red.csv` 第 6 阶段”）。不要只贴截图。

第 4.1、4.4、5.1、5.3 节的预测须在对应的测量之前写好，写好后不要修改。预测错误不扣分，与实测不一致之处在分析中讨论。

## 1. 基本信息

| 项 | 内容 |
|---|---|
| 小组 | 第 4 组 |
| 仓库地址 | https://github.com/Icecube2017/ops-lab2-4 |
| 主机 | 华为云 2 vCPUs 2 GiB |
| CPU | pc-i440fx-4.1  CPU @ 2.0GHz, 2 Core, CPU 0/1 use Core 0 |
| 内存 | 1775 MB |
| 数据目录所在的盘 | SOURCE: /dev/vda1, FSTYPE: ext4, 通用型SSD 40 GiB |
| 操作系统与内核 | Ubuntu 24.04.4 LTS, 6.8.0-136-generic |
| Go、Prometheus、node_exporter | go 1.27.3, prometheus 3.13.4, node_exporter 1.12.1 |
| 分核 | enrolld：1；loadgen、Prometheus、node_exporter：0 |

## 2. 任务 0：基线

`bin/enrolld -bench-fsync 2000` 连续运行三次的输出：

```text
root@ecs-e0a4:/home/sample/ipd_labs/lab2# for i in 1 2 3; do bin/enrolld -bench-fsync 2000; done
fsync 次数 2000，总耗时 3.04 s，数据目录 data
fsync 耗时（毫秒）：mean 1.512  p50 1.351  p90 1.810  p99 4.862  max 10.069
按平均值推算，一个写者串行 fsync 每秒最多约 662 次（= 1000 / 1.512）
fsync 次数 2000，总耗时 2.98 s，数据目录 data
fsync 耗时（毫秒）：mean 1.485  p50 1.341  p90 1.791  p99 4.692  max 6.250
按平均值推算，一个写者串行 fsync 每秒最多约 673 次（= 1000 / 1.485）
fsync 次数 2000，总耗时 2.94 s，数据目录 data
fsync 耗时（毫秒）：mean 1.465  p50 1.339  p90 1.739  p99 4.453  max 5.984
按平均值推算，一个写者串行 fsync 每秒最多约 682 次（= 1000 / 1.465）
```

按平均值计算，单个写者串行 fsync 每秒最多约 673 次。三次之间相差20。

空载和运行 fsync 测试时各工具的读数（各观察 5 秒以上，填写典型值）：

| 工具与列 | 空载 | 运行 `-bench-fsync` 时 | 该列的含义 |
|---|---|---|---|
| `vmstat -w 1`：r、b | 0, 0 | 0, 1 | 可运行的进程数；处于不可中断睡眠的进程数 |
| `vmstat -w 1`：us、sy、id、wa、st | 0, 1, 99, 0, 0  | 1, 3, 50, 47, 0 | 用户态、内核态、空闲、等待 I/O、被 hypervisor 偷走的 CPU 时间占比 |
| `iostat -dx 1 vda`：w/s、wkB/s | 2.00, 8.00 | 2000, 11000 | 每秒完成的写请求数；每秒写入的数据量 |
| `iostat -dx 1 vda`：w_await、f/s、f_await | 0.00, 0.00, 0.00 | 0.48, 1330, 0.04 | 写请求的平均响应时间；每秒 flush 请求数；flush 请求的平均响应时间 |
| `iostat -dx 1 vda`：aqu-sz、%util | 0.00, 0.00 | 1.01, 95.50 | 平均未完成请求数；设备忙碌时间占比 |

运行 fsync 测试时，CPU 是否繁忙？**不繁忙。** `us` 1% + `sy` 3% = 4%，`id` 50%，`st` 0：真正执行指令的时间只有约 4%，CPU 有大量余量，远未饱和；另 47% 是 `wa`（iowait），即 CPU 空闲、只是在等未完成的磁盘 I/O，这部分不能算作“CPU 忙”。与空载（us+sy ≈ 1%）相比，`sy` 从 1% 升到 3%，是 fsync 系统调用和提交 I/O 的开销，量级仍很小。

表明存在等待的列：

- `vmstat`：`wa` 47%，近一半采样时间里 CPU 在等 I/O；`b` 1，有一个进程处于不可中断睡眠（D）状态，正是在等 fsync 返回；`r` 0，没有进程排队等 CPU，说明瓶颈不在 CPU 而在磁盘。
- `iostat`：`%util` 95.5%，vda 几乎整个采样周期都有请求在处理；`aqu-sz` 1.01，平均约有 1 个请求在队列/设备中，与“单写者串行 fsync”一次只有一个未完成 I/O 相符；`w_await` 0.48 ms、`f_await` 0.04 ms，说明单次 I/O 本身很快，时间主要花在等设备确认落盘，而不是 CPU 计算。
- 写量也自洽：`w/s` 2000 × 约 5.5 kB ≈ `wkB/s` 11000；`f/s` 1330 约为 `w/s` 2000 的 2/3，即平均约 1.5 次写伴随 1 次 flush，正是 ext4 日志提交的写放大。

结论：这是 I/O 等待型负载，不是 CPU 密集型负载。判断“CPU 忙不忙”看 `us`+`sy`；判断“在等什么”看 `wa`、`b`、`%util`、`aqu-sz`。

## 3. 任务 1：指标流水线

两个抓取目标的状态（`curl -s 127.0.0.1:9090/api/v1/targets` 中两处 `"health"` 的值，实测于 2026-10-29 00:00，属状态项非数值）：`up`、`up`（enrolld 与 node_exporter 两个目标均正常）

本组编写的四条查询（与 `deploy/queries-red.txt` 一致）：

```text
server_rps          sum(rate(http_requests_total{route="${route}"}[${window}]))
server_error_rate   (sum(rate(http_requests_total{route="${route}",code=~"5.."}[${window}])) or vector(0)) / sum(rate(http_requests_total{route="${route}"}[${window}]))
server_p99_ms       1000 * histogram_quantile(0.99, sum by (le) (rate(http_request_duration_seconds_bucket{route="${route}"}[${window}])))
server_slo_good     sum(rate(http_request_duration_seconds_bucket{route="${route}",code="200",le="${slo}"}[${window}])) / sum(rate(http_request_duration_seconds_count{route="${route}"}[${window}]))
```

`runs/t1-light` 的数据（`scripts/table.sh runs/t1-light ok_rps mean_ms p99_ms server_rps server_mean_ms server_p99_ms cpu_ms_per_req db_query_mean_ms`）：

| step | target_rps | ok_rps | mean_ms | p99_ms | server_rps | server_mean_ms | server_p99_ms | cpu_ms_per_req | db_query_mean_ms |
|---|---|---|---|---|---|---|---|---|---|
| 1 | 0 | 0.0 | - | - | 0 | NaN | NaN | +Inf | NaN |
| 2 | 50 | 50.5 | 11.43 | 12.59 | 51.2083 | 10.7156 | 19.9 | 0.534675 | 10.2989 |
| 3 | 100 | 97.8 | 11.58 | 13.97 | 95.9583 | 10.8815 | 19.9 | 0.515203 | 10.4306 |
| all | - | 74.2 | 11.53 | 13.47 | - | - | - | - | - |

从这次实验读出：每次查询实际占用连接 10.43 ms（`db_query_mean_ms`，出自 `runs/t1-light/red.csv` 第 3 阶段，原值 10.4306）；每个请求消耗的 CPU 0.515 ms（`cpu_ms_per_req`，出自 `runs/t1-light/red.csv` 第 3 阶段，原值 0.515203，即 100 请求/秒的阶段）。

## 4. 任务 2：读路径

### 4.1 预测（R1 之前写）

| 资源 | 容量 | 每个请求占用（数据来源） | 推算的上限（请求/秒） |
|---|---|---|---|
| 连接池 | <`-pool`> 个连接 | <填写> ms | <填写> |
| CPU | enrolld 的 <填写> 个核 | <填写> ms | <填写> |

先达到上限的是：<填写>。预计从多少请求/秒起，客户端 p99 超过 50 ms：<填写>，理由：<填写>

R1 的到达率（`scripts/rates.sh <较小的上限>` 的输出）：<填写>

### 4.2 R1：`-pool 4`

`scripts/table.sh runs/r1-<…> ok_rps error_rate p99_ms server_p99_ms server_mean_ms inflight_avg cpu_ms_per_req pool_in_use_avg pool_wait_p99_ms sched_wait_p99_ms svc_cpu_busy procs_running_avg`：

<粘贴表格>

最大吞吐量 <填写> 请求/秒，出现在第 <填写> 阶段；客户端 p99 首次超过 50 ms 是在第 <填写> 阶段（<填写> 请求/秒）。

### 4.3 CPU 消耗在何处

在约 0.8 倍上限的负载下采集 20 秒 CPU 剖析，`go tool pprof -top` 的前 10 行（同时保存在 `runs/<填写>/pprof-top.txt`）：

```text
<粘贴>
```

`main.spin` 占 <填写>%；按本次的吞吐量和 enrolld 消耗的 CPU 计算，每个请求 <填写> ms，其中 spin 约 <填写> ms，其余 <填写> ms 消耗在：<填写>

### 4.4 R2：调大连接池（预测在 R2 之前写）

| 项 | 值与算法 |
|---|---|
| 每个请求占用的 CPU（取 R1 哪一阶段的 `cpu_ms_per_req`，选取理由） | <填写> |
| 推算的 CPU 上限 C | <填写> |
| R2 的池大小 P2 = ceil(2 × C × 每次查询的秒数) | <填写> |
| R2 的到达率（`scripts/rates.sh C 0.3 0.6 0.8 0.9 1.0 1.1 1.25 1.5`） | <填写> |
| 预测：R2 中先达到上限的是 | <填写> |

`scripts/table.sh runs/r2-<…> ok_rps error_rate p99_ms server_p99_ms inflight_avg cpu_cores cpu_ms_per_req cpu_wait_threads sched_wait_p99_ms pool_in_use_avg svc_cpu_busy procs_running_avg cpu_psi_some`：

<粘贴表格>

最大吞吐量 <填写> 请求/秒；与预测的 C 相差：<填写>

## 5. 任务 3：写路径

### 5.1 预测：`-sync each`（W1 之前写）

| 项 | 值与算法 |
|---|---|
| fsync 平均耗时（任务 0） | <填写> ms |
| 推算的最大 TPS | <填写> |
| 达到上限时 enrolld 大约使用几个核（写路径没有 spin，按 4.3 节拆分出的“spin 以外的开销”估计每个写请求的 CPU） | <填写> |
| W1 的到达率（`scripts/rates.sh <最大 TPS> 0.2 0.4 0.6 0.8 0.9 1.0 1.1 1.2`） | <填写> |

### 5.2 W1：`-sync each`

`scripts/table.sh runs/w1-<…> ok_rps error_rate p99_ms server_p99_ms cpu_cores fsync_per_s fsync_mean_ms batch_mean svc_cpu_busy cpu_iowait disk_w_per_s disk_w_await_ms disk_f_per_s disk_f_await_ms disk_aqu_sz disk_util`：

<粘贴表格>

### 5.3 预测：`-sync group`（W2 之前写）

在 W1 达到上限时的到达率下，改为组提交后：吞吐量 <填写>；平均每次 fsync 处理 <填写> 条记录；客户端 p99 约 <填写>。理由：<填写>

W2 的到达率：<填写>

### 5.4 W2：`-sync group`

（列与 5.2 相同）

<粘贴表格>

### 5.5 W3：`-sync interval`

（列与 5.2 相同）

<粘贴表格>

## 6. 分析问题

每题先列出所用数据（引用上面的表或 `runs/` 中的文件），再写结论，并说明结论的成立条件。每题 300～600 字。

**问题 1：上限由什么决定。** 按 L-07 第 25 页资源账的格式，为 enrolld 的读路径编制一份资源账：资源、每个请求占用（实测）、容量、推算的上限、实测的最大吞吐量、饱和观测项。预测与实测相差多少，差异来自何处？`cpu_ms_per_req` 随负载如何变化，原因是什么？这对“在低负载下测量每请求开销，再推算上限”的做法意味着什么？`db_query_mean_ms` 为什么大于 `-db-ms`？以“p99 ≤ 50 ms”为目标，R1 能承受的最大阶段相当于上限的多少比例？与 L-07 第 6 页“容量 = 上限 × 0.7”比较，为什么尚未达到上限延迟就已上升？R2 调大连接池后，瓶颈转移到了哪个资源？

<填写>

**问题 2：利用率不等于饱和。** R1 达到上限时，enrolld 所在核的 CPU 利用率是多少？哪个数值最先表明资源已经饱和？R2 中 CPU 利用率接近 100% 时，内核层的 `procs_running_avg`、`cpu_psi_some`、`cpu_wait_threads` 和 Go 运行时层的 `sched_wait_p99_ms` 分别如何变化？为什么内核层几乎观察不到排队，排队发生在哪里？这与 L-06 第 16、17 页 Tomcat 的线程池有何相同之处？最后用 Little's Law 核对：从 R1 选取三个阶段，用 `server_rps × server_mean_ms` 计算在途请求数，并与 `inflight_avg` 比较；W 为什么必须用平均值而不能用 p99？哪个阶段不一致，原因是什么？

<填写>

**问题 3：持久性以延迟为代价。** W1 实测的最大 TPS 与 1000 ÷ fsync 平均耗时相比如何？W1 达到上限时 CPU、内存、磁盘的指标各是多少，哪几列表明磁盘饱和？应用测得的 `fsync_mean_ms` 与 `disk_w_await_ms`、`disk_f_await_ms` 为什么不同？一次 fsync 大约对应几次写和几次 flush？W1 与 W2 在 `disk_util` 相近时吞吐量相差多少，`%util` 接近 100% 能否说明磁盘已经饱和？组提交的 `batch_mean` 如何随负载变化，低负载时是否有收益，本组的预测是否正确？W3 快了多少，代价是什么？选课记录应采用哪一种方式，在什么条件下 interval 可以接受？

<填写>

**问题 4：延迟数值的测量位置与方法。** 同一阶段中，客户端与服务端的平均值、p99 各相差多少？列出只计入客户端的时间组成。找出一个服务端 p99 大于客户端 p99 的阶段，并解释原因（L-08 第 10 页）。比较服务端的 `server_slo_good` 与客户端的 `slo_good`；若存在过载阶段，二者为何相差很大？SLI 应在何处测量（L-08 第 19 页）？用 R1 的 `steps.csv`，将各阶段的 p99 求平均，与 `all` 行的 p99 比较，说明百分位数为什么不能平均，以及正确的合并方法。最后计算错误预算：SLO 为“30 天内 99% 的读请求在 50 ms 内成功返回”，若 R1 中第一个不达标阶段的负载持续下去，燃烧率是多少，30 天的预算多久耗尽（L-08 第 21 页）？

<填写>

## 7. 选做记录

| 选做 | 完成情况与证据 |
|---|---|
| O1 内存与 GC | <未做 / 预测、`runs/` 目录、结论> |
| O2 网络 | <未做 / 两台主机的规格与拓扑、`runs/` 或日志、结论> |
| O3 告警与看板 | <未做 / 规则文件、触发记录、看板说明> |
| O4 迁移到小组项目 | <未做 / 仓库链接、测了哪个资源、结论> |

## 8. 成员分工

每位成员一行，只填写 GitHub 用户名，无需填写姓名；行数应与小组人数一致。

| GitHub 用户名 | 负责的部分 |
|---|---|
| sugeladi2450 | 任务 1 指标流水线 |
