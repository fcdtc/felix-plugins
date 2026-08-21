#!/usr/bin/env python3
"""
共享初始化脚本：提前装好依赖并预热 whisper 模型，Bilibili / YouTube 共用。

用法（lang 可选 en/zh，默认两个都准备）：
    python3 setup_whisper.py [en|zh|all]
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common  # noqa: E402


def main():
    lang = sys.argv[1] if len(sys.argv) > 1 else 'all'
    langs = list(common.MODELS) if lang == 'all' else [lang]
    if any(l not in common.MODELS for l in langs):
        print(f'不支持的语言: {lang}（可选 {" / ".join(common.MODELS)} / all）')
        sys.exit(1)
    common.ensure_deps()
    for l in langs:
        print(f'[setup] 准备 {l} 模型: {common.MODELS[l]}', flush=True)
        common.ensure_model(l)
    print('[setup] whisper 初始化完成，Bilibili / YouTube 转写均可直接使用', flush=True)


if __name__ == '__main__':
    main()
