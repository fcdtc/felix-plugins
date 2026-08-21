#!/usr/bin/env bash
# 多信号关联评分：为「正在沉淀/追加的笔记」在全库中找最相关的笔记，输出评分榜。
# 用法:
#   bash find-related.sh --tags "tag1,tag2" [--keywords "词1,词2"] \
#        [--vault DIR] [--note 相对路径] [--top N]
#     --tags      必填，本次笔记的标签（逗号分隔）
#     --keywords  可选，标题/摘要关键词（逗号分隔，来自「分析先行」提炼的概念）
#     --note      可选，相对 vault 根的笔记路径；追加/规整已有笔记时传入，
#                 启用「共同邻居」信号（该笔记的图谱邻居参与评分，自身从候选中剔除）
#     --top       可选，输出条数，默认 5
#
# 信号与权重（借鉴 llm_wiki 的加权融合 + Adamic-Adar 思想）：
#   标签重叠        +3 / 个   frontmatter tags 交集
#   标题/摘要关键词  +2 / 个   关键词命中 title / description / 文件名（同一词只计一次）
#   共同邻居        +1.5 / 个  双方图谱邻居（出链 ∪ 入链）交集，封顶 3 个
#                             —— A、B 都连着 C 时 A-B 候选加分；新笔记无邻居，此信号为 0
#
# 只读不写：仅打印评分榜供 LLM 参考，不修改任何笔记。

set -u

VAULT="/Users/congfei/basic-memory"
TAGS=""
KEYWORDS=""
NOTE=""
TOP=5

while [ $# -gt 0 ]; do
  case "$1" in
    --vault)    VAULT="$2";    shift 2 ;;
    --tags)     TAGS="$2";     shift 2 ;;
    --keywords) KEYWORDS="$2"; shift 2 ;;
    --note)     NOTE="$2";     shift 2 ;;
    --top)      TOP="$2";      shift 2 ;;
    *) echo "未知参数: ${1}（用法见脚本头部注释）" >&2; exit 1 ;;
  esac
done

if [ -z "$TAGS" ]; then
  echo "错误：--tags 必填（逗号分隔的标签列表）" >&2
  exit 1
fi

python3 - "$VAULT" "$TAGS" "$KEYWORDS" "$NOTE" "$TOP" <<'PY'
import os, re, sys
from collections import defaultdict

vault, tags_arg, kw_arg, note_arg, top = sys.argv[1:6]
top = int(top)
q_tags  = {t.strip().lower() for t in tags_arg.split(",") if t.strip()}
q_kws   = {k.strip().lower() for k in kw_arg.split(",") if k.strip()} if kw_arg else set()

SKIP_DIRS = {".git", ".obsidian", "templates", "Excalidraw"}

# ---- 1. 收集全库笔记：路径 → frontmatter(title/description/tags) + 正文 wikilinks ----
notes = {}   # stem(小写) -> note dict
all_paths = []
for dirpath, dirnames, filenames in os.walk(vault):
    dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
    for fn in sorted(filenames):
        if not fn.endswith(".md") or fn in ("INDEX.md", "log.md"):
            continue
        p = os.path.join(dirpath, fn)
        try:
            txt = open(p, encoding="utf-8").read()
        except Exception:
            continue
        rel = os.path.relpath(p, vault)
        stem = fn[:-3]
        title, desc, ntags = "", "", []
        m = re.match(r"^---\n(.*?)\n---\n", txt, re.S)
        if m:
            fm = m.group(1)
            t = re.search(r"^title:\s*(.+?)\s*$", fm, re.M)
            if t: title = t.group(1).strip().strip("\"'")
            d = re.search(r"^description:\s*(.+?)\s*$", fm, re.M | re.I)
            if d: desc = d.group(1).strip().strip("\"'")
            block = re.search(r"^tags:\n((?:\s*-\s*.+\n?)+)", fm, re.M)
            if block:
                ntags = [x.strip() for x in re.findall(r"^\s*-\s*(.+?)\s*$", block.group(1), re.M)]
        if not title:
            title = stem
        links = re.findall(r"\[\[([^\]|]+)(?:\|[^\]]*)?\]\]", txt)
        n = {"rel": rel, "stem": stem, "title": title, "desc": desc,
             "tags": ntags, "links": links}
        notes[stem.lower()] = n
        all_paths.append(n)

# ---- 2. 建邻居图（出链 ∪ 入链，按 stem 小写解析）----
out_nbrs = defaultdict(set)   # stem_lower -> 邻居 stem_lower 集合
for n in all_paths:
    s = n["stem"].lower()
    for l in n["links"]:
        t = l.strip().lower()
        if t in notes and t != s:
            out_nbrs[s].add(t)
            out_nbrs[t].add(s)

# ---- 3. 评分 ----
self_key = note_arg[:-3].lower() if note_arg else None
self_nbrs = out_nbrs.get(self_key, set()) if self_key else set()

scored = []
for n in all_paths:
    k = n["stem"].lower()
    if self_key and k == self_key:
        continue
    # 信号 1：标签重叠 ×3
    shared = q_tags & {t.lower() for t in n["tags"]}
    s_tag = 3 * len(shared)
    # 信号 2：标题/摘要关键词 ×2（同一关键词只计一次）
    hay = (n["title"] + " " + n["desc"] + " " + n["stem"]).lower()
    hit_kws = {kw for kw in q_kws if kw in hay}
    s_kw = 2 * len(hit_kws)
    # 信号 3：共同邻居 ×1.5，封顶 3 个（防枢纽节点一家独大）
    common = (self_nbrs & out_nbrs[k]) if self_key else set()
    s_nb = 15 * min(len(common), 3)  # ×10 便于整数呈现

    total10 = s_tag * 10 + s_kw * 10 + s_nb  # ×10 整数化，呈现时再除回
    if total10 <= 0:
        continue
    scored.append((total10, s_tag, len(hit_kws), min(len(common), 3), n))

scored.sort(key=lambda x: -x[0])

if not scored:
    print("（无相关笔记：三信号均无命中，## 相关笔记 留空节）")
    sys.exit(0)

for i, (t10, s_tag, kw_hits, nb, n) in enumerate(scored[:top], 1):
    nb_pts = nb * 1.5
    parts = []
    if s_tag: parts.append(f"标签 +{s_tag}")
    if kw_hits: parts.append(f"关键词 +{kw_hits*2}")
    if nb: parts.append(f"共同邻居 +{nb_pts:g}")
    score = t10 / 10
    print(f"{i}. [[{n['title']}]]  分数 {score:g}（{' + '.join(parts) if parts else '仅文件名匹配'}）")
    print(f"   ↳ {n['rel']}" + (f" — {n['desc'][:60]}…" if len(n['desc']) > 60 else (f" — {n['desc']}" if n['desc'] else "")))
PY
