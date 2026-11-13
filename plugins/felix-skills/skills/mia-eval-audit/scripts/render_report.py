#!/usr/bin/env python3
"""确定性、离线、仅标准库的评测报告渲染器（Python 3.9+）。"""
import argparse
import copy
import html
import ipaddress
import json
import math
import re
import sys
from collections import Counter
from pathlib import Path
from urllib.parse import urlsplit

TEMPLATES = Path(__file__).resolve().parent.parent / "templates"
SLUG = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*\Z")
SLOT = re.compile(r"\{\{([A-Z][A-Z0-9_]*)\}\}")
METRICS = ("latency_ms", "model_calls", "tool_calls", "tool_failures", "business_errors", "retries", "prompt_tokens", "completion_tokens")
LABELS = {"pass": "顺畅通过", "fail": "失败", "recovered": "恢复/挣扎", "insufficient": "证据不足"}


class ContractError(ValueError):
    pass


def require(condition, message):
    if not condition:
        raise ContractError(message)


def fields(value, names, where):
    require(isinstance(value, dict), where + " 必须是对象")
    require(set(value) == set(names.split()), where + " 字段不匹配；必需且仅允许 " + names)


def text(value, where, empty=False):
    require(isinstance(value, str) and (empty or bool(value.strip())), where + " 必须是字符串" + ("" if empty else "且非空"))
    require(not any(ord(c) < 32 and c not in "\n\r\t" for c in value), where + " 含控制字符")


def texts(value, where):
    require(isinstance(value, list), where + " 必须是字符串数组")
    for item in value:
        text(item, where)


def unique(values, where):
    require(len(values) == len(set(values)), where + " 重复")


def enum(value, choices, where):
    require(isinstance(value, str) and value in choices.split(), where + " 不合法")


def number(value, where, integer=False, nullable=True):
    if value is None and nullable:
        return
    require(type(value) in ((int,) if integer else (int, float)), where + " 必须是非负有限数值；不能用 bool")
    require(value >= 0 and (type(value) is int or math.isfinite(value)), where + " 必须是非负有限数值")


def schema(value):
    require(type(value.get("schema_version")) is int and value["schema_version"] == 1, "schema_version 必须为整数 1")


def identity(value, where):
    fields(value, "motif_id agent_id version", where)
    for key in value:
        text(value[key], where + "." + key, empty=(where == "identity"))


def safe_url(value):
    text(value, "task_url", empty=True)
    if not value:
        return
    require(not any(c.isspace() or ord(c) < 32 for c in value) and "\\" not in value, "task_url 不合法")
    try:
        parsed = urlsplit(value)
        port = parsed.port
        require(parsed.scheme == "https" and bool(parsed.hostname) and parsed.username is None and parsed.password is None, "task_url 仅允许有效 https URL，不能包含凭据")
        require(port is None or 0 < port < 65536, "task_url 端口不合法")
        host = parsed.hostname
        if ":" in host:
            ipaddress.IPv6Address(host)
        else:
            host = host.rstrip(".")
            require(len(host) <= 253 and all(re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?", label) for label in host.split(".")), "task_url 主机不合法")
    except ValueError as exc:
        raise ContractError("task_url 不合法") from exc


def validate_manifest(value):
    fields(value, "schema_version title task_url generated_at scope_complete scope_note target cases", "manifest")
    require(type(value["scope_complete"]) is bool, "scope_complete 必须是 boolean")
    text(value["scope_note"], "scope_note")
    schema(value)
    for key in ("title", "generated_at"):
        text(value[key], "manifest." + key)
    safe_url(value["task_url"])
    identity(value["target"], "target")
    require(isinstance(value["cases"], list), "cases 必须为数组")
    for case in value["cases"]:
        fields(case, "case_key ordinal title session_id message_ids required_check_ids", "manifest.case")
        text(case["case_key"], "case_key")
        require(bool(SLUG.fullmatch(case["case_key"])) and len(case["case_key"]) <= 80, "case_key 必须为最长 80 字符的小写安全 slug")
        number(case["ordinal"], "ordinal", integer=True, nullable=False)
        require(case["ordinal"] >= 1, "ordinal 从 1 开始")
        text(case["title"], "title")
        text(case["session_id"], "session_id", empty=True)
        for key in ("message_ids", "required_check_ids"):
            texts(case[key], key)
            unique(case[key], key)
        require(set(("identity", "design", "execution", "smoothness")).issubset(case["required_check_ids"]), "required_check_ids 至少包含 identity/design/execution/smoothness")
    unique([c["case_key"] for c in value["cases"]], "manifest case_key")
    unique([c["ordinal"] for c in value["cases"]], "manifest ordinal")
    return value


def member(manifest, key):
    matches = [c for c in manifest["cases"] if c["case_key"] == key]
    require(len(matches) == 1, "报告引用 manifest 以外的 case_key: " + str(key))
    return matches[0]


def validate_case(value, manifest):
    fields(value, "schema_version case_key title summary identity context verdict coverage checks timeline findings struggles metrics limitations evidence_index", "case")
    schema(value)
    for key in ("case_key", "title", "summary"):
        text(value[key], key)
    member(manifest, value["case_key"])
    identity(value["identity"], "identity")
    fields(value["context"], "input_summary output_summary annotations_summary", "context")
    for key, item in value["context"].items():
        text(item, key, empty=True)
    fields(value["verdict"], "design execution smoothness", "verdict")
    for key in ("design", "execution"):
        enum(value["verdict"][key], "pass fail unknown", key)
    enum(value["verdict"]["smoothness"], "smooth recovered struggling unknown", "smoothness")
    fields(value["coverage"], "baseline trace thinking", "coverage")
    for key in ("baseline", "trace"):
        enum(value["coverage"][key], "complete partial missing", key)
    enum(value["coverage"]["thinking"], "available partial missing", "thinking")
    for key in ("checks", "timeline", "findings", "struggles", "evidence_index"):
        require(isinstance(value[key], list), key + " 必须为数组")
    for evidence in value["evidence_index"]:
        fields(evidence, "id source locator excerpt", "evidence")
        for key, item in evidence.items():
            text(item, "evidence." + key)
    ids = [e["id"] for e in value["evidence_index"]]
    unique(ids, "evidence id")

    def refs(item, where, required=False):
        texts(item["evidence_refs"], where + ".evidence_refs")
        unique(item["evidence_refs"], where + ".evidence_refs")
        require(all(ref in ids for ref in item["evidence_refs"]), where + " evidence_refs 引用不存在")
        require(not required or bool(item["evidence_refs"]), where + " 必须至少关联一条证据")

    for check in value["checks"]:
        fields(check, "id requirement expected observed status evidence_refs", "check")
        for key in ("id", "requirement", "expected", "observed"):
            text(check[key], "check." + key)
        enum(check["status"], "pass fail unknown", "check.status")
        refs(check, "check", check["status"] != "unknown")
    unique([c["id"] for c in value["checks"]], "check id")
    for step in value["timeline"]:
        fields(step, "step action result duration_ms evidence_refs", "timeline")
        number(step["step"], "timeline.step", integer=True, nullable=False)
        require(step["step"] >= 1, "timeline.step 从 1 开始")
        for key in ("action", "result"):
            text(step[key], "timeline." + key)
        number(step["duration_ms"], "duration_ms")
        refs(step, "timeline")
    unique([t["step"] for t in value["timeline"]], "timeline.step")
    for finding in value["findings"]:
        fields(finding, "id severity category layer claim expected observed impact confidence evidence_refs suggestion regression", "finding")
        for key in ("id", "category", "layer", "claim", "expected", "observed", "impact", "suggestion", "regression"):
            text(finding[key], "finding." + key)
        enum(finding["severity"], "P0 P1 P2", "severity")
        enum(finding["confidence"], "confirmed hypothesis", "confidence")
        refs(finding, "finding", finding["confidence"] == "confirmed")
    unique([f["id"] for f in value["findings"]], "finding id")
    for struggle in value["struggles"]:
        fields(struggle, "pattern description evidence_refs avoidable optimization", "struggle")
        for key in ("pattern", "description", "optimization"):
            text(struggle[key], "struggle." + key)
        require(type(struggle["avoidable"]) is bool, "avoidable 必须是 boolean")
        refs(struggle, "struggle", True)
    fields(value["metrics"], " ".join(METRICS), "metrics")
    for key in METRICS:
        number(value["metrics"][key], "metrics." + key)
    texts(value["limitations"], "limitations")
    if "fail" in (value["verdict"]["design"], value["verdict"]["execution"]):
        require(any(item["evidence_refs"] for group in ("checks", "timeline", "findings") for item in value[group]), "verdict fail 必须关联检查、轨迹或偏差证据")
    return value


def validate_synthesis(value, manifest):
    fields(value, "summary recommendations", "synthesis")
    text(value["summary"], "synthesis.summary")
    require(isinstance(value["recommendations"], list), "recommendations 必须是数组")
    for rec in value["recommendations"]:
        fields(rec, "id priority title layer problem change case_keys validation", "recommendation")
        for key in ("id", "title", "layer", "problem", "change", "validation"):
            text(rec[key], "recommendation." + key)
        enum(rec["priority"], "P0 P1 P2", "priority")
        texts(rec["case_keys"], "recommendation.case_keys")
        unique(rec["case_keys"], "recommendation.case_keys")
        require(bool(rec["case_keys"]), "建议必须引用至少一个 case")
        for key in rec["case_keys"]:
            member(manifest, key)
    unique([r["id"] for r in value["recommendations"]], "recommendation id")
    return value


def assess(manifest, report, page_missing=False):
    reasons = []
    if report is None:
        return "insufficient", ["缺少 case JSON 与可核验报告"]
    case = member(manifest, report["case_key"])
    if report["identity"] != manifest["target"]:
        reasons.append("身份/版本不匹配，不能计顺畅通过")
    for key in ("baseline", "trace"):
        if report["coverage"][key] != "complete":
            reasons.append(key + " 覆盖不完整")
    checks = {c["id"]: c for c in report["checks"]}
    if not case["required_check_ids"]:
        reasons.append("未定义 required_check_ids，必需检查集合不足")
    for key in case["required_check_ids"]:
        if key not in checks or checks[key]["status"] == "unknown":
            reasons.append("必需检查缺失或 unknown: " + key)
    if any(c["status"] == "unknown" for c in report["checks"]):
        reasons.append("存在未判定检查")
    if "unknown" in report["verdict"].values():
        reasons.append("三轴判定存在 unknown")
    if not any(step["evidence_refs"] for step in report["timeline"]):
        reasons.append("缺少支撑顺畅度的轨迹证据")
    if page_missing:
        reasons.append("缺少已渲染 case HTML；已生成证据不足占位页")
    hard_fail = ("fail" in (report["verdict"]["design"], report["verdict"]["execution"])
                 or any(c["status"] == "fail" for c in report["checks"])
                 or any(f["confidence"] == "confirmed" for f in report["findings"]))
    if hard_fail:
        return "fail", ["存在有证据的设计/执行失败、失败检查或已确认实质偏差"] + reasons
    if reasons:
        return "insufficient", reasons
    issues = (report["struggles"] or
              any((report["metrics"][key] or 0) > 0 for key in ("tool_failures", "business_errors", "retries")))
    if report["verdict"]["smoothness"] != "smooth" or issues:
        return "recovered", ["设计与执行通过，但恢复、挣扎或问题记录使其不计顺畅通过"]
    return "pass", ["设计与执行通过；必需检查和轨迹完整；没有已记录问题"]


def esc(value):
    return html.escape(str(value), quote=True)


def fmt(value):
    return "未提供" if value is None else str(value)


def paragraph(value):
    return "<p>" + esc(value) + "</p>"


def listing(values):
    return "<ul>" + "".join("<li>" + esc(v) + "</li>" for v in values) + "</ul>" if values else paragraph("无")


def table(headers, rows, attrs=""):
    return ('<div class="table-scroll"><table' + attrs + '><thead><tr>' +
            "".join("<th scope=\"col\">" + esc(h) + "</th>" for h in headers) +
            "</tr></thead><tbody>" + "".join("<tr>" + "".join("<td>" + cell + "</td>" for cell in row) + "</tr>" for row in rows) + "</tbody></table></div>")


def render_template(name, slots):
    template = (TEMPLATES / name).read_text(encoding="utf-8")
    found = SLOT.findall(template)
    require(len(found) == len(set(found)), "模板 slot 重复")
    require(set(found) == set(slots), "模板 slot 缺失或多余")
    require("{{" not in SLOT.sub("", template) and "}}" not in SLOT.sub("", template), "模板 slot 语法错误")
    # 一次替换，不递归解释来自数据的 {{...}}。
    return SLOT.sub(lambda match: slots[match.group(1)], template)


def page(name, slots):
    slots = dict(slots, STYLE=(TEMPLATES / "style.css").read_text(encoding="utf-8"), SCRIPT=(TEMPLATES / "controls.js").read_text(encoding="utf-8"))
    result = render_template(name, slots)
    require(len(result.encode("utf-8")) <= 16 * 1024 * 1024, "报告超过 16MB")
    return result


def metadata(manifest):
    target = manifest["target"]
    rows = [(esc("生成时间"), esc(manifest["generated_at"])), (esc("目标 motif / agent / version"), esc(" / ".join(target.values())))]
    task = ('<a href="' + esc(manifest["task_url"]) + '" target="_blank" rel="noopener noreferrer">评测任务</a>') if manifest["task_url"] else "未提供"
    rows.append(("任务入口", task))
    rows.append(("样本清单范围", esc(manifest["scope_note"])))
    scope = "完整清单；统计分母为 manifest 中全部样本。" if manifest["scope_complete"] else "部分清单，仅对已取得样本统计；不代表全任务覆盖。"
    return '<p class="notice">' + esc(scope) + '</p>' + table(["元信息", "值"], rows)


def render_case(manifest, report):
    status, reasons = assess(manifest, report)
    refs = lambda item: esc("、".join(item["evidence_refs"]) or "无")
    ident = table(["身份字段", "目标", "实际运行"], [(esc(k), esc(manifest["target"][k]), esc(v or "未知")) for k, v in report["identity"].items()])
    verdict = table(["判定轴", "输入判定"], [(esc(k), esc(v)) for k, v in report["verdict"].items()])
    ident += verdict + paragraph("脚本重算：" + LABELS[status]) + listing(reasons)
    context = "".join("<h3>" + label + "</h3>" + paragraph(report["context"][key] or "未提供") for key, label in (("input_summary", "输入概要"), ("output_summary", "输出概要"), ("annotations_summary", "评测标注（仅背景，不直接参与判断）")))
    checks = table(["检查 / 要求", "预期", "实际", "状态", "证据"], [(esc(c["id"] + " / " + c["requirement"]), esc(c["expected"]), esc(c["observed"]), esc(c["status"]), refs(c)) for c in report["checks"]])
    timeline = table(["步骤", "调用 / 动作", "结果", "耗时 ms", "证据"], [(esc(t["step"]), esc(t["action"]), esc(t["result"]), esc(fmt(t["duration_ms"])), refs(t)) for t in sorted(report["timeline"], key=lambda t: t["step"])])
    findings = table(["偏差 / 级别", "分类 / 层级", "判断", "预期 / 实际", "影响", "置信度", "证据"], [(esc(f["id"] + " / " + f["severity"]), esc(f["category"] + " / " + f["layer"]), esc(f["claim"]), esc(f["expected"] + " / " + f["observed"]), esc(f["impact"]), esc(f["confidence"]), refs(f)) for f in report["findings"]]) if report["findings"] else paragraph("无已记录偏差")
    struggles = table(["模式", "过程", "可避免", "优化", "证据"], [(esc(s["pattern"]), esc(s["description"]), "是" if s["avoidable"] else "否", esc(s["optimization"]), refs(s)) for s in report["struggles"]]) if report["struggles"] else paragraph("无已记录挣扎")
    advice = table(["偏差", "修改建议", "回归验证"], [(esc(f["id"]), esc(f["suggestion"]), esc(f["regression"])) for f in sorted(report["findings"], key=lambda f: (f["severity"], f["id"]))]) if report["findings"] else paragraph("无偏差修复建议")
    metrics = table(["资源指标", "值"], [(esc(key), esc(fmt(report["metrics"][key]))) for key in METRICS])
    evidence = table(["证据 ID", "来源", "脱敏定位", "短引用"], [(esc(e["id"]), esc(e["source"]), esc(e["locator"]), "<pre>" + esc(e["excerpt"]) + "</pre>") for e in report["evidence_index"]])
    coverage = paragraph("覆盖：baseline=" + report["coverage"]["baseline"] + "；trace=" + report["coverage"]["trace"] + "；thinking=" + report["coverage"]["thinking"] + "。思考缺失本身不阻断通过，顺畅度必须有调用轨迹支持。")
    return page("case.html", dict(TITLE=esc(report["title"]), KEY=esc(report["case_key"]), SUMMARY=paragraph(report["summary"]), IDENTITY=ident, CONTEXT=context, CHECKS=checks, TIMELINE=timeline, FINDINGS=findings, STRUGGLES=struggles, ADVICE=advice, METRICS=metrics, EVIDENCE=evidence, LIMITATIONS=coverage + listing(report["limitations"])))


def placeholder(manifest, case, reasons, report=None):
    missing = {"schema_version": 1, "case_key": case["case_key"], "title": case["title"], "summary": "证据不足占位页；不是完整 case 评测报告。", "identity": manifest["target"], "context": {"input_summary": "", "output_summary": "", "annotations_summary": ""}, "verdict": {"design": "unknown", "execution": "unknown", "smoothness": "unknown"}, "coverage": {"baseline": "missing", "trace": "missing", "thinking": "missing"}, "checks": [], "timeline": [], "findings": [], "struggles": [], "metrics": {k: None for k in METRICS}, "limitations": list(reasons), "evidence_index": []}
    missing["identity"] = {key: "" for key in manifest["target"]}
    if report is not None:
        missing["identity"] = report["identity"]
        missing["limitations"].append("已有 JSON 的脚本重算结果：" + LABELS[assess(manifest, report)[0]] + "；原 case 页面缺失，须重新执行 case 命令核验。")
    return render_case(manifest, missing)


def ratio(count, total):
    return "N/A" if not total else "{:.1f}%".format(100 * count / total)


def render_summary(manifest, reports, synthesis, missing_pages):
    rows, gaps = [], []
    counts = Counter({key: 0 for key in LABELS})
    coverage = Counter()
    axes = {axis: Counter() for axis in ("design", "execution")}
    for case in sorted(manifest["cases"], key=lambda c: c["ordinal"]):
        key = case["case_key"]
        report = reports.get(key)
        status, reasons = assess(manifest, report, key in missing_pages)
        counts[status] += 1
        for axis in axes:
            axis_status = "unknown"
            if report:
                relevant = [c for c in report["checks"] if c["id"] == axis]
                if report["verdict"][axis] == "fail" or any(c["status"] == "fail" for c in relevant):
                    axis_status = "fail"
                elif status in ("pass", "recovered") and report["verdict"][axis] == "pass":
                    axis_status = "pass"
            axes[axis][axis_status] += 1
        if status != "insufficient":
            coverage["analysis"] += 1
        if report:
            for field, complete in (("baseline", "complete"), ("trace", "complete"), ("thinking", "available")):
                coverage[field] += report["coverage"][field] == complete
            coverage["reports"] += 1
        if key not in missing_pages and report:
            coverage["pages"] += 1
        link = '<a href="cases/' + key + '.html">' + esc(case["title"]) + '</a>'
        rows.append((esc(case["ordinal"]), link + '<br><code>' + key + '</code>', '<span class="status">' + LABELS[status] + '</span>', esc("；".join(reasons)), esc(fmt(report["metrics"]["business_errors"]) if report else "未提供")))
        for reason in reasons:
            if status == "insufficient" or "不匹配" in reason or "缺少" in reason or "不完整" in reason:
                gaps.append(key + "：" + reason)
        if report:
            if report["coverage"]["thinking"] != "available":
                gaps.append(key + "：思考覆盖 " + report["coverage"]["thinking"] + "（不单独阻断通过）")
            gaps.extend(key + "：" + limitation for limitation in report["limitations"])
    n = len(manifest["cases"])
    stats = '<div class="stats">' + "".join('<div class="stat"><span>' + label + '</span><strong>' + str(value) + '</strong></div>' for label, value in (("全部样本 N", n), ("顺畅通过 P", counts["pass"]), ("失败", counts["fail"]), ("恢复/挣扎", counts["recovered"]), ("证据不足", counts["insufficient"]))) + '</div>'
    stats += table(["覆盖率（完整 / 全部样本）", "分子 / 分母", "比例"], [(label, str(coverage[key]) + " / " + str(n), ratio(coverage[key], n)) for key, label in (("analysis", "已可判定分析（非证据不足）"), ("reports", "JSON 报告"), ("pages", "原始 case 页面（不含占位）"), ("baseline", "设计基线"), ("trace", "调用轨迹"), ("thinking", "思考证据"))])
    stats += table(["保守三轴指标", "通过 / 全部", "率", "失败", "unknown"], [(label, str(axes[axis]["pass"]) + " / " + str(n), ratio(axes[axis]["pass"], n), str(axes[axis]["fail"]), str(axes[axis]["unknown"])) for axis, label in (("design", "保守确认设计遵循率"), ("execution", "保守确认任务完成率"))])
    stats += paragraph("三轴率采用保守口径：对应轴或同名检查明确 fail 则记失败；只有整体顺畅通过或恢复/挣扎且该轴 pass 才计通过；其余均保留 unknown，不以另一轴声明冲淡未定位偏差。分析覆盖指可判定为通过、失败或恢复/挣扎的 case 比例，不等于全部证据覆盖。")
    recommendations = []
    for rec in sorted(synthesis["recommendations"], key=lambda r: (r["priority"], r["id"])):
        links = "、".join('<a href="cases/' + key + '.html">' + esc(member(manifest, key)["title"]) + '</a>' for key in rec["case_keys"])
        recommendations.append('<article><h3>' + esc(rec["priority"] + " · " + rec["title"]) + '</h3>' + paragraph("层级：" + rec["layer"]) + paragraph("问题：" + rec["problem"]) + paragraph("修改：" + rec["change"]) + '<p>影响 ' + str(len(rec["case_keys"])) + ' 个 case：' + links + '</p>' + paragraph("验证：" + rec["validation"]) + '</article>')
    return page("summary.html", dict(TITLE=esc(manifest["title"]), META=metadata(manifest), CONCLUSION=paragraph(synthesis["summary"]), RATE=esc("顺畅通过率=" + str(counts["pass"]) + "/" + str(n) + " · " + ratio(counts["pass"], n)), STATS=stats, RECOMMENDATIONS="".join(recommendations) or paragraph("暂无建议"), CASES=table(["序号", "Case", "重算结论", "判定依据 / 缺口", "业务错误数"], rows, ' id="case-table"'), GAPS=listing(gaps)))


def no_symlink(path):
    require(not any(part == ".." for part in path.parts), "路径不允许包含 ..")
    require(not any(p.is_symlink() for p in (path,) + tuple(path.parents)), "路径不允许符号链接（macOS 临时目录请使用 /private/tmp 或物理绝对路径）: " + str(path))


def load_json(path):
    path = Path(path).absolute()
    no_symlink(path)
    require(path.is_file(), "JSON 文件不存在: " + str(path))

    def pairs(items):
        result = {}
        for key, value in items:
            require(key not in result, "JSON 对象字段重复: " + key)
            result[key] = value
        return result

    def constant(value):
        raise ContractError("JSON 不允许非有限数值: " + value)

    try:
        return json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=pairs, parse_constant=constant)
    except (UnicodeError, ValueError) as exc:
        raise ContractError("不合法 JSON " + str(path) + ": " + str(exc)) from exc


def check_output(path, overwrite):
    no_symlink(path)
    require(not path.exists() or (overwrite and path.is_file()), "输出已存在，需显式 --overwrite: " + str(path))


def save(path, content, overwrite=False):
    check_output(path, overwrite)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w" if overwrite else "x", encoding="utf-8", newline="\n") as stream:
        stream.write(content)


def case_command(args):
    manifest = validate_manifest(load_json(args.manifest))
    report = validate_case(load_json(args.report), manifest)
    out = Path(args.out).absolute()
    require(out.parent.name == "cases" and out.name == report["case_key"] + ".html", "case 输出必须为 OUTPUT/cases/<case_key>.html")
    save(out, render_case(manifest, report), args.overwrite)
    return {"out": str(out), "status": assess(manifest, report)[0]}


def summary_command(args):
    manifest = validate_manifest(load_json(args.manifest))
    synthesis = validate_synthesis(load_json(args.synthesis), manifest)
    out = Path(args.out).absolute()
    require(out.name == "index.html", "summary 输出必须为 OUTPUT/index.html")
    check_output(out, args.overwrite)
    reports_dir = Path(args.reports_dir).absolute()
    no_symlink(reports_dir)
    require(reports_dir.is_dir(), "reports-dir 不存在或不是目录")
    reports = {}
    for path in sorted(reports_dir.iterdir()):
        require(path.suffix == ".json" and path.is_file(), "reports-dir 只允许 case JSON 文件: " + path.name)
        report = validate_case(load_json(path), manifest)
        key = report["case_key"]
        require(key not in reports, "重复 case 报告: " + key)
        reports[key] = report
    placeholders, missing_pages = [], set()
    for case in manifest["cases"]:
        key = case["case_key"]
        path = out.parent / "cases" / (key + ".html")
        no_symlink(path)
        report = reports.get(key)
        missing = not path.is_file()
        if missing or report is None:
            missing_pages.add(key)
            reasons = ["缺少 case JSON" if report is None else "已有 JSON，但缺少独立渲染的 case HTML"]
            if missing:
                reasons.append("缺少 case HTML，汇总时生成占位页并计入全样本分母")
            content = placeholder(manifest, case, reasons, report)
            check_output(path, args.overwrite)
            placeholders.append((path, content))
        else:
            require(path.read_text(encoding="utf-8") == render_case(manifest, report), "case HTML 与当前 JSON/manifest/固定模板不一致，请重新渲染: " + key)
    content = render_summary(manifest, reports, synthesis, missing_pages)
    for path, body in placeholders:
        save(path, body, args.overwrite)
    save(out, content, args.overwrite)
    counts = Counter(assess(manifest, reports.get(c["case_key"]), c["case_key"] in missing_pages)[0] for c in manifest["cases"])
    return {"out": str(out), "total": len(manifest["cases"]), "counts": {key: counts[key] for key in LABELS}, "placeholder_cases": sorted(missing_pages)}


def demo_data():
    target = {"motif_id": "synthetic-motif", "agent_id": "synthetic-agent", "version": "demo-v1"}
    manifest = {"schema_version": 1, "title": "合成匿名演示", "task_url": "", "generated_at": "2026-01-01T00:00:00Z", "scope_complete": True, "scope_note": "仅四个匿名合成样本，非真实任务", "target": target, "cases": [{"case_key": key, "ordinal": i + 1, "title": "合成示例 · " + LABELS[key], "session_id": "synthetic-session-" + key, "message_ids": ["synthetic-message"], "required_check_ids": ["identity", "design", "execution", "smoothness", "result"]} for i, key in enumerate(LABELS)]}
    base = {"schema_version": 1, "case_key": "pass", "title": "合成示例", "summary": "全部数据均为匿名合成，仅展示固定模板，不代表真实评测。", "identity": target, "context": {"input_summary": "合成用户请求返回两项学习建议。", "output_summary": "合成返回包含两项建议。", "annotations_summary": "合成标注仅作为背景。"}, "verdict": {"design": "pass", "execution": "pass", "smoothness": "smooth"}, "coverage": {"baseline": "complete", "trace": "complete", "thinking": "missing"}, "checks": [{"id": "result", "requirement": "返回两项建议", "expected": "2 项", "observed": "2 项", "status": "pass", "evidence_refs": ["e-result"]}], "timeline": [{"step": 1, "action": "合成工具调用", "result": "返回 2 项", "duration_ms": 120, "evidence_refs": ["e-result"]}], "findings": [], "struggles": [], "metrics": {key: None for key in METRICS}, "limitations": ["仅模板演示，未连接任何平台或开展真实评测。"], "evidence_index": [{"id": "e-result", "source": "synthetic-trace", "locator": "合成事件 1", "excerpt": "合成返回：项目 A；项目 B。"}]}
    for key in ("identity", "design", "execution", "smoothness"):
        base["checks"].append({"id": key, "requirement": "合成 " + key + " 检查", "expected": "证据满足检查", "observed": "合成证据满足检查", "status": "pass", "evidence_refs": ["e-result"]})
    reports = {}
    for case in manifest["cases"]:
        report = copy.deepcopy(base)
        report["case_key"] = case["case_key"]
        report["title"] = case["title"]
        reports[case["case_key"]] = report
    reports["recovered"]["verdict"]["smoothness"] = "recovered"
    reports["recovered"]["metrics"]["retries"] = 1
    reports["recovered"]["struggles"] = [{"pattern": "retry", "description": "合成超时后重试成功", "evidence_refs": ["e-result"], "avoidable": True, "optimization": "合成建议：先校验输入"}]
    reports["recovered"]["evidence_index"][0]["excerpt"] = "合成轨迹：首次超时，重试返回两项。"
    reports["fail"]["checks"][0].update(status="fail", observed="仅 1 项")
    reports["fail"]["verdict"]["execution"] = "fail"
    reports["fail"]["evidence_index"][0]["excerpt"] = "合成返回：仅项目 A。"
    reports["fail"]["findings"] = [{"id": "f-result", "severity": "P1", "category": "contract", "layer": "agent", "claim": "合成结果数量不足", "expected": "2 项", "observed": "1 项", "impact": "合成用户需求未满足", "confidence": "confirmed", "evidence_refs": ["e-result"], "suggestion": "返回前检查数量", "regression": "合成输入应返回 2 项"}]
    reports["insufficient"]["coverage"]["trace"] = "partial"
    reports["insufficient"]["verdict"]["execution"] = "unknown"
    reports["insufficient"]["checks"][0].update(status="unknown", evidence_refs=[])
    synthesis = {"summary": "合成演示含顺畅通过、失败、恢复/挣扎、证据不足各一例，顺畅通过率由脚本计算。", "recommendations": [{"id": "rec-1", "priority": "P1", "title": "合成建议：增加输出数量检查", "layer": "agent", "problem": "合成失败 case 数量不足", "change": "返回前检查数量", "case_keys": ["fail", "recovered"], "validation": "合成回归检查返回两项且无需重试"}]}
    return manifest, reports, synthesis


def demo_command(args):
    out = Path(args.out).absolute()
    no_symlink(out)
    require(not out.exists(), "demo 必须使用不存在的新目录")
    manifest, reports, synthesis = demo_data()
    validate_manifest(manifest)
    validate_synthesis(synthesis, manifest)
    for report in reports.values():
        validate_case(report, manifest)
    out.mkdir(parents=True)
    for name, value in (("manifest.json", manifest), ("synthesis.json", synthesis)):
        save(out / name, json.dumps(value, ensure_ascii=False, indent=2) + "\n")
    for key, report in reports.items():
        save(out / "reports" / (key + ".json"), json.dumps(report, ensure_ascii=False, indent=2) + "\n")
        save(out / "cases" / (key + ".html"), render_case(manifest, report))
    return summary_command(argparse.Namespace(manifest=str(out / "manifest.json"), synthesis=str(out / "synthesis.json"), reports_dir=str(out / "reports"), out=str(out / "index.html"), overwrite=False))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("case", "summary"):
        command = commands.add_parser(name)
        command.add_argument("--manifest", required=True)
        command.add_argument("--out", required=True)
        command.add_argument("--overwrite", action="store_true")
        if name == "case":
            command.add_argument("--report", required=True)
        else:
            command.add_argument("--reports-dir", required=True)
            command.add_argument("--synthesis", required=True)
    demo = commands.add_parser("demo")
    demo.add_argument("--out", required=True)
    args = parser.parse_args(argv)
    try:
        result = {"case": case_command, "summary": summary_command, "demo": demo_command}[args.command](args)
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 0
    except (ContractError, OSError, UnicodeError, RecursionError) as exc:
        print("报告生成失败：" + str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
