# SQL / 数据层审计报告（v23 · N2 节点）

- 审计日期：2026-09-28
- 范围（只读，未改任何文件）：`api/db/`（queries / core / migrations / queue_store / lease_store / ip_blocklist_store / queue_db_legacy）、`api/account_pool/`（store / borrow / stats / signin / _base）、`api/email_pool.py`、`api/chat_usage.py`、`api/adaptive_router.py`、`api/agent/memory.py`、`api/agent/human_inbox.py`、`api/agent/skill_sediment.py`、`api/vector/store.py`、`api/routes/admin/query.py`、`api/routes/security.py`、`api/routes/agent_*`
- 验证方式：静态阅读 + `sqlite3` 只读模式 dump 实际 schema + `EXPLAIN QUERY PLAN` 实证热查询执行计划 + aiosqlite 行为实测（isolation_level=None autocommit）。
- 结论标签：`已验证`（实跑观察到）/ `静态确认`（读代码确认）/ `合理推断`（有间接证据）。

---

## 0. 数据规模与 schema 事实（对照实际 sqlite_master）

实测 `data/*.db`（只读，`mode=ro`）：

| 库 | 表 | 行数（本地开发库） | 关键索引 |
|---|---|---|---|
| imagefree.db | requests | 0 | created_at / status / finished_at / (status,finished_at) / (status,created_at) / (created_at,status) |
| | chat_usage | 0 | created_at / (model,created_at) / month / (provider,created_at) / (provider,model) |
| | idempotency_keys / dead_letter_queue / cache_store / mem_* / ip_blocklist | 0~1 | created_at / dlq.created_at 等 |
| account_pool.db | accounts | 2 | (provider,status) / (provider,status,credits,updated_at) |
| email_registry.db | email_registry / domain_risk | 111 / 17 | email_registry(provider) |
| edit_leases.db | edit_leases | — | key PRIMARY KEY |

本地库行数为 0，**索引缺口按生产量级（万~百万行）推断严重度**。`sqlite_stat1` 仅含 mem 表统计，`requests`/`chat_usage`/`accounts` 无 ANALYZE 统计采样（仅表存在），热路径索引选择依赖字段结构而非统计。

---

## 1. 慢查询清单（全表扫描 / 无索引 WHERE / 无 LIMIT）

### S1 【P1】`stats_overview` 全表聚合，每请求全表扫描 — `api/db/queries.py:409-434`
- SQL：`SELECT COUNT(*), SUM(CASE WHEN status='completed' ...), AVG(...) FROM requests WHERE status != 'archived'`
- EXPLAIN 实证：`SCAN requests`（表扫描，无索引可用；`status != 'archived'` 无法走 `idx_requests_status`）。
- 触发面：`/v1/stats`（已缓存）与 **`/metrics` 裸扫**（`api/routes/admin/query.py:484` 直接 `await db.stats_overview()`，每次 Prometheus 抓取都全表聚合）。
- 建议：
  - 建覆盖索引，把表扫描降为索引覆盖扫描：
    ```sql
    CREATE INDEX IF NOT EXISTS idx_requests_status_dur ON requests(status, duration_sec, created_at);
    ```
    （`WHERE status != 'archived'` 退化为 `status IN ('completed','error','failed','processing','pending',...)` 后可用 `idx_requests_status_dur` 覆盖，避免逐行回表。）
  - 或引入周期 rollup 计数器表（夜间任务增量写 `daily_counts`，stats 直接读），完全消除实时全表聚合。**建议 rollup，覆盖索引只是降级**（百万行下 AVG 仍需扫全部命中行）。

### S2 【P1】`gallery_list` 默认（无过滤）走全索引扫描 — `api/db/queries.py:311-355`
- WHERE：`status NOT IN ('deleted','pending','archived') AND image_url IS NOT NULL`。
- EXPLAIN 实证（等价值）：`SCAN requests USING INDEX idx_requests_finished` —— **负向 NOT IN 让优化器放弃 `idx_requests_status_finished` 前缀**，退化为按 finished_at 全索引扫描后逐行校验 status/image_url。同为 `status='completed' AND image_url IS NOT NULL` 时则 `SEARCH ... USING INDEX idx_requests_status_finished`（实证），量级差异巨大。
- 建议：正向白名单化，配合覆盖索引：
    ```sql
    CREATE INDEX IF NOT EXISTS idx_requests_gallery ON requests(status, finished_at);
    ```
    并把 `status NOT IN ('deleted','pending','archived')` 改为 `status IN ('completed','error','failed','cancelled','processing')`（与画廓展示口径一致，见正确性 C3）。

### S3 【P2】`list_tasks` 按 `duration_sec`/`status`/`model` 排序或过滤 — 全扫 + 临时排序 — `api/db/queries.py:256-296`
- `allowed_sort` 含 `duration_sec`、`status`、`model`；EXPLAIN 实证 `sort=duration_sec`：`SCAN requests | USE TEMP B-TREE FOR ORDER BY`；`model=?` 过滤（无 model 索引）：`SCAN requests`。
- 触发面：管理面板任务列表/仪表盘，中等量级下可感知。
- 建议：
    ```sql
    CREATE INDEX IF NOT EXISTS idx_requests_model ON requests(model, created_at);
    CREATE INDEX IF NOT EXISTS idx_requests_duration ON requests(duration_sec);
    ```
    （更深优化：按主键 id keyset 游标翻页替代 OFFSET，见 C1。）

### S4 【P1】`stats_daily` / `stats_monthly` 按 day/month 聚合全表扫描 — `api/db/queries.py:436-478`
- SQL：`... WHERE day >= ? AND status != 'archived' GROUP BY day ORDER BY day`。
- EXPLAIN 实证：`SCAN requests | USE TEMP B-TREE FOR GROUP BY`。**requests 表的 day/month 列（`_apply_request_migrations` 补列，migrations.py:139-155）没有任何索引**。
- 建议：
    ```sql
    CREATE INDEX IF NOT EXISTS idx_requests_day ON requests(day, status);
    CREATE INDEX IF NOT EXISTS idx_requests_month ON requests(month, status);
    ```

### S5 【P2】`recent_errors` 按 status='error' 排序 created_at — 有 (status,created_at) 索引，联通但回表
- `api/db/queries.py:396-406`：`WHERE status='error' ORDER BY created_at DESC LIMIT ?` —— 有 `idx_requests_status_created` 可走，列数少回表代价小，P3/观察项（所列多余，**不构成问题，仅记录"覆盖索引可再省回表"**）。

### S6 【P2】导出端点 `export_tasks` 大表时间范围扫描 + 排序 — `api/routes/admin/query.py:824-828`
- SQL：`SELECT <10列> FROM requests <where> ORDER BY created_at DESC LIMIT 100001`。
- EXPLAIN 实证：`SEARCH requests USING INDEX idx_requests_created (created_at>? AND created_at<?)` —— 有索引，但**取 `_EXPORT_MAX_ROWS+1=100001` 行全量进内存**再截断；`model LIKE 'provider/%'` 过滤（无索引）会回退全扫。行数上限存在（好），但上限过大（10 万行 JSON/CSV 流式写内存 `StringIO`）。
- 建议：改流式游标逐行写 `StreamingResponse`（当前一次性 `fetchall` + `io.StringIO`），并给 `(model)` 加索引（同 S3）。

### S7 【P2】`recover_stale_tasks` / `cleanup` / `cleanup_batched` / `archive_tasks` 写侧
- `recover_stale_tasks`（queries.py:168-179）：`UPDATE ... WHERE status IN ('pending','processing') AND created_at < ?` —— EXPLAIN 实证用 `idx_requests_status_created` 覆盖，连通。
- `cleanup`（queries.py:481-509）：`DELETE FROM requests WHERE created_at < ?` 走 `idx_requests_created`，但**无 LIMIT 单条全删**（注释已说明被 `cleanup_batched` 取代，仍留作兼容入口）；`cleanup_batched`（537-581）每批 5000 且 `while` 循环 `SELECT id ... LIMIT ?` + `DELETE ... IN`，设计正确。`DELETE ... IN` 的占位符串由 `",".join("?"*len(ids))` 生成，参数安全（ids 为 DB 自产）。**注意：这两条大 DELETE 在持 `conn_lock` 的全程执行，若 retention 覆盖全表（如 0 行清理），长事务期间写连接被独占，批内其他写请求排队** —— 可接受但建议批间隔 `await asyncio.sleep(0)` 让出 loop。
- `archive_tasks`（511-535）：无批处理，单条 `UPDATE ... WHERE finished_at < ?` 大范围更新（按 `finished_at` 索引定位后全量更新）；终态行量大时单事务耗时，建议参照 `cleanup_batched` 分批。**P2 观察项**。

### S8 【P2】`account_pool.list_page` / `dashboard` — `accounts` 无 created_at 索引 — `api/account_pool/store.py:64-112`、`stats.py:24`
- EXPLAIN 实证：`list_page(provider=) ORDER BY created_at DESC LIMIT ? OFFSET ?` → `SEARCH accounts USING INDEX idx_acc_provider_status_credits (provider=?) | USE TEMP B-TREE FOR ORDER BY`（命中后全量排序）。`GROUP BY provider,status` 走覆盖索引（好）。
- 建议：
    ```sql
    CREATE INDEX IF NOT EXISTS idx_acc_provider_created ON accounts(provider, created_at);
    ```
    同时 `list_page` 的 `search` 分支 `(email LIKE ? OR status LIKE ? OR register_ip LIKE ?)`（store.py:91-93）为 `%term%` 前导通配，**任何索引均失效**，全表扫号池——管理端搜索可接受，但需注意量级（号池百万行时一次搜索秒级）。

### S9 【P3】`wake_cooling_accounts` 无 provider 过滤时全表扫 — `api/account_pool/borrow.py:220-317`
- SQL：`SELECT provider,email,credits FROM accounts WHERE status IN ('cooling','exhausted') AND (cooling_since IS NULL OR (?-cooling_since)>=?)`。`idx_acc_provider_status` 以 provider 为前缀，**status-only 无法命中** → 全表扫。同时 `borrow.py:246` 的 SELECT 与后续逐行 UPDATE 在同一 `self._lock` + 单连接内串行，无并发问题，仅性能。建议（号池大时）：
    ```sql
    CREATE INDEX IF NOT EXISTS idx_acc_status_cooling ON accounts(status, cooling_since);
    ```

### S10 【P2】无 LIMIT 的读取（潜在全量加载）
- `api/email_pool.py:172-175` `_load_used`：启动时 `SELECT email FROM email_registry` **全表读入内存 set**，注册量达十万级时启动内存/耗时上升；建议批读或改为"注册时按需去重查询"。
- `api/agent/memory.py:416-418/472` `consolidate()`：`SELECT id,user_key,scene,... FROM mem_observations` 与 `refresh()` 全表加载 L0 —— L0 表若累积数万条会膨胀内存；P2 观察项（consolidate 会随后清空该表，尚可控）。
- `api/agent/human_inbox.py:102` `SELECT * FROM inbox_requests` 启动全量载入内存缓存 —— 与 `_load_used` 同类，规模小暂可接受（P3）。

---

## 2. 注入审查（逐文件结论）

**总评：未发现可利用的 SQL 注入。** 全仓 27 个含 SQL 文件：

- `api/db/queries.py` / `core.py` / `migrations.py` —— 全部 `?` 占位符参数化；唯一 f-string 是列名单 `cols = ", ".join(self._XXX_COLS)`（queries.py:248/289/302/349）与 `sort in allowed_sort` 白名单（285-287），**静态确认安全**。
- `api/routes/admin/query.py:824-828` —— where 条件全部 `?`，`_EXPORT_COLUMNS` 常量；验证同上。
- `api/account_pool/store.py` —— 全部 `?`；`WHERE` 由 conds 列表拼接但值全部走占位符；`status` 白名单分支（store.py:48-55）非拼接（P3 提示：`else: conds.append("status=?")` 已正确参数化）。
- `api/account_pool/borrow.py` / `stats.py` / `signin.py` —— 全部参数化（borrow.py `f"...{where_clause}"` 仅拼白名单 conds）。
- `api/email_pool.py` —— 全部参数化。
- `api/chat_usage.py` —— 全部参数化；`where` 字符串拼接为常量 `"created_at >= ?"`。
- `api/adaptive_router.py` —— 全部参数化（含 `json.dumps(rec.scores)` 存 JSON 文本后再读取，无注入）。
- `api/db/ip_blocklist_store.py` —— 全部参数化；`get_many` 的 `placeholders = ",".join("?"*len(ips))` 是占位符串，`ips` 列表不拼接进 SQL，**安全**（ip_blocklist_store.py:167-168）。
- `api/agent/memory.py` —— **f-string 拼接表名/过滤串但值域为硬白名单**，静态确认安全：
  - `table = {"L0":...,"L1":...}.get(layer)`（memory.py:263）—— layer 非白名单键时返回 None，**无法注入表名**；
  - `supersede_filter` 仅两态常量 `""` / `" AND superseded_by IS NULL"`（279）；
  - FTS5 `MATCH` 表达式经 `_fts_match_expr()`（memory.py:93-98）—— `query.replace('"',' ').split()` 剥掉引号再用双引号包裹 token，**防 MATCH 语法注入**（已验证读码）；
  - `_touch_access` 的 `ids` 来自 DB 自产自增主键（memory.py:365/372），**非用户输入**。
- `api/agent/human_inbox.py` / `skill_sediment.py` / `vector/store.py` / `lease_store.py` / `queue_store.py` / `queue_db_legacy.py` —— 全部参数化。
- `api/routes/security.py` —— 仅调用 store 方法，无拼 SQL。

**唯一保留项（P3，无利用路径）**：`memory.py:337` `f"SELECT * FROM {table} WHERE id IN ({','.join('?'*len(top_ids))})"` 与 `365` `ids=",".join(str(r.id) ...)` —— 表名为白名单、id 为自增整数，当前无注入面；若未来 table 参数改为外部输入（如"任意层名"），必须走白名单校验后再进 f-string。

---

## 3. 锁与并发

### L1 【P1·设计与实现不符】批量写"合并 commit"实际**非事务非原子** — `api/db/core.py:194-207` + `275-296`
- **实测已验证**：`aiosqlite.connect(path, isolation_level=None)`（core.py:197）下，连接处于 **autocommit 模式**，`conn.execute()` 每条语句执行完立即提交，无需、也不等待 `conn.commit()`。实测：无 commit 插入的行 10ms 内即可被另一条连接读到。
- 由此：
  1. `_flush_buffer`（core.py:286-296）的 N 条 execute + 末尾 1 次 `commit()` —— **末尾 commit 是 no-op**，N 条语句各自独立提交，**并非"0.2s 窗口合并成一个事务"**：文档承诺的"合并提交"（CLAUDE.md「DB 批量写：0.2s 窗口合并 commit」）与实际不符；
  2. **原子性丢失**：批内第 k 条失败时前 k-1 条已落盘、第 k..N 条随缓冲区弹出被丢弃（`buf, self._write_buffer = self._write_buffer, []` 在循环前弹出，异常后无人重放/补偿）——产生**部分写入 + 该批剩余语句静默丢失**；
  3. 性能：每条语句独立 commit（WAL 模式 `synchronous=NORMAL` 下无强制 fsync，开销有限但仍有事务 begin/commit 成本；`wal_autocheckpoint=1000` 已设）。
- 影响评估：单语句仍原子（SQLite autocommit 单语句原子），非批量模式路径不受影响；批量模式下的业务写多为单语句（create_request/mark_*），**当前正确性未破**；但任何依赖"同批两条语句要么都成功要么都失败"的未来需求会在该模式下静默踩坑。
- 修复建议（二选一，非本次实施）：
  - 方案 A（推荐）：`create_conn` 不传 `isolation_level=None`，在 `_flush_buffer` 内显式 `BEGIN` ... 循环 execute ... `COMMIT`（失败 `ROLLBACK` + 保留缓冲区重放），恢复单事务原子批；
  - 方案 B：若坚持 autocommit，则更新文档口径（0.2s 窗口仅合并"调度"，非"事务"），并给 `_flush_buffer` 加幂等重试补偿（失败重入缓冲区），明确"无批原子性"。

### L2 【P2】`LeaseStore.acquire/renew/release` 已用 `BEGIN IMMEDIATE` + `_tx_lock` 双保险 — `api/db/lease_store.py:64-99`
- `async with self._tx_lock, self._conn.execute("BEGIN IMMEDIATE")`：单连接 + 应用层互斥 + IMMEDIATE 排他写锁，**无死锁点**（无嵌套事务、无锁顺序交叉）；`renew` 用 `WHERE key=? AND token=? AND expires_at>?` 条件更新保证持锁人语义。**静态确认健康**。

### L3 【P2】`ip_blocklist_store` 每次读写新建短连接 — `api/db/ip_blocklist_store.py:38-44/84/121-122/290`
- `_get_conn()` 每操作 `aiosqlite.connect` + PRAGMA 若干 → 高频请求路径（每条 IP 校验一次 `get_many`）**每次建连成本显著**；`_write_lock` 串行化写解决了旧 `database is locked`。建议：复用连接池（主 DB 池模式）或至少保留 2~3 条连接长驻；P2 性能优化项（非正确性）。

### L4 【P2】`recover_stale_tasks`/`cleanup`/`cleanup_batched`/`archive_tasks` 持写锁长事务 — 见 S7
- 大范围 DELETE/UPDATE 在 `async with conn_lock` 期间独占写连接[round-robin 下该 idx]，批间隔无 `await` 让出（cleanup_batched 有批循环但无 sleep）。SQLite 单写者模型下其他写请求会排队等 `busy_timeout=10000ms`；量级不大时无感，**夜间巡检期建议批间 `await asyncio.sleep(0)`**。

### L5 【P3】`AccountPool.borrow_account` check-then-act 依赖进程内单连接 + `self._lock` — `api/account_pool/borrow.py:50-101`
- SELECT 好号 → UPDATE 置 working 在 `self._lock` 内完成，单进程安全；**多进程/多实例部署时同一账号可能被两个 worker 同时借出**（无 `UPDATE ... WHERE status IN ('active','ok') RETURNING` 式原子抢占）。当前单进程部署，仅作多实例扩展性提示。

---

## 4. 正确性（聚合 / 排序 / 时区 / 分页漂移）

### C1 【P2】OFFSET 分页存在排序漂移风险（delete/insert 混入时跳行/重行）
- `list_tasks`（queries.py:290-293）、`gallery_list`（349-353）、`account_pool.list_page`（store.py:101-104）、`ip_blocklist.list_all` offset 分支（ip_blocklist_store.py:204-222）均用 `ORDER BY ... LIMIT ? OFFSET ?`：
  - `list_tasks` 按非唯一键（status/model）排序时无二级排序键，SQLite 以 rowid（≠ TEXT 主键 id）隐式决定 tie-break；**同 status 翻页时新插入/删除行会导致跨页重复或跳过**（DELETE 后 rowid 可复用；
  - `ip_blocklist.list_all` 已提供 `updated_before` keyset 游标（静态确认正确，ip_blocklist_store.py:198-207），建议其余列表页同样迁移 keyset（按主键 id 做 `WHERE id < ?` 游标）。

### C2 【P1】`list_tasks` COUNT 与数据查询同 where（一致），但 `gallery_list` 有修复史
- `gallery_list` total 与数据查询同口径（queries.py:328/341-353，H2 修复后一致：`status NOT IN ... AND image_url IS NOT NULL` 两处都带）；`list_tasks` total 带同一 where（282/290）。**静态确认两边口径一致**。唯一偏差点：export_tasks 不返回 total（明确 truncation 语义，非不一致）。

### C3 【P2】`gallery_list` `status NOT IN ('deleted','pending','archived')` 语义过宽
- 会把 `cancelled`、`processing`、`failed`、`queued`、`pending_retry` 等**无完成图行**也纳入计数并在"排序后 LIMIT 50"中占据槽位，与"画廊=已完成有图"的产品语义不符；且负向 NOT IN 正是 S2 索引失效根因。建议改正向白名单 `status IN ('completed','error','failed')`。

### C4 【P3】时区边界：day/month 列按 UTC 生成，dashboard 按本地日切分
- `requests.day/month`（queries.py:79-82）与 `chat_usage.day/month`（chat_usage.py:51-52）由 `datetime.datetime.fromtimestamp(now, tz=datetime.UTC).strftime(...)` 生成（UTC）；而 `stats_daily` 的 cutoff 用 `datetime.date.today()`（本地日期，queries.py:441）、`chat_usage.stats` 的 `today_start` 用 `datetime.now().date()`（本地凌晨，chat_usage.py:127）。**跨日服务器（UTC+8）凌晨 0-8 点会看到"今日"与 day 桶错位一天**；同一报表口径内部自洽（都按 UTC day 分桶），但与"今日成本"（本地 midnight 切）存在桶边界不一致。建议统一到单一时区，或显式文档化"报表按 UTC 日切分"。

### C5 【P2】聚合边界
- `stats_overview.avg_duration`：`AVG(CASE WHEN status='completed' AND duration_sec IS NOT NULL THEN duration_sec END)` 正确处理 NULL（静态确认）；`round(float(avg_duration),1)/if avg_duration` 返回 None 而非 0（语义得当）。
- `stats_daily/monthly` 用 `COALESCE` 聚合；未加 `分钟/小时桶`，量级大时聚合窗口粗，属产品口径非缺陷。
- `chat_usage by_model` 的 `GROUP BY model,provider` + `ORDER BY COUNT(*) DESC` 触发 temp B-tree（S8 同类，P2）。

### C6 【P3】`_touch_access` 用 `ids` 字符串拼接 IN — memory.py:365/372（详见解析于 §2，值域为自增 int，安全但建议改占位符统一风格）。

---

## 5. 索引缺口总表（对照实际 sqlite_master 实测）

| 表 | 高频查询（WHERE/ORDER BY/JOIN） | 现状 | 缺口 | 建议 DDL | 级别 |
|---|---|---|---|---|---|
| requests | `status != 'archived'` + 全量聚合 | 无覆盖 | 聚合全扫 | `CREATE INDEX IF NOT EXISTS idx_requests_status_dur ON requests(status, duration_sec, created_at);` | P1 |
| requests | `status NOT IN (...)` + `finished_at` 画廊 | 负向写法弃用 (status,finished_at) | 正向白名单 | `CREATE INDEX IF NOT EXISTS idx_requests_gallery ON requests(status, finished_at);` + SQL 改 IN | P1 |
| requests | `day/month >= 'YYYY-MM-DD'` 分组 | **无 day/month 索引** | ✓ | `CREATE INDEX IF NOT EXISTS idx_requests_day ON requests(day, status); CREATE INDEX IF NOT EXISTS idx_requests_month ON requests(month, status);` | P1 |
| requests | `model=?`（list 过滤/导出前缀） | **无 model 索引** | ✓ | `CREATE INDEX IF NOT EXISTS idx_requests_model ON requests(model, created_at);` | P2 |
| requests | `ORDER BY duration_sec` | **无 duration 索引** | ✓ | `CREATE INDEX IF NOT EXISTS idx_requests_duration ON requests(duration_sec);` | P2 |
| requests | `prompt LIKE '%..%'` | 必然全扫 | 无解（可接受） | （保底）控制 search 长度+并发 | P3 |
| chat_usage | `GROUP BY model,provider` 24h | 有 created_at | temp sort | `CREATE INDEX IF NOT EXISTS idx_chat_usage_model_created ON chat_usage(model, created_at);`（幂等，若已存在忽略） | P2 |
| accounts | `ORDER BY created_at DESC` 分页 | **无 created_at 索引** | ✓ | `CREATE INDEX IF NOT EXISTS idx_acc_provider_created ON accounts(provider, created_at);` | P2 |
| accounts | `status IN ('cooling','exhausted')` 唤醒 | status 非前缀 | ✓ | `CREATE INDEX IF NOT EXISTS idx_acc_status_cooling ON accounts(status, cooling_since);` | P3 |
| accounts | `provider=? AND last_used_at>=?` 预测 | 无 last_used 索引 | ✓ | `CREATE INDEX IF NOT EXISTS idx_acc_last_used ON accounts(provider, last_used_at);` | P3 |
| ip_blocklist | `WHERE (expire_at=0 OR expire_at>?) ORDER BY updated_at` | expire 有索引、**updated_at 无** | ✓ | `CREATE INDEX IF NOT EXISTS idx_ip_blocklist_updated ON ip_blocklist(updated_at);` | P2 |
| routing_outcomes | `DELETE ... WHERE ts < ?` 剪枝 | 索引为 (provider_id,ts) | ts 前缀不可用 | `CREATE INDEX IF NOT EXISTS idx_routing_outcomes_ts ON routing_outcomes(ts);`（或直接替换现有索引） | P3 |
| email_registry | `SELECT email` 全表启动载入 | provider 索引无帮助 | 批读 | （架构级，非 DDL） | P3 |

**冗余索引（清理候选，P3）**：`idx_requests_created(created_at)` 被 `idx_requests_created_status(created_at,status)` 前缀覆盖；`idx_requests_status(status)` 被 `idx_requests_status_created` / `idx_requests_status_finished` 前缀覆盖。可 `DROP INDEX` 省写放大（当前写频率高时收益小，量级大时建议清理）。

---

## 6. TOP 问题清单（≤15 条，按严重度排序）

| # | 级别 | 位置 | 问题 | 修复建议 DDL / 动作 |
|---|---|---|---|---|
| 1 | **P1** | `api/db/core.py:286-296` `_flush_buffer` + `:197` `isolation_level=None` | 批量写**非事务非原子**（实测 autocommit；批内失败产生部分写入+剩余静默丢失；文档"0.2s 合并 commit"不符） | 方案 A：去 isolation_level=None 或显式 BEGIN/COMMIT+ROLLBACK 重放；方案 B：改文档口径+失败重入缓冲 |
| 2 | **P1** | `api/db/queries.py:409-434` `stats_overview` | `/metrics` 每抓一次全表聚合扫描 | `CREATE INDEX IF NOT EXISTS idx_requests_status_dur ON requests(status, duration_sec, created_at);`；建议改 rollup 计数器 |
| 3 | **P1** | `api/db/queries.py:311-355` `gallery_list` | `status NOT IN` 负向条件弃用索引 → 全索引扫描 | 改 `status IN ('completed','error','failed')` + `CREATE INDEX idx_requests_gallery ON requests(status, finished_at);` |
| 4 | **P1** | `api/db/queries.py:436-478` `stats_daily/monthly` | day/month 列无索引，报表全扫+临时分组 | `CREATE INDEX IF NOT EXISTS idx_requests_day ON requests(day, status); idx_requests_month ON requests(month, status);` |
| 5 | **P2** | `api/routes/admin/query.py:824-828` `export_tasks` | 10 万行一次 fetchall + StringIO，model 过滤无索引 | 流式 StreamingResponse 分页游标；加 `idx_requests_model` |
| 6 | **P2** | `api/db/queries.py:256-296` `list_tasks` | duration_sec/status/model 排序或过滤触发全扫+临时排序 | `CREATE INDEX idx_requests_duration; idx_requests_model(model,created_at);` |
| 7 | **P2** | `api/account_pool/store.py:64-112` `list_page` | accounts 无 created_at 索引，分页全量排序；search 前导通配全扫 | `CREATE INDEX IF NOT EXISTS idx_acc_provider_created ON accounts(provider, created_at);` |
| 8 | **P2** | 分页漂移（4 处 OFFSET 列表） | 非唯一键排序无二级键，delete/insert 混入跳/重行 | 迁移 keyset（参照 `ip_blocklist.list_all` `updated_before` 已实现路径） |
| 9 | **P2** | `api/db/ip_blocklist_store.py:38-44` | 每次操作新建连接（每请求 get_many 建连） | 改长驻连接池（3~5 条） |
| 10 | **P2** | `api/routes/admin/query.py:484` `/metrics` 裸扫 `stats_overview` | 聚合无缓存 | 给 `/metrics` 的 stats 加短 TTL 缓存或走 rollup |
| 11 | **P2** | `api/email_pool.py:172-175` + `api/agent/human_inbox.py:102` | 启动全表载入内存 | 批读/按需查询；量大时改定时增量同步 |
| 12 | **P2** | `api/db/queries.py:481-509` `cleanup`（兼容入口） | 无 LIMIT；与 cleanup_batched 并存 | 文档标注已废弃，或直接内部委托 cleanup_batched |
| 13 | **P2** | `api/chat_usage.py:127` 与 `queries.py:441` | day 桶按 UTC、today 按本地，跨日服务器报表错位一天 | 统一时区口径（建议全部 UTC + 前端本地化展示） |
| 14 | **P2** | `api/db/ip_blocklist_store.py:198-222` + S9 | ip_blocklist `updated_at` 无索引；`wake_cooling_accounts` status 非前缀全扫 | `CREATE INDEX idx_ip_blocklist_updated(updated_at); idx_acc_status_cooling(status,cooling_since);` |
| 15 | **P3** | 冗余索引 | `idx_requests_created`/`idx_requests_status` 被复合索引前缀覆盖 | 量大后 `DROP INDEX` 清理写放大 |

**未发现问题面（结论明确）**：
- **SQL 注入**：全仓 27 文件全部 `?` 占位符参数化；f-string 拼接值域全部为源码常量/白名单（`_PUBLIC_COLS`、`allowed_sort`、`table` 白名单、`ids` 自增主键），**无注入**（证据见 §2）。
- **锁死锁**：lease 的 BEGIN IMMEDIATE + 单连接互斥设计正确；ip_blocklist 已串行化写规避 database is locked。
- **表结构健全性**：schema 与代码迁移一致（`_apply_request_migrations` 补 11 列运行正常，`sqlite_master` 实测列齐全）；唯一注释失真：migrations.py:42 "requests 无 model 列"与实际（model 已补列）不符，建议改注释（P3）。

---

## 7. 附：验证方法留痕
- 真实 schema 来自 `sqlite3 file:X?mode=ro` 只读 dump（未写任何库）。
- EXPLAIN QUERY PLAN 在 `data/imagefree.db`、`data/account_pool.db` 上只读执行，给出具体计划文本。
- autocommit 行为用独立临时库实测（isolation_level=None 下无 commit 插入即被他连可见）。
- 所有"建议 DDL"均为 `IF NOT EXISTS` 幂等形式，供夜间巡检批次实施（实施属写库操作，本审计未执行）。