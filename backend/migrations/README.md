# 迁移

`src/models/tables.py` 定义当前关系结构。API 启动时显式创建演示库的表；新装环境不需要额外的迁移步骤。

已有部署升级到「委派版本与调度更正」后，先执行 `0002_delegation_versions.sql` 再发布应用。脚本只做 `CREATE ... IF NOT EXISTS` 和 `ADD COLUMN IF NOT EXISTS`，可以重复执行。升级前已存在的执行记录与证据没有锁定版本，页面按「未记录」展示；新执行的验签会写入锁定版本。

- `0002_delegation_versions.sql`：不可变的委派版本、发布台账，以及调度更正片段。
