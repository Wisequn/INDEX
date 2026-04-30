# Index Monitor

一个本地量化数据平台（新手友好版）。

## 项目目标

- 抓取并存储 BTC、美股、A股等市场的历史与实时数据
- 所有数据存入本地 SQLite（通过 SQLAlchemy ORM 管理）
- 使用 Streamlit 做图表展示，辅助判断市场高低点

## 1. 环境准备

请先确认你已经安装 Python 3.10+。

## 2. 安装依赖

在项目根目录运行：

```bash
pip install -r requirements.txt
```

## 3. 初始化数据库

```bash
python -m app.scripts.init_db
```

成功后会在项目根目录生成 `index_monitor.db`。

## 4. 启动可视化页面

```bash
streamlit run app/ui/main.py
```

浏览器打开后，你可以：

- 选择市场（BTC / 美股 / A股）
- 输入代码（例如：BTC-USD、AAPL、000001.SZ）
- 指定时间范围
- 点击按钮抓取历史数据并写入数据库
- 看到折线图和表格

## 5. 项目结构

```text
Index/
├─ app/
│  ├─ db/
│  │  ├─ base.py         # 数据库连接与会话
│  │  └─ models.py       # ORM 数据表模型
│  ├─ services/
│  │  ├─ fetchers.py     # 抓取数据（BTC/美股/A股）
│  │  └─ data_service.py # 数据入库与查询服务
│  ├─ scripts/
│  │  └─ init_db.py      # 初始化数据库脚本
│  └─ ui/
│     └─ main.py         # Streamlit 页面
└─ requirements.txt
```

## 6. 说明（给新手）

- 你先跑通这个最小版本，就已经是一个完整的数据平台雏形了。
- 后续可以继续加：
  - 定时任务（每隔几分钟自动抓一次实时数据）
  - 更多技术指标（均线、RSI、MACD）
  - 更完整的回测与策略模块
