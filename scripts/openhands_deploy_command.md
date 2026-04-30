# Open Hands 一键部署指令（可直接复制）

下面这段可以直接发给 Open Hands 执行：

```bash
set -e
cd /path/to/Index
git fetch --all
git reset --hard origin/main
bash deploy.sh production
echo "=== 验证结果 ==="
echo "[1] Streamlit 进程："
ps -ef | grep "streamlit run app/ui/main.py" | grep -v grep || true
echo "[2] Caddy 配置校验："
caddy validate --config /etc/caddy/Caddyfile || true
echo "[3] 最近 50 行 Streamlit 日志："
tail -n 50 /path/to/Index/logs/streamlit_8504.log || true
echo "[4] 最近 50 行 Daily Sync 日志："
tail -n 50 /path/to/Index/logs/daily_sync.log || true
echo "[5] crontab："
crontab -l || true
```

## 让 Open Hands 返回的内容（复制给你核对）

请 Open Hands 在执行后回传：

1. `deploy.sh production` 的完整输出  
2. `ps -ef` 中 Streamlit 进程行  
3. `caddy validate` 结果  
4. `crontab -l` 结果  
5. `tail -n 50 logs/streamlit_8504.log` 输出  
6. 最终访问地址是否可打开：`https://www.quant919.com`
