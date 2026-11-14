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
CHECK_LABELS = {"identity": "执行版本和对象", "design": "设计要求", "tool_usage": "工具是否用对", "execution": "任务是否处理完", "completion_gate": "是否提前结束", "smoothness": "是否需要返工"}


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
    require(type(value.get("schema_version")) is int and value["schema_version"] in (1, 2), "schema_version 必须为整数 1 或 2")


def agent_flow(manifest):
    return manifest["schema_version"] == 2


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
    fields(value, "schema_version case_key title summary identity context verdict coverage checks timeline findings struggles metrics limitations evidence_index", "case")
    schema(value)
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


def render_case(manifest, report):
    status, reasons = assess(manifest, report)
    ident = table(["身份字段", "目标", "实际运行"], [(esc(k), esc(manifest["target"][k]), esc(v or "未知")) for k, v in report["identity"].items()])
    verdict = table(["判定轴", "输入判定"], [(esc(k), esc(v)) for k, v in report["verdict"].items()])
    scope = manifest["audit_scope"] if agent_flow(manifest) else None
    scope_note = ("审计Agent流程、工具使用和结束时机；不评Tool是否Mock或真实持久化/送达。" if scope else "历史schema v1原口径；未按新标准重审。")
    ident += paragraph(scope_note) + (paragraph("Agent目的：" + scope["agent_purpose"]) if scope else "") + verdict + paragraph("脚本重算：" + LABELS[status]) + listing(reasons)
    context = "".join("<h3>" + label + "</h3>" + paragraph(report["context"][key] or "未提供") for key, label in (("input_summary", "输入概要"), ("output_summary", "输出概要"), ("annotations_summary", "评测标注（仅背景，不直接参与判断）")))
    checks = table(["检查要求", "应该怎么做", "实际做了什么", "状态"], [(esc(c["requirement"]), esc(c["expected"]), esc(c["observed"]), esc(c["status"])) for c in report["checks"]])
    timeline = table(["步骤", "调用 / 动作", "结果", "耗时 ms"], [(esc(t["step"]), esc(t["action"]), esc(t["result"]), esc(fmt(t["duration_ms"]))) for t in sorted(report["timeline"], key=lambda t: t["step"])])
    struggles = table(["模式", "过程", "可避免", "优化"], [(esc(s["pattern"]), esc(s["description"]), "是" if s["avoidable"] else "否", esc(s["optimization"])) for s in report["struggles"]]) if report["struggles"] else paragraph("无已记录挣扎")
    actionable = scoped_findings(manifest, report)
    advice = table(["偏差", "修改建议", "回归验证"], [(esc(f["id"]), esc(f["suggestion"]), esc(f["regression"])) for f in sorted(actionable, key=lambda f: (f["severity"], f["id"]))]) if actionable else paragraph("无本次范围内修复建议")
    metrics = table(["资源指标", "值"], [(esc(key), esc(fmt(report["metrics"][key]))) for key in METRICS])
    coverage = paragraph("覆盖：baseline=" + report["coverage"]["baseline"] + "；trace=" + report["coverage"]["trace"] + "；thinking=" + report["coverage"]["thinking"] + "。思考缺失本身不阻断通过，顺畅度必须有调用轨迹支持。")
    return page("case.html", dict(TITLE=esc(report["title"]), KEY=esc(report["case_key"]), SUMMARY=paragraph(report["summary"]), IDENTITY=ident, CONTEXT=context, CHECKS=checks, TIMELINE=timeline, STRUGGLES=struggles, ADVICE=advice, METRICS=metrics, LIMITATIONS=coverage + listing(report["limitations"])))


def placeholder(manifest, case, reasons, report=None):
    missing = {"schema_version": manifest["schema_version"], "case_key": case["case_key"], "title": case["title"], "summary": "证据不足占位页；不是完整 case 评测报告。", "identity": manifest["target"], "context": {"input_summary": "", "output_summary": "", "annotations_summary": ""}, "verdict": {"design": "unknown", "execution": "unknown", "smoothness": "unknown"}, "coverage": {"baseline": "missing", "trace": "missing", "thinking": "missing"}, "checks": [], "timeline": [], "findings": [], "struggles": [], "metrics": {k: None for k in METRICS}, "limitations": list(reasons), "evidence_index": []}
    missing["identity"] = {key: "" for key in manifest["target"]}
    if report is not None:
        missing["identity"] = report["identity"]
        missing["limitations"].append("已有 JSON 的脚本重算结果：" + LABELS[assess(manifest, report)[0]] + "；原 case 页面缺失，须重新执行 case 命令核验。")
    return render_case(manifest, missing)


def ratio(count, total):
    return "N/A" if not total else "{:.1f}%".format(100 * count / total)


def render_summary(manifest, reports, synthesis, missing_pages):
    rows = []
    counts = Counter({key: 0 for key in LABELS})
    coverage = Counter()
    axes = {axis: Counter() for axis in (("design", "execution", "tool_usage", "completion_gate") if agent_flow(manifest) else ("design", "execution"))}
    for case in sorted(manifest["cases"], key=lambda c: c["ordinal"]):
        key = case["case_key"]
        report = reports.get(key)
        status, reasons = assess(manifest, report, key in missing_pages)
        if report and status == "fail":
            details = [c["observed"] for c in report["checks"] if c["status"] == "fail"]
            details += [f["claim"] for f in scoped_findings(manifest, report) if f["confidence"] == "confirmed"]
            if details:
                reasons = list(dict.fromkeys(details)) + reasons[1:]
        elif report and status == "recovered":
            reasons += [s["description"] for s in report["struggles"]]
        counts[status] += 1
        for axis in axes:
            axis_status = "unknown"
            if report:
                relevant = [c for c in report["checks"] if c["id"] == axis]
                declared = report["verdict"].get(axis)
                if declared == "fail" or any(c["status"] == "fail" for c in relevant):
                    axis_status = "fail"
                elif agent_flow(manifest):
                    known = (report["identity"] == manifest["target"] and
                             all(report["coverage"][k] == "complete" for k in ("baseline", "trace")) and
                             relevant and all(c["status"] == "pass" for c in relevant))
                    if known and (declared == "pass" or axis not in report["verdict"]):
                        axis_status = "pass"
                elif status in ("pass", "recovered") and declared == "pass":
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
    n = len(manifest["cases"])
    stats = '<div class="stats">' + "".join('<div class="stat"><span>' + label + '</span><strong>' + str(value) + '</strong></div>' for label, value in (("全部样本 N", n), ("顺畅通过 P", counts["pass"]), ("失败", counts["fail"]), ("恢复/挣扎", counts["recovered"]), ("证据不足", counts["insufficient"]))) + '</div>'
    stats += table(["覆盖率（完整 / 全部样本）", "分子 / 分母", "比例"], [(label, str(coverage[key]) + " / " + str(n), ratio(coverage[key], n)) for key, label in (("analysis", "已可判定分析（非证据不足）"), ("reports", "JSON 报告"), ("pages", "原始 case 页面（不含占位）"), ("baseline", "设计基线"), ("trace", "调用轨迹"), ("thinking", "思考证据"))])
    axis_labels = (("design", "设计遵循率"), ("execution", "流程完成率"), ("tool_usage", "工具正确使用率"), ("completion_gate", "结束时机正确率")) if agent_flow(manifest) else (("design", "保守确认设计遵循率"), ("execution", "保守确认任务完成率"))
    stats += table(["分项指标", "通过 / 全部", "率", "失败", "unknown"], [(label, str(axes[axis]["pass"]) + " / " + str(n), ratio(axes[axis]["pass"], n), str(axes[axis]["fail"]), str(axes[axis]["unknown"])) for axis, label in axis_labels])
    stats += paragraph("各项按自身判定及同名检查分别统计；身份和基线/轨迹完整时，本项通过不因另一项失败而变unknown。流程完成包括规则允许的静默和受阻收尾，不要求后端真实落盘；平台背景发现不参与Agent通过率。" if agent_flow(manifest) else "历史三轴率保留原保守口径：整体失败时未失败轴仍为unknown。旧报告没有按新标准重审，不用于推算Agent流程通过率。")
    issues = []
    for issue in sorted(synthesis["issues"], key=lambda item: (item["priority"], item["id"])):
        examples = []
        for example in sorted(issue["examples"], key=lambda item: member(manifest, item["case_key"])["ordinal"]):
            key = example["case_key"]
            case = member(manifest, key)
            link = '<a href="cases/' + key + '.html">' + esc(str(case["ordinal"]) + " · " + case["title"]) + '</a>'
            examples.append('<li>' + link + paragraph(example["description"]) + '</li>')
        issues.append('<article><h3>' + esc(issue["priority"] + " · " + issue["title"]) + '</h3>' + paragraph("层级：" + issue["layer"]) + paragraph("问题：" + issue["problem"]) + '<p>具体 case（涉及 ' + str(len(examples)) + ' 个 case）：</p><ul>' + "".join(examples) + '</ul></article>')
    return page("summary.html", dict(TITLE=esc(manifest["title"]), META=metadata(manifest), CONCLUSION=paragraph(synthesis["summary"]), RATE=esc("顺畅通过率=" + str(counts["pass"]) + "/" + str(n) + " · " + ratio(counts["pass"], n)), STATS=stats, ISSUES="".join(issues) or paragraph("未发现本次范围内的问题"), CASES=table(["序号", "Case", "重算结论", "判定依据 / 缺口", "业务错误数"], rows, ' id="case-table"')))


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


def demo_data(version=2):
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
    if version == 2:
        manifest["schema_version"] = 2
        manifest["audit_scope"] = {"profile": "agent_flow", "focus": ["完整流程", "正确工具使用", "最终结束时机"], "agent_purpose": "处理合成请求中的两项学习建议", "completion_criteria": ["每项建议有结果或规则允许的受阻结论", "最终结束前完成全部独立项"], "exclusions": ["Tool是否Mock", "真实持久化与实际送达"]}
        for case in manifest["cases"]:
            case["required_check_ids"].extend(["tool_usage", "completion_gate"])
        for report in reports.values():
            report["schema_version"] = 2
            for key in ("tool_usage", "completion_gate"):
                report["checks"].append({"id": key, "requirement": "合成 " + key + " 检查", "expected": "工具与流程结束符合合成规则", "observed": "合成轨迹支持检查", "status": "pass", "evidence_refs": ["e-result"]})
            for finding in report["findings"]:
                finding["scope"] = "agent_flow"
    else:
        require(version == 1, "demo仅支持schema 1或2")
    return manifest, reports, synthesis


def axis_examples():
    """匿名交叉判定示例；不改变既有demo四类样本与统计。"""
    manifest, reports, _ = demo_data(version=2)
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
    examples = []
    for key, item in (("admission-fails-but-closed", admission), ("unfinished-item", unfinished), ("rule-prefetch", prefetch)):
        validate_case(item, manifest)
        examples.append({"id": key, "description": item["summary"], "verdict": item["verdict"],
                         "check_statuses": {c["id"]: c["status"] for c in item["checks"]},
                         "classification": assess(manifest, item)[0]})
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
