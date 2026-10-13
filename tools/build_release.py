"""构建发布包 (release).

产出 (放进 dist/):
  proxy-tester-<version>-src.zip   干净的源码包 (不含凭据/结果/虚拟环境)
  proxy-tester-<version>-src.zip.sha256
  RELEASE_NOTES.md                 本版本说明

设计要点:
  - 用 git 仓库内容作为白名单, 只打包被跟踪的文件 —— 避免把
    proxiers.txt / results.json / .venv 这类不该分发的文件打进去.
  - 额外补上 .env.example (模板), 但绝不打包真实 .env 里的私密值
    (当前 .env 本身只含本机路径, 无凭据).
  - 打包后立即校验: 解压到临时目录, 确认没有凭据文件、且代码可导入.
"""

from __future__ import annotations

import hashlib
import shutil
import subprocess
import sys
import tempfile
import zipfile
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# 允许打进发布包的文件 (git 跟踪的白名单 + 额外模板)
EXTRA_FILES = [".env.example"]

# 绝对不能进发布包的东西 (打包后校验用)
FORBIDDEN = [
    "proxiers.txt", "proxiers_google.txt", "results.json",
    ".env", ".venv/", "configs/", "__pycache__",
]


def sh(args: list[str], **kw) -> str:
    r = subprocess.run(args, cwd=ROOT, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", **kw)
    if r.returncode != 0:
        raise RuntimeError(f"{' '.join(args)} 失败:\n{r.stderr}")
    return r.stdout


def tracked_files() -> list[Path]:
    """git 跟踪的文件列表 (相对路径)."""
    out = sh(["git", "ls-files"])
    return [Path(p) for p in out.splitlines() if p.strip()]


def version() -> str:
    for line in (ROOT / "pyproject.toml").read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if s.startswith("version"):
            return s.split("=", 1)[1].strip().strip('"').strip("'")
    return "0.0.0"


def main() -> int:
    ver = version()
    dist = ROOT / "dist"
    dist.mkdir(exist_ok=True)
    name = f"proxy-tester-{ver}"
    zip_path = dist / f"{name}-src.zip"

    print("=" * 84)
    print(f"  构建发布包  {name}")
    print("=" * 84)

    files = tracked_files()
    print(f"  git 跟踪文件: {len(files)} 个")

    # 额外模板文件
    extras: list[Path] = []
    for e in EXTRA_FILES:
        p = ROOT / e
        if p.is_file() and e not in {str(x) for x in files}:
            extras.append(Path(e))
    if extras:
        print(f"  额外加入: {[str(x) for x in extras]}")

    if zip_path.exists():
        zip_path.unlink()

    # ---- 写 zip
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
        for rel in files + extras:
            src = ROOT / rel
            if not src.is_file():
                continue
            # zip 内统一用正斜杠, 且带一层顶层目录便于解压
            z.write(src, arcname=f"{name}/{rel.as_posix()}")

    size = zip_path.stat().st_size
    print(f"  已写入: {zip_path.name}  ({size:,} 字节)")

    # ---- 校验 1: 禁止文件不得入包
    with zipfile.ZipFile(zip_path) as z:
        names = z.namelist()
        bad = [n for n in names
               if any(f in n for f in FORBIDDEN)]
    if bad:
        print(f"\n[错误] 发布包含有禁止文件: {bad}")
        return 1
    print(f"  [OK] 不含凭据/结果/虚拟环境 (共 {len(names)} 个条目)")

    # ---- 校验 2: 必须包含的关键文件
    required = ["main.py", "README.md", "pyproject.toml",
                "core/detector.py", "web/server.py",
                "web/static/index.html", "web/static/app.js", "web/static/style.css"]
    missing = [r for r in required if f"{name}/{r}" not in names]
    if missing:
        print(f"\n[错误] 发布包缺少关键文件: {missing}")
        return 1
    print(f"  [OK] 关键文件齐全")

    # ---- 校验 3: 解压后代码可导入
    tmp = Path(tempfile.mkdtemp())
    try:
        with zipfile.ZipFile(zip_path) as z:
            z.extractall(tmp)
        ex = tmp / name
        r = subprocess.run(
            [sys.executable, "-c",
             "import ast,sys,pathlib;"
             "[ast.parse(p.read_text(encoding='utf-8')) "
             "for p in pathlib.Path('.').rglob('*.py')];"
             "print('syntax ok')"],
            cwd=ex, capture_output=True, text=True, encoding="utf-8",
            errors="replace",
        )
        if r.returncode != 0:
            print(f"\n[错误] 解压后源码语法检查失败:\n{r.stdout}\n{r.stderr}")
            return 1
        print("  [OK] 解压后全部 .py 语法正常")

        # 再确认一遍解压目录里没有凭据
        leaked = [str(p.relative_to(ex)) for p in ex.rglob("*")
                  if p.is_file() and p.name in
                  {"proxiers.txt", "proxiers_google.txt", "results.json"}]
        if leaked:
            print(f"\n[错误] 解压后仍有凭据文件: {leaked}")
            return 1
        print("  [OK] 解压后无凭据文件")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    # ---- 校验和
    sha = hashlib.sha256(zip_path.read_bytes()).hexdigest()
    (dist / f"{zip_path.name}.sha256").write_text(
        f"{sha}  {zip_path.name}\n", encoding="utf-8")
    print(f"  SHA256: {sha}")

    # ---- 发布说明
    commit = sh(["git", "rev-parse", "--short", "HEAD"]).strip()
    notes = dist / "RELEASE_NOTES.md"
    notes.write_text(
        f"""# 代理节点批量体检工具 v{ver}

- 构建时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
- 对应提交: {commit}
- 包内文件: {len(names)} 个 (含顶层目录 `{name}/`)

## 解压后怎么用

```powershell
# 1) 解压, 进入目录
cd {name}

# 2) 装依赖 (任选其一)
uv venv; uv sync
# 或
python -m venv .venv
.venv\\Scripts\\python.exe -m pip install "httpx[socks]" fastapi uvicorn

# 3) 打开 Web 面板 (推荐)
.venv\\Scripts\\python.exe web\\server.py
#    然后浏览器打开 http://127.0.0.1:8787
#    在页面右上角「导入节点」粘贴代理链接 -> 「重新检测」

# 或者纯命令行
.venv\\Scripts\\python.exe main.py
```

## 首次使用要改的配置

复制 `.env.example` 为 `.env`, 按需修改 `V2RAYN_BIN` 指向你的
v2rayN `bin` 目录 (内含 `sing_box/` 与 `xray/` 子目录).

## 本包含有什么

- 源码 (core / web / main.py / tests)
- 说明文档 README.md
- 配置模板 .env.example

## 本包不包含什么

出于安全与体积考虑, 以下内容**不在**包内:

- 代理节点列表 (`proxiers.txt`) 与结果 (`results.json`) —— 含真实凭据
- 内核可执行文件 (`sing-box.exe` / `xray.exe`) —— 请自备 (v2rayN 自带)
- 虚拟环境与缓存

## 校验

```powershell
# 确认下载完整 (与 .sha256 文件里的值比对)
Get-FileHash {zip_path.name} -Algorithm SHA256
```
""",
        encoding="utf-8",
    )
    print(f"  已写入: {notes.name}")
    print("=" * 84)
    print("  构建完成")
    print("=" * 84)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
