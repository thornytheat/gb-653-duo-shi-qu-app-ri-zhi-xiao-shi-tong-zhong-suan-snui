# 多时区应用日志小时桶重算

        面向一线复核人员的单服务数据工作台。样例数据随仓库提供，结论保留行号、原因码和原值。

        ## 业务模块

        - M1 日志采信：`/api/logs/import`
- M2 时间解析：`/api/time/normalize`
- M3 事件去重：`/api/events/dedupe`
- M4 小时分桶：`/api/buckets/rebuild`；服务时区更正：`/api/buckets/correct`
- M5 迟到修订：`/api/watermark/advance`
- M6 批次对账：`/api/export/buckets.csv`

        ## 核心规则

        事件先归一到 UTC，DST 重叠时保留 fold；小时桶右端开，水位只能前进且迟到事件仍进入修订。

        ## 时区更正（M4）

        服务搬迁后按服务登记更正：更正后时区、生效起点（服务本地时间，含边界）与理由，并携带 `base_version` 做并发控制，过期决定以 `STALE_VERSION` 拒绝。当前重算采用更正后时区；已留存批次连同原日志、fold 选择与所用决定版本一并封存，永不改写。无时区日志落在夏令时跳时空档（`DST_GAP`）或重叠时段缺少 fold 选择（`FOLD_REQUIRED`）时隔离。重算按输入摘要幂等，中断凭断点续跑，成功提交才切换当前批次指针，失败沿用旧版。

        ## 启动

        ```bash
        docker compose up --build
        ```

        浏览器访问 `http://localhost:18212`。运行测试：

        ```bash
        docker compose exec -T app pytest -q
        ```

        ## 样例流程

        依次运行六个模块；最后在批次页复核输入、有效、隔离与输出合计。同一输入摘要重复执行时复用原 `run_id`。

        ## 已知不覆盖

        不处理加密文件与需要外部密钥的对象；不连接真实生产存储。
