# Emergency Beam-Stop Relay Network Audit

大型加速器紧急停束信号沿有向继电网络送达保护终端。本服务从根控制器的
可达子图计算每个节点的**立即支配者**（immediate dominator），并汇总每个
非根、非终端继电器所支配的保护终端数——即该继电器失效时**必然失联**的
终端数量，从而避免把存在旁路的节点误判为单点故障。

## 核心算法

代码位于 `app/dominators.py`：

1. **一轮迭代式 DFS**（显式栈，无递归）得到可达集合与 DFS 树；
2. **Lengauer–Tarjan** 并查集算法在全局一趟 O(E·α(V)) 的扫描中求出所有
   可达节点的立即支配者——**不**逐节点删边重跑可达性；`eval`/`compress`
   同样写成迭代形式，数万层深图也不会触发递归深度问题；
3. 在支配树上按逆 DFS 序对子树的终端指示量求和，O(V) 得到每个节点支配的
   终端数；
4. 全部仅使用 Python 内建类型，**不依赖任何图算法库**。

正确性由 `tests/test_dominators.py` 中基于"删点可达性"的朴素参考实现，
在手工用例（菱形、串联、平行边、LT 论文经典图例）和大量随机图上交叉验证。

## API

`POST /api/audit`

```json
{
  "nodes": ["R", "A", "B", "M", "T1"],
  "root": "R",
  "terminals": ["T1"],
  "edges": [["R","A"], ["R","B"], ["A","M"], ["B","M"], ["M","T1"]]
}
```

约束：2–200000 个唯一 ASCII 节点；1–20000 个无出边保护终端；至多
500000 条有向边；允许平行边，禁止自环。

成功响应（按节点标识排序）：

```json
{
  "root": "R",
  "reachable_node_count": 5,
  "unreachable_terminals": [],
  "dominators": [
    {"node": "A", "immediate_dominator": "R"},
    {"node": "B", "immediate_dominator": "R"},
    {"node": "M", "immediate_dominator": "R"},
    {"node": "R", "immediate_dominator": null},
    {"node": "T1", "immediate_dominator": "M"}
  ],
  "critical_relays": [{"node": "M", "dominated_terminals": 1}]
}
```

- `dominators` 仅含**可达**节点（根的立即支配者为 `null`）；
- `critical_relays` 仅含计数 > 0 的非根非终端关键继电器，按标识排序。

校验失败一律返回 **HTTP 422**（非法 JSON 为 400），body 形如：

```json
{
  "error": "validation_failed",
  "details": [
    {"loc": ["edges", 0, 1], "type": "dangling_reference",
     "message": "edge target 'GHOST' is not declared in nodes"}
  ]
}
```

所有问题一次性报告且均可定位（`loc` 与 FastAPI 风格一致：数组下标、边端
点下标），错误时**绝不返回部分审计**。覆盖：悬空引用、重复节点/终端标识、
终端带出边、自环、非 ASCII 标识、数量越界、边格式错误等。

`GET /health` 返回 `{"status": "available", ...}` 反映进程与分析核心可用；
容器 healthcheck 同样探测该路径，compose 以其健康状态作为启动完成判据。

## 本地运行（需要 Docker Compose）

```bash
# 使用默认宿主机端口 8000
docker compose up --build

# 宿主机端口可配置
RELAY_AUDIT_PORT=9000 docker compose up --build
```

## 验证服务

```bash
docker compose run --build verify
```

`verify` 服务依赖 `api` 健康后启动，单次执行并以退出码给出结论：

1. **代码测试**：`pytest` 全套（核心算法、校验、HTTP 层，含随机图交叉验证）；
2. **应用构建检查**：ASGI 应用完整导入，`/health`、`/api/audit` 路由就位；
3. **HTTP 冒烟**：对运行中的服务验证
   [菱形旁路] [串联关键点] [平行边] [不可达终端] 四种拓扑及 422 错误定位。

全部通过退出码 0，任一失败非 0。

## 无 Docker 时的本地开发

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8000
python verify.py          # 需要先启动服务（API_BASE_URL 可覆盖）
pytest -q
```
