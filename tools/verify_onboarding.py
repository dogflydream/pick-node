"""验证: 无节点/无结果时, 启动引导与 main.py 提示是否友好且可执行."""
import subprocess
import sys
from pathlib import Path

PKG = Path(sys.argv[1]) if len(sys.argv) > 1 else Path.cwd()
sys.path.insert(0, str(PKG))

from core.util import setup_console

setup_console()

print(f"验收目录: {PKG}")
print()

# 1) 模拟 web/server.py main() 里的引导输出 (不真的起服务)
print("=" * 78)
print("1. Web 启动引导 (无结果时打印的内容)")
print("=" * 78)
from core.retest import RetestManager
from core.store import ResultStore

store = ResultStore(PKG / "results.json")
retest = RetestManager(
    root=PKG,
    nodes_file=PKG / "proxiers.txt",
    results_file=PKG / "results.json",
    pool_file=PKG / "proxiers_google.txt",
)
result_count = len(store.all())
try:
    node_count = len(retest.existing_ids())
except Exception:
    node_count = 0

print(f"  节点文件 : {retest.nodes_file}  (已导入 {node_count} 个节点)")
print(f"  结果文件 : {store.path}  ({result_count} 条结果)")
if result_count == 0:
    print("-" * 72)
    print("  还没有检测结果. 直接用浏览器打开上面的地址:")
    print("    1) 右上角点「导入节点」, 粘贴代理链接 (一行一个)")
    print("    2) 点「重新检测」开始体检")
    print("    3) 测完可一键导出可用节点")
    if node_count == 0:
        print()
        print("  提示: 你也可以直接把节点写进上面那个节点文件, 再刷新页面.")

# 2) main.py 在无节点文件时的提示
print()
print("=" * 78)
print("2. main.py 无节点文件时的提示")
print("=" * 78)
r = subprocess.run(
    [sys.executable, str(PKG / "main.py"), "-i", "no-such-file.txt"],
    cwd=PKG, capture_output=True, text=True, encoding="utf-8",
    errors="replace",
)
out = (r.stdout or "") + (r.stderr or "")
print(out.rstrip()[:900])
print(f"  (exit={r.returncode})")

# 3) 关键断言: 不能再出现"请先运行 python main.py"这类反向引导
print()
print("=" * 78)
print("3. 检查项")
print("=" * 78)
static = PKG / "web" / "static" / "app.js"
js = static.read_text(encoding="utf-8")
bad = "请先运行" in js or "尚未生成" in js
print(f"  [{'FAIL' if bad else 'OK  '}] 前端不再让用户去跑命令行")
assert not bad, "前端仍在引导用户去跑 python main.py"

has_guide = "导入节点" in js and "还没有检测结果" in js
print(f"  [{'OK  ' if has_guide else 'FAIL'}] 前端有导入引导文案")

has_main_hint = "web\\server.py" in out or "web/server.py" in out
print(f"  [{'OK  ' if has_main_hint else 'FAIL'}] main.py 引导用户改用 Web 面板")

print()
print("验收通过")
