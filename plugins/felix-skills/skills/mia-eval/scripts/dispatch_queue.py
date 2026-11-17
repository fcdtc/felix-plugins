#!/usr/bin/env python3
"""离线调度记账；不启动子代理、不访问平台、不修改宿主容量。"""
import argparse
import json
import sys
from pathlib import Path


class QueueError(ValueError):
    pass


def require(ok, message):
    if not ok:
        raise QueueError(message)


def positive(value, name):
    require(type(value) is int and value > 0, name + " 必须为正整数")


def new_queue(case_keys, requested, capacity=None, ordinals=None):
    positive(requested, "requested")
    if capacity is not None:
        positive(capacity, "capacity")
    require(isinstance(case_keys, list) and all(isinstance(k, str) and k for k in case_keys), "case_keys 必须是字符串数组")
    require(len(case_keys) == len(set(case_keys)), "case_key 重复")
    ordinals = list(range(1, len(case_keys) + 1)) if ordinals is None else ordinals
    require(isinstance(ordinals, list) and len(ordinals) == len(case_keys), "ordinal 必须与冻结 case 清单一一对应")
    for ordinal in ordinals:
        positive(ordinal, "ordinal")
    require(len(ordinals) == len(set(ordinals)), "ordinal 重复")
    return {"requested": requested, "capacity": capacity,
            "cases": {key: {"state": "pending", "worker_id": None, "attempt": 0, "ordinal": ordinal}
                      for key, ordinal in zip(case_keys, ordinals)}}


def validate(queue):
    require(set(queue) == {"requested", "capacity", "cases"}, "调度账字段不匹配")
    positive(queue["requested"], "requested")
    if queue["capacity"] is not None:
        positive(queue["capacity"], "capacity")
    require(isinstance(queue["cases"], dict), "cases 必须是对象")
    workers, ordinals = [], []
    for key, row in queue["cases"].items():
        require(isinstance(key, str) and bool(key), "case_key 不能为空")
        require(set(row) in ({"state", "worker_id", "attempt"}, {"state", "worker_id", "attempt", "ordinal"}), "case 调度字段不匹配")
        if "ordinal" in row:
            positive(row["ordinal"], "ordinal")
            ordinals.append(row["ordinal"])
        require(row["state"] in {"pending", "reserved", "running", "report-ready", "repair-pending", "repair-reserved", "repairing", "done", "insufficient"}, "调度状态不合法")
        require(type(row["attempt"]) is int and row["attempt"] >= 0, "attempt 不合法")
        worker = row["worker_id"]
        require(worker is None or (isinstance(worker, str) and bool(worker)), "worker_id 不合法")
        if row["state"] in {"running", "report-ready", "repair-pending", "repair-reserved", "repairing", "done"}:
            require(worker is not None, "已分派 case 缺 worker_id")
        if worker is not None:
            workers.append(worker)
    require(len(workers) == len(set(workers)), "一个 worker 不能初审多个 case")
    require(len(ordinals) == len(set(ordinals)), "ordinal 重复")
    return queue


def status(queue):
    validate(queue)
    limit = min(queue["requested"], queue["capacity"]) if queue["capacity"] is not None else queue["requested"]
    active = [key for key, row in queue["cases"].items() if row["state"] in {"reserved", "running", "report-ready", "repair-reserved", "repairing"}]
    pending = [key for key, row in queue["cases"].items() if row["state"] in {"pending", "repair-pending"}]
    return {"requested": queue["requested"], "confirmed_capacity": queue["capacity"],
            "limit": limit, "active": active, "available": max(0, limit - len(active)),
            "pending": pending, "finished": [key for key, row in queue["cases"].items() if row["state"] in {"done", "insufficient"}]}


def apply(queue, event, case_key=None, worker_id=None, capacity=None, ordinal=None):
    validate(queue)
    if event == "capacity":
        positive(capacity, "capacity")
        queue["capacity"] = capacity
        return status(queue)
    if event == "status":
        return status(queue)
    require(case_key in queue["cases"], "case_key 不在冻结清单中")
    row = queue["cases"][case_key]
    if event == "reserve":
        require(row["state"] == "pending", "只能预占待初审 case")
        require(status(queue)["available"] > 0, "没有空槽位")
        row["state"] = "reserved"
    elif event == "started":
        require(row["state"] == "reserved", "启动前必须预占槽位")
        require("ordinal" in row, "历史调度账缺 ordinal；先按冻结 manifest 补齐后启动")
        positive(ordinal, "首条回传 ordinal")
        require(ordinal == row["ordinal"], "首条回传 ordinal 与冻结 case_key 不一致")
        require(isinstance(worker_id, str) and bool(worker_id), "started 必须提供 worker_id")
        require(all(r["worker_id"] != worker_id for r in queue["cases"].values()), "worker_id 已用于其他 case")
        row.update(state="running", worker_id=worker_id, attempt=1)
    elif event == "start-rejected":
        require(row["state"] == "reserved", "只能释放启动被拒的预占")
        row["state"] = "pending"
    elif event == "report-ready":
        require(row["state"] in {"running", "repairing"}, "没有正在交付报告的任务")
        require(worker_id == row["worker_id"], "报告通知 worker_id 与 case 不一致")
        row["state"] = "report-ready"
    elif event == "finished":
        # 此事件仅在收到实际宿主 completed/停止通知后记账；报告消息不调用 finished。
        require(row["state"] in {"running", "repairing", "report-ready"}, "没有正在运行的任务")
        require(worker_id == row["worker_id"], "完成通知 worker_id 与 case 不一致")
        row["state"] = "done"
    elif event == "repair-needed":
        require(row["state"] == "done", "返修只能来自已完成报告")
        row["state"] = "repair-pending"
    elif event == "reserve-repair":
        require(row["state"] == "repair-pending", "没有待返修任务")
        require(worker_id == row["worker_id"], "返修必须复用原 worker")
        require(status(queue)["available"] > 0, "没有空槽位，返修继续排队")
        row["state"] = "repair-reserved"
    elif event == "resume-rejected":
        require(row["state"] == "repair-reserved", "只能释放返修启动被拒的预占")
        require(worker_id == row["worker_id"], "返修必须复用原 worker")
        row["state"] = "repair-pending"
    elif event == "resume":
        require(row["state"] == "repair-reserved", "返修前必须 reserve-repair 预占槽位")
        require(worker_id == row["worker_id"], "返修必须复用原 worker")
        row["state"] = "repairing"
        row["attempt"] += 1
    elif event == "insufficient":
        require(row["state"] in {"done", "pending", "repair-pending"}, "先确认 worker 已停止，再保存失败占位")
        row["state"] = "insufficient"
    else:
        raise QueueError("未知调度事件：" + event)
    return status(queue)


def file_path(value):
    path = Path(value).absolute()
    require(".." not in path.parts and not any(p.is_symlink() for p in (path,) + tuple(path.parents)), "调度账路径不能含 .. 或符号链接")
    return path


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state", required=True)
    sub = parser.add_subparsers(dest="event", required=True)
    init = sub.add_parser("init")
    init.add_argument("--manifest", required=True)
    init.add_argument("--requested", required=True, type=int)
    init.add_argument("--capacity", type=int)
    sub.add_parser("status")
    capacity = sub.add_parser("capacity")
    capacity.add_argument("--capacity", required=True, type=int)
    for name in ("reserve", "started", "start-rejected", "report-ready", "finished", "repair-needed", "reserve-repair", "resume-rejected", "resume", "insufficient"):
        command = sub.add_parser(name)
        command.add_argument("--case-key", required=True)
        if name in {"started", "report-ready", "finished", "reserve-repair", "resume-rejected", "resume"}:
            command.add_argument("--worker-id", required=True)
        if name == "started":
            command.add_argument("--ordinal", required=True, type=int)
    args = parser.parse_args(argv)
    try:
        path = file_path(args.state)
        if args.event == "init":
            require(not path.exists(), "调度账已存在，不能重新初始化")
            manifest = json.loads(file_path(args.manifest).read_text(encoding="utf-8"))
            queue = new_queue([c["case_key"] for c in manifest["cases"]], args.requested, args.capacity,
                              [c["ordinal"] for c in manifest["cases"]])
            result = status(queue)
        else:
            queue = json.loads(path.read_text(encoding="utf-8"))
            result = apply(queue, args.event, getattr(args, "case_key", None), getattr(args, "worker_id", None),
                           getattr(args, "capacity", None), getattr(args, "ordinal", None))
        if args.event != "status":
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("x" if args.event == "init" else "w", encoding="utf-8") as stream:
                stream.write(json.dumps(queue, ensure_ascii=False, indent=2) + "\n")
            path.chmod(0o600)
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except (QueueError, OSError, ValueError, KeyError, TypeError) as exc:
        print("调度记账失败：" + str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
