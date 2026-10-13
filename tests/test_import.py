"""测试节点导入: 去重、无效行、持久化 (不依赖网络)."""
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from core.parser import parse_uri
from core.retest import RetestManager, analyze_import, parse_nodes_text
from core.util import setup_console

setup_console()

FAIL = []


def check(name, cond, detail=""):
    print(f"[{'OK  ' if cond else 'FAIL'}] {name}" + (f"  {detail}" if detail else ""))
    if not cond:
        FAIL.append(name)


tmp = Path(tempfile.mkdtemp())
nf = tmp / "nodes.txt"
rf = tmp / "results.json"
pf = tmp / "pool.txt"

V = "vless://11111111-2222-3333-4444-555555555555@1.2.3.4:443?encryption=none&security=tls&sni=a.example.com#"
H = "hysteria2://pw@h.example.com:443?sni=h.example.com#"


def vless(host: str, tag: str, port: int = 443) -> str:
    """构造一个真正不同的节点 (改 server 才有不同 node_id).
    注意: node_id 不含 fragment, 所以只改 #tag 会被正确判为同一节点. """
    return (f"vless://11111111-2222-3333-4444-555555555555@{host}:{port}"
            f"?encryption=none&security=tls&sni=a.example.com#{tag}")

# 初始文件: 已有 2 个节点
nf.write_text(vless("1.2.3.4", "node-A") + "\n" + H + "node-B\n", encoding="utf-8")
m = RetestManager(root=tmp, nodes_file=nf, results_file=rf, pool_file=pf)

print("=" * 92)
print("1. 去重: 重复已有 + 本批内部重复")
print("=" * 92)
text = "\n".join([
    vless("1.2.3.4", "node-A"),      # 与已有重复
    vless("1.2.3.4", "node-A-rename"),  # 同一节点换个备注名 -> 仍应判重
    H + "node-B",                    # 与已有重复
    vless("9.9.9.9", "node-C"),      # 新增
    vless("9.9.9.9", "node-C-dup"),  # 本批内部重复 -> 只算一次
    "garbage-not-a-uri",             # 无效
    "",                              # 空行
    "# comment",                     # 注释
    "// another comment",            # 注释
])
info = analyze_import(text, m.existing_ids())
print(f"  total_lines={info['total_lines']} new={info['new_count']} "
      f"dup={info['dup_count']} bad={info['bad_count']}")
check("无效行数为 1", info["bad_count"] == 1, str(info["bad_count"]))
check("重复数为 4 (A / A-rename / B / C-dup)",
      info["dup_count"] == 4, str(info["dup_count"]))
check("新增数为 1 (C)", info["new_count"] == 1, str(info["new_count"]))
check("注释行不计入 total_lines", info["total_lines"] == 6, str(info["total_lines"]))
check("三类合计 == 有效行数 (1+4+1==6)",
      info["new_count"] + info["dup_count"] + info["bad_count"] == info["total_lines"],
      f"{info['new_count']}+{info['dup_count']}+{info['bad_count']}")

print()
print("=" * 92)
print("2. 实际导入并校验文件")
print("=" * 92)
res = m.import_nodes(text)
print(f"  added={res['added']} total_after={res['total_after']}")
check("导入返回 ok", res["ok"] is True)
check("只新增 1 条", res["added"] == 1, str(res["added"]))
check("导入后总数 3", res["total_after"] == 3, str(res["total_after"]))

content = nf.read_text(encoding="utf-8")
lines = [l for l in content.splitlines() if l.strip()]
print(f"  文件行数: {len(lines)}")
check("文件确实是 3 行", len(lines) == 3, str(len(lines)))
check("原有节点未被破坏", "node-A" in content and "node-B" in content)
check("新节点已追加", "node-C" in content)
check("没有重复节点", len(parse_nodes_text(content)) == 3)

print()
print("=" * 92)
print("3. 再次导入同一批: 应全部判重, 不新增")
print("=" * 92)
res2 = m.import_nodes(text)
print(f"  added={res2['added']} dup={res2['dup_count']} total_after={res2['total_after']}")
check("第二次导入新增 0 条", res2["added"] == 0, str(res2["added"]))
check("总数仍为 3", res2["total_after"] == 3, str(res2["total_after"]))
after2 = nf.read_text(encoding="utf-8")
check("文件内容未变长", len(parse_nodes_text(after2)) == 3)

print()
print("=" * 92)
print("4. 文件末尾无换行时也能正确追加")
print("=" * 92)
nf3 = tmp / "n3.txt"
nf3.write_text(vless("7.7.7.7", "only-one"), encoding="utf-8")   # 故意不加结尾换行
m3 = RetestManager(root=tmp, nodes_file=nf3, results_file=rf, pool_file=pf)
r3 = m3.import_nodes(vless("8.8.8.8", "second"))
c3 = nf3.read_text(encoding="utf-8")
print(f"  added={r3['added']} 内容行={len([l for l in c3.splitlines() if l.strip()])}")
check("追加成功且未粘行", "only-one" in c3 and "second" in c3
      and "only-onesecond" not in c3.replace("\n", "").replace("only-one\nsecond", ""))
check("解析出 2 个节点", len(parse_nodes_text(c3)) == 2)

print()
print("=" * 92)
print("5. 全部无效时不写文件")
print("=" * 92)
before = nf3.read_text(encoding="utf-8")
r4 = m3.import_nodes("not-a-uri\nstill-bad")
after = nf3.read_text(encoding="utf-8")
print(f"  added={r4['added']} bad={r4['bad_count']}")
check("无效输入新增 0", r4["added"] == 0)
check("文件未被改动", before == after)

print()
print("=" * 92)
if FAIL:
    print(f"失败 {len(FAIL)} 项: {FAIL}")
    sys.exit(1)
print("ALL IMPORT TESTS PASSED")
