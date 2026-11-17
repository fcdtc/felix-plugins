#!/usr/bin/env python3
"""确定性审计准备（标准库 Python 3.9+）：freeze 固定资源，packet 本地索引证据。"""
# 在导入 json 等模块之前隔离启动；下载目录中的同名模块不能进入导入路径。
import sys
if __name__ == "__main__" and not sys.flags.isolated:
    import os
    os.execv(sys.executable, [sys.executable, "-I", "-B", os.path.abspath(__file__)] + sys.argv[1:])

import argparse
import ast
import hashlib
import json
import os
import re
import stat
from datetime import datetime, timezone
from pathlib import Path
from types import ModuleType


class PrepareError(ValueError):
    pass


def require(condition, message):
    if not condition:
        raise PrepareError(message)


def physical_path(value):
    path = Path(value)
    require(path.is_absolute() and ".." not in path.parts,
            "必须使用不含 .. 的物理绝对路径: " + str(path))
    for part in reversed((path,) + tuple(path.parents)):
        require(not part.is_symlink(), "拒绝符号链接: " + str(part))
    return path


def read_bytes(path):
    path = physical_path(path)
    fd = os.open(str(path), os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, "rb") as stream:
        before = os.fstat(stream.fileno())
        require(stat.S_ISREG(before.st_mode), "只允许普通文件: " + str(path))
        value = stream.read()
        after = os.fstat(stream.fileno())
    require((before.st_size, before.st_mtime_ns, before.st_ctime_ns) ==
            (after.st_size, after.st_mtime_ns, after.st_ctime_ns),
            "读取期间源文件变化: " + str(path))
    return value


def private_dir(path):
    path = physical_path(path)
    if not path.exists():
        if not path.parent.exists():
            private_dir(path.parent)
        path.mkdir(mode=0o700)
    require(path.is_dir(), "不是目录: " + str(path))
    path.chmod(0o700)


def new_private_dir(path):
    """目录本身以独占 mkdir 创建，不接管竞争者已经创建的输出目录。"""
    path = physical_path(path)
    if not path.parent.exists():
        private_dir(path.parent)
    path.mkdir(mode=0o700)
    path.chmod(0o700)


def write_bytes(path, content):
    path = physical_path(path)
    require(not path.exists(), "冻结/派生目标已存在: " + str(path))
    if not path.parent.exists():
        private_dir(path.parent)
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "wb") as stream:
        os.fchmod(stream.fileno(), 0o600)
        stream.write(content)


def json_bytes(value):
    return (json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode("utf-8")


def save_json(path, value):
    write_bytes(path, json_bytes(value))


def sha256(value):
    return hashlib.sha256(value).hexdigest()


def value_hash(value):
    return sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                            separators=(",", ":"), allow_nan=False).encode("utf-8"))


def parse_json(value):
    def pairs(items):
        result = {}
        for key, item in items:
            require(key not in result, "JSON 字段重复: " + key)
            result[key] = item
        return result

    def constant(item):
        raise PrepareError("JSON 不允许非有限数值: " + item)

    try:
        return json.loads(value, object_pairs_hook=pairs, parse_constant=constant)
    except (ValueError, UnicodeError) as exc:
        raise PrepareError("不合法 JSON: " + str(exc)) from exc


def json_type(value):
    if value is None:
        return "null"
    if type(value) is bool:
        return "boolean"
    if isinstance(value, dict):
        return "object"
    if isinstance(value, list):
        return "array"
    if isinstance(value, str):
        return "string"
    return "number"


def pointer_token(value):
    return str(value).replace("~", "~0").replace("/", "~1")


def resolve_pointer(value, pointer):
    """严格 RFC6901（非 URI fragment），数组下标遵循无前导零的 ABNF。"""
    require(isinstance(pointer, str) and (pointer == "" or pointer.startswith("/")),
            "定位必须是 RFC6901 JSON Pointer: " + repr(pointer))
    if not pointer:
        return value
    for encoded in pointer[1:].split("/"):
        require(re.search(r"~(?![01])", encoded) is None, "JSON Pointer 含无效 ~ 转义")
        token = encoded.replace("~1", "/").replace("~0", "~")
        if isinstance(value, dict):
            require(token in value, "JSON Pointer 对象字段不存在: " + token)
            value = value[token]
        elif isinstance(value, list):
            require(re.fullmatch(r"0|[1-9][0-9]*", token) is not None,
                    "JSON Pointer 数组下标必须是非负整数且无前导零: " + token)
            # 超长下标必定越界，无需转换成任意长度整数。
            require(len(token) <= len(str(len(value))), "JSON Pointer 数组下标越界: " + token)
            position = int(token)
            require(position < len(value), "JSON Pointer 数组下标越界: " + token)
            value = value[position]
        else:
            raise PrepareError("JSON Pointer 不能继续遍历 " + json_type(value))
    return value


def locator_chain(locator):
    if isinstance(locator, str):
        locator = parse_json(locator) if locator.startswith("[") else [locator]
    require(isinstance(locator, list) and bool(locator) and all(isinstance(p, str) for p in locator),
            "locator 必须是 JSON Pointer 或非空 JSON Pointer 字符串数组")
    return locator


def resolve_locator(value, locator):
    """仅显式链边界解码 JSON 字符串，不把自然文本自动当对象。"""
    for position, pointer in enumerate(locator_chain(locator)):
        if position:
            require(isinstance(value, str), "JSON Pointer 链边界必须是 JSON 字符串")
            value = parse_json(value)
        value = resolve_pointer(value, pointer)
    return value


def citation_source(index, source):
    require(isinstance(source, str), "证据 source 必须是字符串")
    require(isinstance(index, dict), "packet 必须是对象")
    sources = index.get("sources")
    require(isinstance(sources, dict), "packet 缺 sources 对象")
    for key, descriptor in sources.items():
        require(isinstance(key, str) and isinstance(descriptor, dict) and isinstance(descriptor.get("path"), str),
                "packet source 必须有字符串 key/path")
        digest = descriptor.get("sha256")
        require(isinstance(digest, str) and re.fullmatch(r"[0-9a-f]{64}", digest) is not None,
                "packet source 缺合法 SHA-256: " + key)
    if source in sources:
        return source
    matches = [key for key, row in sources.items() if source in (row["path"], Path(row["path"]).name)]
    require(len(matches) < 2, "证据 source 不唯一，请使用 packet source ID 或完整物理路径: " + source)
    return matches[0] if matches else None


def verify_report_citations(report, packet_index):
    """校验 packet 已注册来源；未知来源不读取，以 unchecked 明示未覆盖。"""
    require(isinstance(report, dict) and isinstance(report.get("evidence_index"), list), "报告缺 evidence_index")
    checked, unchecked, loaded = [], [], {}
    for evidence in report["evidence_index"]:
        require(isinstance(evidence, dict) and all(isinstance(evidence.get(k), str) for k in ("id", "source", "locator")),
                "证据必须有 id/source/locator 字符串")
        key = citation_source(packet_index, evidence["source"])
        if key is None:
            unchecked.append({"id": evidence["id"], "source": evidence["source"]})
            continue
        if key not in loaded:
            descriptor = packet_index["sources"][key]
            body = read_bytes(descriptor["path"])
            require(sha256(body) == descriptor.get("sha256"), "证据来源哈希变化: " + key)
            value = parse_json(body)
            if "root_type" in descriptor:
                require(json_type(value) == descriptor["root_type"], "证据来源根类型不一致: " + key)
            loaded[key] = value
        try:
            value = resolve_locator(loaded[key], evidence["locator"])
        except PrepareError as exc:
            raise PrepareError("证据 " + evidence["id"] + " 定位无效: " + str(exc)) from exc
        checked.append({"id": evidence["id"], "source": key, "locator": locator_chain(evidence["locator"]),
                        "source_sha256": packet_index["sources"][key]["sha256"],
                        "value_sha256": value_hash(value), "type": json_type(value)})
    return {"checked_count": len(checked), "checked": checked, "unchecked": unchecked,
            "complete": not unchecked}


def citations_command(args):
    require(sys.flags.isolated, "解析下载 JSON 必须使用 python -I")
    report = parse_json(read_bytes(args.report))
    index = parse_json(read_bytes(args.packet))
    return verify_report_citations(report, index)


def resource_files(root):
    """仅固定技能维护资源；templates 的目录树也完整保留。"""
    root = physical_path(root)
    require(root.is_dir(), "skill-root 不存在")
    files, directories = [root / "SKILL.md"], []
    for name, suffix in (("references", ".md"), ("scripts", ".py"), ("templates", None)):
        directory = physical_path(root / name)
        require(directory.is_dir(), "缺少固定资源目录: " + name)
        directories.append(directory)
        pending = [directory]
        while pending:
            parent = pending.pop()
            for child in sorted(parent.iterdir()):
                physical_path(child)
                if child.is_dir():
                    if suffix is None:
                        directories.append(child)
                        pending.append(child)
                elif suffix is None or child.suffix == suffix:
                    require(child.is_file(), "固定资源不是普通文件: " + str(child))
                    files.append(child)
    for path in files:
        physical_path(path)
        require(path.is_file(), "缺少固定资源文件: " + str(path))
    return sorted(files), sorted(directories)


def report_contract(renderer, source):
    """字段来自已验证的所有 demo，枚举直接来自冻结校验器的 AST。"""
    manifest, reports, synthesis = renderer.demo_data(version=3)
    renderer.validate_manifest(manifest)
    renderer.validate_synthesis(synthesis, manifest)
    for report in reports.values():
        renderer.validate_case(report, manifest)
    require(renderer.assess(manifest, reports["pass"])[0] == "pass", "renderer pass 示例不通过")
    objects = {}

    def collect(value, where):
        if isinstance(value, dict):
            names = list(value)
            if where in objects:
                require(objects[where] == names, "demo 对象字段不一致: " + where)
            objects[where] = names
            for key, item in value.items():
                collect(item, where + "." + key)
        elif isinstance(value, list):
            for item in value:
                collect(item, where + "[]")

    for report in reports.values():
        collect(report, "case")
    enums = {}

    def walk(node, bindings):
        if isinstance(node, ast.For) and isinstance(node.target, ast.Name):
            if isinstance(node.iter, (ast.Tuple, ast.List)) and all(
                    isinstance(item, ast.Constant) and isinstance(item.value, str) for item in node.iter.elts):
                for item in node.iter.elts:
                    for statement in node.body:
                        walk(statement, dict(bindings, **{node.target.id: item.value}))
                return
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "enum":
            choices, where = node.args[1:3]
            if isinstance(choices, ast.Constant) and isinstance(choices.value, str):
                name = where.value if isinstance(where, ast.Constant) else bindings.get(getattr(where, "id", ""))
                if isinstance(name, str):
                    enums[name] = choices.value.split()
        for child in ast.iter_child_nodes(node):
            walk(child, bindings)

    tree = ast.parse(source)
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == "validate_case":
            walk(node, {})
    contract = {
        "schema_version": manifest["schema_version"],
        "renderer": "skill/scripts/render_report.py",
        "example": "worker-report-example.json",
        "object_fields": objects,
        "enums": enums,
        "required_check_ids": sorted(renderer.required_checks(manifest)),
        "numeric_nullable_fields": ["case.timeline[].duration_ms"] + ["case.metrics." + key for key in renderer.METRICS],
        "rules": [
            "对象字段必需且仅允许 object_fields 所列字段；数组可为空，ID 和关联字段用字符串。",
            "tool_usage_review 必需且仅含 inventory_complete:boolean 与 calls:array；calls 每项仅含 message_id/tool_call_id/tool_name/status/reason 字符串与 evidence_refs 字符串数组。",
            "calls 状态仅 pass/fail/unknown；pass/fail 必须引用本报告 evidence_index，unknown 说明缺口且保留在已知调用分母。",
            "调用键 (message_id,tool_call_id) 在本 case 唯一，start/end 合并一次，初次错误和修正重试分别记一次，修正不覆盖原 fail。",
            "逐条核对 packet 的 index.json 与 tool-calls.json 全部调用索引，核对完整轮次，calls 无缺项；主线程再按复合 ID 核对集合，不只核对数量。",
            "inventory_complete 仅在全部轮次及调用索引核对无缺项时为 true；清单不完整时仅列已知调用下限，不伪造未知总量。",
            "metrics.tool_calls 为 null 或非负整数；inventory_complete=true 时非 null 值必须等于 calls 长度，不完整时不得小于已知 calls 长度。",
            "工具遵循率=全部选定 case 的 calls pass 数/全部 calls 数，按总调用加权，不取案例平均；清单不完整只显示已知占比，零调用 N/A。",
            "设计遵循率=全部 checks 的 pass 条数/全部 checks 条数，不筛检查 ID 或类型、不平均各 case；零 checks 为 N/A。设计遵循通过案例占比单独按 case 统计。",
            "先读 baseline/audit-brief.json：design_summary 指向历史职责；focus/exclusions/scope_rules 限定本次可检查内容。引用领域原规则前核对适用范围，显式排除的细则不扣分。",
            "新增主动事项依据实际独立目的、额外内容和生成/push 参数；任务介绍、原交付说明及通知规则允许的有据关怀按原通知职责判断，思考标签不是新增或发送证据。",
            "逐项账包含适用查询、安排评估、生产及通知阶段；必要分页仍可续且无同范围完整替代时，execution 与 completion_gate 分别引用未收尾证据，不推测隐藏页具体遗漏。",
            "packet input-index.json 提供原输入及 mapped 输入的根类型、源哈希和规范定位；直接引用自动 locator（RFC6901 根为空字符串，嵌套容器用 JSON 编码 Pointer 链），用 verify-citations --packet INDEX --report REPORT 或 case --packet INDEX 解析并核验已注册来源。未经注册的来源保留 unchecked，不冒充全量证据定位已验证。",
            "进度或报告交付不是宿主 completed；首条 case_key+ordinal 核对后 started，report-ready 仍占槽，宿主完成通知后才 finished 并持续补位。只有必要人工输入阻塞全部剩余工作时暂停。",
            "逐调用 fail 不自动改变整体分类，原 checks/findings/struggles 仍按审计标准决定；tool_usage 同名 check 为工具使用案例检查，不是调用率。",
            "业务调用判定读取 skill/references/tool-call-scoring.md；HTML 仅展示调用顺序、工具名、判定、原因，不展示调用敏感 ID 或证据原文。",
            "schema_version 与 manifest 一致；case_key 必须属于 manifest。身份未知用空字符串，不能认领目标身份。",
            "checks/evidence_index/findings 的 id 与 timeline 的正整数 step 各自唯一。",
            "evidence_refs 引用本报告 evidence_index；非 unknown 检查、confirmed 发现和 struggle 必须有引用。",
            "metrics 与 duration_ms 未提供用 null；已提供须非负有限数值，不能用 bool。",
            "thinking 缺失不阻断通过；baseline/trace 不完整、必要检查或判定 unknown 不能认领顺畅通过。",
            "终态正常不等于流程完成；工具 Mock 不等于 Agent 失败。分类/比率由固定 renderer 重算。",
        ],
    }
    return reports["pass"], contract


def freeze_command(args):
    root, run = physical_path(args.skill_root), physical_path(args.run_dir)
    require(root != run and root not in run.parents, "RUN_DIR 不允许位于技能源码目录内")
    runtime = physical_path(run / "runtime")
    require(not runtime.exists(), "冻结目标 runtime 已存在")
    if run.exists():
        require(run.is_dir() and set(p.name for p in run.iterdir()).issubset({"acquisition"}),
                "已有 RUN_DIR 必须为空或仅有 acquisition")
        if (run / "acquisition").exists():
            physical_path(run / "acquisition")
            require((run / "acquisition").is_dir(), "acquisition 必须是目录")
    files, directories = resource_files(root)
    scopes = [physical_path(path) for path in (args.scope_file or [])]
    require(all(run != path and run not in path.parents for path in scopes), "规则源不允许位于 RUN_DIR 内")
    sources = files + scopes
    contents = {path: read_bytes(path) for path in sources}
    for path in sources:
        require(read_bytes(path) == contents[path], "复制前源文件字节变化: " + str(path))
    private_dir(run)
    new_private_dir(runtime)
    private_dir(runtime / "skill")
    for directory in directories:
        private_dir(runtime / "skill" / directory.relative_to(root))
    entries = []
    for path in files:
        destination = runtime / "skill" / path.relative_to(root)
        write_bytes(destination, contents[path])
        entries.append({"source_path": str(path), "path": str(destination.relative_to(runtime)),
                        "sha256": sha256(contents[path]), "size_bytes": len(contents[path])})
    for i, path in enumerate(scopes, 1):
        destination = runtime / "scope" / ("scope-%04d.md" % i)
        write_bytes(destination, contents[path])
        entries.append({"source_path": str(path), "path": str(destination.relative_to(runtime)),
                        "sha256": sha256(contents[path]), "size_bytes": len(contents[path])})
    require(resource_files(root) == (files, directories), "复制期间固定资源文件集合变化")
    for path in sources:
        require(read_bytes(path) == contents[path], "复制期间源文件字节变化: " + str(path))
    # 只执行调用者指定的可信维护目录之冻结副本；绝不从下载目录导入。
    renderer_path = runtime / "skill/scripts/render_report.py"
    require(renderer_path.is_file(), "缺少可信 render_report.py")
    renderer_source = read_bytes(renderer_path)
    renderer = ModuleType("mia_eval_frozen_renderer")
    renderer.__file__ = str(renderer_path)
    exec(compile(renderer_source, str(renderer_path), "exec"), renderer.__dict__)
    example, contract = report_contract(renderer, renderer_source)
    derived = [("worker-report-example.json", example)]
    if callable(getattr(renderer, "axis_examples", None)):
        derived.append(("axis-examples.json", renderer.axis_examples()))
        contract["axis_examples"] = "axis-examples.json"
    derived.append(("report-contract.json", contract))
    for name, value in derived:
        body = json_bytes(value)
        write_bytes(runtime / name, body)
        derivation = "axis_examples()" if name == "axis-examples.json" else "demo_data(version=3) / validator"
        entries.append({"source_path": str(renderer_path), "derived_from": derivation,
                        "path": name, "sha256": sha256(body), "size_bytes": len(body)})
    # 包括可信脚本执行期间的改动也不能悄悄改变本轮来源。
    require(resource_files(root) == (files, directories), "生成示例期间源资源集合变化")
    for path in sources:
        require(read_bytes(path) == contents[path], "冻结期间源文件字节变化: " + str(path))
    for entry in entries:
        require(sha256(read_bytes(runtime / entry["path"])) == entry["sha256"], "冻结副本哈希不一致")
    snapshot = {"schema_version": 1, "skill_root": str(root), "run_dir": str(run),
                "frozen_at": datetime.now(timezone.utc).isoformat(), "files": entries}
    save_json(runtime / "snapshot.json", snapshot)
    return {"snapshot": str(runtime / "snapshot.json"), "files": len(entries)}


# 仅访问明确的结构字段才解析 JSON 字符串；request/output/content 等自然语言不解析。
JSON_CONTAINERS = {
    "message_snapshot_json", "raw_events_json", "runtime_steps_json", "runtime_usage_json",
    "token_usage_json", "tool_calls_json", "runtime_segments_json", "selected_skills_json",
    "selected_tools_json", "skill_refs_json", "data_json", "facts_json", "usage_json",
}
IDENTITY_KEYS = ("session_id", "message_id", "motif_id", "agent_id", "version", "run_id", "trace_id")
ID_ALIASES = {"session_id": "sessionId", "message_id": "messageId", "run_id": "runId"}
TERMINAL_PAYLOADS = ("run_finished_payload", "run_failed_payload", "run_cancelled_payload",
                     "run_interrupted_payload", "terminal_payload")
TERMINAL_TYPES = {"run_finished", "run_failed", "run_cancelled", "run_interrupted", "terminal"}


class Node:
    """locator 是 JSON Pointer 链；链边界仅在值为字符串时进行一次 JSON 解码。"""
    def __init__(self, value, source, locator=None):
        self.value, self.source, self.locator = value, source, locator or [""]

    def child(self, key):
        escaped = pointer_token(key)
        locator = self.locator[:-1] + [self.locator[-1] + "/" + escaped]
        return Node(self.value[key], self.source, locator)

    def ref(self):
        return {"source": self.source, "locator": self.locator}

    def container(self, key, issues):
        if not isinstance(self.value, dict) or key not in self.value:
            return None
        node = self.child(key)
        if isinstance(node.value, str):
            require(key in JSON_CONTAINERS, "拒绝解析未知 JSON 容器: " + key)
            try:
                return Node(parse_json(node.value), node.source, node.locator + [""])
            except PrepareError as exc:
                issues.append(dict(node.ref(), field=key, error=str(exc)))
                return None
        return node


def identifier(value):
    if isinstance(value, str) and value:
        return value
    if type(value) is int:
        return str(value)
    return None


def identities(node):
    result, invalid = {}, []
    if not isinstance(node.value, dict):
        return result, invalid
    for key in IDENTITY_KEYS:
        for alias in (key, ID_ALIASES.get(key, key)):
            if alias not in node.value:
                continue
            value = identifier(node.value[alias])
            if value is None:
                invalid.append(key)
            elif key in result and result[key] != value:
                invalid.append(key)
            else:
                result[key] = value
    return result, sorted(set(invalid))


def response_status(node):
    value = node.value
    infos = [value["status_info"]] if "status_info" in value else []
    if isinstance(value.get("data"), dict) and "status_info" in value["data"]:
        infos.append(value["data"]["status_info"])
    if not infos or any(not isinstance(info, dict) or "status_code" not in info for info in infos):
        return {"state": "unknown", "raw": infos}
    ok = all((type(info["status_code"]) is int and info["status_code"] == 0) or
             info["status_code"] == "0" for info in infos)
    return {"state": "ok" if ok else "failed", "raw": infos}


def evidence_nodes(node, issues):
    """history 已知 message/debug/snapshot 层，不遍历任意业务对象。"""
    result = [node]
    if isinstance(node.value, dict) and isinstance(node.value.get("debug"), dict):
        result.insert(0, node.child("debug"))
    snapshot = node.container("message_snapshot_json", issues)
    if snapshot and isinstance(snapshot.value, dict):
        result.append(snapshot)
        if isinstance(snapshot.value.get("debug"), dict):
            result.append(snapshot.child("debug"))
    return result


def ref_below(ref, node):
    if ref["source"] != node.source or len(ref["locator"]) < len(node.locator):
        return False
    position = len(node.locator) - 1
    if ref["locator"][:position] != node.locator[:position]:
        return False
    child, parent = ref["locator"][position], node.locator[position]
    return child == parent or child.startswith(parent + "/")


def described(node):
    value = node.value
    item = dict(node.ref(), sha256=value_hash(value), type=type(value).__name__)
    if isinstance(value, (dict, list, str)):
        item["length"] = len(value)
    if isinstance(value, dict):
        item["fields"] = list(value)
    return item


def deduplicate(nodes):
    groups = {}
    for node in nodes:
        digest = value_hash(node.value)
        if digest not in groups:
            groups[digest] = {"node": node, "sources": []}
        if node.ref() not in groups[digest]["sources"]:
            groups[digest]["sources"].append(node.ref())
    return list(groups.values())


def first_body(node):
    if isinstance(node.value, dict) and isinstance(node.value.get("data"), dict):
        return node.child("data")
    return node


def nested_identity_conflicts(node, expected, issues):
    """检查明确的调用/事件/快照结构，不扫描自然语言或工具回包内业务 ID。"""
    for key in ("raw_events_json", "tool_calls_json", "runtime_steps_json", "runtime_segments_json"):
        container = node.container(key, issues)
        if not container or not isinstance(container.value, list):
            continue
        for i, value in enumerate(container.value):
            if not isinstance(value, dict):
                continue
            child = container.child(i)
            checks = [child]
            if key == "raw_events_json" and isinstance(value.get("payload"), dict) and isinstance(value["payload"].get("tool_payload"), dict):
                checks.append(child.child("payload").child("tool_payload"))
            for candidate in checks:
                ids, invalid = identities(candidate)
                if invalid or any(field in expected and expected[field] != val for field, val in ids.items()):
                    return True
    return False


def message_prompt_nodes(nodes, issues):
    snapshots, sp_pe = [], []
    for node in nodes:
        if all(isinstance(node.value.get(key), str) and node.value[key].strip() for key in ("sp", "pe")):
            sp_pe.append(Node({key: node.value[key] for key in ("sp", "pe")}, node.source, node.locator))
        steps = node.container("runtime_steps_json", issues)
        if steps and isinstance(steps.value, list):
            for i, step in enumerate(steps.value):
                if isinstance(step, dict) and isinstance(step.get("prompt_built"), dict):
                    snapshots.append(steps.child(i).child("prompt_built"))
        events = node.container("raw_events_json", issues)
        if not events or not isinstance(events.value, list):
            continue
        for i, event in enumerate(events.value):
            if not isinstance(event, dict) or not isinstance(event.get("payload"), dict):
                continue
            payload = events.child(i).child("payload")
            if isinstance(payload.value.get("prompt_built_payload"), dict):
                snapshots.append(payload.child("prompt_built_payload"))
            debug = payload.value.get("debug_payload")
            if isinstance(debug, dict):
                data = payload.child("debug_payload").container("data_json", issues)
                if data and isinstance(data.value, dict):
                    if isinstance(data.value.get("prompt_built"), dict):
                        snapshots.append(data.child("prompt_built"))
                    elif "message_list" in data.value:
                        snapshots.append(data)
    return deduplicate(snapshots), deduplicate(sp_pe)


def usable_prompt(value):
    return (isinstance(value.get("message_list"), list) and bool(value["message_list"])) or (
        isinstance(value.get("prompt_parts"), dict) and bool(value["prompt_parts"]) and
        isinstance(value.get("user_message"), dict) and bool(value["user_message"]))


def raw_event_nodes(nodes, issues):
    result, present = [], False
    for node in nodes:
        events = node.container("raw_events_json", issues)
        if events and isinstance(events.value, list):
            present = True
            for i, event in enumerate(events.value):
                if isinstance(event, dict):
                    result.append(events.child(i))
                else:
                    issues.append(dict(events.child(i).ref(), field="raw_events_json", error="事件必须是对象"))
    return deduplicate(result), present


def terminal_event(value):
    payload = value.get("payload", {})
    return (isinstance(payload, dict) and any(isinstance(payload.get(key), dict) for key in TERMINAL_PAYLOADS)) or (
        isinstance(value.get("type"), str) and value["type"] in TERMINAL_TYPES)


def request_comparison_hash(value):
    """只为明确调用 request/input/arguments 做 JSON 容器比较，原值永不改写。"""
    if isinstance(value, str):
        try:
            parsed = parse_json(value)
            if isinstance(parsed, (dict, list)):
                return value_hash(parsed)
        except PrepareError:
            pass
    return value_hash(value)


def tool_index(nodes, events, message_id, issues):
    calls, present = [], False
    for node in nodes:
        array = node.container("tool_calls_json", issues)
        if array and isinstance(array.value, list):
            present = True
            for i, value in enumerate(array.value):
                if isinstance(value, dict):
                    calls.append(array.child(i))
                else:
                    issues.append(dict(array.child(i).ref(), field="tool_calls_json", error="调用必须是对象"))
    for event in events:
        node = event["node"]
        payload = node.value.get("payload")
        if isinstance(payload, dict) and isinstance(payload.get("tool_payload"), dict):
            present = True
            calls.append(node.child("payload").child("tool_payload"))
    grouped = {}
    for node in calls:
        value = node.value
        call_id = identifier(value.get("tool_call_id", value.get("id")))
        key = ("id", call_id) if call_id is not None else ("unassociated", value_hash(value))
        entry = grouped.setdefault(key, {"message_id": message_id, "tool_call_id": call_id, "sources": [], "fields": {}})
        if node.ref() not in entry["sources"]:
            entry["sources"].append(node.ref())
        for field, aliases in (("name", ("name",)), ("request", ("request", "arguments", "input")),
                               ("output", ("output", "result", "response")), ("error", ("error",)),
                               ("status", ("status",)), ("raw_status", ("raw_status",)),
                               ("latency_ms", ("latency_ms", "latencyMs")), ("tool_type", ("tool_type",))):
            for alias in aliases:
                if alias not in value:
                    continue
                variants = entry["fields"].setdefault(field, [])
                digest = value_hash(value[alias])
                match = next((item for item in variants if item["sha256"] == digest), None)
                if match is None:
                    match = {"value": value[alias], "sha256": digest, "sources": []}
                    variants.append(match)
                if node.child(alias).ref() not in match["sources"]:
                    match["sources"].append(node.child(alias).ref())
                break
    missing = []
    for entry in grouped.values():
        fields = entry["fields"]
        gaps = []
        if entry["tool_call_id"] is None:
            gaps.append("tool_call_id")
        for key in ("name", "request"):
            if key not in fields:
                gaps.append(key)
        has_output = any(item["value"] is not None for item in fields.get("output", []))
        has_error = any(bool(item["value"].strip()) if isinstance(item["value"], str) else bool(item["value"])
                        for item in fields.get("error", []))
        if not has_output and not has_error:
            gaps.append("output_or_error")
        if not any(key in fields for key in ("status", "raw_status")):
            gaps.append("status")
        if len(fields.get("name", [])) > 1 or len({request_comparison_hash(item["value"])
                                                  for item in fields.get("request", [])}) > 1:
            gaps.append("identity_or_request_conflict")
        entry["missing_fields"] = gaps
        if gaps:
            missing.append("tool_calls.details")
    return list(grouped.values()), present, sorted(set(missing))


def usage_nodes(nodes, events, issues):
    runtime, tokens, observation = [], [], []
    for node in nodes:
        for key, target in (("runtime_usage_json", runtime), ("token_usage_json", tokens)):
            item = node.container(key, issues)
            if item and isinstance(item.value, dict) and item.value:
                target.append(item)
    for event in events:
        node = event["node"]
        payload = node.value.get("payload")
        if not isinstance(payload, dict) or not isinstance(payload.get("observation_payload"), dict):
            continue
        obs = node.child("payload").child("observation_payload")
        if isinstance(obs.value.get("usage_payload"), dict) and obs.value["usage_payload"]:
            observation.append(obs.child("usage_payload"))
        else:
            item = obs.container("usage_json", issues)
            if item and isinstance(item.value, dict) and item.value:
                observation.append(item)
    # 每消息只选一份；优先官方 runtime，其次 token，最后一个原始 usage 观察。
    candidates = runtime + tokens + list(reversed(observation))
    usage_keys = {"prompt_tokens", "completion_tokens", "total_tokens", "latency_ms", "total_latency_ms",
                  "model_call_count", "tool_call_count", "token_usage", "latency", "tool_usage", "workflow_usage"}
    return deduplicate([node for node in candidates if any(
        key in node.value and node.value[key] not in (None, {}, []) for key in usage_keys)])


def packet_command(args):
    require(sys.flags.isolated, "解析下载 JSON 必须使用 python -I")
    history_path, out = physical_path(args.history), physical_path(args.out)
    debug_paths = [physical_path(path) for path in (args.debug or [])]
    input_paths = [physical_path(path) for path in getattr(args, "input", [])]
    require(not out.exists(), "packet 必须使用尚不存在的独立目录")
    require(out != history_path.parent and all(out not in path.parents for path in [history_path] + debug_paths + input_paths),
            "派生目录不能包含输入文件")
    sources, roots, input_bytes = {}, {}, {}
    for name, path in [("history", history_path)] + [("debug-%04d" % i, p) for i, p in enumerate(debug_paths, 1)] + [("input-%04d" % i, p) for i, p in enumerate(input_paths, 1)]:
        body = read_bytes(path)
        value = parse_json(body)
        is_input = name.startswith("input-")
        require(is_input or isinstance(value, dict), "官方 JSON 根必须是对象: " + str(path))
        root = Node(value, name)
        roots[name] = root
        input_bytes[path] = body
        sources[name] = {"path": str(path), "sha256": sha256(body), "root_type": json_type(value),
                         "root_locator": [""], "kind": "input" if is_input else "runtime",
                         "status_info": {"state": "not_applicable", "raw": []} if is_input else response_status(root)}
    history = first_body(roots["history"])
    issues, gaps, rejected = [], [], []
    rows = history.value.get("messages")
    if not isinstance(rows, list):
        rows = []
        gaps.append("history.messages")
    has_more = history.value.get("has_more")
    if type(has_more) is not bool:
        gaps.append("history.has_more")
    elif has_more:
        gaps.append("necessary_previous_history")
    if sources["history"]["status_info"]["state"] != "ok":
        gaps.append("history.status_info")
    root_ids, root_invalid = identities(roots["history"])
    body_ids, body_invalid = identities(history)
    root_invalid += body_invalid + [key for key, val in body_ids.items() if key in root_ids and root_ids[key] != val]
    root_ids.update(body_ids)
    messages, seen = [], set()
    for i, value in enumerate(rows):
        if not isinstance(value, dict):
            issues.append(dict(history.child("messages").child(i).ref(), field="messages", error="消息必须是对象"))
            gaps.append("history.message_shape")
            continue
        node = history.child("messages").child(i)
        own, invalid = identities(node)
        candidates = evidence_nodes(node, issues)
        message_id = own.get("message_id")
        if message_id is None:
            embedded_ids = {identities(candidate)[0]["message_id"] for candidate in candidates
                            if "message_id" in identities(candidate)[0]}
            if len(embedded_ids) == 1:
                message_id = embedded_ids.pop()
                own["message_id"] = message_id
            elif len(embedded_ids) > 1:
                invalid.append("message_id")
        entry = {"ordinal": len(messages) + 1, "message_id": message_id, "role": value.get("role"),
                 "identity": dict(root_ids, **own), "sources": [node.ref()], "missing_fields": [],
                 "optional_missing_fields": [], "nodes": []}
        if message_id is None:
            entry["missing_fields"].append("identity.message_id")
        elif message_id in seen:
            entry["missing_fields"].append("identity.duplicate_message_id")
            gaps.append("history.duplicate_message_id")
        else:
            seen.add(message_id)
        if any(ref_below(issue, node) for issue in issues):
            entry["missing_fields"].append("evidence.parse_or_identity_issue")
        # session 可来自权威 history 内嵌 debug，但不能从 run_id 或外部 debug 猜。
        if "session_id" not in entry["identity"]:
            sessions = {identities(candidate)[0]["session_id"] for candidate in candidates
                        if "session_id" in identities(candidate)[0]}
            if len(sessions) == 1:
                entry["identity"]["session_id"] = sessions.pop()
        if invalid or root_invalid or any(key in own and own[key] != val for key, val in root_ids.items()):
            entry["missing_fields"].append("identity.conflict")
        for candidate in candidates:
            ids, bad = identities(candidate)
            conflict = bad or any(key in entry["identity"] and entry["identity"][key] != val for key, val in ids.items())
            if conflict:
                entry["missing_fields"].append("identity.conflict")
                issues.append(dict(candidate.ref(), field="identity", error="history 内身份冲突"))
            else:
                entry["nodes"].append(candidate)
                for key, val in ids.items():
                    entry["identity"].setdefault(key, val)
        if "session_id" not in entry["identity"]:
            entry["missing_fields"].append("identity.session_id")
        messages.append(entry)
    indexes = history.value.get("message_indexes", [])
    if isinstance(indexes, list):
        for i, value in enumerate(indexes):
            # 标量 message_indexes 是平台序号，不是 message_id；仅显式 ID 对象可占位。
            message_id = identities(Node(value, "history"))[0].get("message_id") if isinstance(value, dict) else None
            if message_id and message_id not in seen:
                seen.add(message_id)
                messages.append({"ordinal": len(messages) + 1, "message_id": message_id, "role": None,
                                 "identity": dict(root_ids, message_id=message_id),
                                 "sources": [history.child("message_indexes").child(i).ref()],
                                 "missing_fields": ["history.message_body"], "optional_missing_fields": [], "nodes": []})
                gaps.append("history.message_body")
    require(messages or gaps, "history 不含可识别消息")
    sessions = {entry["identity"].get("session_id") for entry in messages} - {None}
    if len(sessions) > 1:
        gaps.append("history.session_conflict")
        for entry in messages:
            entry["missing_fields"].append("identity.conflict")
    for name, root in roots.items():
        if name == "history" or name.startswith("input-"):
            continue
        body = first_body(root)
        candidates = evidence_nodes(body, issues)
        ids, bad = identities(root)
        body_identity, body_invalid = identities(body)
        bad += body_invalid + [key for key, val in body_identity.items() if key in ids and ids[key] != val]
        ids.update(body_identity)
        for candidate in candidates:
            nested, invalid = identities(candidate)
            bad += invalid + [key for key, val in nested.items() if key in ids and ids[key] != val]
            for key, val in nested.items():
                ids.setdefault(key, val)
        matches = [entry for entry in messages if ids.get("message_id") is not None and entry["message_id"] == ids["message_id"]]
        reason = None
        if sources[name]["status_info"]["state"] != "ok":
            reason = "debug.status_info"
        elif bad:
            reason = "debug.identity_conflict"
        elif len(matches) != 1:
            reason = "debug.message_id_unmatched"
        elif not ids.get("session_id") or not matches[0]["identity"].get("session_id"):
            reason = "debug.session_id_unverified"
        elif any(key in matches[0]["identity"] and val != matches[0]["identity"][key] for key, val in ids.items()):
            reason = "debug.identity_conflict"
        if reason:
            rejected.append({"source": name, "identity": ids, "reason": reason})
            gaps.append(reason)
            for entry in matches:
                entry["missing_fields"].append(reason)
        else:
            expected = dict(matches[0]["identity"], **ids)
            if any(nested_identity_conflicts(candidate, expected, issues) for candidate in candidates):
                rejected.append({"source": name, "identity": ids, "reason": "debug.nested_identity_conflict"})
                gaps.append("debug.nested_identity_conflict")
                matches[0]["missing_fields"].append("debug.nested_identity_conflict")
                continue
            matches[0]["nodes"].extend(candidates)
            matches[0]["sources"].append(body.ref())
            for key, val in ids.items():
                matches[0]["identity"].setdefault(key, val)
    new_private_dir(out)
    tools, timeline, thinking, prompts, usages, needs_debug = [], [], [], [], [], []
    for entry in messages:
        message_id, nodes = entry["message_id"], entry.pop("nodes")
        missing = entry["missing_fields"]
        input_only = entry["role"] in ("user", "system")
        if any(nested_identity_conflicts(node, entry["identity"], issues) for node in nodes):
            missing.append("evidence.identity_conflict")
        events, events_present = raw_event_nodes(nodes, issues)
        snapshots, sp_pe = message_prompt_nodes(nodes, issues)
        has_prompt = any(usable_prompt(item["node"].value) for item in snapshots) or bool(sp_pe)
        first_saved = False
        for kind, groups in (("prompt_built", snapshots), ("sp_pe", sp_pe)):
            for group in groups:
                node = group["node"]
                item = {"message_id": message_id, "kind": kind, "sha256": value_hash(node.value),
                        "fields": {key: {"sha256": value_hash(value), "type": type(value).__name__,
                                         "length": len(value) if isinstance(value, (dict, list, str)) else None}
                                   for key, value in node.value.items()}, "sources": group["sources"]}
                # sp_pe 是两个字段的投影，不伪称 source locator 指向该新对象。
                if kind == "sp_pe":
                    item["projection_fields"] = ["sp", "pe"]
                usable = usable_prompt(node.value) if kind == "prompt_built" else True
                if not first_saved and usable:
                    path = "prompts/message-%04d-first.json" % entry["ordinal"]
                    save_json(out / path, node.value)
                    item["snapshot_file"] = path
                    first_saved = True
                prompts.append(item)
        terminal_refs, event_ids = [], {}
        for group in events:
            node, value = group["node"], group["node"].value
            ids, bad = identities(node)
            event_id = identifier(value.get("event_id"))
            conflict = bad or any(key in entry["identity"] and entry["identity"][key] != val for key, val in ids.items())
            if event_id and event_id in event_ids and event_ids[event_id] != value_hash(value):
                conflict = True
            if event_id:
                event_ids[event_id] = value_hash(value)
            if conflict:
                missing.append("raw_events.identity_conflict")
            is_terminal = terminal_event(value) and not conflict
            payload = value.get("payload")
            item = {"message_id": message_id, "event_id": event_id,
                    "identity": ids, "identity_conflict": bool(conflict),
                    "type": value.get("type"), "sequence": value.get("sequence"),
                    "timestamp_ms": value.get("timestamp_ms"),
                    "payload_fields": list(payload) if isinstance(payload, dict) else [],
                    "terminal_observed": is_terminal, "sha256": value_hash(value), "sources": group["sources"]}
            timeline.append(item)
            if is_terminal:
                terminal_refs.extend(group["sources"])
            if isinstance(payload, dict) and isinstance(payload.get("thinking_payload"), dict):
                think = node.child("payload").child("thinking_payload")
                if isinstance(think.value.get("text"), str) and think.value["text"]:
                    text = think.child("text")
                    path = "thinking/thinking-%06d.txt" % (len(thinking) + 1)
                    encoded = text.value.encode("utf-8")
                    write_bytes(out / path, encoded)
                    thinking.append({"message_id": message_id, "file": path, "sha256": sha256(encoded),
                                     "size_bytes": len(encoded), "source": text.ref(), "read_on_demand": True})
        calls, calls_present, calls_missing = tool_index(nodes, events, message_id, issues)
        tools.extend(calls)
        usage = usage_nodes(nodes, events, issues)
        if usage:
            selected = usage[0]["node"]
            usages.append({"message_id": message_id, "value": selected.value,
                           "sources": usage[0]["sources"], "alternatives": [described(g["node"]) for g in usage[1:]]})
        if not input_only:
            for field, present in (("prompt", has_prompt), ("tool_calls", calls_present),
                                   ("raw_events", events_present), ("terminal_event", bool(terminal_refs)), ("usage", bool(usage))):
                if not present:
                    missing.append(field)
            missing.extend(calls_missing)
            if sources["history"]["status_info"]["state"] != "ok":
                missing.append("history.status_info")
        if not any(item["message_id"] == message_id for item in thinking):
            entry["optional_missing_fields"].append("thinking")
        for node in nodes:
            if "status_info" in node.value and response_status(node)["state"] != "ok":
                missing.append("evidence.status_info")
                issues.append(dict(node.ref(), field="status_info", error="内嵌证据返回非成功状态"))
        source_names = {node.source for node in nodes}
        if any(issue["source"] in source_names and any(ref_below(issue, node) for node in nodes)
               for issue in issues):
            missing.append("evidence.parse_or_identity_issue")
        entry["missing_fields"] = sorted(set(missing))
        entry["terminal_sources"] = terminal_refs
        entry["debug_complete"] = not entry["missing_fields"] and not input_only
        if entry["missing_fields"] and not input_only and message_id is not None:
            needs_debug.append(message_id)
    for issue in issues:
        gaps.append("parse_or_identity_issue")
    # 源字节不改写，全部原事件经 timeline + pointer 链可追溯。
    for path, body in input_bytes.items():
        require(read_bytes(path) == body, "解析期间输入字节变化: " + str(path))
    index = {"schema_version": 1, "sources": sources,
             "locator_format": "sources 的物理文件；locator 是 RFC6901 JSON Pointer 链，链边界只对字符串做 JSON 解码；projection_fields 表示字段投影。",
             "has_more": has_more, "message_indexes": indexes,
             "history_complete": not gaps and not any(e["missing_fields"] for e in messages),
             "messages": messages, "needs_debug_message_ids": list(dict.fromkeys(needs_debug)),
             "missing_fields": sorted(set(gaps) | {field for entry in messages for field in entry["missing_fields"]}),
             "global_missing_fields": sorted(set(gaps)), "issues": issues, "rejected_debug": rejected,
             "files": {"tool_calls": "tool-calls.json", "timeline": "timeline-index.json", "usage": "usage.json",
                       "thinking": "thinking-index.json", "prompts": "prompt-index.json", "inputs": "input-index.json"},
             "note": "仅证据覆盖索引，不是 Agent 通过判定；未知原始 enum 不映射成成功或失败。"}
    input_index = []
    for name, root in roots.items():
        if not name.startswith("input-"):
            continue
        # 提供根及顶层输入字段定位，数组还提供每个元素的字段；正文仍从原文件完整读取。
        nodes = [root]
        if isinstance(root.value, dict):
            nodes.extend(root.child(key) for key in root.value)
        elif isinstance(root.value, list):
            for position, value in enumerate(root.value):
                element = root.child(position)
                nodes.append(element)
                if isinstance(value, dict):
                    nodes.extend(element.child(key) for key in value)
        input_index.append({"source": name, "path": sources[name]["path"], "sha256": sources[name]["sha256"],
                            "root_type": sources[name]["root_type"],
                            "locations": [dict(described(node), report_locator=json.dumps(node.locator, ensure_ascii=False)) for node in nodes]})
    for name, value in (("input-index.json", input_index), ("tool-calls.json", tools), ("timeline-index.json", timeline),
                        ("usage.json", {"aggregation": "none; one original usage per message", "messages": usages}),
                        ("thinking-index.json", thinking), ("prompt-index.json", prompts), ("index.json", index)):
        save_json(out / name, value)
    return {"index": str(out / "index.json"), "messages": len(messages),
            "needs_debug_message_ids": index["needs_debug_message_ids"], "history_complete": index["history_complete"]}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    freeze = commands.add_parser("freeze", help="从可信技能维护目录冻结本轮资源")
    freeze.add_argument("--skill-root", required=True)
    freeze.add_argument("--run-dir", required=True)
    freeze.add_argument("--scope-file", action="append", default=[])
    packet = commands.add_parser("packet", help="纯本地派生一个 case 的 history/debug 索引")
    packet.add_argument("--history", required=True)
    packet.add_argument("--debug", action="append", default=[])
    packet.add_argument("--input", action="append", default=[], help="原始输入或 mapped 输入 JSON；可重复，保留根类型与自动定位")
    packet.add_argument("--out", required=True)
    citations = commands.add_parser("verify-citations", help="核验报告中 packet 已注册来源的哈希与 JSON Pointer")
    citations.add_argument("--packet", required=True, help="packet 的 index.json 物理绝对路径")
    citations.add_argument("--report", required=True)
    args = parser.parse_args(argv)
    try:
        result = {"freeze": freeze_command, "packet": packet_command, "verify-citations": citations_command}[args.command](args)
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 0
    except (PrepareError, OSError, UnicodeError, RecursionError) as exc:
        print("审计准备失败：" + str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
