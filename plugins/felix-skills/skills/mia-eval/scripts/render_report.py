#!/usr/bin/env python3
"""确定性、离线、仅标准库的评测报告渲染器（Python 3.9+）。"""
import argparse
import copy
import html
import importlib.util
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
CHECK_LABELS = {"identity": "执行版本和对象", "design": "设计要求", "tool_usage": "工具使用案例检查", "execution": "任务是否处理完", "completion_gate": "是否提前结束", "smoothness": "是否需要返工"}
CALL_STATUS_LABELS = {"pass": "正确", "fail": "错误", "unknown": "无法判断"}


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
    require(isinstance(value, dict), "报告/manifest 必须是对象")
    require(type(value.get("schema_version")) is int and value["schema_version"] in (1, 2, 3), "schema_version 必须为整数 1、2 或 3")


def agent_flow(manifest):
    return manifest["schema_version"] in (2, 3)


def required_checks(manifest):
    base = {"identity", "design", "execution", "smoothness"}
    return base | {"tool_usage", "completion_gate"} if agent_flow(manifest) else base


def scoped_findings(manifest, report):
    return [f for f in report["findings"] if not agent_flow(manifest) or f["scope"] == "agent_flow"]


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
    schema(value)
    names = "schema_version title task_url generated_at scope_complete scope_note target cases"
    fields(value, names + (" audit_scope" if agent_flow(value) else ""), "manifest")
    if agent_flow(value):
        scope = value["audit_scope"]
        fields(scope, "profile focus agent_purpose completion_criteria exclusions", "audit_scope")
        enum(scope["profile"], "agent_flow", "audit_scope.profile")
        for key in ("focus", "completion_criteria", "exclusions"):
            texts(scope[key], "audit_scope." + key)
            require(bool(scope[key]), "audit_scope." + key + " 不能为空")
        text(scope["agent_purpose"], "audit_scope.agent_purpose")
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
        require(required_checks(value).issubset(case["required_check_ids"]), "required_check_ids 缺少必需检查: " + "/".join(sorted(required_checks(value))))
    unique([c["case_key"] for c in value["cases"]], "manifest case_key")
    unique([c["ordinal"] for c in value["cases"]], "manifest ordinal")
    return value


def member(manifest, key):
    matches = [c for c in manifest["cases"] if c["case_key"] == key]
    require(len(matches) == 1, "报告引用 manifest 以外的 case_key: " + str(key))
    return matches[0]


def validate_case(value, manifest):
    schema(value)
    names = "schema_version case_key title summary identity context verdict coverage checks timeline findings struggles metrics limitations evidence_index"
    fields(value, names + (" tool_usage_review" if value["schema_version"] == 3 else ""), "case")
    require(value["schema_version"] == manifest["schema_version"], "case 与 manifest schema_version 必须一致；历史报告须重新审计而非改标签")
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
            text(item, "evidence." + key, empty=key == "locator")
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
        names = "id severity category layer claim expected observed impact confidence evidence_refs suggestion regression"
        fields(finding, names + (" scope" if agent_flow(manifest) else ""), "finding")
        if agent_flow(manifest):
            enum(finding["scope"], "agent_flow platform_context", "finding.scope")
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
    if value["schema_version"] == 3:
        review = value["tool_usage_review"]
        fields(review, "inventory_complete calls", "tool_usage_review")
        require(type(review["inventory_complete"]) is bool, "tool_usage_review.inventory_complete 必须是 boolean")
        require(isinstance(review["calls"], list), "tool_usage_review.calls 必须是数组")
        for call in review["calls"]:
            fields(call, "message_id tool_call_id tool_name status reason evidence_refs", "tool_usage_review.call")
            for key in ("message_id", "tool_call_id", "tool_name", "reason"):
                text(call[key], "tool_usage_review.call." + key)
            enum(call["status"], "pass fail unknown", "tool_usage_review.call.status")
            refs(call, "tool_usage_review.call", call["status"] != "unknown")
        unique([(call["message_id"], call["tool_call_id"]) for call in review["calls"]], "tool_usage_review 调用 ID (message_id, tool_call_id)")
        total = value["metrics"]["tool_calls"]
        number(total, "metrics.tool_calls", integer=True)
        if total is not None:
            if review["inventory_complete"]:
                require(total == len(review["calls"]), "metrics.tool_calls 与完整 calls 数量必须一致")
            else:
                require(total >= len(review["calls"]), "metrics.tool_calls 不能小于已知 calls 数量下限")
    texts(value["limitations"], "limitations")
    if "fail" in (value["verdict"]["design"], value["verdict"]["execution"]):
        require(any(item["evidence_refs"] for group in ("checks", "timeline", "findings") for item in value[group]), "verdict fail 必须关联检查、轨迹或偏差证据")
    return value


def validate_synthesis(value, manifest):
    fields(value, "summary issues", "synthesis")
    text(value["summary"], "synthesis.summary")
    require(isinstance(value["issues"], list), "issues 必须是数组")
    for issue in value["issues"]:
        fields(issue, "id priority title layer problem examples", "issue")
        for key in ("id", "title", "layer", "problem"):
            text(issue[key], "issue." + key)
        enum(issue["priority"], "P0 P1 P2", "issue.priority")
        require(isinstance(issue["examples"], list) and bool(issue["examples"]), "问题必须引用至少一个具体 case")
        for example in issue["examples"]:
            fields(example, "case_key description", "issue.example")
            for key in ("case_key", "description"):
                text(example[key], "issue.example." + key)
            member(manifest, example["case_key"])
        unique([example["case_key"] for example in issue["examples"]], "issue example case_key")
    unique([issue["id"] for issue in value["issues"]], "issue id")
    return value


def assess(manifest, report, page_missing=False):
    reasons = []
    if report is None:
        return "insufficient", ["缺少 case JSON 与可核验报告"]
    case = member(manifest, report["case_key"])
    if report["identity"] != manifest["target"]:
        reasons.append("身份/版本不匹配，不能计顺畅通过")
    for key, label in (("baseline", "Agent 设计内容"), ("trace", "实际调用记录")):
        if report["coverage"][key] != "complete":
            reasons.append(label + "不完整")
    checks = {c["id"]: c for c in report["checks"]}
    if not case["required_check_ids"]:
        reasons.append("尚未列出这个 case 必须检查的内容")
    for key in case["required_check_ids"]:
        if key not in checks or checks[key]["status"] == "unknown":
            label = checks[key]["requirement"] if key in checks else CHECK_LABELS.get(key, "一项必要要求")
            reasons.append("还无法确认：" + label)
    if any(c["status"] == "unknown" for c in report["checks"]):
        reasons.append("有检查项尚未得出结论")
    if "unknown" in report["verdict"].values():
        reasons.append("是否按设计执行、任务是否做完或是否需要返工，仍有一项无法判断")
    if not any(step["evidence_refs"] for step in report["timeline"]):
        reasons.append("缺少实际调用记录，无法判断过程是否顺利")
    if page_missing:
        reasons.append("缺少已渲染 case HTML；已生成证据不足占位页")
    hard_fail = ("fail" in (report["verdict"]["design"], report["verdict"]["execution"])
                 or any(c["status"] == "fail" for c in report["checks"])
                 or any(f["confidence"] == "confirmed" for f in scoped_findings(manifest, report)))
    if hard_fail:
        return "fail", ["实际记录显示，至少有一项设计要求没有满足"] + reasons
    if reasons:
        return "insufficient", reasons
    # v2错误计数仅是事实：首次不可用后正确受阻并继续不自动算挣扎。
    issues = report["struggles"] or (report["metrics"]["retries"] or 0) > 0 or (not agent_flow(manifest) and
              any((report["metrics"][key] or 0) > 0 for key in ("tool_failures", "business_errors")))
    if report["verdict"]["smoothness"] != "smooth" or issues:
        return "recovered", ["最终按设计要求处理完了任务，但过程中有重试、返工或其他已记录问题"]
    return "pass", ["按设计要求处理完了任务；必要记录完整，没有发现本次范围内的问题"]


def esc(value):
    return html.escape(str(value), quote=True)


def fmt(value):
    return "未提供" if value is None else str(value)


def paragraph(value):
    return "<p>" + esc(value) + "</p>"


def listing(values):
    return "<ul>" + "".join("<li>" + esc(v) + "</li>" for v in values) + "</ul>" if values else paragraph("无")


def table_row(cells):
    return "<tr>" + "".join("<td>" + cell + "</td>" for cell in cells) + "</tr>"


def table_markup(headers, body, attrs=""):
    return ('<div class="table-scroll"><table' + attrs + '><thead><tr>' +
            "".join("<th scope=\"col\">" + esc(h) + "</th>" for h in headers) +
            "</tr></thead><tbody>" + body + "</tbody></table></div>")


def table(headers, rows, attrs=""):
    return table_markup(headers, "".join(table_row(row) for row in rows), attrs)


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
    rows.append(("样本清单范围", esc(manifest["scope_note"])))
    if agent_flow(manifest):
        audit = manifest["audit_scope"]
        rows.extend([(esc("审计口径"), esc("Agent流程执行；不评Tool是否Mock")),
                     (esc("Agent主要目的"), esc(audit["agent_purpose"])),
                     (esc("关注点"), listing(audit["focus"])),
                     (esc("流程收尾标准"), listing(audit["completion_criteria"])),
                     (esc("不在判定范围"), listing(audit["exclusions"]))])
    else:
        rows.append(("审计口径", "历史schema v1原口径；未按Agent流程新标准重新审计"))
    scope = "完整清单；统计分母为 manifest 中全部样本。" if manifest["scope_complete"] else "部分清单，仅对已取得样本统计；不代表全任务覆盖。"
    return '<p class="notice">' + esc(scope) + '</p>' + table(["元信息", "值"], rows)


def call_counts(calls):
    counts = Counter(call["status"] for call in calls)
    return dict(total=len(calls), **{key: counts[key] for key in CALL_STATUS_LABELS})


def call_statistics(manifest, reports):
    """总调用和逐工具统计共用一次分组；范围外报告不进入统计。"""
    require(manifest["schema_version"] == 3, "逐调用统计仅适用于 schema v3")
    grouped = {}
    incomplete = 0
    for case in manifest["cases"]:
        report = reports.get(case["case_key"])
        review = report["tool_usage_review"] if report else None
        incomplete += review is None or not review["inventory_complete"]
        if review:
            for call in review["calls"]:
                grouped.setdefault(call["tool_name"], Counter()).update([call["status"]])
    by_tool = [dict(tool_name=name, total=sum(counts.values()),
                    **{key: counts[key] for key in CALL_STATUS_LABELS})
               for name, counts in sorted(grouped.items())]
    totals = {key: sum(item[key] for item in by_tool) for key in ("total",) + tuple(CALL_STATUS_LABELS)}
    return dict(totals, incomplete_cases=incomplete, inventory_complete=incomplete == 0), by_tool


def tool_adherence_stats(manifest, reports):
    """工具遵循率的分母仅为 v3 实际 calls。"""
    return call_statistics(manifest, reports)[0]


def tool_usage_stats(manifest, reports):
    """历史 Python 接口兼容别名，始终统计 calls；新代码使用 tool_adherence_stats。"""
    return tool_adherence_stats(manifest, reports)


def design_adherence_stats(manifest, reports):
    """设计遵循率统计全部 checks；每条 pass/fail/unknown 各计一次。"""
    counts = Counter()
    incomplete = 0
    for case in manifest["cases"]:
        report = reports.get(case["case_key"])
        if report is None:
            incomplete += 1
            continue
        checks = report["checks"]
        counts.update(check["status"] for check in checks)
        present = {check["id"] for check in checks}
        incomplete += not set(case["required_check_ids"]).issubset(present)
    return dict(total=sum(counts.values()), **{key: counts[key] for key in ("pass", "fail", "unknown")},
                incomplete_cases=incomplete, checks_complete=incomplete == 0)


def render_call_stats(counts, complete):
    label = "工具遵循率" if complete else "已知调用正确占比（非全量工具遵循率）"
    content = paragraph(label + "=" + str(counts["pass"]) + "/" + str(counts["total"]) + " · " + ratio(counts["pass"], counts["total"]))
    content += paragraph("计算方式：正确实际调用数 / 全部实际调用数；按调用总数加权，不取案例平均。无法判断的已知调用保留在分母中；没有调用时为 N/A。")
    return content + table(["调用统计", "数量"], [("总调用数" if complete else "已知调用数（总量下限）", str(counts["total"]))] + [(label, str(counts[key])) for key, label in CALL_STATUS_LABELS.items()])


def render_call_review(manifest, report):
    if manifest["schema_version"] != 3:
        return ""
    review = report["tool_usage_review"]
    content = render_call_stats(call_counts(review["calls"]), review["inventory_complete"])
    if not review["inventory_complete"]:
        content = '<p class="notice">调用清单不完整：仅统计已知调用，数量是下限，无法声称全量工具遵循率；未知总量不补写。</p>' + content
    rows = [(str(i), esc(call["tool_name"]), esc(CALL_STATUS_LABELS[call["status"]]), esc(call["reason"])) for i, call in enumerate(review["calls"], 1)]
    content += table(["调用顺序", "工具名", "判定", "原因"], rows) if rows else paragraph("没有已列出的实际调用。" if not review["inventory_complete"] else "本 case 没有实际工具调用。")
    return '<section><h2>逐调用工具使用检查</h2>' + content + '</section>'


def render_case(manifest, report):
    status, reasons = assess(manifest, report)
    ident = table(["身份字段", "目标", "实际运行"], [(esc(k), esc(manifest["target"][k]), esc(v or "未知")) for k, v in report["identity"].items()])
    verdict = table(["判定轴", "输入判定"], [(esc(k), esc(v)) for k, v in report["verdict"].items()])
    scope = manifest["audit_scope"] if agent_flow(manifest) else None
    scope_note = ("审计Agent流程、工具使用和结束时机；不评Tool是否Mock或真实持久化/送达。" if scope else "历史schema v1原口径；未按新标准重审。")
    ident += paragraph(scope_note) + (paragraph("Agent目的：" + scope["agent_purpose"]) if scope else "") + verdict + paragraph("脚本重算：" + LABELS[status]) + listing(reasons)
    context = "".join("<h3>" + label + "</h3>" + paragraph(report["context"][key] or "未提供") for key, label in (("input_summary", "输入概要"), ("output_summary", "输出概要"), ("annotations_summary", "评测标注（仅背景，不直接参与判断）")))
    checks = table(["检查要求", "应该怎么做", "实际做了什么", "状态"], [(esc("工具使用案例检查" if agent_flow(manifest) and c["id"] == "tool_usage" else c["requirement"]), esc(c["expected"]), esc(c["observed"]), esc(c["status"])) for c in report["checks"]])
    timeline = table(["步骤", "调用 / 动作", "结果", "耗时 ms"], [(esc(t["step"]), esc(t["action"]), esc(t["result"]), esc(fmt(t["duration_ms"]))) for t in sorted(report["timeline"], key=lambda t: t["step"])])
    struggles = table(["模式", "过程", "可避免", "优化"], [(esc(s["pattern"]), esc(s["description"]), "是" if s["avoidable"] else "否", esc(s["optimization"])) for s in report["struggles"]]) if report["struggles"] else paragraph("无已记录挣扎")
    actionable = scoped_findings(manifest, report)
    advice = table(["偏差", "修改建议", "回归验证"], [(esc(f["id"]), esc(f["suggestion"]), esc(f["regression"])) for f in sorted(actionable, key=lambda f: (f["severity"], f["id"]))]) if actionable else paragraph("无本次范围内修复建议")
    metrics = table(["资源指标", "值"], [(esc(key), esc(fmt(report["metrics"][key]))) for key in METRICS])
    coverage = paragraph("覆盖：baseline=" + report["coverage"]["baseline"] + "；trace=" + report["coverage"]["trace"] + "；thinking=" + report["coverage"]["thinking"] + "。思考缺失本身不阻断通过，顺畅度必须有调用轨迹支持。")
    return page("case.html", dict(TITLE=esc(report["title"]), KEY=esc(report["case_key"]), SUMMARY=paragraph(report["summary"]), IDENTITY=ident, CONTEXT=context, CHECKS=checks, CALL_REVIEW=render_call_review(manifest, report), TIMELINE=timeline, STRUGGLES=struggles, ADVICE=advice, METRICS=metrics, LIMITATIONS=coverage + listing(report["limitations"])))


def placeholder(manifest, case, reasons, report=None):
    missing = {"schema_version": manifest["schema_version"], "case_key": case["case_key"], "title": case["title"], "summary": "证据不足占位页；不是完整 case 评测报告。", "identity": manifest["target"], "context": {"input_summary": "", "output_summary": "", "annotations_summary": ""}, "verdict": {"design": "unknown", "execution": "unknown", "smoothness": "unknown"}, "coverage": {"baseline": "missing", "trace": "missing", "thinking": "missing"}, "checks": [], "timeline": [], "findings": [], "struggles": [], "metrics": {k: None for k in METRICS}, "limitations": list(reasons), "evidence_index": []}
    if manifest["schema_version"] == 3:
        missing["tool_usage_review"] = {"inventory_complete": False, "calls": []}
    missing["identity"] = {key: "" for key in manifest["target"]}
    if report is not None:
        missing["identity"] = report["identity"]
        missing["limitations"].append("已有 JSON 的脚本重算结果：" + LABELS[assess(manifest, report)[0]] + "；原 case 页面缺失，须重新执行 case 命令核验。")
    return render_case(manifest, missing)


def ratio(count, total):
    return "N/A" if not total else "{:.1f}%".format(100 * count / total)


def audit_statistics(manifest, reports, missing_pages=()):
    """汇总、逐工具下钻及机器输出使用同一份可复算统计。"""
    counts = Counter({key: 0 for key in LABELS})
    labels = (("design", "设计遵循通过案例占比"), ("execution", "流程完成率"),
              ("completion_gate", "结束时机正确率")) if agent_flow(manifest) else (
                  ("design", "设计遵循通过案例占比"), ("execution", "保守确认任务完成率"))
    axes = {axis: Counter() for axis, _ in labels}
    for case in manifest["cases"]:
        key = case["case_key"]
        report = reports.get(key)
        status, _ = assess(manifest, report, key in missing_pages)
        counts[status] += 1
        for axis in axes:
            axis_status = "unknown"
            if report:
                relevant = [check for check in report["checks"] if check["id"] == axis]
                declared = report["verdict"].get(axis)
                if declared == "fail" or any(check["status"] == "fail" for check in relevant):
                    axis_status = "fail"
                elif agent_flow(manifest):
                    known = (report["identity"] == manifest["target"] and
                             all(report["coverage"][key] == "complete" for key in ("baseline", "trace")) and
                             relevant and all(check["status"] == "pass" for check in relevant))
                    if known and (declared == "pass" or axis not in report["verdict"]):
                        axis_status = "pass"
                elif status in ("pass", "recovered") and declared == "pass":
                    axis_status = "pass"
            axes[axis][axis_status] += 1
    total = len(manifest["cases"])

    def with_rate(value):
        return dict(value, rate=ratio(value["pass"], value["total"]))

    design = with_rate(design_adherence_stats(manifest, reports))
    tools, by_tool = None, []
    if manifest["schema_version"] == 3:
        tools, by_tool = call_statistics(manifest, reports)
        tools, by_tool = with_rate(tools), [with_rate(item) for item in by_tool]
    return {
        "statistics_schema_version": 2,
        "total": total, "counts": {key: counts[key] for key in LABELS},
        "placeholder_cases": sorted(missing_pages), "smooth_rate": ratio(counts["pass"], total),
        "design_adherence": design, "tool_adherence": tools, "tool_adherence_by_tool": by_tool,
        "case_axes": {axis: dict({key: axes[axis][key] for key in ("pass", "fail", "unknown")},
                                 total=total, rate=ratio(axes[axis]["pass"], total), label=label)
                      for axis, label in labels},
        "metric_definitions": {
            "design_adherence": "全部选定案例的 checks 中 pass 条数 / 全部 checks 条数",
            "tool_adherence": "全部选定案例的正确实际工具调用数 / 全部实际工具调用数；历史 v1/v2 无 calls 时为 null",
            "case_axes.design": "设计判定通过案例数 / 全部选定案例数，单独保留案例统计，不称设计遵循率",
        },
    }


def metric_cells(label, metric):
    return (label, str(metric["pass"]) + " / " + str(metric["total"]), metric["rate"],
            str(metric["fail"]), str(metric["unknown"]))


def render_metrics(statistics):
    counts, total = statistics["counts"], statistics["total"]
    cards = (("全部案例", total), ("顺畅通过", counts["pass"]), ("不通过", counts["fail"]),
             ("恢复/挣扎", counts["recovered"]), ("证据不足", counts["insufficient"]))
    content = '<div class="stats">' + "".join(
        '<div class="stat"><span>' + label + '</span><strong>' + str(value) + '</strong></div>'
        for label, value in cards) + '</div>'
    design = statistics["design_adherence"]
    label = "设计遵循率" if design["checks_complete"] else "已知检查通过占比（非完整设计遵循率）"
    rows = table_row(metric_cells(esc(label), design))
    rows += "".join(table_row(metric_cells(esc(metric["label"]), metric))
                    for metric in statistics["case_axes"].values())
    tools = statistics["tool_adherence"]
    if tools is None:
        rows += table_row(("工具遵循率（历史格式无逐调用数据）", "未提供", "N/A", "未提供", "未提供"))
    else:
        label = "工具遵循率" if tools["inventory_complete"] else "已知调用正确占比（非全量工具遵循率）"
        button = ('<button id="tool-drilldown-toggle" class="metric-drilldown" type="button" '
                  'aria-expanded="false" aria-controls="tool-drilldown-row">' + esc(label) +
                  '<span id="tool-drilldown-label">展开 ▾</span></button>')
        rows += table_row(metric_cells(button, tools))
        by_tool = statistics["tool_adherence_by_tool"]
        breakdown = table(["工具名", "调用总数" if tools["inventory_complete"] else "已知调用数（总量下限）",
                           "正确", "错误", "无法判断", "使用正确率"],
                          [(esc(item["tool_name"]), str(item["total"]), str(item["pass"]),
                            str(item["fail"]), str(item["unknown"]), item["rate"]) for item in by_tool])
        if not by_tool:
            breakdown = paragraph("没有已列出的实际调用。" if not tools["inventory_complete"] else "选定案例没有实际工具调用。")
        rows += '<tr id="tool-drilldown-row" hidden><td colspan="5">' + breakdown + '</td></tr>'
    return content + table_markup(["分项指标", "通过 / 全部", "率", "失败", "unknown"], rows)


def render_summary(manifest, reports, synthesis, missing_pages, statistics=None):
    statistics = audit_statistics(manifest, reports, missing_pages) if statistics is None else statistics
    meta = metadata(manifest)
    design = statistics["design_adherence"]
    if not design["checks_complete"]:
        meta += paragraph(str(design["incomplete_cases"]) + " 个 case 缺报告或必要检查；只展示已知检查通过占比，不补造缺失条目。")
    tools = statistics["tool_adherence"]
    if tools is not None and not tools["inventory_complete"]:
        meta += paragraph(str(tools["incomplete_cases"]) + " 个 case 的调用清单不完整（含缺报告）；已列调用数是下限，只能声称已知调用正确占比，未知总量不补写。")
    if tools is None:
        meta += paragraph("历史 v1/v2 格式没有逐调用判定；工具遵循率为 N/A，不从 checks 或工具错误计数推造 calls。历史案例轴保留原统计口径，本次只是重新渲染，不是重新审计。")
    task = ('<a href="' + esc(manifest["task_url"]) + '" target="_blank" rel="noopener noreferrer">评测任务</a>') if manifest["task_url"] else "未提供"
    inventory_rows = []
    for case in sorted(manifest["cases"], key=lambda item: item["ordinal"]):
        report = reports.get(case["case_key"])
        review = report["tool_usage_review"] if report and manifest["schema_version"] == 3 else None
        note = "历史格式未提供逐调用清单" if manifest["schema_version"] < 3 else (
            "完整" if review and review["inventory_complete"] else "不完整；仅已知下限")
        inventory_rows.append((esc(case["ordinal"]), '<a href="cases/' + case["case_key"] + '.html">' +
                               esc(case["title"]) + '</a>', str(len(review["calls"])) if review else "未知", note))
    stats = render_metrics(statistics) + table(["序号", "Case", "已列调用数", "调用清单"], inventory_rows)
    issues = []
    for issue in sorted(synthesis["issues"], key=lambda item: (item["priority"], item["id"])):
        examples = []
        for example in sorted(issue["examples"], key=lambda item: member(manifest, item["case_key"])["ordinal"]):
            key = example["case_key"]
            case = member(manifest, key)
            link = '<a href="cases/' + key + '.html">' + esc(str(case["ordinal"]) + " · " + case["title"]) + '</a>'
            examples.append('<li>' + link + paragraph(example["description"]) + '</li>')
        issues.append('<article><h3>' + esc(issue["priority"] + " · " + issue["title"]) + '</h3>' + paragraph("层级：" + issue["layer"]) + paragraph("问题：" + issue["problem"]) + '<p>具体 case（涉及 ' + str(len(examples)) + ' 个 case）：</p><ul>' + "".join(examples) + '</ul></article>')
    return page("summary.html", dict(TITLE=esc(manifest["title"]), TASK_LINK=task, META=meta, STATS=stats,
                                     ISSUES="".join(issues) or paragraph("未发现本次范围内的问题")))


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


def verify_citations(report, packet):
    """只从可信同级脚本加载校验器，下载源由其安全读取与哈希核验。"""
    require(sys.flags.isolated, "引用校验必须使用 python -I")
    source = Path(__file__).resolve().with_name("prepare_audit.py")
    no_symlink(source)
    spec = importlib.util.spec_from_file_location("mia_eval_citation_verifier", source)
    verifier = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(verifier)
    try:
        return verifier.verify_report_citations(report, load_json(packet))
    except verifier.PrepareError as exc:
        raise ContractError("引用校验失败：" + str(exc)) from exc


def case_command(args):
    manifest = validate_manifest(load_json(args.manifest))
    report = validate_case(load_json(args.report), manifest)
    verification = verify_citations(report, args.packet) if getattr(args, "packet", None) else None
    out = Path(args.out).absolute()
    require(out.parent.name == "cases" and out.name == report["case_key"] + ".html", "case 输出必须为 OUTPUT/cases/<case_key>.html")
    save(out, render_case(manifest, report), args.overwrite)
    result = {"out": str(out), "status": assess(manifest, report)[0]}
    if verification is not None:
        result["citation_verification"] = verification
    return result


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
    statistics = audit_statistics(manifest, reports, missing_pages)
    content = render_summary(manifest, reports, synthesis, missing_pages, statistics)
    for path, body in placeholders:
        save(path, body, args.overwrite)
    save(out, content, args.overwrite)
    return dict(statistics, out=str(out))


def demo_data(version=3):
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
    synthesis = {"summary": "四个合成示例中，一个顺利完成，一个漏掉了第二项建议，一个重试后完成，一个没有足够记录判断是否完成。顺畅通过率由脚本计算。", "issues": [{"id": "issue-1", "priority": "P1", "title": "要求返回两项建议，只返回了一项", "layer": "Agent", "problem": "用户需要两项学习建议，Agent 只返回了第一项就结束，第二项没有给出。用户拿到的内容少了一项，任务没有做完。", "examples": [{"case_key": "fail", "description": "这个合成 case 要求返回项目 A 和项目 B 的学习建议，实际只返回项目 A，没有项目 B 的建议。"}]}]}
    require(type(version) is int and version in (1, 2, 3), "demo仅支持schema 1、2 或 3")
    if version in (2, 3):
        manifest["schema_version"] = version
        manifest["audit_scope"] = {"profile": "agent_flow", "focus": ["完整流程", "正确工具使用", "最终结束时机"], "agent_purpose": "处理合成请求中的两项学习建议", "completion_criteria": ["每项建议有结果或规则允许的受阻结论", "最终结束前完成全部独立项"], "exclusions": ["Tool是否Mock", "真实持久化与实际送达"]}
        for case in manifest["cases"]:
            case["required_check_ids"].extend(["tool_usage", "completion_gate"])
        for report in reports.values():
            report["schema_version"] = version
            for key in ("tool_usage", "completion_gate"):
                report["checks"].append({"id": key, "requirement": "合成 " + key + " 检查", "expected": "工具与流程结束符合合成规则", "observed": "合成轨迹支持检查", "status": "pass", "evidence_refs": ["e-result"]})
            for finding in report["findings"]:
                finding["scope"] = "agent_flow"
    if version == 3:
        for report in reports.values():
            for check in report["checks"]:
                check["requirement"] = CHECK_LABELS.get(check["id"], check["requirement"])
            report["tool_usage_review"] = {"inventory_complete": True, "calls": [{"message_id": "synthetic-message", "tool_call_id": "synthetic-call-1", "tool_name": "synthetic-tool", "status": "pass", "reason": "请求符合合成工具约定，正确处理回包并完成必要后续。", "evidence_refs": ["e-result"]}]}
            report["metrics"]["tool_calls"] = 1
        retry = reports["recovered"]
        retry["tool_usage_review"]["calls"][0].update(status="fail", reason="首次调用参数不符合约定，工具拒绝请求；修正后成功不抹去本次错误。")
        retry["tool_usage_review"]["calls"].append({"message_id": "synthetic-message", "tool_call_id": "synthetic-call-2", "tool_name": "synthetic-tool", "status": "pass", "reason": "修正参数后重新调用，取得结果并正确处理。", "evidence_refs": ["e-result"]})
        retry["metrics"]["tool_calls"] = 2
        retry["struggles"][0]["description"] = "首次参数错误被拒绝，修正参数后重试成功。"
        retry["evidence_index"][0]["excerpt"] = "合成轨迹：首次参数错误被拒绝；修正后重新调用，返回两项。"
        reports["insufficient"]["tool_usage_review"]["inventory_complete"] = False
        reports["insufficient"]["tool_usage_review"]["calls"][0].update(status="unknown", reason="请求记录可见，但没有取得回包与后续处理记录。", evidence_refs=[])
        reports["insufficient"]["metrics"]["tool_calls"] = None
    return manifest, reports, synthesis


def axis_examples(version=3):
    """匿名交叉判定示例；不改变既有demo四类样本与统计。"""
    manifest, reports, _ = demo_data(version=version)
    report = copy.deepcopy(reports["pass"])
    report["summary"] = "查重失败后仍新增内容，但生成和通知均有结果，没有其他事项遗漏。"
    report["verdict"]["design"] = "fail"
    for check in report["checks"]:
        if check["id"] in ("design", "tool_usage"):
            check.update(status="fail", expected="新增前取得必要查重依据", observed="查重失败后仍新增并通知")
    report["findings"] = [dict(reports["fail"]["findings"][0], id="f-admission", claim="新增前未取得必要查重依据", expected="查重结果允许后新增", observed="查重失败仍新增", impact="可能重复提供内容", suggestion="保留缺口并停止依赖动作", regression="缺必要依据时不新增")]
    report["evidence_index"][0]["excerpt"] = "合成事件：查重失败；随后生成saved、通知success；没有其他事项。"
    report["timeline"][0].update(action="合成生成及通知", result="saved及success，所有已选事项收尾")
    admission = copy.deepcopy(report)
    unfinished = copy.deepcopy(report)
    unfinished["summary"] = "查重失败仍新增第一项，第二项尚未处理就结束。"
    unfinished["verdict"]["execution"] = "fail"
    for check in unfinished["checks"]:
        if check["id"] in ("execution", "completion_gate"):
            check.update(status="fail", expected="处理完全部独立项再结束", observed="第二项尚未处理就最终结束")
    unfinished["evidence_index"][0]["excerpt"] = "合成事件：第一项通知完成；第二项可执行但未处理；最终结束。"
    prefetch = copy.deepcopy(reports["pass"])
    prefetch["summary"] = "先预读通知规则，实际发送等待生成成功后才执行。"
    prefetch["timeline"][0].update(action="合成规则预读、生成及通知", result="预读无业务动作；生成saved后通知success")
    prefetch["evidence_index"][0]["excerpt"] = "合成顺序：skill_read；生成saved；push成功；最终结束。"
    if version == 3:
        for item in (admission, unfinished):
            item["tool_usage_review"] = {"inventory_complete": True, "calls": [
                {"message_id": "synthetic-message", "tool_call_id": "synthetic-query", "tool_name": "history_query", "status": "pass", "reason": "查重请求正确，服务不可用本身不是调用错误。", "evidence_refs": ["e-result"]},
                {"message_id": "synthetic-message", "tool_call_id": "synthetic-generate", "tool_name": "generate", "status": "fail", "reason": "未取得必要查重依据就首次生成新增内容。", "evidence_refs": ["e-result"]},
                {"message_id": "synthetic-message", "tool_call_id": "synthetic-push", "tool_name": "push", "status": "pass", "reason": "通知请求及回包处理正确；此前生成错误不连带改变本次调用判定。", "evidence_refs": ["e-result"]},
            ]}
            item["metrics"]["tool_calls"] = 3
        prefetch["tool_usage_review"] = {"inventory_complete": True, "calls": [
            {"message_id": "synthetic-message", "tool_call_id": "synthetic-read", "tool_name": "skill_read", "status": "pass", "reason": "预读必要规则，没有提前执行业务动作。", "evidence_refs": ["e-result"]},
            {"message_id": "synthetic-message", "tool_call_id": "synthetic-generate", "tool_name": "generate", "status": "pass", "reason": "前置条件满足后生成内容并取得成功结果。", "evidence_refs": ["e-result"]},
            {"message_id": "synthetic-message", "tool_call_id": "synthetic-push", "tool_name": "push", "status": "pass", "reason": "生成成功后发送通知，正确处理回包。", "evidence_refs": ["e-result"]},
        ]}
        prefetch["metrics"]["tool_calls"] = 3
    care = copy.deepcopy(reports["pass"])
    care["summary"] = "正常任务通知回应了有据近况并简短鼓励；思考中的 Message 标签不证明新增独立主动事项。"
    care["timeline"][0].update(action="介绍原任务并回应有据近况", result="只发送原任务的通知，没有额外生成或新增独立帮助")
    care["evidence_index"][0]["excerpt"] = "合成通知参数：原任务介绍与有据短关怀；没有独立额外目的。思考写过选择Message，但未新增Message。"
    proactive = copy.deepcopy(admission)
    proactive["summary"] = "实际生成参数新增了原任务外的独立回顾；没有取得该新增事项所需查重依据。"
    proactive["timeline"][0].update(action="额外回顾生成和通知", result="额外帮助确已生成并通知；其准入依据仍缺失，正常任务已完成")
    proactive["evidence_index"][0]["excerpt"] = "合成实际参数：新增独立回顾目的和内容；generate后push成功；生成前未取得必要查重结果。"
    query = copy.deepcopy(unfinished)
    query["summary"] = "必要查询返回非空 next_cursor 后未续页就结束；已知生产和通知成功不能证明整个流程收尾。"
    query["timeline"][0].update(action="查询首分页、生产和通知", result="首分页 next_cursor 非空；已知内容 saved/push success；未继续查询便最终结束")
    query["evidence_index"][0]["excerpt"] = "合成轨迹：query返回非空next_cursor；generate saved；push success；随后最终结束，没有同范围完整替代查询。"
    for check in query["checks"]:
        if check["id"] in ("design", "tool_usage", "execution", "completion_gate"):
            check.update(status="fail", expected="必要查询完成或有同范围完整替代后收尾",
                         observed="首分页仍有可执行续页且无完整替代，已知内容交付后直接结束")
    query["findings"][0].update(id="f-query", claim="必要查询尚未完成就结束", expected="完成必要续页",
                                 observed="next_cursor非空但无续查或完整替代", impact="查询流程未收尾；不能推断隐藏页必有某个具体遗漏对象")
    excluded = copy.deepcopy(reports["pass"])
    excluded["summary"] = "本次排除领域编写细则评分；类型选择、必要指南加载和原素材范围仍逐项核对且符合要求。"
    excluded["timeline"][0].update(action="加载入口指南并传入原要求和素材", result="类型、指南和素材范围均满足要求；不对排除的逐题教学细则扣分")
    excluded["evidence_index"][0]["excerpt"] = "合成范围补充排除领域编写细则。实际参数保留原素材和要求；类型及必要指南加载均正确。"
    if version == 3:
        care["tool_usage_review"] = {"inventory_complete": True, "calls": [
            {"message_id": "synthetic-message", "tool_call_id": "synthetic-push", "tool_name": "push", "status": "pass",
             "reason": "通知只介绍原任务并含版本允许的有据短关怀，没有新增独立主动事项。", "evidence_refs": ["e-result"]}]}
        query["tool_usage_review"] = {"inventory_complete": True, "calls": [
            {"message_id": "synthetic-message", "tool_call_id": "synthetic-query", "tool_name": "query", "status": "fail",
             "reason": "必要查询返回非空 next_cursor，仍可续页且无完整替代，却未按回包完成必要查询范围；遗漏续页不补造另一条调用。", "evidence_refs": ["e-result"]},
            {"message_id": "synthetic-message", "tool_call_id": "synthetic-generate", "tool_name": "generate", "status": "pass",
             "reason": "已知任务的生产参数、前提和回包处理正确；独立查询遗漏不抹掉生产成功。", "evidence_refs": ["e-result"]},
            {"message_id": "synthetic-message", "tool_call_id": "synthetic-push", "tool_name": "push", "status": "pass",
             "reason": "已知生产成功后正确通知；独立查询未完成不连带改变此调用。", "evidence_refs": ["e-result"]}]}
        query["metrics"]["tool_calls"] = 3
        excluded["tool_usage_review"]["calls"][0]["reason"] = "类型、必要指南和素材参数正确；本次明确排除的领域细则不参与该调用扣分。"
    samples = (("admission-fails-but-closed", admission), ("unfinished-item", unfinished), ("rule-prefetch", prefetch),
               ("normal-task-care", care), ("independent-proactive-item", proactive),
               ("unfinished-required-query", query), ("excluded-domain-detail", excluded))
    examples = []
    for key, item in samples:
        validate_case(item, manifest)
        examples.append({"id": key, "description": item["summary"], "verdict": item["verdict"],
                         "check_statuses": {check["id"]: check["status"] for check in item["checks"]},
                         "classification": assess(manifest, item)[0]})
        if key == "excluded-domain-detail":
            examples[-1]["audit_exclusions"] = ["仅本合成审计排除领域编写细则；类型、指南加载和素材范围仍在本次检查范围"]
        if version == 3:
            examples[-1]["tool_usage_review"] = item["tool_usage_review"]
    return examples


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
            command.add_argument("--packet", help="本例 packet/index.json；解析已注册证据定位与源哈希，未注册来源明示 unchecked")
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
